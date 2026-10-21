"""纸面账户当日日志：记录一键日课 / 股价唤醒 / 对话 Agent，以及当时观察状态。"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime
from typing import Any, Dict, Iterator, List, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import text as sa_text

from data_layer.db_manager import storage_manager
from server.paper_broker_service import (
    _f,
    _row_to_dict,
    _to_date,
    paper_broker_service,
)

logger = logging.getLogger(__name__)
SH = ZoneInfo("Asia/Shanghai")

# 当前循环的触发来源，供盘中做T/LLM 写入日志时读取
activity_trigger: ContextVar[str] = ContextVar("paper_activity_trigger", default="")


@contextmanager
def trigger_scope(source: str) -> Iterator[str]:
    token = activity_trigger.set(source or "")
    try:
        yield source or ""
    finally:
        activity_trigger.reset(token)

TRIGGER_TITLE = {
    "manual_daily_lesson": "点击一键日课",
    "scheduler_daily_lesson": "定时器开市日课",
    "price_scan": "股价触发盘中扫描",
    "manual_price_scan": "手动扫描分时做T",
    "scheduler_exposure": "13:00 仓位规则",
    "chat_agent": "对话唤醒交易 Agent",
    "user_button": "手动操作",
    "risk_check": "回撤风控",
}

ACTIVITY_DDL = [
    """
    CREATE TABLE IF NOT EXISTS paper_activity_log (
        id              SERIAL PRIMARY KEY,
        account_id      INT           NOT NULL REFERENCES paper_account(id) ON DELETE CASCADE,
        trade_date      DATE          NOT NULL,
        event_type      VARCHAR(32)   NOT NULL,
        trigger_source  VARCHAR(32)   NOT NULL DEFAULT '',
        ts_code         VARCHAR(16),
        stock_name      VARCHAR(64),
        title           TEXT,
        detail          TEXT,
        observation     JSONB,
        llm_woken       BOOLEAN       DEFAULT FALSE,
        llm_wake_index  INT,
        llm_model       VARCHAR(64),
        llm_raw         TEXT,
        action          VARCHAR(24),
        quantity        NUMERIC(20,4),
        price           NUMERIC(16,4),
        order_id        INT,
        run_id          INT,
        meta            JSONB,
        created_at      TIMESTAMPTZ   DEFAULT NOW()
    )
    """,
]

ACTIVITY_INDEX_DDL = [
    "CREATE INDEX IF NOT EXISTS idx_paper_activity_acct_date ON paper_activity_log (account_id, trade_date DESC, id DESC)",
    "CREATE INDEX IF NOT EXISTS idx_paper_activity_wake ON paper_activity_log (account_id, trade_date, llm_woken)",
]


class PaperActivityService:
    def __init__(self):
        self._engine = None
        self._ready = False

    @property
    def engine(self):
        if self._engine is None:
            self._engine = storage_manager.get_postgres_engine()
        return self._engine

    def _ensure(self) -> None:
        if self._ready:
            return
        if not self.engine:
            raise RuntimeError("PostgreSQL 未连接")
        with self.engine.begin() as conn:
            for ddl in ACTIVITY_DDL:
                conn.execute(sa_text(ddl))
            for idx in ACTIVITY_INDEX_DDL:
                try:
                    conn.execute(sa_text(idx))
                except Exception as e:
                    logger.warning("活动日志索引: %s", e)
        self._ready = True

    def today(self) -> date:
        return datetime.now(SH).date()

    def _stock_name(self, conn, ts_code: Optional[str]) -> str:
        if not ts_code:
            return ""
        try:
            row = conn.execute(
                sa_text("SELECT name FROM ref_stock_basic WHERE ts_code = :c LIMIT 1"),
                {"c": ts_code},
            ).fetchone()
            return str(row.name or "") if row else ""
        except Exception:
            return ""

    def stock_names(self, codes: List[str]) -> Dict[str, str]:
        self._ensure()
        codes = [c for c in codes if c]
        if not codes:
            return {}
        placeholders = ",".join(f":c{i}" for i in range(len(codes)))
        params = {f"c{i}": c for i, c in enumerate(codes)}
        with self.engine.connect() as conn:
            try:
                rows = conn.execute(
                    sa_text(
                        f"SELECT ts_code, name FROM ref_stock_basic WHERE ts_code IN ({placeholders})"
                    ),
                    params,
                ).fetchall()
            except Exception as e:
                logger.warning("股票名称查询失败: %s", e)
                return {}
        return {str(r.ts_code): str(r.name or "") for r in rows}

    def log_event(
        self,
        account_id: int,
        *,
        event_type: str,
        trigger_source: str = "",
        ts_code: Optional[str] = None,
        stock_name: Optional[str] = None,
        title: Optional[str] = None,
        detail: Optional[str] = None,
        observation: Optional[Dict[str, Any]] = None,
        llm_woken: bool = False,
        llm_model: Optional[str] = None,
        llm_raw: Optional[str] = None,
        action: Optional[str] = None,
        quantity: Optional[float] = None,
        price: Optional[float] = None,
        order_id: Optional[int] = None,
        run_id: Optional[int] = None,
        meta: Optional[Dict[str, Any]] = None,
        trade_date: Optional[date] = None,
    ) -> Dict[str, Any]:
        self._ensure()
        d = trade_date or self.today()
        src = trigger_source or activity_trigger.get() or "user_button"
        with self.engine.begin() as conn:
            name = stock_name if stock_name is not None else self._stock_name(conn, ts_code)
            wake_idx = None
            if llm_woken:
                row_n = conn.execute(
                    sa_text(
                        """
                        SELECT COALESCE(MAX(llm_wake_index), 0) AS n
                        FROM paper_activity_log
                        WHERE account_id = :aid AND trade_date = :d AND llm_woken = TRUE
                        """
                    ),
                    {"aid": account_id, "d": d},
                ).fetchone()
                wake_idx = int((row_n.n if row_n else 0) or 0) + 1
            if llm_woken and not title:
                title = f"第 {wake_idx} 次唤醒大模型 · {TRIGGER_TITLE.get(src, src)}"
                if ts_code:
                    title += f" · {ts_code}"
            row = conn.execute(
                sa_text(
                    """
                    INSERT INTO paper_activity_log (
                        account_id, trade_date, event_type, trigger_source,
                        ts_code, stock_name, title, detail, observation,
                        llm_woken, llm_wake_index, llm_model, llm_raw,
                        action, quantity, price, order_id, run_id, meta
                    ) VALUES (
                        :aid, :d, :etype, :src,
                        :code, :sname, :title, :detail, CAST(:obs AS jsonb),
                        :woken, :widx, :model, :raw,
                        :action, :qty, :px, :oid, :rid, CAST(:meta AS jsonb)
                    )
                    RETURNING *
                    """
                ),
                {
                    "aid": account_id,
                    "d": d,
                    "etype": (event_type or "note")[:32],
                    "src": (src or "")[:32],
                    "code": ts_code,
                    "sname": (name or "")[:64],
                    "title": (title or TRIGGER_TITLE.get(src, src) or event_type)[:500],
                    "detail": (detail or "")[:8000],
                    "obs": json.dumps(observation or {}, ensure_ascii=False, default=str),
                    "woken": bool(llm_woken),
                    "widx": wake_idx,
                    "model": (llm_model or "")[:64] or None,
                    "raw": (llm_raw or "")[:8000] or None,
                    "action": (action or "")[:24] or None,
                    "qty": quantity,
                    "px": price,
                    "oid": order_id,
                    "rid": run_id,
                    "meta": json.dumps(meta or {}, ensure_ascii=False, default=str),
                },
            ).fetchone()
        return {"ok": True, "event": _row_to_dict(row)}

    def log_llm_wake(
        self,
        account_id: int,
        *,
        ts_code: Optional[str] = None,
        observation: Optional[Dict[str, Any]] = None,
        llm_raw: Optional[str] = None,
        llm_model: Optional[str] = None,
        action: Optional[str] = None,
        quantity: Optional[float] = None,
        price: Optional[float] = None,
        order_id: Optional[int] = None,
        run_id: Optional[int] = None,
        detail: Optional[str] = None,
        trade_date: Optional[date] = None,
        trigger_source: Optional[str] = None,
    ) -> Dict[str, Any]:
        src = trigger_source or activity_trigger.get() or "price_scan"
        obs = observation or {}
        snap_bits = []
        if obs.get("clock"):
            snap_bits.append(f"时间 {obs.get('clock')}")
        if obs.get("price") is not None:
            snap_bits.append(f"现价 {obs.get('price')}")
        if obs.get("bias"):
            snap_bits.append(f"初判 {obs.get('bias')}")
        if obs.get("session_high") is not None:
            snap_bits.append(f"截至高 {obs.get('session_high')}")
        if obs.get("session_low") is not None:
            snap_bits.append(f"截至低 {obs.get('session_low')}")
        if obs.get("user_query"):
            snap_bits.append(f"指令 {obs.get('user_query')}")
        compact = "，".join(str(x) for x in snap_bits)
        return self.log_event(
            account_id,
            event_type="llm_wake",
            trigger_source=src,
            ts_code=ts_code,
            title=None,
            detail=detail or compact,
            observation=obs,
            llm_woken=True,
            llm_model=llm_model,
            llm_raw=llm_raw,
            action=action,
            quantity=quantity,
            price=price,
            order_id=order_id,
            run_id=run_id,
            trade_date=trade_date,
        )

    def get_journal(self, account_id: int, trade_date: Optional[str] = None) -> Dict[str, Any]:
        self._ensure()
        d = _to_date(trade_date) or self.today()
        acct_res = paper_broker_service.get_account(account_id, mark=True)
        if not acct_res.get("ok"):
            return acct_res
        positions = enrich_positions(
            acct_res.get("positions") or [],
            equity=_f((acct_res.get("account") or {}).get("equity")),
            names=None,
        )
        names = {p["ts_code"]: p.get("name") or "" for p in positions}

        with self.engine.connect() as conn:
            events = conn.execute(
                sa_text(
                    """
                    SELECT * FROM paper_activity_log
                    WHERE account_id = :aid AND trade_date = :d
                    ORDER BY id DESC
                    LIMIT 300
                    """
                ),
                {"aid": account_id, "d": d},
            ).fetchall()
            fills = conn.execute(
                sa_text(
                    """
                    SELECT * FROM paper_fill
                    WHERE account_id = :aid AND trade_date = :d
                    ORDER BY id DESC
                    LIMIT 100
                    """
                ),
                {"aid": account_id, "d": d},
            ).fetchall()

        event_list = [_row_to_dict(r) for r in events]
        fill_list = [_row_to_dict(r) for r in fills]
        missing = [f["ts_code"] for f in fill_list if f.get("ts_code") and f["ts_code"] not in names]
        if missing:
            names.update(self.stock_names(missing))
        for f in fill_list:
            f["name"] = names.get(f.get("ts_code") or "", "")
        for e in event_list:
            obs = e.get("observation")
            if isinstance(obs, str):
                try:
                    e["observation"] = json.loads(obs)
                except Exception:
                    e["observation"] = {}
            elif obs is None:
                e["observation"] = {}
            if not e.get("stock_name") and e.get("ts_code"):
                e["stock_name"] = names.get(e["ts_code"], "")
            e["trigger_label"] = TRIGGER_TITLE.get(e.get("trigger_source") or "", e.get("trigger_source") or "")

        wake_n = sum(1 for e in event_list if e.get("llm_woken"))
        ops = [e for e in event_list if e.get("event_type") in ("session_start", "order", "fill", "cycle_step", "llm_wake")]
        src_counts: Dict[str, int] = {}
        for e in event_list:
            src = e.get("trigger_source") or ""
            if src:
                src_counts[src] = src_counts.get(src, 0) + (1 if e.get("event_type") == "session_start" else 0)
        lesson_n = sum(1 for e in event_list if e.get("event_type") == "session_start" and e.get("trigger_source") in ("manual_daily_lesson", "scheduler_daily_lesson"))
        price_wake = sum(1 for e in event_list if e.get("llm_woken") and e.get("trigger_source") in ("price_scan", "manual_price_scan"))
        chat_wake = sum(1 for e in event_list if e.get("llm_woken") and e.get("trigger_source") == "chat_agent")
        trade_n = len(fill_list)
        bits = []
        if lesson_n:
            bits.append(f"一键/定时日课 {lesson_n} 次")
        if price_wake:
            bits.append(f"股价扫描唤醒模型 {price_wake} 次")
        if chat_wake:
            bits.append(f"对话唤醒 {chat_wake} 次")
        if trade_n:
            bits.append(f"成交 {trade_n} 笔")
        if not bits:
            bits.append("当日尚无操作")

        return {
            "ok": True,
            "account_id": account_id,
            "trade_date": d.isoformat(),
            "account": acct_res.get("account"),
            "positions": positions,
            "events": event_list,
            "fills": fill_list,
            "llm_wake_count": wake_n,
            "operation_count": len(ops),
            "fill_count": trade_n,
            "summary": "；".join(bits),
            "trigger_counts": {
                "daily_lesson": lesson_n,
                "price_llm_wake": price_wake,
                "chat_llm_wake": chat_wake,
            },
        }


def enrich_positions(
    positions: List[Dict[str, Any]],
    *,
    equity: float = 0.0,
    names: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    codes = [p.get("ts_code") for p in positions if p.get("ts_code")]
    if names is None:
        try:
            names = paper_activity_service.stock_names(codes)
        except Exception:
            names = {}
    out = []
    for p in positions:
        d = dict(p)
        qty = _f(d.get("quantity"))
        avg = _f(d.get("avg_cost"))
        px = _f(d.get("market_price")) if str(d.get("quote_source") or "") == "intraday" else 0.0
        quoted = str(d.get("quote_source") or "") == "intraday" and px > 0
        if not quoted:
            d["name"] = d.get("name") or names.get(d.get("ts_code") or "", "") or ""
            d["market_price"] = None
            d["market_value"] = None
            d["unrealized_pnl"] = None
            d["cost_amount"] = round(qty * avg, 4)
            d["pnl_pct"] = None
            d["weight"] = None
            d["pnl_ready"] = False
            out.append(d)
            continue
        mv = _f(d.get("market_value")) if d.get("market_value") is not None else qty * px
        pnl = _f(d.get("unrealized_pnl")) if d.get("unrealized_pnl") is not None else (px - avg) * qty
        d["name"] = d.get("name") or names.get(d.get("ts_code") or "", "") or ""
        d["market_price"] = round(px, 4)
        d["market_value"] = round(mv, 4)
        d["unrealized_pnl"] = round(pnl, 4)
        d["cost_amount"] = round(qty * avg, 4)
        d["pnl_pct"] = round(px / avg - 1.0, 6) if avg > 0 else None
        d["weight"] = round(mv / equity, 6) if equity and equity > 0 else None
        d["pnl_ready"] = True
        out.append(d)
    return out


paper_activity_service = PaperActivityService()

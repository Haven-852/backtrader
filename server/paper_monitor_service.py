"""
模拟交易 · 现实自选监控（不绑定虚拟账户持仓）

- 用户手动添加要盯的股票（可填真实成本）
- 盘中/手动扫描：大涨跌、冲高回落、相对成本止损止盈
- 触发后用大模型分析「为什么出现」，整理成 Markdown 文档推送
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy import text as sa_text

logger = logging.getLogger(__name__)
SH = ZoneInfo("Asia/Shanghai")

MONITOR_DDL = [
    """
    CREATE TABLE IF NOT EXISTS paper_monitor_pool (
        id              SERIAL PRIMARY KEY,
        ts_code         VARCHAR(24) NOT NULL UNIQUE,
        name            VARCHAR(64),
        cost_basis      NUMERIC(16,4),
        note            TEXT,
        active          BOOLEAN DEFAULT TRUE,
        created_at      TIMESTAMPTZ DEFAULT NOW(),
        updated_at      TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS paper_monitor_state (
        ts_code         VARCHAR(24) NOT NULL,
        trade_date      DATE NOT NULL,
        day_high        NUMERIC(16,4),
        day_low         NUMERIC(16,4),
        last_price      NUMERIC(16,4),
        pre_close       NUMERIC(16,4),
        updated_at      TIMESTAMPTZ DEFAULT NOW(),
        PRIMARY KEY (ts_code, trade_date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS paper_monitor_dedupe (
        ts_code         VARCHAR(24) NOT NULL,
        rule_id         VARCHAR(32) NOT NULL,
        trade_date      DATE NOT NULL,
        last_fired_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (ts_code, rule_id, trade_date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS paper_monitor_report (
        id              SERIAL PRIMARY KEY,
        trade_date      DATE NOT NULL,
        title           VARCHAR(200),
        why_summary     TEXT,
        markdown        TEXT NOT NULL,
        triggers        JSONB,
        symbols         JSONB,
        notified        BOOLEAN DEFAULT FALSE,
        notify_result   TEXT,
        model           VARCHAR(64),
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_paper_monitor_report_d ON paper_monitor_report (trade_date DESC, id DESC)",
    "CREATE INDEX IF NOT EXISTS idx_paper_monitor_pool_active ON paper_monitor_pool (active, ts_code)",
]

MONITOR_CONFIG_DEFAULTS: Dict[str, Any] = {
    "monitor_auto_enabled": False,
    "monitor_daily_drop_pct": -0.04,
    "monitor_daily_rise_pct": 0.06,
    "monitor_peak_pullback_pct": -0.03,
    "monitor_stop_loss_pct": -0.05,
    "monitor_take_profit_pct": 0.08,
    "monitor_cooldown_minutes": 45,
    "monitor_use_llm": True,
    "monitor_model": "deepseek-chat",
    "monitor_report_prompt": "",
    "monitor_enable_daily_drop": True,
    "monitor_enable_daily_rise": True,
    "monitor_enable_peak_pullback": True,
    "monitor_enable_stop_loss": True,
    "monitor_enable_take_profit": True,
    "monitor_last_scan": None,
    "monitor_last_scan_bucket": None,
}

DEFAULT_REPORT_PROMPT = (
    "请输出一份中文 Markdown 监控简报，结构必须如下：\n"
    "1. 标题用 `# 自选股监控简报 · 日期`。\n"
    "2. 二级标题 `## 为什么会出现这些情况`，用 3～8 句话说明触发原因。"
    "可以合理推断，但要标明不确定。\n"
    "3. `## 触发明细`，用列表列出每只股票、规则、现价、昨收和成本。\n"
    "4. `## 个股简析`，对每只触发股票各写一小段。\n"
    "5. `## 关注建议`，只写观察点，强调风险，不做荐股承诺。"
)


def _f(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        x = float(v)
        if x != x:
            return default
        return x
    except (TypeError, ValueError):
        return default


def _to_date(v: Any) -> Optional[date]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v).strip().replace("/", "-")[:10])
    except Exception:
        return None


def _jsonable(obj: Any) -> Any:
    """把 Decimal / date / datetime 等转成 JSON 可序列化类型。"""
    if obj is None:
        return None
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    return obj


def _row(row) -> Dict[str, Any]:
    if row is None:
        return {}
    d = dict(row._mapping)
    for k, v in list(d.items()):
        if isinstance(v, Decimal):
            d[k] = float(v)
        elif hasattr(v, "isoformat"):
            d[k] = v.isoformat()
    return d


class PaperMonitorService:
    def __init__(self) -> None:
        self._engine = None
        self._ready = False

    @property
    def engine(self):
        if self._engine is None:
            from server.storage_service import storage_manager

            self._engine = storage_manager.get_postgres_engine()
            if self._engine is None:
                raise RuntimeError("PostgreSQL 不可用")
        return self._engine

    def _ensure(self) -> None:
        if self._ready:
            return
        with self.engine.begin() as conn:
            for ddl in MONITOR_DDL:
                conn.execute(sa_text(ddl))
        from server import paper_cycle_service as pcs
        from server.paper_cycle_service import paper_cycle_service

        paper_cycle_service._ensure()
        for k, v in MONITOR_CONFIG_DEFAULTS.items():
            if k not in pcs.DEFAULT_CONFIG:
                pcs.DEFAULT_CONFIG[k] = v
        cfg = paper_cycle_service.get_config().get("config") or {}
        patch = {k: v for k, v in MONITOR_CONFIG_DEFAULTS.items() if k not in cfg}
        if patch:
            paper_cycle_service.update_config(patch)
        self._ready = True

    def _cfg(self) -> Dict[str, Any]:
        from server.paper_cycle_service import paper_cycle_service

        cfg = paper_cycle_service.get_config().get("config") or {}
        out = dict(MONITOR_CONFIG_DEFAULTS)
        out.update({k: cfg[k] for k in MONITOR_CONFIG_DEFAULTS if k in cfg})
        return out

    def get_settings(self) -> Dict[str, Any]:
        self._ensure()
        from server.paper_alert_service import paper_alert_service

        settings = self._cfg()
        return {
            "ok": True,
            "default_report_prompt": DEFAULT_REPORT_PROMPT,
            "settings": settings,
            "channels": paper_alert_service.available_channels(),
            "pool_count": self.list_pool(active_only=True).get("count", 0),
        }

    def update_settings(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        self._ensure()
        from server import paper_cycle_service as pcs
        from server.paper_cycle_service import paper_cycle_service

        for k, v in MONITOR_CONFIG_DEFAULTS.items():
            if k not in pcs.DEFAULT_CONFIG:
                pcs.DEFAULT_CONFIG[k] = v
        clean = {k: patch[k] for k in patch if k in MONITOR_CONFIG_DEFAULTS}
        if isinstance(clean.get("monitor_report_prompt"), str):
            clean["monitor_report_prompt"] = clean["monitor_report_prompt"].strip()[:2000]
        if clean:
            paper_cycle_service.update_config(clean)
        return self.get_settings()

    # ------------------------------------------------------------------ #
    # pool CRUD
    # ------------------------------------------------------------------ #
    def resolve_symbol(self, raw: str) -> Dict[str, Any]:
        self._ensure()
        s = (raw or "").strip().upper()
        if not s:
            return {"ok": False, "error": "代码为空"}
        with self.engine.connect() as conn:
            if "." in s:
                row = conn.execute(
                    sa_text(
                        "SELECT ts_code, name FROM ref_stock_basic WHERE ts_code = :c LIMIT 1"
                    ),
                    {"c": s},
                ).fetchone()
            else:
                digits = re.sub(r"\D", "", s)
                row = conn.execute(
                    sa_text(
                        """
                        SELECT ts_code, name FROM ref_stock_basic
                        WHERE ts_code LIKE :p OR symbol = :d
                        ORDER BY ts_code LIMIT 1
                        """
                    ),
                    {"p": f"{digits}.%", "d": digits},
                ).fetchone()
            if not row and "." not in s:
                # 常见沪深后缀猜测
                for suf in (".SH", ".SZ", ".BJ"):
                    row = conn.execute(
                        sa_text(
                            "SELECT ts_code, name FROM ref_stock_basic WHERE ts_code = :c LIMIT 1"
                        ),
                        {"c": f"{digits}{suf}"},
                    ).fetchone()
                    if row:
                        break
        if not row:
            digits_only = re.sub(r"[^\d]", "", s) or s
            code = s if "." in s else f"{digits_only}.SH"
            return {"ok": True, "ts_code": code, "name": None, "resolved": False}
        return {
            "ok": True,
            "ts_code": str(row.ts_code),
            "name": str(row.name or ""),
            "resolved": True,
        }

    def list_pool(self, active_only: bool = True) -> Dict[str, Any]:
        self._ensure()
        sql = "SELECT * FROM paper_monitor_pool"
        if active_only:
            sql += " WHERE active = TRUE"
        sql += " ORDER BY updated_at DESC, id DESC"
        with self.engine.connect() as conn:
            rows = conn.execute(sa_text(sql)).fetchall()
        return {"ok": True, "count": len(rows), "items": [_row(r) for r in rows]}

    def add_symbol(
        self,
        ts_code: str,
        *,
        name: Optional[str] = None,
        cost_basis: Optional[float] = None,
        note: Optional[str] = None,
    ) -> Dict[str, Any]:
        self._ensure()
        resolved = self.resolve_symbol(ts_code)
        if not resolved.get("ok"):
            return resolved
        code = str(resolved["ts_code"])
        display = name or resolved.get("name") or code
        cost = _f(cost_basis) if cost_basis is not None else None
        if cost is not None and cost <= 0:
            cost = None
        with self.engine.begin() as conn:
            row = conn.execute(
                sa_text(
                    """
                    INSERT INTO paper_monitor_pool
                        (ts_code, name, cost_basis, note, active, updated_at)
                    VALUES (:c, :n, :cost, :note, TRUE, NOW())
                    ON CONFLICT (ts_code) DO UPDATE SET
                        name = COALESCE(EXCLUDED.name, paper_monitor_pool.name),
                        cost_basis = COALESCE(EXCLUDED.cost_basis, paper_monitor_pool.cost_basis),
                        note = COALESCE(EXCLUDED.note, paper_monitor_pool.note),
                        active = TRUE,
                        updated_at = NOW()
                    RETURNING *
                    """
                ),
                {"c": code, "n": display, "cost": cost, "note": note},
            ).fetchone()
        return {"ok": True, "item": _row(row)}

    def remove_symbol(self, ts_code: str, *, hard: bool = False) -> Dict[str, Any]:
        self._ensure()
        code = (ts_code or "").strip().upper()
        with self.engine.begin() as conn:
            if hard:
                conn.execute(
                    sa_text("DELETE FROM paper_monitor_pool WHERE ts_code = :c"),
                    {"c": code},
                )
            else:
                conn.execute(
                    sa_text(
                        "UPDATE paper_monitor_pool SET active = FALSE, updated_at = NOW() WHERE ts_code = :c"
                    ),
                    {"c": code},
                )
        return {"ok": True, "ts_code": code}

    def update_symbol(
        self,
        ts_code: str,
        *,
        cost_basis: Optional[float] = None,
        note: Optional[str] = None,
        name: Optional[str] = None,
        active: Optional[bool] = None,
    ) -> Dict[str, Any]:
        self._ensure()
        code = (ts_code or "").strip().upper()
        sets = ["updated_at = NOW()"]
        params: Dict[str, Any] = {"c": code}
        if cost_basis is not None:
            sets.append("cost_basis = :cost")
            params["cost"] = _f(cost_basis) if _f(cost_basis) > 0 else None
        if note is not None:
            sets.append("note = :note")
            params["note"] = note
        if name is not None:
            sets.append("name = :name")
            params["name"] = name
        if active is not None:
            sets.append("active = :active")
            params["active"] = bool(active)
        with self.engine.begin() as conn:
            row = conn.execute(
                sa_text(
                    f"UPDATE paper_monitor_pool SET {', '.join(sets)} WHERE ts_code = :c RETURNING *"
                ),
                params,
            ).fetchone()
        if not row:
            return {"ok": False, "error": f"未找到 {code}"}
        return {"ok": True, "item": _row(row)}

    # ------------------------------------------------------------------ #
    # state / rules
    # ------------------------------------------------------------------ #
    def _update_state(
        self,
        ts_code: str,
        trade_date: date,
        price: float,
        pre_close: Optional[float],
    ) -> Dict[str, float]:
        with self.engine.begin() as conn:
            row = conn.execute(
                sa_text(
                    """
                    SELECT day_high, day_low, last_price, pre_close
                    FROM paper_monitor_state
                    WHERE ts_code=:c AND trade_date=:d
                    """
                ),
                {"c": ts_code, "d": trade_date},
            ).fetchone()
            if row:
                hi = max(_f(row.day_high, price), price)
                lo = min(_f(row.day_low, price) if _f(row.day_low) > 0 else price, price)
                pre = _f(row.pre_close) or _f(pre_close) or 0.0
            else:
                hi = lo = price
                pre = _f(pre_close) or 0.0
            conn.execute(
                sa_text(
                    """
                    INSERT INTO paper_monitor_state
                        (ts_code, trade_date, day_high, day_low, last_price, pre_close, updated_at)
                    VALUES (:c, :d, :hi, :lo, :px, :pre, NOW())
                    ON CONFLICT (ts_code, trade_date) DO UPDATE SET
                        day_high = EXCLUDED.day_high,
                        day_low = EXCLUDED.day_low,
                        last_price = EXCLUDED.last_price,
                        pre_close = COALESCE(EXCLUDED.pre_close, paper_monitor_state.pre_close),
                        updated_at = NOW()
                    """
                ),
                {"c": ts_code, "d": trade_date, "hi": hi, "lo": lo, "px": price, "pre": pre or None},
            )
        return {"day_high": hi, "day_low": lo, "last_price": price, "pre_close": pre}

    def _cooldown_ok(
        self,
        ts_code: str,
        rule_id: str,
        trade_date: date,
        cooldown_minutes: int,
    ) -> bool:
        with self.engine.connect() as conn:
            row = conn.execute(
                sa_text(
                    """
                    SELECT last_fired_at FROM paper_monitor_dedupe
                    WHERE ts_code=:c AND rule_id=:r AND trade_date=:d
                    """
                ),
                {"c": ts_code, "r": rule_id, "d": trade_date},
            ).fetchone()
        if not row or row.last_fired_at is None:
            return True
        last = row.last_fired_at
        if getattr(last, "tzinfo", None) is None:
            last = last.replace(tzinfo=SH)
        return (datetime.now(SH) - last.astimezone(SH)) >= timedelta(
            minutes=max(1, int(cooldown_minutes or 45))
        )

    def _mark_fired(self, ts_code: str, rule_id: str, trade_date: date) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                sa_text(
                    """
                    INSERT INTO paper_monitor_dedupe (ts_code, rule_id, trade_date, last_fired_at)
                    VALUES (:c, :r, :d, NOW())
                    ON CONFLICT (ts_code, rule_id, trade_date) DO UPDATE SET last_fired_at = NOW()
                    """
                ),
                {"c": ts_code, "r": rule_id, "d": trade_date},
            )

    def _eval_one(
        self,
        item: Dict[str, Any],
        quote: Dict[str, Any],
        cfg: Dict[str, Any],
        asof: date,
    ) -> List[Dict[str, Any]]:
        if not quote.get("ok"):
            return []
        px = _f(quote.get("price"))
        if px <= 0:
            return []
        code = str(item.get("ts_code") or "").upper()
        name = str(item.get("name") or quote.get("name") or code)
        pre = _f(quote.get("pre_close"))
        state = self._update_state(code, asof, px, pre if pre > 0 else None)
        day_high = _f(state.get("day_high"), px)
        pre = _f(state.get("pre_close")) or pre
        day_chg = (px / pre - 1.0) if pre > 0 else None
        pullback = (px / day_high - 1.0) if day_high > 0 else 0.0
        cost = _f(item.get("cost_basis"))
        cost_pnl = (px / cost - 1.0) if cost > 0 else None

        hits: List[Dict[str, Any]] = []
        cd = int(cfg.get("monitor_cooldown_minutes") or 45)

        def maybe(rule_id: str, enabled_key: str, cond: bool, thr: float, severity: str, title: str, ref: float, pct: float):
            if not cfg.get(enabled_key, True) or not cond:
                return
            if not self._cooldown_ok(code, rule_id, asof, cd):
                return
            hits.append(
                {
                    "rule_id": rule_id,
                    "severity": severity,
                    "title": title,
                    "ts_code": code,
                    "name": name,
                    "price": round(px, 4),
                    "ref_price": round(ref, 4),
                    "pct": round(pct, 6),
                    "threshold": thr,
                    "pre_close": round(pre, 4) if pre > 0 else None,
                    "day_high": round(day_high, 4),
                    "cost_basis": round(cost, 4) if cost > 0 else None,
                    "day_chg_pct": round(day_chg, 6) if day_chg is not None else None,
                    "cost_pnl_pct": round(cost_pnl, 6) if cost_pnl is not None else None,
                    "note": item.get("note"),
                    "quote_clock": quote.get("clock"),
                }
            )

        drop_thr = _f(cfg.get("monitor_daily_drop_pct"), -0.04)
        rise_thr = _f(cfg.get("monitor_daily_rise_pct"), 0.06)
        pull_thr = _f(cfg.get("monitor_peak_pullback_pct"), -0.03)
        stop_thr = _f(cfg.get("monitor_stop_loss_pct"), -0.05)
        take_thr = _f(cfg.get("monitor_take_profit_pct"), 0.08)

        if day_chg is not None:
            maybe(
                "daily_drop",
                "monitor_enable_daily_drop",
                day_chg <= drop_thr,
                drop_thr,
                "warn",
                f"大跌：相对昨收 {day_chg*100:.2f}%",
                pre,
                day_chg,
            )
            maybe(
                "daily_rise",
                "monitor_enable_daily_rise",
                day_chg >= rise_thr,
                rise_thr,
                "info",
                f"大涨：相对昨收 {day_chg*100:.2f}%",
                pre,
                day_chg,
            )
        maybe(
            "peak_pullback",
            "monitor_enable_peak_pullback",
            day_high > 0 and pullback <= pull_thr and px < day_high,
            pull_thr,
            "warn",
            f"冲高回落：自日内高点 {day_high:.3f} 回撤 {pullback*100:.2f}%",
            day_high,
            pullback,
        )
        if cost_pnl is not None:
            maybe(
                "stop_loss",
                "monitor_enable_stop_loss",
                cost_pnl <= stop_thr,
                stop_thr,
                "critical",
                f"相对你的成本止损：{cost_pnl*100:.2f}%",
                cost,
                cost_pnl,
            )
            maybe(
                "take_profit",
                "monitor_enable_take_profit",
                cost_pnl >= take_thr,
                take_thr,
                "good",
                f"相对你的成本止盈：{cost_pnl*100:.2f}%",
                cost,
                cost_pnl,
            )
        return hits

    # ------------------------------------------------------------------ #
    # LLM markdown
    # ------------------------------------------------------------------ #
    async def _llm_markdown(
        self,
        triggers: List[Dict[str, Any]],
        asof: date,
        model: str,
        report_prompt: str = "",
    ) -> Tuple[str, str, str]:
        """返回 (why_summary, markdown, raw_or_error)"""
        from server.chat_service import ChatRequest, chat_service

        lines = []
        for t in triggers:
            lines.append(
                f"- {t.get('name')}({t.get('ts_code')}) | {t.get('rule_id')} | {t.get('title')} | "
                f"现价{t.get('price')} 昨收{t.get('pre_close')} 成本{t.get('cost_basis')} "
                f"日内{((t.get('day_chg_pct') or 0)*100):.2f}% 备注:{t.get('note') or '-'}"
            )
        structure = (report_prompt or "").strip() or DEFAULT_REPORT_PROMPT
        prompt = (
            f"交易日 {asof.isoformat()}。下面是系统已经算好的监控事实，价格、涨跌幅和规则名必须原样使用：\n"
            + "\n".join(lines)
            + "\n\n没有给出的成交额、涨停家数、资金流向和公告不要编造。"
            "不要输出 JSON，不要用代码块包裹全文。\n\n"
            "写作要求：\n"
            + structure
        )
        req = ChatRequest(
            query=prompt,
            model=model or "deepseek-chat",
            temperature=0.3,
            max_tokens=2200,
            system_prompt=(
                "你是A股监控简报助手。只根据给出的监控事实，按写作要求整理 Markdown。"
                "语气克制，不做荐股承诺。"
            ),
        )
        try:
            resp = await chat_service.chat(req)
            raw = (resp.content or "").strip()
            if not raw or getattr(resp, "provider", "") == "fallback":
                return self._fallback_markdown(triggers, asof)
            why = self._extract_why(raw)
            if not raw.startswith("#"):
                raw = f"# 自选股监控简报 · {asof.isoformat()}\n\n{raw}"
            return why, raw, raw
        except Exception as e:
            logger.warning("监控 LLM 失败: %s", e)
            why, md, _ = self._fallback_markdown(triggers, asof)
            return why, md, f"LLM_ERROR: {e}"

    def _extract_why(self, md: str) -> str:
        m = re.search(
            r"##\s*为什么会出现这些情况\s*\n+([\s\S]*?)(?=\n##\s|\Z)",
            md,
        )
        if m:
            return m.group(1).strip()[:1200]
        # 取标题后第一段
        parts = re.split(r"\n#{1,3}\s+", md, maxsplit=2)
        if len(parts) >= 2:
            return parts[1].strip()[:800]
        return md.strip()[:500]

    def _fallback_markdown(
        self, triggers: List[Dict[str, Any]], asof: date
    ) -> Tuple[str, str, str]:
        why_bits = []
        for t in triggers:
            why_bits.append(
                f"{t.get('name')}({t.get('ts_code')}) 触发「{t.get('rule_id')}」："
                f"{t.get('title')}（阈值 {(_f(t.get('threshold'))*100):.1f}%）。"
            )
        why = (
            "本轮扫描按你设定的自选监控规则命中。以下为规则层结论（大模型暂不可用，未做深度归因）："
            + "".join(why_bits)
        )
        detail = "\n".join(
            f"- **{t.get('name')}** `{t.get('ts_code')}` · {t.get('title')} · "
            f"现价 {t.get('price')} · 昨收 {t.get('pre_close') or '-'} · "
            f"成本 {t.get('cost_basis') or '-'}"
            for t in triggers
        )
        md = (
            f"# 自选股监控简报 · {asof.isoformat()}\n\n"
            f"## 为什么会出现这些情况\n\n{why}\n\n"
            f"## 触发明细\n\n{detail}\n\n"
            f"## 关注建议\n\n请结合盘面与自身仓位自行判断；本简报仅作监控提醒，不构成投资建议。\n"
        )
        return why, md, md

    def _save_report(
        self,
        *,
        asof: date,
        title: str,
        why: str,
        markdown: str,
        triggers: List[Dict[str, Any]],
        notified: bool,
        notify_result: Dict[str, Any],
        model: str,
    ) -> int:
        symbols = sorted({str(t.get("ts_code")) for t in triggers if t.get("ts_code")})
        with self.engine.begin() as conn:
            row = conn.execute(
                sa_text(
                    """
                    INSERT INTO paper_monitor_report
                        (trade_date, title, why_summary, markdown, triggers, symbols,
                         notified, notify_result, model)
                    VALUES
                        (:d, :title, :why, :md, CAST(:trig AS jsonb), CAST(:syms AS jsonb),
                         :notified, :nr, :model)
                    RETURNING id
                    """
                ),
                {
                    "d": asof,
                    "title": title[:200],
                    "why": why,
                    "md": markdown,
                    "trig": json.dumps(_jsonable(triggers), ensure_ascii=False),
                    "syms": json.dumps(_jsonable(symbols), ensure_ascii=False),
                    "notified": notified,
                    "nr": json.dumps(_jsonable(notify_result), ensure_ascii=False)[:2000],
                    "model": model,
                },
            ).fetchone()
            return int(row.id)

    def list_reports(self, limit: int = 20) -> Dict[str, Any]:
        self._ensure()
        with self.engine.connect() as conn:
            rows = conn.execute(
                sa_text(
                    """
                    SELECT id, trade_date, title, why_summary, symbols, notified, model, created_at,
                           LEFT(markdown, 400) AS markdown_preview
                    FROM paper_monitor_report
                    ORDER BY id DESC LIMIT :lim
                    """
                ),
                {"lim": limit},
            ).fetchall()
        return {"ok": True, "count": len(rows), "reports": [_row(r) for r in rows]}

    def get_report(self, report_id: int) -> Dict[str, Any]:
        self._ensure()
        with self.engine.connect() as conn:
            row = conn.execute(
                sa_text("SELECT * FROM paper_monitor_report WHERE id = :id"),
                {"id": int(report_id)},
            ).fetchone()
        if not row:
            return {"ok": False, "error": "报告不存在"}
        return {"ok": True, "report": _row(row)}

    # ------------------------------------------------------------------ #
    # scan
    # ------------------------------------------------------------------ #
    async def scan(
        self,
        *,
        notify: bool = True,
        use_llm: Optional[bool] = None,
        force_report: bool = False,
    ) -> Dict[str, Any]:
        self._ensure()
        from server.paper_alert_service import paper_alert_service
        from server.paper_cycle_service import paper_cycle_service
        from server.paper_quote import last_intraday_quotes, session_context

        cfg = self._cfg()
        pool = self.list_pool(active_only=True).get("items") or []
        if not pool:
            return {"ok": True, "fired_count": 0, "message": "监控池为空，请先添加股票"}

        ctx = session_context()
        asof = _to_date(ctx.get("trade_date")) or datetime.now(SH).date()
        codes = [str(p["ts_code"]) for p in pool]
        quotes = last_intraday_quotes(codes, trade_date=asof.isoformat())

        triggers: List[Dict[str, Any]] = []
        snapshots: List[Dict[str, Any]] = []
        for item in pool:
            code = str(item["ts_code"]).upper()
            q = quotes.get(code) or {}
            cost_v = item.get("cost_basis")
            snaps = {
                "ts_code": code,
                "name": item.get("name"),
                "cost_basis": round(_f(cost_v), 4) if cost_v is not None and _f(cost_v) > 0 else None,
                "quote_ok": bool(q.get("ok")),
                "price": float(q["price"]) if q.get("price") is not None else None,
                "pre_close": float(q["pre_close"]) if q.get("pre_close") is not None else None,
                "pct_chg": float(q["pct_chg"]) if q.get("pct_chg") is not None else None,
                "clock": q.get("clock"),
                "error": q.get("error"),
            }
            snapshots.append(snaps)
            hits = self._eval_one(item, q, cfg, asof)
            for h in hits:
                self._mark_fired(h["ts_code"], h["rule_id"], asof)
                triggers.append(h)

        paper_cycle_service.update_config(
            {"monitor_last_scan": datetime.now(SH).isoformat(timespec="seconds")}
        )

        if not triggers and not force_report:
            return {
                "ok": True,
                "asof": asof.isoformat(),
                "pool_count": len(pool),
                "fired_count": 0,
                "triggers": [],
                "snapshots": snapshots,
                "message": "未触发规则",
            }

        if not triggers and force_report:
            # 强制日报：把当日涨跌概况塞进触发列表供 LLM
            for s in snapshots:
                if not s.get("quote_ok"):
                    continue
                pct = _f(s.get("pct_chg")) / 100.0 if s.get("pct_chg") is not None else None
                triggers.append(
                    {
                        "rule_id": "daily_digest",
                        "severity": "info",
                        "title": f"日报摘要：涨跌 {(_f(s.get('pct_chg'))):.2f}%",
                        "ts_code": s["ts_code"],
                        "name": s.get("name") or s["ts_code"],
                        "price": s.get("price"),
                        "ref_price": s.get("pre_close"),
                        "pct": pct,
                        "threshold": 0,
                        "pre_close": s.get("pre_close"),
                        "day_high": None,
                        "cost_basis": s.get("cost_basis"),
                        "day_chg_pct": pct,
                        "cost_pnl_pct": None,
                        "note": None,
                        "quote_clock": s.get("clock"),
                    }
                )
            if not triggers:
                return {
                    "ok": True,
                    "asof": asof.isoformat(),
                    "pool_count": len(pool),
                    "fired_count": 0,
                    "triggers": [],
                    "snapshots": snapshots,
                    "message": "强制日报失败：监控池暂无可用分时行情",
                }

        if not triggers:
            return {
                "ok": True,
                "asof": asof.isoformat(),
                "pool_count": len(pool),
                "fired_count": 0,
                "triggers": [],
                "snapshots": snapshots,
                "message": "未触发规则",
            }

        do_llm = cfg.get("monitor_use_llm", True) if use_llm is None else bool(use_llm)
        model = str(cfg.get("monitor_model") or "deepseek-chat")
        if do_llm:
            why, markdown, _raw = await self._llm_markdown(
                triggers,
                asof,
                model,
                str(cfg.get("monitor_report_prompt") or ""),
            )
        else:
            why, markdown, _raw = self._fallback_markdown(triggers, asof)
            model = "rules"

        title = f"自选股监控简报 · {asof.isoformat()} · {len(triggers)}条触发"
        # 企微 markdown 有长度限制，推送用裁剪版；完整内容入库
        push_body = markdown
        if len(push_body) > 3500:
            push_body = (
                f"**{title}**\n\n## 为什么会出现这些情况\n{why}\n\n"
                f"（全文过长已截断，请在系统「监控简报」查看完整 Markdown）\n\n"
                + markdown[:2800]
            )

        notify_res: Dict[str, Any] = {"ok": False, "results": [], "skipped": not notify}
        if notify:
            notify_res = paper_alert_service.send_notify(title, push_body)

        rid = self._save_report(
            asof=asof,
            title=title,
            why=why,
            markdown=markdown,
            triggers=triggers,
            notified=bool(notify_res.get("ok")),
            notify_result=notify_res,
            model=model,
        )
        return _jsonable(
            {
                "ok": True,
                "asof": asof.isoformat(),
                "pool_count": len(pool),
                "fired_count": len(triggers),
                "triggers": triggers,
                "snapshots": snapshots,
                "report_id": rid,
                "title": title,
                "why_summary": why,
                "markdown": markdown,
                "notified": bool(notify_res.get("ok")),
                "notify": notify_res,
                "model": model,
            }
        )


paper_monitor_service = PaperMonitorService()

"""
Paper Cycle Service - 纸面交易日/周循环

流程：
1. evening_screen  前一晚：策略 A/B 各取得分 TopN → watchlist
2. open_decide     开市：做 T hit_test 判近高/近低 → LLM 决策买卖并记理由
3. risk_check      持仓回撤 ≥ 阈值 → 无条件清可卖仓
4. daily_t0        对持仓做 T / 判断能否续涨，否则抛弃可卖仓
5. weekly_review   对比上周净值，复盘买卖，调整 A/B 分数阈值

说明：
- 卖出受 T+1 约束（仅 sellable_qty）
- LLM 只做结构化裁决与理由记录；下单由本服务执行
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text as sa_text

from data_layer.db_manager import storage_manager
from server.paper_broker_service import paper_broker_service, _f, _to_date, _row_to_dict
from server.trade_watch_service import trade_watch_service

logger = logging.getLogger(__name__)

CYCLE_DDL = [
    """
    CREATE TABLE IF NOT EXISTS paper_cycle_config (
        key         VARCHAR(64) PRIMARY KEY,
        value       JSONB NOT NULL,
        updated_at  TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS paper_cycle_run (
        id              SERIAL PRIMARY KEY,
        account_id      INT,
        step            VARCHAR(32) NOT NULL,
        asof            DATE NOT NULL,
        status          VARCHAR(16) NOT NULL DEFAULT 'ok',
        summary         JSONB,
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS paper_cycle_action (
        id              SERIAL PRIMARY KEY,
        run_id          INT REFERENCES paper_cycle_run(id) ON DELETE CASCADE,
        account_id      INT,
        asof            DATE NOT NULL,
        step            VARCHAR(32) NOT NULL,
        ts_code         VARCHAR(16),
        setup_type      VARCHAR(8),
        action          VARCHAR(16) NOT NULL,
        quantity        NUMERIC(20,4),
        price           NUMERIC(16,4),
        reason          TEXT,
        market_context  JSONB,
        t0_summary      JSONB,
        order_id        INT,
        llm_raw         TEXT,
        created_at      TIMESTAMPTZ DEFAULT NOW()
    )
    """,
]

DEFAULT_CONFIG = {
    "top_n_a": 5,
    "top_n_b": 5,
    "min_score_a": 55.0,
    "min_score_b": 60.0,
    "drawdown_stop_pct": 5.0,
    "default_buy_qty": 500,
    "lookback_guard_open": False,
    "model": "deepseek-chat",
    "scheduler_enabled": False,
    "scheduler_account_id": None,
    "scheduler_use_llm": False,
    "scheduler_evening_hour": 21,
    "scheduler_evening_minute": 0,
    "scheduler_open_hour": 9,
    "scheduler_open_minute": 30,
    "scheduler_weekly": True,
    "scheduler_last_evening": None,
    "scheduler_last_open": None,
    "scheduler_last_weekly": None,
}


def _parse_json_block(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


class PaperCycleService:
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
            for ddl in CYCLE_DDL:
                conn.execute(sa_text(ddl))
            # 初始化默认配置
            for k, v in DEFAULT_CONFIG.items():
                conn.execute(
                    sa_text(
                        """
                        INSERT INTO paper_cycle_config (key, value)
                        VALUES (:k, CAST(:v AS jsonb))
                        ON CONFLICT (key) DO NOTHING
                        """
                    ),
                    {"k": k, "v": json.dumps(v)},
                )
        self._ready = True

    # ------------------------------------------------------------------ #
    # 配置
    # ------------------------------------------------------------------ #
    def get_config(self) -> Dict[str, Any]:
        self._ensure()
        cfg = dict(DEFAULT_CONFIG)
        with self.engine.connect() as conn:
            rows = conn.execute(sa_text("SELECT key, value FROM paper_cycle_config")).fetchall()
            for r in rows:
                val = r.value
                if isinstance(val, str):
                    try:
                        val = json.loads(val)
                    except Exception:
                        pass
                cfg[r.key] = val
        return {"ok": True, "config": cfg}

    def update_config(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        self._ensure()
        with self.engine.begin() as conn:
            for k, v in (patch or {}).items():
                if k not in DEFAULT_CONFIG and k not in ("notes",):
                    continue
                conn.execute(
                    sa_text(
                        """
                        INSERT INTO paper_cycle_config (key, value, updated_at)
                        VALUES (:k, CAST(:v AS jsonb), NOW())
                        ON CONFLICT (key) DO UPDATE SET
                            value = EXCLUDED.value,
                            updated_at = NOW()
                        """
                    ),
                    {"k": k, "v": json.dumps(v, ensure_ascii=False, default=str)},
                )
        return self.get_config()

    def _cfg(self) -> Dict[str, Any]:
        return self.get_config()["config"]

    def _latest_trade_date(self) -> date:
        with self.engine.connect() as conn:
            row = conn.execute(sa_text("SELECT MAX(trade_date) d FROM stock_daily")).fetchone()
            d = _to_date(row.d) if row else None
        return d or date.today()

    def _create_run(
        self, conn, *, step: str, asof: date, account_id: Optional[int], summary: Dict[str, Any], status: str = "ok"
    ) -> int:
        row = conn.execute(
            sa_text(
                """
                INSERT INTO paper_cycle_run (account_id, step, asof, status, summary)
                VALUES (:aid, :step, :asof, :st, CAST(:sum AS jsonb))
                RETURNING id
                """
            ),
            {
                "aid": account_id,
                "step": step,
                "asof": asof,
                "st": status,
                "sum": json.dumps(summary, ensure_ascii=False, default=str),
            },
        ).fetchone()
        return int(row.id)

    def _log_action(self, conn, **kwargs) -> None:
        conn.execute(
            sa_text(
                """
                INSERT INTO paper_cycle_action (
                    run_id, account_id, asof, step, ts_code, setup_type,
                    action, quantity, price, reason, market_context, t0_summary,
                    order_id, llm_raw
                ) VALUES (
                    :run_id, :account_id, :asof, :step, :ts_code, :setup_type,
                    :action, :quantity, :price, :reason,
                    CAST(:market_context AS jsonb), CAST(:t0_summary AS jsonb),
                    :order_id, :llm_raw
                )
                """
            ),
            {
                "run_id": kwargs.get("run_id"),
                "account_id": kwargs.get("account_id"),
                "asof": kwargs.get("asof"),
                "step": kwargs.get("step"),
                "ts_code": kwargs.get("ts_code"),
                "setup_type": kwargs.get("setup_type"),
                "action": kwargs.get("action"),
                "quantity": kwargs.get("quantity"),
                "price": kwargs.get("price"),
                "reason": kwargs.get("reason"),
                "market_context": json.dumps(kwargs.get("market_context") or {}, ensure_ascii=False, default=str),
                "t0_summary": json.dumps(kwargs.get("t0_summary") or {}, ensure_ascii=False, default=str),
                "order_id": kwargs.get("order_id"),
                "llm_raw": (kwargs.get("llm_raw") or "")[:8000],
            },
        )

    # ------------------------------------------------------------------ #
    # 1) 晚间选股
    # ------------------------------------------------------------------ #
    def evening_screen(
        self,
        asof: Optional[str] = None,
        *,
        fill: bool = False,
        limit: int = 200,
    ) -> Dict[str, Any]:
        """前一晚：跑 screener A/B，各取 TopN 写入 watchlist。"""
        self._ensure()
        trade_watch_service._ensure()
        from server.screener_service import screener_service

        cfg = self._cfg()
        asof_d = _to_date(asof) or self._latest_trade_date()
        top_a = int(cfg.get("top_n_a") or 5)
        top_b = int(cfg.get("top_n_b") or 5)
        min_a = float(cfg.get("min_score_a") or 55)
        min_b = float(cfg.get("min_score_b") or 60)

        screen = screener_service.screen_setups(
            asof=asof_d.isoformat(),
            include_st=False,
            include_main=True,
            include_star=True,
            include_chinext=True,
            include_bse=False,
            include_cdr=False,
            include_a=True,
            include_b=True,
            fill=fill,
            limit=limit,
        )
        if not screen.get("ok"):
            return {"ok": False, "error": screen.get("error") or "选股失败", "screen": screen}

        hits = screen.get("hits") or screen.get("records") or []
        # screen_setups 返回结构：检查实际字段
        if not hits:
            hits = []
            for key in ("hits_a", "hits_b", "results"):
                if isinstance(screen.get(key), list):
                    hits.extend(screen[key])
        # 兼容：有的返回在 hits 字段内混合
        if not hits and isinstance(screen.get("data"), list):
            hits = screen["data"]

        # 再从 DB 读最近 run（最稳）
        with self.engine.begin() as conn:
            run = conn.execute(
                sa_text(
                    """
                    SELECT id FROM strategy_screener_run
                    WHERE asof <= :d ORDER BY asof DESC, id DESC LIMIT 1
                    """
                ),
                {"d": asof_d},
            ).fetchone()
            db_hits = []
            if run:
                db_hits = conn.execute(
                    sa_text(
                        """
                        SELECT ts_code, name, setup_type, score, reason, label, pct_chg, close
                        FROM strategy_screener_hit WHERE run_id = :rid
                        """
                    ),
                    {"rid": run.id},
                ).fetchall()
                hits = [
                    {
                        "ts_code": r.ts_code,
                        "name": r.name,
                        "setup_type": r.setup_type,
                        "score": _f(r.score),
                        "reason": r.reason or r.label,
                        "pct_chg": _f(r.pct_chg),
                        "close": _f(r.close),
                    }
                    for r in db_hits
                ]

            hits_a = sorted(
                [h for h in hits if str(h.get("setup_type")).upper() == "A" and _f(h.get("score")) >= min_a],
                key=lambda x: _f(x.get("score")),
                reverse=True,
            )[:top_a]
            hits_b = sorted(
                [h for h in hits if str(h.get("setup_type")).upper() == "B" and _f(h.get("score")) >= min_b],
                key=lambda x: _f(x.get("score")),
                reverse=True,
            )[:top_b]

            # 清空当日旧名单后写入
            conn.execute(
                sa_text("UPDATE trade_watchlist SET active = FALSE WHERE asof = :d"),
                {"d": asof_d},
            )
            saved = []
            for src, bucket in (("screener_b", hits_b), ("screener_a", hits_a)):
                for h in bucket:
                    row = conn.execute(
                        sa_text(
                            """
                            INSERT INTO trade_watchlist
                                (ts_code, name, source, reason, score, asof, active, meta)
                            VALUES
                                (:code, :name, :src, :reason, :score, :asof, TRUE, CAST(:meta AS jsonb))
                            ON CONFLICT (ts_code, asof, source) DO UPDATE SET
                                name = EXCLUDED.name,
                                reason = EXCLUDED.reason,
                                score = EXCLUDED.score,
                                active = TRUE,
                                meta = EXCLUDED.meta
                            RETURNING *
                            """
                        ),
                        {
                            "code": str(h["ts_code"]).upper(),
                            "name": h.get("name"),
                            "src": src,
                            "reason": h.get("reason") or f"晚间选股 {src}",
                            "score": _f(h.get("score")),
                            "asof": asof_d,
                            "meta": json.dumps(
                                {
                                    "setup_type": h.get("setup_type"),
                                    "pct_chg": h.get("pct_chg"),
                                    "close": h.get("close"),
                                    "pool": "main" if src.endswith("b") else "aux",
                                },
                                ensure_ascii=False,
                                default=str,
                            ),
                        },
                    ).fetchone()
                    saved.append(_row_to_dict(row))

            summary = {
                "top_n_a": top_a,
                "top_n_b": top_b,
                "min_score_a": min_a,
                "min_score_b": min_b,
                "selected_a": len(hits_a),
                "selected_b": len(hits_b),
                "watchlist": saved,
                "screen_run_id": screen.get("run_id"),
            }
            run_id = self._create_run(
                conn, step="evening_screen", asof=asof_d, account_id=None, summary=summary
            )

        return {
            "ok": True,
            "step": "evening_screen",
            "asof": asof_d.isoformat(),
            "run_id": run_id,
            "count": len(saved),
            "watchlist": saved,
            "selected_a": hits_a,
            "selected_b": hits_b,
            "config": {"min_score_a": min_a, "min_score_b": min_b, "top_n_a": top_a, "top_n_b": top_b},
        }

    # ------------------------------------------------------------------ #
    # T0 摘要
    # ------------------------------------------------------------------ #
    def _t0_brief(self, ts_code: str, trade_date: date) -> Dict[str, Any]:
        try:
            from agents.strategy.strategy_t0.tools import run_hit_test

            res = run_hit_test(
                {
                    "ts_codes": [ts_code],
                    "trade_date": trade_date.isoformat(),
                    "combos": "all",
                }
            )
        except Exception as e:
            logger.warning("hit_test 失败 %s: %s", ts_code, e)
            return {"ok": False, "error": str(e), "bias": "unknown"}

        if not res.get("ok"):
            return {"ok": False, "error": res.get("error"), "bias": "unknown"}
        items = res.get("results") or []
        if not items:
            return {"ok": False, "error": "无 hit_test 结果", "bias": "unknown"}
        item = items[0]
        if not item.get("ok"):
            return {"ok": False, "error": item.get("error"), "bias": "unknown", "raw": item}

        ranking = item.get("combo_ranking") or []
        best = ranking[0] if ranking else None
        labels = item.get("labels") or {}
        day_high = labels.get("day_high") or item.get("day_high")
        day_low = labels.get("day_low") or item.get("day_low")
        last_close = labels.get("close") or item.get("close")
        range_pos = None
        if day_high is not None and day_low is not None and last_close is not None:
            hi, lo, cl = float(day_high), float(day_low), float(last_close)
            if hi > lo:
                range_pos = (cl - lo) / (hi - lo)

        bias = "watch"
        # 近高 → 偏卖；近低 → 偏买
        if range_pos is not None:
            if range_pos >= 0.7:
                bias = "sell_high"
            elif range_pos <= 0.3:
                bias = "buy_low"

        # combo 命中高低补充
        if best:
            if best.get("hit_high") and not best.get("hit_low"):
                bias = "sell_high"
            elif best.get("hit_low") and not best.get("hit_high"):
                bias = "buy_low"
            elif best.get("hit_both"):
                # 两者都能打到：看收盘更靠哪边
                if range_pos is not None and range_pos >= 0.5:
                    bias = "sell_high"
                elif range_pos is not None:
                    bias = "buy_low"

        return {
            "ok": True,
            "bias": bias,
            "range_pos": None if range_pos is None else round(range_pos, 4),
            "day_high": day_high,
            "day_low": day_low,
            "close": last_close,
            "best_combo": best,
            "combo_ranking_top3": ranking[:3],
            "regime": item.get("regime"),
            "complete": item.get("complete"),
            "warning": item.get("warning"),
            "market_position": (
                f"当日波幅位置 {range_pos:.0%}（0%=日低, 100%=日高）"
                if range_pos is not None
                else "波幅位置未知（可能缺分时）"
            ),
        }

    async def _llm_decide(self, prompt: str, model: str) -> Tuple[Dict[str, Any], str]:
        from server.chat_service import ChatRequest, chat_service

        req = ChatRequest(
            query=prompt,
            model=model,
            temperature=0.2,
            max_tokens=1200,
            system_prompt=(
                "你是A股纸面交易裁决员。只输出一个 JSON 对象，不要 Markdown。"
                "字段: action(buy|sell|hold|clear|abandon), quantity(number), "
                "reason(string), market_position(string), confidence(0-1)。"
            ),
        )
        try:
            resp = await chat_service.chat(req)
            raw = resp.content or ""
            data = _parse_json_block(raw) or {}
            return data, raw
        except Exception as e:
            logger.warning("LLM 决策失败: %s", e)
            return {}, f"LLM_ERROR: {e}"

    def _fallback_decide(self, bias: str, sellable: float, buy_qty: float) -> Dict[str, Any]:
        if bias == "sell_high" and sellable >= 100:
            return {
                "action": "sell",
                "quantity": float(min(sellable, buy_qty) // 100 * 100) or float(sellable // 100 * 100),
                "reason": "规则回退：做T显示近高点且有可卖旧仓",
                "market_position": "近高",
                "confidence": 0.55,
            }
        if bias == "buy_low":
            return {
                "action": "buy",
                "quantity": float(buy_qty),
                "reason": "规则回退：做T显示近低点，辅/主池试探买入",
                "market_position": "近低",
                "confidence": 0.55,
            }
        return {
            "action": "hold",
            "quantity": 0,
            "reason": "规则回退：无明确高低偏置，观望",
            "market_position": "中性",
            "confidence": 0.4,
        }

    # ------------------------------------------------------------------ #
    # 2) 开市决策
    # ------------------------------------------------------------------ #
    async def open_decide(
        self,
        account_id: int,
        asof: Optional[str] = None,
        *,
        use_llm: bool = True,
        roll_t1: bool = True,
    ) -> Dict[str, Any]:
        self._ensure()
        cfg = self._cfg()
        asof_d = _to_date(asof) or self._latest_trade_date()
        buy_qty = float(cfg.get("default_buy_qty") or 500)
        guard = bool(cfg.get("lookback_guard_open"))
        model = str(cfg.get("model") or "deepseek-chat")

        if roll_t1:
            paper_broker_service.roll_t1(account_id=account_id, asof=asof_d.isoformat(), force=False)

        wl = trade_watch_service.list_watchlist(asof=asof_d.isoformat(), active_only=True)
        items = wl.get("watchlist") or []
        # 若当日无名单，尝试最近一次
        if not items:
            wl = trade_watch_service.list_watchlist(active_only=True)
            items = wl.get("watchlist") or []

        acct = paper_broker_service.get_account(account_id, mark=True)
        positions = {p["ts_code"]: p for p in (acct.get("positions") or [])}

        actions_out: List[Dict[str, Any]] = []
        with self.engine.begin() as conn:
            run_id = self._create_run(
                conn,
                step="open_decide",
                asof=asof_d,
                account_id=account_id,
                summary={"watchlist_count": len(items)},
            )

        for item in items:
            code = item["ts_code"]
            setup = "B" if "b" in str(item.get("source") or "").lower() else "A"
            # 辅池 A 仓位减半
            qty_plan = buy_qty if setup == "B" else max(100.0, (buy_qty // 2) // 100 * 100)

            t0 = self._t0_brief(code, asof_d)
            pos = positions.get(code) or {}
            sellable = _f(pos.get("sellable_qty"))
            frozen = _f(pos.get("frozen_qty"))
            held = _f(pos.get("quantity"))

            context = {
                "ts_code": code,
                "name": item.get("name"),
                "setup_type": setup,
                "pool": "main_B" if setup == "B" else "aux_A",
                "watch_score": item.get("score"),
                "watch_reason": item.get("reason"),
                "position": {"quantity": held, "sellable_qty": sellable, "frozen_qty": frozen},
                "t0": t0,
                "account_cash": (acct.get("account") or {}).get("cash"),
            }

            decision: Dict[str, Any] = {}
            llm_raw = ""
            if use_llm:
                prompt = (
                    f"交易日 {asof_d.isoformat()} 开市决策。\n"
                    f"上下文 JSON:\n{json.dumps(context, ensure_ascii=False, default=str)}\n\n"
                    "规则：\n"
                    "- 近高(sell_high)且 sellable_qty>=100 可卖旧仓；\n"
                    "- 近低(buy_low)可买，A 池仓位应更小；\n"
                    "- 当日买入不可卖(T+1)；\n"
                    "- 输出 JSON：action/quantity/reason/market_position/confidence。\n"
                )
                decision, llm_raw = await self._llm_decide(prompt, model)

            if not decision.get("action"):
                decision = self._fallback_decide(t0.get("bias") or "watch", sellable, qty_plan)

            action = str(decision.get("action") or "hold").lower()
            qty = _f(decision.get("quantity"), qty_plan)
            reason = str(decision.get("reason") or "")
            market_pos = str(
                decision.get("market_position")
                or t0.get("market_position")
                or ""
            )

            exec_result = None
            order_id = None
            px = None

            if action == "sell" and sellable >= 100:
                qty = min(qty if qty >= 100 else sellable, sellable)
                qty = (qty // 100) * 100
                if qty >= 100:
                    exec_result = paper_broker_service.place_order(
                        account_id,
                        code,
                        "sell",
                        qty,
                        lookback_guard=guard,
                        signal_date=asof_d.isoformat(),
                        reason=f"[open_decide] {reason}",
                    )
            elif action == "buy":
                qty = max(100.0, (qty // 100) * 100)
                if setup == "A":
                    qty = min(qty, qty_plan)
                exec_result = paper_broker_service.place_order(
                    account_id,
                    code,
                    "buy",
                    qty,
                    lookback_guard=guard,
                    signal_date=asof_d.isoformat(),
                    reason=f"[open_decide/{setup}] {reason}",
                )
            else:
                action = "hold"
                qty = 0

            if exec_result and exec_result.get("ok"):
                order_id = (exec_result.get("order") or {}).get("id")
                px = (exec_result.get("fill") or {}).get("price")
            elif exec_result and not exec_result.get("ok"):
                reason = f"{reason} | 执行失败: {exec_result.get('error')}"
                action = f"{action}_failed"

            row_out = {
                "ts_code": code,
                "setup_type": setup,
                "action": action,
                "quantity": qty,
                "reason": reason,
                "market_position": market_pos,
                "t0_bias": t0.get("bias"),
                "order_id": order_id,
                "exec": exec_result,
            }
            actions_out.append(row_out)

            with self.engine.begin() as conn:
                self._log_action(
                    conn,
                    run_id=run_id,
                    account_id=account_id,
                    asof=asof_d,
                    step="open_decide",
                    ts_code=code,
                    setup_type=setup,
                    action=action,
                    quantity=qty,
                    price=px,
                    reason=f"{reason} | 位置: {market_pos}",
                    market_context=context,
                    t0_summary=t0,
                    order_id=order_id,
                    llm_raw=llm_raw,
                )

        # 更新 run summary
        with self.engine.begin() as conn:
            conn.execute(
                sa_text(
                    """
                    UPDATE paper_cycle_run SET summary = CAST(:s AS jsonb)
                    WHERE id = :id
                    """
                ),
                {
                    "id": run_id,
                    "s": json.dumps(
                        {"actions": actions_out, "watchlist_count": len(items)},
                        ensure_ascii=False,
                        default=str,
                    ),
                },
            )

        return {
            "ok": True,
            "step": "open_decide",
            "asof": asof_d.isoformat(),
            "run_id": run_id,
            "account_id": account_id,
            "actions": actions_out,
        }

    # ------------------------------------------------------------------ #
    # 3) 回撤风控
    # ------------------------------------------------------------------ #
    async def risk_check(
        self,
        account_id: int,
        asof: Optional[str] = None,
        *,
        use_llm: bool = False,
    ) -> Dict[str, Any]:
        self._ensure()
        cfg = self._cfg()
        asof_d = _to_date(asof) or self._latest_trade_date()
        stop_pct = float(cfg.get("drawdown_stop_pct") or 5.0) / 100.0
        guard = bool(cfg.get("lookback_guard_open"))

        marked = paper_broker_service.mark_to_market(account_id, asof=asof_d.isoformat(), write_equity=True)
        positions = marked.get("positions") or []
        actions = []

        with self.engine.begin() as conn:
            run_id = self._create_run(
                conn,
                step="risk_check",
                asof=asof_d,
                account_id=account_id,
                summary={"stop_pct": stop_pct},
            )

        for p in positions:
            code = p["ts_code"]
            avg = _f(p.get("avg_cost"))
            px = _f(p.get("market_price"))
            sellable = _f(p.get("sellable_qty"))
            if avg <= 0 or px <= 0:
                continue
            dd = px / avg - 1.0
            if dd > -stop_pct:
                continue

            reason = f"回撤 {dd:.2%} ≤ -{stop_pct:.0%}，无条件清可卖仓"
            qty = (sellable // 100) * 100
            exec_result = None
            order_id = None
            action = "clear"
            if qty >= 100:
                exec_result = paper_broker_service.place_order(
                    account_id,
                    code,
                    "sell",
                    qty,
                    lookback_guard=guard,
                    signal_date=asof_d.isoformat(),
                    reason=f"[risk_check] {reason}",
                )
                if exec_result.get("ok"):
                    order_id = (exec_result.get("order") or {}).get("id")
                else:
                    action = "clear_failed"
                    reason += f" | {exec_result.get('error')}"
            else:
                reason += " | 无可卖仓（可能全在当日冻结）"
                action = "clear_pending_t1"

            actions.append(
                {
                    "ts_code": code,
                    "action": action,
                    "drawdown": round(dd, 4),
                    "quantity": qty,
                    "reason": reason,
                    "order_id": order_id,
                }
            )
            with self.engine.begin() as conn:
                self._log_action(
                    conn,
                    run_id=run_id,
                    account_id=account_id,
                    asof=asof_d,
                    step="risk_check",
                    ts_code=code,
                    action=action,
                    quantity=qty,
                    price=px,
                    reason=reason,
                    market_context={"avg_cost": avg, "market_price": px, "drawdown": dd},
                    order_id=order_id,
                )

        return {
            "ok": True,
            "step": "risk_check",
            "asof": asof_d.isoformat(),
            "run_id": run_id,
            "stop_pct": stop_pct,
            "actions": actions,
        }

    # ------------------------------------------------------------------ #
    # 4) 日度持仓做 T / 续涨判断
    # ------------------------------------------------------------------ #
    async def daily_t0(
        self,
        account_id: int,
        asof: Optional[str] = None,
        *,
        use_llm: bool = True,
    ) -> Dict[str, Any]:
        self._ensure()
        cfg = self._cfg()
        asof_d = _to_date(asof) or self._latest_trade_date()
        model = str(cfg.get("model") or "deepseek-chat")
        guard = bool(cfg.get("lookback_guard_open"))

        acct = paper_broker_service.get_account(account_id, mark=True)
        positions = acct.get("positions") or []
        actions = []

        with self.engine.begin() as conn:
            run_id = self._create_run(
                conn,
                step="daily_t0",
                asof=asof_d,
                account_id=account_id,
                summary={"positions": len(positions)},
            )

        for p in positions:
            code = p["ts_code"]
            sellable = _f(p.get("sellable_qty"))
            held = _f(p.get("quantity"))
            t0 = self._t0_brief(code, asof_d)
            context = {
                "ts_code": code,
                "position": p,
                "t0": t0,
                "question": "能否继续上涨？若否应 abandon 清可卖仓；近高可做T卖出部分；近低可持有或回补。",
            }

            decision = {}
            llm_raw = ""
            if use_llm:
                prompt = (
                    f"持仓日度做T/续涨裁决 {asof_d.isoformat()}\n"
                    f"{json.dumps(context, ensure_ascii=False, default=str)}\n"
                    "输出 JSON：action=hold|sell|abandon, quantity, reason, market_position, confidence。"
                    "abandon 表示抛弃该标的可卖仓。"
                )
                decision, llm_raw = await self._llm_decide(prompt, model)

            if not decision.get("action"):
                bias = t0.get("bias")
                if bias == "sell_high" and sellable >= 100:
                    decision = {
                        "action": "sell",
                        "quantity": min(sellable, 500),
                        "reason": "规则回退：近高做T卖出",
                        "market_position": t0.get("market_position"),
                    }
                elif bias == "buy_low":
                    decision = {
                        "action": "hold",
                        "quantity": 0,
                        "reason": "规则回退：近低暂持，等待续涨",
                        "market_position": t0.get("market_position"),
                    }
                else:
                    # 弱信号且跌破成本较多 → 抛弃
                    avg, px = _f(p.get("avg_cost")), _f(p.get("market_price"))
                    if avg > 0 and px / avg - 1 <= -0.03 and sellable >= 100:
                        decision = {
                            "action": "abandon",
                            "quantity": sellable,
                            "reason": "规则回退：续涨概率低且已浮亏，清可卖仓",
                            "market_position": t0.get("market_position"),
                        }
                    else:
                        decision = {
                            "action": "hold",
                            "quantity": 0,
                            "reason": "规则回退：观望",
                            "market_position": t0.get("market_position"),
                        }

            action = str(decision.get("action") or "hold").lower()
            qty = _f(decision.get("quantity"))
            reason = str(decision.get("reason") or "")
            order_id = None
            exec_result = None
            px = _f(p.get("market_price"))

            if action in ("sell", "abandon", "clear") and sellable >= 100:
                if action == "abandon" or action == "clear":
                    qty = (sellable // 100) * 100
                else:
                    qty = min(max(100.0, (qty // 100) * 100), sellable)
                if qty >= 100:
                    exec_result = paper_broker_service.place_order(
                        account_id,
                        code,
                        "sell",
                        qty,
                        lookback_guard=guard,
                        signal_date=asof_d.isoformat(),
                        reason=f"[daily_t0/{action}] {reason}",
                    )
                    if exec_result.get("ok"):
                        order_id = (exec_result.get("order") or {}).get("id")
                        px = (exec_result.get("fill") or {}).get("price") or px
                    else:
                        action = f"{action}_failed"
                        reason += f" | {exec_result.get('error')}"
            else:
                action = "hold"
                qty = 0

            actions.append(
                {
                    "ts_code": code,
                    "action": action,
                    "quantity": qty,
                    "reason": reason,
                    "t0_bias": t0.get("bias"),
                    "market_position": decision.get("market_position") or t0.get("market_position"),
                    "order_id": order_id,
                }
            )
            with self.engine.begin() as conn:
                self._log_action(
                    conn,
                    run_id=run_id,
                    account_id=account_id,
                    asof=asof_d,
                    step="daily_t0",
                    ts_code=code,
                    action=action,
                    quantity=qty,
                    price=px,
                    reason=reason,
                    market_context=context,
                    t0_summary=t0,
                    order_id=order_id,
                    llm_raw=llm_raw,
                )

        return {
            "ok": True,
            "step": "daily_t0",
            "asof": asof_d.isoformat(),
            "run_id": run_id,
            "actions": actions,
        }

    # ------------------------------------------------------------------ #
    # 5) 周复盘 + 调 A/B
    # ------------------------------------------------------------------ #
    async def weekly_review(
        self,
        account_id: int,
        asof: Optional[str] = None,
        *,
        use_llm: bool = True,
        auto_apply: bool = True,
    ) -> Dict[str, Any]:
        self._ensure()
        cfg = self._cfg()
        asof_d = _to_date(asof) or self._latest_trade_date()
        week_ago = asof_d - timedelta(days=7)
        model = str(cfg.get("model") or "deepseek-chat")

        stats = paper_broker_service.get_stats(account_id)
        equity_now = _f((stats.get("stats") or {}).get("equity"))
        initial = _f((stats.get("stats") or {}).get("initial_cash"))

        with self.engine.connect() as conn:
            prev = conn.execute(
                sa_text(
                    """
                    SELECT equity, trade_date FROM paper_equity_daily
                    WHERE account_id = :aid AND trade_date <= :d
                    ORDER BY trade_date DESC LIMIT 1
                    """
                ),
                {"aid": account_id, "d": week_ago},
            ).fetchone()
            equity_prev = _f(prev.equity) if prev else initial

            actions = conn.execute(
                sa_text(
                    """
                    SELECT setup_type, action, ts_code, reason, quantity, price, asof, step
                    FROM paper_cycle_action
                    WHERE account_id = :aid AND asof > :d
                    ORDER BY id DESC LIMIT 200
                    """
                ),
                {"aid": account_id, "d": week_ago},
            ).fetchall()
            action_rows = [_row_to_dict(r) for r in actions]

        week_return = (equity_now / equity_prev - 1.0) if equity_prev > 0 else 0.0
        by_setup = {"A": {"buys": 0, "sells": 0}, "B": {"buys": 0, "sells": 0}}
        for a in action_rows:
            st = (a.get("setup_type") or "").upper()
            if st not in by_setup:
                continue
            if str(a.get("action", "")).startswith("buy"):
                by_setup[st]["buys"] += 1
            if str(a.get("action", "")).startswith("sell") or a.get("action") in (
                "clear",
                "abandon",
            ):
                by_setup[st]["sells"] += 1

        # 简单调参：周收益为负 → 提高门槛；为正且选得少 → 略降
        new_a = float(cfg.get("min_score_a") or 55)
        new_b = float(cfg.get("min_score_b") or 60)
        notes = []
        if week_return < -0.02:
            new_a = min(80.0, new_a + 3)
            new_b = min(85.0, new_b + 3)
            notes.append("周净值下跌超2%，提高 A/B 入选分数门槛")
        elif week_return > 0.02:
            new_a = max(50.0, new_a - 1)
            new_b = max(55.0, new_b - 1)
            notes.append("周净值上涨超2%，略降门槛以增加候选")
        else:
            notes.append("周净值波动不大，维持门槛，关注执行质量")

        llm_review = ""
        if use_llm:
            prompt = (
                f"周复盘 {week_ago} ~ {asof_d}\n"
                f"equity_prev={equity_prev}, equity_now={equity_now}, week_return={week_return:.4f}\n"
                f"by_setup={json.dumps(by_setup, ensure_ascii=False)}\n"
                f"recent_actions={json.dumps(action_rows[:40], ensure_ascii=False, default=str)}\n"
                f"current_min_score_a={cfg.get('min_score_a')}, min_score_b={cfg.get('min_score_b')}\n"
                "输出 JSON：summary, what_worked, what_failed, "
                "suggest_min_score_a, suggest_min_score_b, suggest_top_n_a, suggest_top_n_b。"
            )
            data, llm_review = await self._llm_decide(prompt, model)
            if data.get("suggest_min_score_a") is not None:
                try:
                    new_a = float(data["suggest_min_score_a"])
                except Exception:
                    pass
            if data.get("suggest_min_score_b") is not None:
                try:
                    new_b = float(data["suggest_min_score_b"])
                except Exception:
                    pass
            if data.get("summary"):
                notes.append(str(data["summary"]))

        applied = None
        if auto_apply:
            applied = self.update_config(
                {
                    "min_score_a": round(new_a, 2),
                    "min_score_b": round(new_b, 2),
                    "notes": " | ".join(notes)[:500],
                }
            )

        summary = {
            "equity_prev": equity_prev,
            "equity_now": equity_now,
            "week_return": round(week_return, 6),
            "week_direction": "up" if week_return >= 0 else "down",
            "by_setup": by_setup,
            "notes": notes,
            "new_min_score_a": new_a,
            "new_min_score_b": new_b,
            "action_count": len(action_rows),
        }

        with self.engine.begin() as conn:
            run_id = self._create_run(
                conn,
                step="weekly_review",
                asof=asof_d,
                account_id=account_id,
                summary=summary,
            )
            self._log_action(
                conn,
                run_id=run_id,
                account_id=account_id,
                asof=asof_d,
                step="weekly_review",
                action="review",
                reason="；".join(notes),
                market_context=summary,
                llm_raw=llm_review,
            )

        return {
            "ok": True,
            "step": "weekly_review",
            "asof": asof_d.isoformat(),
            "run_id": run_id,
            "summary": summary,
            "config_applied": applied.get("config") if applied else None,
        }

    # ------------------------------------------------------------------ #
    # 组合：开市日跑完整日课
    # ------------------------------------------------------------------ #
    async def run_trading_day(
        self,
        account_id: int,
        asof: Optional[str] = None,
        *,
        use_llm: bool = True,
    ) -> Dict[str, Any]:
        """开市流程：T+1日切 → 开市决策 → 风控 → 日度做T。"""
        asof_d = _to_date(asof) or self._latest_trade_date()
        paper_broker_service.roll_t1(account_id=account_id, asof=asof_d.isoformat())
        open_res = await self.open_decide(account_id, asof=asof_d.isoformat(), use_llm=use_llm, roll_t1=False)
        risk_res = await self.risk_check(account_id, asof=asof_d.isoformat(), use_llm=False)
        t0_res = await self.daily_t0(account_id, asof=asof_d.isoformat(), use_llm=use_llm)
        return {
            "ok": True,
            "asof": asof_d.isoformat(),
            "open_decide": open_res,
            "risk_check": risk_res,
            "daily_t0": t0_res,
        }

    def list_runs(self, limit: int = 50, step: Optional[str] = None) -> Dict[str, Any]:
        self._ensure()
        sql = "SELECT * FROM paper_cycle_run WHERE 1=1"
        params: Dict[str, Any] = {"lim": limit}
        if step:
            sql += " AND step = :step"
            params["step"] = step
        sql += " ORDER BY id DESC LIMIT :lim"
        with self.engine.connect() as conn:
            rows = conn.execute(sa_text(sql), params).fetchall()
        return {"ok": True, "count": len(rows), "runs": [_row_to_dict(r) for r in rows]}

    def list_actions(
        self,
        account_id: Optional[int] = None,
        asof: Optional[str] = None,
        limit: int = 100,
    ) -> Dict[str, Any]:
        self._ensure()
        sql = "SELECT * FROM paper_cycle_action WHERE 1=1"
        params: Dict[str, Any] = {"lim": limit}
        if account_id is not None:
            sql += " AND account_id = :aid"
            params["aid"] = account_id
        if asof:
            sql += " AND asof = :d"
            params["d"] = _to_date(asof)
        sql += " ORDER BY id DESC LIMIT :lim"
        with self.engine.connect() as conn:
            rows = conn.execute(sa_text(sql), params).fetchall()
        return {"ok": True, "count": len(rows), "actions": [_row_to_dict(r) for r in rows]}


paper_cycle_service = PaperCycleService()

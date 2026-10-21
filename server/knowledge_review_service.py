"""知识库读取与交易复盘分析师。

复盘只判断假设是否成立，不因盈亏直接判定逻辑对错。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from sqlalchemy import text as sa_text

from data_layer.db_manager import storage_manager

logger = logging.getLogger(__name__)

REVIEW_SYSTEM_PROMPT = """你是我的交易复盘分析师。

你的任务不是判断这笔交易赚钱还是亏钱，
而是判断：

1. 原始交易假设是什么？
2. 假设中哪些成立？
3. 哪些没有成立？
4. 实际结果是否由交易逻辑导致？
5. 是否存在运气因素？
6. 是否存在执行偏差？
7. 是否存在市场状态变化？
8. 是否与我的历史失败案例相似？
9. 是否产生新的交易经验？
10. 是否值得形成新的交易规则？

严禁因为交易盈利就判定逻辑正确。
严禁因为交易亏损就判定逻辑错误。

必须区分：
Strategy Error
Timing Error
Execution Error
Risk Error
Market Regime Change
Random Outcome

只输出一个 JSON 对象，不要 Markdown。字段：
why_buy: string[]
hypothesis_if: string[]
hypothesis_then: string
actual: [{text, ok}]
scores: {strategy, direction, timing, execution, risk} 取值 ok|partial|bad
logic_result: LOGIC_CORRECT|LOGIC_INCORRECT|PARTIALLY_CORRECT|UNDETERMINED
outcome_label: string
lesson: string
failure_types: string[]
why_failed: string
core_error_not: string
core_error: string
suggested_rule: {name, description}
dimensions: {market, sector, stock, timing, execution, risk} 取值 ok|partial|bad
primary_cause: string
secondary_cause: string
severity: 轻微|中等|严重|致命
narrative: string
experiment_hypothesis: string

一次复盘只能产生经验和待验证假设。禁止输出“以后绝对不能”。禁止把单次结果写成已经生效的规则。
"""


def _row(row) -> Dict[str, Any]:
    if row is None:
        return {}
    d = dict(row._mapping)
    for k, v in list(d.items()):
        if hasattr(v, "isoformat"):
            d[k] = v.isoformat()
        elif hasattr(v, "hex") and hasattr(v, "int"):
            d[k] = str(v)
        elif hasattr(v, "as_tuple"):
            d[k] = float(v)
    return d


_CATEGORY = {
    "strategy error": "STRATEGY",
    "timing error": "TIMING",
    "execution error": "EXECUTION",
    "risk error": "RISK",
    "market regime change": "MARKET_REGIME",
    "random outcome": "LUCK",
}

_ERROR_CAUSES = {
    "市场环境错误", "板块判断错误", "个股判断错误", "入场太晚", "追高", "止损太晚",
    "仓位过大", "没有按照计划执行", "信息判断错误", "突发事件", "情绪影响",
}
_SEVERE = {"严重", "致命", "SEVERE", "FATAL"}

_LIST_SQL = """
    SELECT t.id, t.created_at, t.trade_date, t.symbol, t.stock_name, t.thesis_title, t.thesis_text,
           t.entry_reason, t.market_context, t.direction, t.status AS thesis_status,
           t.strategy_name, t.entry_conditions, t.invalidation_conditions, t.exit_conditions,
           t.planned_entry_price, t.target_price, t.stop_price, t.planned_position_pct,
           t.planned_holding_days, t.expected_return, t.expected_risk, t.confidence, t.skill_inputs,
           s.symbol AS stock_symbol, s.name AS stock_joined_name,
           o.id AS outcome_id, o.return_pct, o.realized_pnl, o.holding_days, o.outcome_type,
           o.logic_result, o.exit_reason, o.actual_vs_expected, o.market_change,
           o.max_favorable_excursion, o.max_adverse_excursion, o.buy_price AS outcome_buy, o.sell_price,
           e.executed_at, e.planned_price, e.executed_price, e.executed_quantity, e.fee, e.slippage,
           e.planned_position_pct AS exec_planned_pos, e.actual_position_pct, e.execution_deviation,
           a.id AS attribution_id, a.primary_category, a.secondary_category, a.severity,
           a.confirmed, a.redo_choice, a.mistake_note, a.dimensions, a.ai_analysis, a.evidence
    FROM trade_thesis t
    LEFT JOIN stocks s ON s.id = t.stock_id
    LEFT JOIN LATERAL (
        SELECT * FROM trade_outcome WHERE thesis_id = t.id ORDER BY completed_at DESC NULLS LAST LIMIT 1
    ) o ON TRUE
    LEFT JOIN LATERAL (
        SELECT * FROM trade_execution
        WHERE thesis_id = t.id AND COALESCE(side, 'BUY') = 'BUY'
        ORDER BY executed_at NULLS LAST LIMIT 1
    ) e ON TRUE
    LEFT JOIN LATERAL (
        SELECT * FROM attribution WHERE outcome_id = o.id ORDER BY created_at DESC LIMIT 1
    ) a ON TRUE
    ORDER BY t.created_at DESC
    LIMIT 300
"""


def _as_list(v: Any) -> List[str]:
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    if isinstance(v, str) and v.strip():
        return [s.strip() for s in v.replace("，", "、").split("、") if s.strip()]
    return []


def _followed(v: Any) -> Optional[bool]:
    if v in (True, "yes", "是", "true", "True"):
        return True
    if v in (False, "no", "否", "false", "False"):
        return False
    return None


def _skill_inputs(body: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key in ("market_view", "sector_view", "sector_name", "stock_reason", "exit_style", "exit_reason", "review_conclusion"):
        text = str(body.get(key) or "").strip()
        if text:
            out[key] = text
    for key in ("market_return_pct", "sector_return_pct", "actual_stop_price"):
        num = _num(body.get(key))
        if num is not None:
            out[key] = num
    return out


def _num(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _list_state(item: Dict[str, Any]) -> str:
    if item.get("outcome_id"):
        return "待复盘" if not item.get("attribution_id") else "已完成"
    if item.get("executed_price") is not None:
        return "持仓中"
    return "未执行"


def _reviewed(item: Dict[str, Any]) -> str:
    if not item.get("outcome_id"):
        return "—"
    return "已复盘" if item.get("attribution_id") else "—"


def _is_failure(item: Dict[str, Any]) -> bool:
    if not item.get("outcome_id"):
        return False
    if item.get("logic_result") == "LOGIC_INCORRECT":
        return True
    if (item.get("severity") or "") in _SEVERE:
        return True
    if (item.get("primary_category") or "") in _ERROR_CAUSES:
        return True
    dims = item.get("dimensions") if isinstance(item.get("dimensions"), dict) else {}
    if item.get("confirmed") and any(dims.get(k) == "bad" for k in ("timing", "execution", "risk")):
        return True
    return False


def _match_view(item: Dict[str, Any], view: str) -> bool:
    state = item.get("list_state")
    if view in ("all", ""):
        return True
    if view == "open":
        return state == "持仓中"
    if view == "pending":
        return state == "待复盘"
    if view == "win":
        return item.get("outcome_type") == "WIN"
    if view == "loss":
        return item.get("outcome_type") == "LOSS"
    if view == "idle":
        return state == "未执行"
    if view == "failure":
        return bool(item.get("is_failure"))
    if view == "logic_ok":
        return item.get("logic_result") == "LOGIC_CORRECT"
    if view == "logic_bad":
        return item.get("logic_result") == "LOGIC_INCORRECT"
    if view == "exec_bad":
        dev = item.get("execution_deviation")
        return bool(isinstance(dev, dict) and dev.get("risk"))
    if view == "exec_ok":
        dev = item.get("execution_deviation")
        return item.get("executed_price") is not None and not (isinstance(dev, dict) and dev.get("risk"))
    if view == "reuse":
        return item.get("outcome_type") == "WIN" and item.get("logic_result") == "LOGIC_CORRECT"
    if view == "watch":
        return bool(item.get("is_failure"))
    return True


def _stats(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    decided = [i for i in items if i.get("outcome_type") in ("WIN", "LOSS")]
    wins = [i for i in decided if i.get("outcome_type") == "WIN"]
    losses = [i for i in decided if i.get("outcome_type") == "LOSS"]
    win_rets = [float(i["return_pct"]) for i in wins if i.get("return_pct") is not None]
    loss_rets = [float(i["return_pct"]) for i in losses if i.get("return_pct") is not None]
    n = len(decided)
    win_rate = (len(wins) / n) if n else None
    avg_win = sum(win_rets) / len(win_rets) if win_rets else None
    avg_loss = sum(loss_rets) / len(loss_rets) if loss_rets else None
    expectancy = None
    if win_rate is not None and avg_win is not None and avg_loss is not None:
        expectancy = win_rate * avg_win + (1 - win_rate) * avg_loss
    return {
        "total": len(items),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": None if win_rate is None else round(win_rate, 4),
        "avg_win": None if avg_win is None else round(avg_win, 2),
        "avg_loss": None if avg_loss is None else round(avg_loss, 2),
        "expectancy": None if expectancy is None else round(expectancy, 2),
        "note": "胜率只统计已结束交易的盈亏次数，不是对交易质量的评价。",
    }


def _deviation(planned_pos, actual_pos, planned_price, executed_price) -> Dict[str, Any]:
    lines = []
    risk = False
    pp, ap = _num(planned_pos), _num(actual_pos)
    if pp is not None and ap is not None and abs(ap - pp) >= 0.05:
        gap = round(ap - pp, 2)
        lines.append(f"计划仓位 {pp:g}%，实际仓位 {ap:g}%，超出计划 {gap:+g}%")
        if gap > 0:
            risk = True
    p0, p1 = _num(planned_price), _num(executed_price)
    if p0 and p1:
        slip = (p1 - p0) / p0 * 100
        if abs(slip) >= 0.3:
            lines.append(f"计划价格 {p0:g}，实际成交 {p1:g}，滑点 {slip:+.2f}%")
            if slip > 0.5:
                risk = True
    return {"risk": risk, "lines": lines}


def _parse_json(text: str) -> Dict[str, Any]:
    if not text:
        return {}
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except Exception:
        return {}


class KnowledgeReviewService:
    def __init__(self):
        self._engine = None

    @property
    def engine(self):
        if self._engine is None:
            self._engine = storage_manager.get_postgres_engine()
        if self._engine is None:
            raise RuntimeError("PostgreSQL 未连接")
        return self._engine

    def overview(self) -> Dict[str, Any]:
        with self.engine.connect() as conn:
            snap = conn.execute(
                sa_text(
                    """
                    SELECT trade_date, index_name, change_pct, market_regime, sentiment_score, hot_sectors
                    FROM market_snapshot
                    ORDER BY trade_date DESC, id DESC
                    LIMIT 1
                    """
                )
            ).fetchone()
            opps = conn.execute(
                sa_text(
                    """
                    SELECT o.id, o.trade_date, o.opportunity_type, o.opportunity_score,
                           o.confidence, o.status, o.phase, o.sector_name, o.trigger_reason,
                           s.symbol, s.name
                    FROM opportunity o
                    LEFT JOIN stocks s ON s.id = o.stock_id
                    ORDER BY o.trade_date DESC NULLS LAST, o.opportunity_score DESC NULLS LAST, o.created_at DESC
                    LIMIT 8
                    """
                )
            ).fetchall()
            lesson = conn.execute(
                sa_text(
                    """
                    SELECT title, content, sample_size, win_rate, avg_return, max_drawdown
                    FROM lesson
                    WHERE status IN ('VALIDATED', 'OBSERVING', 'DRAFT')
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """
                )
            ).fetchone()
            stats = conn.execute(
                sa_text(
                    """
                    SELECT COUNT(*) AS n,
                           AVG(CASE WHEN outcome_type = 'WIN' THEN 1.0 ELSE 0.0 END) AS win_rate,
                           AVG(return_pct) AS avg_return,
                           MIN(return_pct) AS max_drawdown
                    FROM trade_outcome
                    """
                )
            ).fetchone()
        return {
            "ok": True,
            "snapshot": _row(snap) if snap else None,
            "opportunities": [_row(r) for r in opps],
            "lesson": _row(lesson) if lesson else None,
            "stats": _row(stats) if stats else None,
        }

    def ensure(self) -> None:
        if getattr(self, "_ready", False):
            return
        from data_layer.schema import POSTGRES_DDL_LIST

        with self.engine.begin() as conn:
            for ddl in POSTGRES_DDL_LIST:
                if (
                    "trade_thesis ADD COLUMN" in ddl
                    or "trade_outcome ADD COLUMN" in ddl
                    or "attribution ADD COLUMN" in ddl
                    or "opportunity ADD COLUMN" in ddl
                ):
                    conn.execute(sa_text(ddl))
        self._ready = True

    def list_trades(self, failures_only: bool = False, view: str = "all") -> Dict[str, Any]:
        self.ensure()
        if failures_only:
            view = "failure"
        with self.engine.connect() as conn:
            rows = conn.execute(sa_text(_LIST_SQL)).fetchall()
            codes = self._code_map(conn)
        items = [self._annotate(_row(r), codes) for r in rows]
        stats = _stats(items)
        shown = [item for item in items if _match_view(item, view)]
        return {
            "ok": True,
            "view": view,
            "trades": shown,
            "stats": stats,
            "failure_note": "亏损且逻辑正确的交易留在我的交易，不进入我的失败。胜率只统计盈亏次数，不评价交易质量。",
        }

    def _code_map(self, conn) -> Dict[str, str]:
        rows = conn.execute(
            sa_text("SELECT id FROM trade_thesis ORDER BY created_at, id")
        ).fetchall()
        return {str(r.id): f"T-{i:03d}" for i, r in enumerate(rows, start=1)}

    def _annotate(self, item: Dict[str, Any], codes: Dict[str, str]) -> Dict[str, Any]:
        item["code"] = codes.get(str(item.get("id")), "")
        item["name"] = item.get("stock_name") or item.get("stock_joined_name") or item.get("thesis_title") or ""
        item["symbol"] = item.get("symbol") or item.get("stock_symbol") or ""
        if item.get("buy_price") is None:
            item["buy_price"] = item.get("outcome_buy") or item.get("executed_price")
        item["list_state"] = _list_state(item)
        item["review_state"] = _reviewed(item)
        item["is_failure"] = _is_failure(item)
        dev = item.get("execution_deviation") if isinstance(item.get("execution_deviation"), dict) else {}
        item["followed_plan"] = dev.get("followed_plan")
        item["deviation_reason"] = dev.get("reason") or ""
        raw_inputs = item.get("skill_inputs")
        if isinstance(raw_inputs, str):
            try:
                raw_inputs = json.loads(raw_inputs)
            except json.JSONDecodeError:
                raw_inputs = {}
        if isinstance(raw_inputs, dict):
            item["skill_inputs"] = raw_inputs
            for key, value in raw_inputs.items():
                if item.get(key) in (None, ""):
                    item[key] = value
        if not isinstance(item.get("execution_deviation"), dict):
            item["execution_deviation"] = _deviation(
                item.get("exec_planned_pos") if item.get("exec_planned_pos") is not None else item.get("planned_position_pct"),
                item.get("actual_position_pct"),
                item.get("planned_price") or item.get("planned_entry_price"),
                item.get("executed_price"),
            )
        return item

    def get_trade(self, thesis_id: str) -> Dict[str, Any]:
        self.ensure()
        with self.engine.connect() as conn:
            row = conn.execute(
                sa_text(_LIST_SQL.replace("ORDER BY t.created_at DESC\n    LIMIT 300", "WHERE t.id = CAST(:id AS uuid)")),
                {"id": thesis_id},
            ).fetchone()
            if not row:
                return {"ok": False, "error": "交易不存在"}
            codes = self._code_map(conn)
            item = self._annotate(_row(row), codes)
            item["similar"] = self._similar_stats(conn, item)
            item["pattern"] = self._pattern(conn, item.get("primary_category"), codes, str(item.get("id")))
            lesson = None
            if item.get("outcome_id"):
                lesson = conn.execute(
                    sa_text(
                        """
                        SELECT id, title, content, status FROM lesson
                        WHERE source_type = 'outcome' AND source_id = :sid
                        ORDER BY created_at DESC LIMIT 1
                        """
                    ),
                    {"sid": item["outcome_id"]},
                ).fetchone()
            item["lesson"] = _row(lesson) if lesson else None
        return {"ok": True, "trade": item}

    def _pattern(self, conn, cause: Optional[str], codes: Dict[str, str], current_id: str) -> Dict[str, Any]:
        if not cause:
            return {"cause": "", "count": 0, "wins": 0, "losses": 0, "avg_return": None, "max_loss": None, "codes": []}
        rows = conn.execute(
            sa_text(
                """
                SELECT DISTINCT ON (o.id) t.id, o.outcome_type, o.return_pct
                FROM attribution a
                JOIN trade_outcome o ON o.id = a.outcome_id
                JOIN trade_thesis t ON t.id = o.thesis_id
                WHERE a.primary_category = :cause
                ORDER BY o.id, a.created_at DESC
                """
            ),
            {"cause": cause},
        ).fetchall()
        rets = [float(r.return_pct) for r in rows if r.return_pct is not None]
        losses = [float(r.return_pct) for r in rows if r.outcome_type == "LOSS" and r.return_pct is not None]
        return {
            "cause": cause,
            "count": len(rows),
            "wins": sum(1 for r in rows if r.outcome_type == "WIN"),
            "losses": sum(1 for r in rows if r.outcome_type == "LOSS"),
            "avg_return": round(sum(rets) / len(rets), 2) if rets else None,
            "max_loss": round(min(losses), 2) if losses else None,
            "codes": [codes.get(str(r.id), "") for r in rows if str(r.id) != current_id and codes.get(str(r.id))],
        }

    def _similar_stats(self, conn, trade: Dict[str, Any]) -> Dict[str, Any]:
        rows = conn.execute(
            sa_text(
                """
                SELECT o.outcome_type, o.return_pct
                FROM trade_outcome o
                JOIN trade_thesis t ON t.id = o.thesis_id
                WHERE t.id <> CAST(:id AS uuid)
                  AND (
                    (
                      (SELECT stock_id FROM trade_thesis WHERE id = CAST(:id AS uuid)) IS NOT NULL
                      AND t.stock_id = (SELECT stock_id FROM trade_thesis WHERE id = CAST(:id AS uuid))
                    )
                    OR (NULLIF(:ctx, '') IS NOT NULL AND t.market_context = :ctx)
                  )
                """
            ),
            {"id": trade["id"], "ctx": trade.get("market_context") or ""},
        ).fetchall()
        rets = [float(r.return_pct) for r in rows if r.return_pct is not None]
        wins = sum(1 for r in rows if (r.outcome_type or "") == "WIN")
        losses = sum(1 for r in rows if (r.outcome_type or "") == "LOSS")
        n = len(rows)
        return {
            "count": n,
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / n, 4) if n else None,
            "avg_return": round(sum(rets) / len(rets), 4) if rets else None,
        }

    async def review(self, thesis_id: str) -> Dict[str, Any]:
        packed = self.get_trade(thesis_id)
        if not packed.get("ok"):
            return packed
        trade = packed["trade"]
        similar = trade.get("similar") or {}
        from server.chat_service import ChatRequest, chat_service

        prompt = (
            "请复盘下面这笔交易。历史统计已给出，不要改写这些数字。\n"
            f"交易：{json.dumps(trade, ensure_ascii=False, default=str)}\n"
            f"历史类似统计：{json.dumps(similar, ensure_ascii=False)}"
        )
        try:
            resp = await chat_service.chat(
                ChatRequest(
                    query=prompt,
                    model="deepseek-chat",
                    provider="deepseek",
                    temperature=0.2,
                    max_tokens=1600,
                    system_prompt=REVIEW_SYSTEM_PROMPT,
                )
            )
            raw = resp.content or ""
        except Exception as e:
            logger.warning("复盘分析师失败: %s", e)
            return {"ok": False, "error": str(e)}
        data = _parse_json(raw)
        if not data:
            return {"ok": False, "error": "分析师没有返回可解析的复盘", "raw": raw[:1000]}
        data["similar"] = similar
        self._save_review(trade, data, raw)
        return {"ok": True, "review": data, "trade": self.get_trade(thesis_id).get("trade")}

    async def preview_review(self, trade: Dict[str, Any]) -> Dict[str, Any]:
        """对尚未入库的示例交易调用同一位分析师，不写复盘结果。"""
        from server.chat_service import ChatRequest, chat_service

        prompt = (
            "请复盘下面这笔交易。历史统计已给出，不要改写这些数字。\n"
            f"交易：{json.dumps(trade, ensure_ascii=False, default=str)}"
        )
        try:
            resp = await chat_service.chat(
                ChatRequest(
                    query=prompt,
                    model="deepseek-chat",
                    provider="deepseek",
                    temperature=0.2,
                    max_tokens=1600,
                    system_prompt=REVIEW_SYSTEM_PROMPT,
                )
            )
            raw = resp.content or ""
        except Exception as e:
            logger.warning("复盘分析师失败: %s", e)
            return {"ok": False, "error": str(e)}
        data = _parse_json(raw)
        if not data:
            return {"ok": False, "error": "分析师没有返回可解析的复盘", "raw": raw[:1000]}
        data["similar"] = trade.get("similar") or {}
        return {"ok": True, "review": data}

    def _category(self, data: Dict[str, Any]) -> str:
        types = data.get("failure_types") or []
        if types:
            key = str(types[0]).strip().lower()
            return _CATEGORY.get(key, "OTHER")
        return "OTHER"

    def _save_review(self, trade: Dict[str, Any], data: Dict[str, Any], raw: str) -> None:
        logic = data.get("logic_result") or "UNDETERMINED"
        if logic not in ("LOGIC_CORRECT", "LOGIC_INCORRECT", "PARTIALLY_CORRECT", "UNDETERMINED"):
            logic = "UNDETERMINED"
        cause = (data.get("primary_cause") or "").strip() or self._category(data)
        secondary = (data.get("secondary_cause") or "").strip() or None
        severity = data.get("severity") if data.get("severity") in ("轻微", "中等", "严重", "致命") else "中等"
        dims = data.get("dimensions") if isinstance(data.get("dimensions"), dict) else {}
        with self.engine.begin() as conn:
            if not trade.get("outcome_id"):
                return
            conn.execute(
                sa_text(
                    """
                    UPDATE trade_outcome
                    SET logic_result = CASE
                            WHEN logic_result IS NULL OR logic_result = '' OR logic_result = 'UNDETERMINED' THEN :logic
                            ELSE logic_result
                        END,
                        actual_vs_expected = COALESCE(actual_vs_expected, :actual)
                    WHERE id = :id
                    """
                ),
                {"logic": logic, "actual": data.get("narrative") or data.get("lesson") or "", "id": trade["outcome_id"]},
            )
            latest = conn.execute(
                sa_text(
                    """
                    SELECT id, confirmed FROM attribution
                    WHERE outcome_id = :oid
                    ORDER BY created_at DESC LIMIT 1
                    """
                ),
                {"oid": trade["outcome_id"]},
            ).fetchone()
            payload = {
                "oid": trade["outcome_id"],
                "cat": cause[:30],
                "sec": (secondary or "")[:50] or None,
                "sev": severity,
                "ev": json.dumps(data, ensure_ascii=False, default=str),
                "ai": raw[:8000],
                "dims": json.dumps(dims, ensure_ascii=False),
            }
            if latest and latest.confirmed:
                conn.execute(
                    sa_text("UPDATE attribution SET ai_analysis = :ai, evidence = CAST(:ev AS jsonb) WHERE id = :id"),
                    {"ai": payload["ai"], "ev": payload["ev"], "id": latest.id},
                )
                return
            conn.execute(
                sa_text(
                    """
                    INSERT INTO attribution (
                        outcome_id, primary_category, secondary_category, severity,
                        evidence, ai_analysis, dimensions, confirmed
                    ) VALUES (
                        :oid, :cat, :sec, :sev, CAST(:ev AS jsonb), :ai, CAST(:dims AS jsonb), FALSE
                    )
                    """
                ),
                payload,
            )

    def save_plan(self, body: Dict[str, Any]) -> Dict[str, Any]:
        self.ensure()
        name = str(body.get("stock_name") or "").strip()
        logic = str(body.get("thesis_text") or "").strip()
        reasons = _as_list(body.get("reasons") if body.get("reasons") is not None else body.get("entry_conditions"))
        invalid = _as_list(body.get("invalidation"))
        if not name or not logic or not reasons:
            return {"ok": False, "error": "交易计划需要股票、至少一条买入理由，以及核心逻辑。计划要在成交前写下。"}
        if not str(body.get("strategy_name") or "").strip():
            return {"ok": False, "error": "请选择策略"}
        followed = _followed(body.get("followed_plan"))
        deviation_reason = str(body.get("deviation_reason") or "").strip()
        if followed is False and not deviation_reason:
            return {"ok": False, "error": "没有按计划执行时，要写下偏离原因"}
        if followed is False and _num(body.get("executed_price")) is None:
            return {"ok": False, "error": "没有按计划执行时，要填写实际买入"}
        has_result = any(_num(body.get(k)) is not None for k in ("return_pct", "sell_price", "max_favorable_excursion", "max_adverse_excursion"))
        if str(body.get("failure_cause") or "").strip() and not has_result:
            return {"ok": False, "error": "填写失败归因前，先写下实际收益或实际卖出"}
        intent = str(body.get("rule_intent") or "none")
        if intent in ("lesson", "experiment") and not str(body.get("lesson") or "").strip():
            return {"ok": False, "error": "要形成经验或实验时，先写下交易经验"}
        with self.engine.begin() as conn:
            tid = conn.execute(
                sa_text(
                    """
                    INSERT INTO trade_thesis (
                        trade_date, symbol, stock_name, direction, strategy_name, thesis_title, thesis_text,
                        entry_reason, entry_conditions, invalidation_conditions,
                        planned_entry_price, target_price, stop_price,
                        planned_position_pct, planned_holding_days, expected_return, expected_risk, status
                    ) VALUES (
                        :d, :symbol, :name, :direction, :strategy, :title, :logic,
                        :reason, CAST(:reasons AS jsonb), CAST(:invalid AS jsonb),
                        :entry, :target, :stop, :pos, :days, :exp, :risk, 'DRAFT'
                    ) RETURNING id
                    """
                ),
                {
                    "d": (body.get("trade_date") or None) or None,
                    "symbol": str(body.get("symbol") or "")[:32],
                    "name": name[:100],
                    "direction": str(body.get("direction") or "做多")[:10],
                    "strategy": str(body.get("strategy_name") or "")[:100],
                    "title": name[:255],
                    "logic": logic,
                    "reason": "、".join(reasons),
                    "reasons": json.dumps(reasons, ensure_ascii=False),
                    "invalid": json.dumps(invalid, ensure_ascii=False),
                    "entry": _num(body.get("planned_entry_price")),
                    "target": None if str(body.get("strategy_name") or "") == "无固定目标" else _num(body.get("target_price")),
                    "stop": _num(body.get("stop_price")),
                    "pos": _num(body.get("planned_position_pct")),
                    "days": 0 if body.get("holding_mode") == "intraday" else (
                        int(_num(body.get("planned_holding_days"))) if _num(body.get("planned_holding_days")) is not None else None
                    ),
                    "exp": _num(body.get("expected_return")),
                    "risk": _num(body.get("expected_risk")),
                },
            ).scalar()
        tid = str(tid)
        inputs = _skill_inputs(body)
        if inputs:
            with self.engine.begin() as conn:
                conn.execute(
                    sa_text("UPDATE trade_thesis SET skill_inputs = CAST(:s AS jsonb) WHERE id = :id"),
                    {"id": tid, "s": json.dumps(inputs, ensure_ascii=False)},
                )
        if _num(body.get("executed_price")) is not None:
            packed = self.save_execution(tid, {
                "executed_price": body.get("executed_price"),
                "actual_position_pct": body.get("actual_position_pct"),
                "planned_price": body.get("planned_entry_price"),
                "followed_plan": body.get("followed_plan"),
                "deviation_reason": deviation_reason,
            })
            if not packed.get("ok"):
                return packed
        if has_result:
            ret = _num(body.get("return_pct"))
            sell = _num(body.get("sell_price"))
            buy = _num(body.get("executed_price")) or _num(body.get("planned_entry_price"))
            if ret is None and sell and buy:
                ret = round((sell - buy) / buy * 100, 2)
            kind = str(body.get("outcome_type") or "").upper()
            if kind not in ("WIN", "LOSS", "FLAT"):
                kind = "WIN" if (ret or 0) > 0 else "LOSS" if (ret or 0) < 0 else "FLAT"
            logic = str(body.get("logic_result") or "UNDETERMINED")
            if logic not in ("LOGIC_CORRECT", "PARTIALLY_CORRECT", "LOGIC_INCORRECT", "UNDETERMINED"):
                logic = "UNDETERMINED"
            packed = self.save_outcome(tid, {
                "outcome_type": kind,
                "logic_result": logic,
                "return_pct": ret,
                "sell_price": sell,
                "buy_price": buy,
                "holding_days": 0 if body.get("holding_mode") == "intraday" else body.get("planned_holding_days"),
                "max_favorable_excursion": body.get("max_favorable_excursion"),
                "max_adverse_excursion": body.get("max_adverse_excursion"),
            })
            if not packed.get("ok"):
                return packed
        cause = str(body.get("failure_cause") or "").strip()
        if cause:
            packed = self.save_attribution(tid, {
                "primary_category": cause,
                "severity": body.get("severity") or "中等",
                "mistake_note": deviation_reason,
            })
            if not packed.get("ok"):
                return packed
        lesson = str(body.get("lesson") or "").strip()
        if intent in ("lesson", "experiment"):
            if not lesson:
                return {"ok": False, "error": "要形成经验或实验时，先写下交易经验"}
            packed = self.save_lesson(tid, lesson)
            if not packed.get("ok"):
                return packed
        if intent == "experiment":
            packed = self.start_experiment(tid, lesson)
            if not packed.get("ok"):
                return packed
            self._remember_skill(tid)
            out = self.get_trade(tid)
            out["message"] = "已保存交易，并放进实验室。验证之前不会变成规则。"
            out["experiment"] = packed.get("experiment")
            return out
        self._remember_skill(tid)
        out = self.get_trade(tid)
        if intent == "lesson":
            out["message"] = "已保存为经验。它还不是规则。"
        return out

    def update_trade(self, thesis_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        self.ensure()
        current = self.get_trade(thesis_id)
        if not current.get("ok"):
            return current
        trade = current["trade"]
        name = str(body.get("stock_name") or "").strip()
        logic = str(body.get("thesis_text") or "").strip()
        reasons = _as_list(body.get("reasons") if body.get("reasons") is not None else body.get("entry_conditions"))
        invalid = _as_list(body.get("invalidation"))
        if not name or not logic or not reasons:
            return {"ok": False, "error": "修改时仍需要股票、至少一条买入理由，以及核心逻辑"}
        if not str(body.get("strategy_name") or "").strip():
            return {"ok": False, "error": "请选择策略"}
        followed = _followed(body.get("followed_plan"))
        deviation_reason = str(body.get("deviation_reason") or "").strip()
        if followed is False and not deviation_reason:
            return {"ok": False, "error": "没有按计划执行时，要写下偏离原因"}
        if followed is False and _num(body.get("executed_price")) is None:
            return {"ok": False, "error": "没有按计划执行时，要填写实际买入"}
        has_result = any(_num(body.get(k)) is not None for k in ("return_pct", "sell_price", "max_favorable_excursion", "max_adverse_excursion"))
        if str(body.get("failure_cause") or "").strip() and not has_result and not trade.get("outcome_id"):
            return {"ok": False, "error": "填写失败归因前，先写下实际收益或实际卖出"}
        days = 0 if body.get("holding_mode") == "intraday" else (
            int(_num(body.get("planned_holding_days"))) if _num(body.get("planned_holding_days")) is not None else None
        )
        has_exec = _num(body.get("executed_price")) is not None or trade.get("executed_price") is not None
        with self.engine.begin() as conn:
            conn.execute(
                sa_text(
                    """
                    UPDATE trade_thesis SET
                        trade_date = :d, symbol = :symbol, stock_name = :name, thesis_title = :title,
                        direction = :direction, strategy_name = :strategy, thesis_text = :logic,
                        entry_reason = :reason, entry_conditions = CAST(:reasons AS jsonb),
                        invalidation_conditions = CAST(:invalid AS jsonb),
                        planned_entry_price = :entry, target_price = :target, stop_price = :stop,
                        planned_position_pct = :pos, planned_holding_days = :days,
                        expected_return = :exp, expected_risk = :risk,
                        skill_inputs = CAST(:skills AS jsonb),
                        status = CASE
                            WHEN :has_result THEN 'CLOSED'
                            WHEN status = 'DRAFT' AND :has_exec THEN 'OPEN'
                            ELSE status
                        END
                    WHERE id = CAST(:id AS uuid)
                    """
                ),
                {
                    "id": thesis_id,
                    "skills": json.dumps(_skill_inputs(body), ensure_ascii=False),
                    "d": (body.get("trade_date") or None) or None,
                    "symbol": str(body.get("symbol") or "")[:32],
                    "name": name[:100],
                    "title": name[:255],
                    "direction": str(body.get("direction") or trade.get("direction") or "做多")[:10],
                    "strategy": str(body.get("strategy_name") or "")[:100],
                    "logic": logic,
                    "reason": "、".join(reasons),
                    "reasons": json.dumps(reasons, ensure_ascii=False),
                    "invalid": json.dumps(invalid, ensure_ascii=False),
                    "entry": _num(body.get("planned_entry_price")),
                    "target": None if str(body.get("strategy_name") or "") == "无固定目标" else _num(body.get("target_price")),
                    "stop": _num(body.get("stop_price")),
                    "pos": _num(body.get("planned_position_pct")),
                    "days": days,
                    "exp": _num(body.get("expected_return")),
                    "risk": _num(body.get("expected_risk")),
                    "has_result": has_result or bool(trade.get("outcome_id")),
                    "has_exec": has_exec,
                },
            )
        if _num(body.get("executed_price")) is not None:
            exec_body = {
                "executed_price": body.get("executed_price"),
                "actual_position_pct": body.get("actual_position_pct"),
                "planned_price": body.get("planned_entry_price"),
                "planned_position_pct": body.get("planned_position_pct"),
                "followed_plan": body.get("followed_plan"),
                "deviation_reason": deviation_reason,
            }
            if trade.get("executed_price") is not None:
                packed = self._replace_execution(thesis_id, exec_body)
            else:
                packed = self.save_execution(thesis_id, exec_body)
            if not packed.get("ok"):
                return packed
        if has_result:
            ret = _num(body.get("return_pct"))
            sell = _num(body.get("sell_price"))
            buy = _num(body.get("executed_price")) or _num(body.get("planned_entry_price"))
            if ret is None and sell and buy:
                ret = round((sell - buy) / buy * 100, 2)
            kind = str(body.get("outcome_type") or trade.get("outcome_type") or "").upper()
            if kind not in ("WIN", "LOSS", "FLAT"):
                kind = "WIN" if (ret or 0) > 0 else "LOSS" if (ret or 0) < 0 else "FLAT"
            logic_result = str(body.get("logic_result") or trade.get("logic_result") or "UNDETERMINED")
            if logic_result not in ("LOGIC_CORRECT", "PARTIALLY_CORRECT", "LOGIC_INCORRECT", "UNDETERMINED"):
                logic_result = "UNDETERMINED"
            packed = self.save_outcome(thesis_id, {
                "outcome_type": kind,
                "logic_result": logic_result,
                "return_pct": ret,
                "sell_price": sell,
                "buy_price": buy,
                "holding_days": days,
                "max_favorable_excursion": body.get("max_favorable_excursion"),
                "max_adverse_excursion": body.get("max_adverse_excursion"),
                "realized_pnl": body.get("realized_pnl") if body.get("realized_pnl") not in (None, "") else trade.get("realized_pnl"),
            })
            if not packed.get("ok"):
                return packed
        cause = str(body.get("failure_cause") or "").strip()
        if cause:
            if trade.get("attribution_id"):
                with self.engine.begin() as conn:
                    conn.execute(
                        sa_text(
                            """
                            UPDATE attribution
                            SET primary_category = :pri,
                                mistake_note = CASE WHEN :note = '' THEN mistake_note ELSE :note END,
                                confirmed = TRUE
                            WHERE id = :aid
                            """
                        ),
                        {"pri": cause[:30], "note": deviation_reason, "aid": trade["attribution_id"]},
                    )
            else:
                packed = self.save_attribution(thesis_id, {
                    "primary_category": cause,
                    "severity": body.get("severity") or trade.get("severity") or "中等",
                    "mistake_note": deviation_reason,
                })
                if not packed.get("ok"):
                    return packed
        lesson = str(body.get("lesson") or "").strip()
        if lesson:
            lesson_id = (trade.get("lesson") or {}).get("id")
            if lesson_id:
                with self.engine.begin() as conn:
                    conn.execute(
                        sa_text("UPDATE lesson SET content = :content, title = :title, updated_at = NOW() WHERE id = :id"),
                        {"content": lesson, "title": f"{name} 的经验"[:255], "id": lesson_id},
                    )
            else:
                packed = self.save_lesson(thesis_id, lesson)
                if not packed.get("ok"):
                    return packed
        self._remember_skill(thesis_id)
        return self.get_trade(thesis_id)

    def _remember_skill(self, thesis_id: str) -> None:
        try:
            packed = self.get_trade(thesis_id)
            if not packed.get("ok"):
                return
            from server.skill_profile_service import skill_profile_service
            skill_profile_service.save_trade(packed["trade"])
        except Exception:
            logger.exception("交易能力评分没有写入")

    def _replace_execution(self, thesis_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        packed = self.get_trade(thesis_id)
        if not packed.get("ok"):
            return packed
        trade = packed["trade"]
        planned_pos = _num(body.get("planned_position_pct"))
        if planned_pos is None:
            planned_pos = _num(trade.get("planned_position_pct"))
        actual_pos = _num(body.get("actual_position_pct"))
        planned_price = _num(body.get("planned_price")) or _num(trade.get("planned_entry_price"))
        executed = _num(body.get("executed_price"))
        dev = _deviation(planned_pos, actual_pos, planned_price, executed)
        followed = _followed(body.get("followed_plan"))
        reason = str(body.get("deviation_reason") or "").strip()
        dev["followed_plan"] = followed
        dev["reason"] = reason
        if followed is False:
            dev["risk"] = True
            if reason and reason not in dev["lines"]:
                dev["lines"].append(reason)
        elif followed is True:
            dev["risk"] = False
        slip = None
        if planned_price and executed:
            slip = round((executed - planned_price) / planned_price, 6)
        with self.engine.begin() as conn:
            conn.execute(
                sa_text(
                    """
                    UPDATE trade_execution SET
                        planned_price = :pp, executed_price = :ep,
                        planned_position_pct = :ppos, actual_position_pct = :apos,
                        slippage = :slip, execution_deviation = CAST(:dev AS jsonb), notes = :notes
                    WHERE id = (
                        SELECT id FROM trade_execution
                        WHERE thesis_id = CAST(:id AS uuid) AND COALESCE(side, 'BUY') = 'BUY'
                        ORDER BY executed_at DESC NULLS LAST
                        LIMIT 1
                    )
                    """
                ),
                {
                    "id": thesis_id,
                    "pp": planned_price,
                    "ep": executed,
                    "ppos": planned_pos,
                    "apos": actual_pos,
                    "slip": slip,
                    "dev": json.dumps(dev, ensure_ascii=False),
                    "notes": reason or "手动修改",
                },
            )
        return self.get_trade(thesis_id)

    def save_execution(self, thesis_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        self.ensure()
        packed = self.get_trade(thesis_id)
        if not packed.get("ok"):
            return packed
        trade = packed["trade"]
        planned_pos = _num(body.get("planned_position_pct"))
        if planned_pos is None:
            planned_pos = _num(trade.get("planned_position_pct"))
        actual_pos = _num(body.get("actual_position_pct"))
        planned_price = _num(body.get("planned_price")) or _num(trade.get("planned_entry_price"))
        executed = _num(body.get("executed_price"))
        if executed is None:
            return {"ok": False, "error": "实际成交价还没有填"}
        dev = _deviation(planned_pos, actual_pos, planned_price, executed)
        followed = _followed(body.get("followed_plan"))
        reason = str(body.get("deviation_reason") or "").strip()
        dev["followed_plan"] = followed
        dev["reason"] = reason
        if followed is False:
            dev["risk"] = True
            if reason and reason not in dev["lines"]:
                dev["lines"].append(reason)
        elif followed is True:
            dev["risk"] = False
        slip = None
        if planned_price and executed:
            slip = round((executed - planned_price) / planned_price, 6)
        with self.engine.begin() as conn:
            conn.execute(
                sa_text(
                    """
                    INSERT INTO trade_execution (
                        thesis_id, side, planned_price, executed_price, executed_quantity,
                        planned_position_pct, actual_position_pct, fee, slippage, executed_at,
                        execution_deviation, notes
                    ) VALUES (
                        CAST(:id AS uuid), 'BUY', :pp, :ep, :qty, :ppos, :apos, :fee, :slip, :at,
                        CAST(:dev AS jsonb), :notes
                    )
                    """
                ),
                {
                    "id": thesis_id,
                    "pp": planned_price,
                    "ep": executed,
                    "qty": _num(body.get("executed_quantity")),
                    "ppos": planned_pos,
                    "apos": actual_pos,
                    "fee": _num(body.get("fee")),
                    "slip": slip,
                    "at": body.get("executed_at") or None,
                    "dev": json.dumps(dev, ensure_ascii=False),
                    "notes": reason or "手动记录。接入成交回报后，这里改为自动写入。",
                },
            )
            conn.execute(
                sa_text("UPDATE trade_thesis SET status = 'OPEN' WHERE id = CAST(:id AS uuid) AND status = 'DRAFT'"),
                {"id": thesis_id},
            )
        return self.get_trade(thesis_id)

    def save_outcome(self, thesis_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        self.ensure()
        packed = self.get_trade(thesis_id)
        if not packed.get("ok"):
            return packed
        kind = str(body.get("outcome_type") or "").upper()
        logic = str(body.get("logic_result") or "")
        if kind not in ("WIN", "LOSS", "FLAT"):
            return {"ok": False, "error": "结果类型要选盈利、亏损或持平"}
        if logic not in ("LOGIC_CORRECT", "PARTIALLY_CORRECT", "LOGIC_INCORRECT", "UNDETERMINED"):
            return {"ok": False, "error": "逻辑结果要单独选择，不能用盈亏代替"}
        trade = packed["trade"]
        params = {
            "id": thesis_id,
            "pnl": _num(body.get("realized_pnl")),
            "ret": _num(body.get("return_pct")),
            "mfe": _num(body.get("max_favorable_excursion")),
            "mae": _num(body.get("max_adverse_excursion")),
            "days": _num(body.get("holding_days")),
            "kind": kind,
            "logic": logic,
            "buy": _num(body.get("buy_price")) or _num(trade.get("executed_price")),
            "sell": _num(body.get("sell_price")),
        }
        with self.engine.begin() as conn:
            if trade.get("outcome_id"):
                conn.execute(
                    sa_text(
                        """
                        UPDATE trade_outcome SET
                            realized_pnl = :pnl, return_pct = :ret,
                            max_favorable_excursion = :mfe, max_adverse_excursion = :mae,
                            holding_days = :days, outcome_type = :kind, logic_result = :logic,
                            buy_price = :buy, sell_price = :sell, completed_at = NOW()
                        WHERE id = :oid
                        """
                    ),
                    {**params, "oid": trade["outcome_id"]},
                )
            else:
                conn.execute(
                    sa_text(
                        """
                        INSERT INTO trade_outcome (
                            thesis_id, realized_pnl, return_pct, max_favorable_excursion, max_adverse_excursion,
                            holding_days, outcome_type, logic_result, buy_price, sell_price, completed_at
                        ) VALUES (
                            CAST(:id AS uuid), :pnl, :ret, :mfe, :mae, :days, :kind, :logic, :buy, :sell, NOW()
                        )
                        """
                    ),
                    params,
                )
            conn.execute(
                sa_text("UPDATE trade_thesis SET status = 'CLOSED' WHERE id = CAST(:id AS uuid)"),
                {"id": thesis_id},
            )
        return self.get_trade(thesis_id)

    def save_attribution(self, thesis_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        self.ensure()
        packed = self.get_trade(thesis_id)
        if not packed.get("ok"):
            return packed
        trade = packed["trade"]
        if not trade.get("outcome_id"):
            return {"ok": False, "error": "先记录交易结果，再写归因"}
        primary = str(body.get("primary_category") or "").strip()
        if not primary:
            return {"ok": False, "error": "请选择主要原因"}
        severity = body.get("severity") if body.get("severity") in ("轻微", "中等", "严重", "致命") else "中等"
        dims = body.get("dimensions") if isinstance(body.get("dimensions"), dict) else {}
        with self.engine.begin() as conn:
            conn.execute(
                sa_text(
                    """
                    INSERT INTO attribution (
                        outcome_id, primary_category, secondary_category, severity, dimensions,
                        redo_choice, mistake_note, human_verdict, confirmed
                    ) VALUES (
                        :oid, :pri, :sec, :sev, CAST(:dims AS jsonb), :redo, :note, :verdict, TRUE
                    )
                    """
                ),
                {
                    "oid": trade["outcome_id"],
                    "pri": primary[:30],
                    "sec": (str(body.get("secondary_category") or "").strip()[:50] or None),
                    "sev": severity,
                    "dims": json.dumps(dims, ensure_ascii=False),
                    "redo": (str(body.get("redo_choice") or "")[:40] or None),
                    "note": str(body.get("mistake_note") or ""),
                    "verdict": str(body.get("human_verdict") or ""),
                },
            )
        return self.get_trade(thesis_id)

    def save_lesson(self, thesis_id: str, content: str) -> Dict[str, Any]:
        self.ensure()
        packed = self.get_trade(thesis_id)
        if not packed.get("ok"):
            return packed
        text = (content or "").strip()
        if not text:
            return {"ok": False, "error": "经验内容是空的"}
        trade = packed["trade"]
        source_id = trade.get("outcome_id") or trade.get("id")
        source_type = "outcome" if trade.get("outcome_id") else "thesis"
        with self.engine.begin() as conn:
            conn.execute(
                sa_text(
                    """
                    INSERT INTO lesson (source_type, source_id, title, content, status)
                    VALUES (:stype, :sid, :title, :content, 'DRAFT')
                    """
                ),
                {
                    "stype": source_type,
                    "sid": source_id,
                    "title": f"{trade.get('name') or '交易'} 的经验"[:255],
                    "content": text,
                },
            )
        out = self.get_trade(thesis_id)
        out["message"] = "已保存为经验。它还不是规则。"
        return out

    def delete_trade(self, thesis_id: str) -> Dict[str, Any]:
        self.ensure()
        packed = self.get_trade(thesis_id)
        if not packed.get("ok"):
            return packed
        with self.engine.begin() as conn:
            conn.execute(
                sa_text(
                    """
                    DELETE FROM lesson
                    WHERE source_id = CAST(:id AS uuid)
                       OR source_id IN (
                            SELECT id FROM trade_outcome WHERE thesis_id = CAST(:id AS uuid)
                       )
                    """
                ),
                {"id": thesis_id},
            )
            conn.execute(
                sa_text("DELETE FROM trade_thesis WHERE id = CAST(:id AS uuid)"),
                {"id": thesis_id},
            )
        return {"ok": True, "deleted": thesis_id}

    def start_experiment(self, thesis_id: str, hypothesis: str) -> Dict[str, Any]:
        self.ensure()
        packed = self.get_trade(thesis_id)
        if not packed.get("ok"):
            return packed
        text = (hypothesis or "").strip()
        if not text:
            return {"ok": False, "error": "先写出要比较的假设，再放进实验室"}
        trade = packed["trade"]
        with self.engine.begin() as conn:
            row = conn.execute(
                sa_text(
                    """
                    INSERT INTO experiment (strategy_name, hypothesis, hypothesis_source, status)
                    VALUES (:name, :hyp, 'LESSON', 'IDEA')
                    RETURNING id, strategy_name, hypothesis, status
                    """
                ),
                {"name": (trade.get("strategy_name") or "交易实验")[:255], "hyp": text},
            ).fetchone()
        return {
            "ok": True,
            "experiment": _row(row),
            "message": "已放进实验室，状态是想法。验证有效之前，不会写成交易规则。",
        }

    def create_lesson(self, body: Dict[str, Any]) -> Dict[str, Any]:
        self.ensure()
        content = str(body.get("content") or "").strip()
        title = str(body.get("title") or "").strip()
        if not content:
            return {"ok": False, "error": "经验内容不能为空"}
        if not title:
            title = content.splitlines()[0][:40]
        with self.engine.begin() as conn:
            row = conn.execute(
                sa_text(
                    """
                    INSERT INTO lesson (source_type, title, content, status)
                    VALUES ('manual', :title, :content, 'DRAFT')
                    RETURNING id, title, content, status
                    """
                ),
                {"title": title[:255], "content": content},
            ).fetchone()
        return {"ok": True, "lesson": _row(row)}

    def delete_lesson(self, lesson_id: str) -> Dict[str, Any]:
        self.ensure()
        with self.engine.begin() as conn:
            found = conn.execute(
                sa_text("SELECT id FROM lesson WHERE id = CAST(:id AS uuid)"),
                {"id": lesson_id},
            ).fetchone()
            if not found:
                return {"ok": False, "error": "这条经验不存在"}
            conn.execute(
                sa_text("UPDATE rule SET lesson_id = NULL WHERE lesson_id = CAST(:id AS uuid)"),
                {"id": lesson_id},
            )
            conn.execute(
                sa_text("DELETE FROM lesson WHERE id = CAST(:id AS uuid)"),
                {"id": lesson_id},
            )
        return {"ok": True, "deleted": lesson_id}

    def list_section(self, kind: str) -> Dict[str, Any]:
        sql = {
            "market": "SELECT id, trade_date, index_name, change_pct, market_regime, sentiment_score FROM market_snapshot ORDER BY trade_date DESC LIMIT 50",
            "opportunity": """
                SELECT o.id, o.trade_date, o.opportunity_type, o.opportunity_score, o.status,
                       o.phase, o.sector_name, o.trigger_reason, o.amount_ratio, s.symbol, s.name
                FROM opportunity o LEFT JOIN stocks s ON s.id = o.stock_id
                ORDER BY o.trade_date DESC NULLS LAST, o.opportunity_score DESC NULLS LAST
                LIMIT 80
            """,
            "lesson": "SELECT id, title, content, sample_size, win_rate, avg_return, max_drawdown, status FROM lesson ORDER BY updated_at DESC LIMIT 50",
            "rule": "SELECT id, rule_code, name, category, description, status, win_rate FROM rule ORDER BY updated_at DESC LIMIT 50",
            "external": "SELECT id, title, author, source_type, evidence_level, summary FROM external_knowledge ORDER BY created_at DESC LIMIT 50",
            "experiment": "SELECT id, strategy_name, hypothesis, status, win_rate, expectancy, max_drawdown FROM experiment ORDER BY created_at DESC LIMIT 50",
        }.get(kind)
        if not sql:
            return {"ok": False, "error": "未知栏目"}
        with self.engine.connect() as conn:
            rows = conn.execute(sa_text(sql)).fetchall()
        return {"ok": True, "items": [_row(r) for r in rows]}

    def adopt_rule(self, thesis_id: str, name: str, description: str) -> Dict[str, Any]:
        return {
            "ok": False,
            "error": "单次交易不能直接变成规则。先保存为经验，再放到实验室验证。验证有效之后，才进入交易规则。",
        }


knowledge_review_service = KnowledgeReviewService()

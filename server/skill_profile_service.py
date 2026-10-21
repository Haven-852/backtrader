"""交易能力：按计划、实际执行和结果打分，不按盈亏。

每一项只有 100 / 75 / 50 / 25 / 0。证据不够就留空，不补一个 50。
历史平均后再向 50 收缩：最终分 = 原始平均 × N/(N+20) + 50 × (1 - N/(N+20))。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text

from data_layer.db_manager import storage_manager

logger = logging.getLogger(__name__)

SKILLS: List[Tuple[str, str]] = [
    ("direction", "方向判断"),
    ("sector", "板块判断"),
    ("stock", "个股选择"),
    ("entry", "买点"),
    ("exit", "卖点"),
    ("position", "仓位"),
    ("stop", "止损"),
    ("execution", "执行"),
]

_CREATE = """
CREATE TABLE IF NOT EXISTS trade_skill_evaluation (
    thesis_id UUID PRIMARY KEY REFERENCES trade_thesis(id) ON DELETE CASCADE,
    direction_score SMALLINT,
    sector_score SMALLINT,
    stock_score SMALLINT,
    entry_score SMALLINT,
    exit_score SMALLINT,
    position_score SMALLINT,
    stop_score SMALLINT,
    execution_score SMALLINT,
    reasons JSONB,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""


def _num(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _inputs(trade: Dict[str, Any]) -> Dict[str, Any]:
    raw = trade.get("skill_inputs")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = {}
    return raw if isinstance(raw, dict) else {}


def _pick(trade: Dict[str, Any], key: str) -> Any:
    value = trade.get(key)
    if value not in (None, ""):
        return value
    return _inputs(trade).get(key)


def _band(value: float, strong: float, ok: float, flat: float, wrong: float) -> int:
    """看多、会强于大盘、个股跑赢板块共用。value 越大越好。"""
    if value >= strong:
        return 100
    if value >= ok:
        return 75
    if abs(value) < flat:
        return 50
    if value <= wrong:
        return 0
    return 25


def _stop_distance(entry: Optional[float], stop: Optional[float], risk_pct: Optional[float]) -> Optional[float]:
    if entry not in (None, 0) and stop is not None:
        return (stop - entry) / entry * 100
    if risk_pct is not None:
        return -abs(risk_pct)
    return None


def _direction(trade: Dict[str, Any]) -> Tuple[Optional[int], str]:
    view = str(_pick(trade, "market_view") or "").strip()
    market = _num(_pick(trade, "market_return_pct"))
    if view not in ("看多", "看空", "震荡") or market is None:
        return None, "还没有市场方向判断和指数涨跌，方向先不计分。"
    if view == "看多":
        score = _band(market, 0.8, 0.2, 0.2, -0.8)
    elif view == "看空":
        score = _band(-market, 0.8, 0.2, 0.2, -0.8)
    else:
        span = abs(market)
        if span <= 0.6:
            score = 100
        elif span <= 1.2:
            score = 75
        elif span >= 2:
            score = 0
        else:
            score = 25
    return score, f"判断{view}，指数 {market:+.2f}%。对照的是指数，不是这笔交易的盈亏。"


def _sector(trade: Dict[str, Any]) -> Tuple[Optional[int], str]:
    view = str(_pick(trade, "sector_view") or "").strip()
    sector = _num(_pick(trade, "sector_return_pct"))
    market = _num(_pick(trade, "market_return_pct"))
    if view not in ("会强于大盘", "与大盘同步", "会弱于大盘") or sector is None or market is None:
        return None, "还没有板块判断、板块涨跌和指数涨跌，板块先不计分。"
    excess = sector - market
    name = str(_pick(trade, "sector_name") or "").strip()
    prefix = f"{name} " if name else ""
    if view == "会强于大盘":
        score = _band(excess, 2, 0.5, 0.5, -2)
    elif view == "会弱于大盘":
        score = _band(-excess, 2, 0.5, 0.5, -2)
    else:
        span = abs(excess)
        score = 100 if span <= 0.5 else 75 if span <= 1.2 else 0 if span >= 2 else 25
    return score, f"{prefix}判断{view}，板块 {sector:+.2f}%，指数 {market:+.2f}%，超额 {excess:+.2f}%。"


def _stock(trade: Dict[str, Any]) -> Tuple[Optional[int], str]:
    sector = _num(_pick(trade, "sector_return_pct"))
    ret = _num(trade.get("return_pct"))
    if sector is None or ret is None:
        return None, "还没有板块涨跌和个股区间涨跌，选股先不计分。不直接用赚亏当选股分。"
    excess = ret - sector
    reason = str(_pick(trade, "stock_reason") or "").strip()
    score = _band(excess, 2, 0.5, 0.5, -2)
    text = f"个股 {ret:+.2f}%，所属板块 {sector:+.2f}%，超额 {excess:+.2f}%。"
    if reason:
        text += f"选择理由：{reason}"
    return score, text


def _entry(trade: Dict[str, Any]) -> Tuple[Optional[int], str]:
    buy = _num(trade.get("executed_price"))
    plan = _num(trade.get("planned_entry_price"))
    mae = _num(trade.get("max_adverse_excursion"))
    mfe = _num(trade.get("max_favorable_excursion"))
    if buy is None:
        return None, "还没有实际买入价，买点先不计分。"
    slip = (buy - plan) / plan * 100 if plan not in (None, 0) else None
    if mae is None or mfe is None:
        if slip is None:
            return None, "有实际买入价，但没有计划价，也没有买入后的价格路径，买点先不计分。"
        if slip <= 0.5:
            score = 75
        elif slip <= 1.5:
            score = 50
        elif slip <= 3:
            score = 25
        else:
            score = 0
        return score, f"实际买入 {buy:g}，计划 {plan:g}，偏离 {slip:+.2f}%。还没有最大不利/有利波动，只对照成交价。"
    chased = slip is not None and slip > 2
    near = slip is None or slip <= 0.5
    close = slip is None or slip <= 1.5
    if near and mae > -1.5 and mfe >= 2:
        score = 100
    elif close and mae > -3 and mfe >= 1:
        score = 75
    elif mae <= -5 and mfe < abs(mae):
        score = 0
    elif chased or mae <= -3:
        score = 25
    else:
        score = 50
    slip_text = f"，相对计划偏离 {slip:+.2f}%" if slip is not None else ""
    return score, f"实际买入 {buy:g}{slip_text}。买入后最大回撤 {mae:+.2f}%，最大有利 {mfe:+.2f}%。不看最终赚亏。"


def _exit(trade: Dict[str, Any]) -> Tuple[Optional[int], str]:
    style = str(_pick(trade, "exit_style") or "").strip()
    if style == "计划退出":
        score = 100
    elif style in ("提前退出", "延迟退出"):
        score = 25
    else:
        return None, "还没有卖出理由（计划退出、提前退出或延迟退出），卖点先不计分。"
    note = str(_pick(trade, "exit_reason") or "").strip()
    text = f"卖出理由是{style}。卖出以后继续涨，不单独判成卖点错误。"
    if note:
        text += note
    return score, text


def _position(trade: Dict[str, Any]) -> Tuple[Optional[int], str]:
    planned = _num(trade.get("planned_position_pct"))
    actual = _num(trade.get("actual_position_pct"))
    if planned is None or actual is None:
        return None, "还没有计划仓位和实际仓位，仓位先不计分。"
    gap = abs(actual - planned)
    if gap <= 5:
        score = 100
    elif gap <= 10:
        score = 75
    elif gap <= 20:
        score = 25
    else:
        score = 0
    entry = _num(trade.get("executed_price")) or _num(trade.get("planned_entry_price"))
    dist = _stop_distance(entry, _num(trade.get("stop_price")), _num(trade.get("expected_risk")))
    risk_text = ""
    if dist is not None:
        risk = actual / 100 * abs(dist)
        risk_text = f"组合风险约 {risk:.2f}%（仓位 × 止损距离）。"
        if risk >= 8:
            score = 0
        elif risk >= 5 and score > 25:
            score = 25
    return score, f"计划仓位 {planned:g}%，实际 {actual:g}%，相差 {gap:.1f} 个百分点。{risk_text}仓位大不等于错，看的是和计划、止损是否匹配。"


def _stop(trade: Dict[str, Any]) -> Tuple[Optional[int], str]:
    entry = _num(trade.get("executed_price")) or _num(trade.get("planned_entry_price"))
    planned = _stop_distance(entry, _num(trade.get("stop_price")), _num(trade.get("expected_risk")))
    if planned is None:
        return None, "还没有计划止损，止损先不计分。"
    actual_price = _num(_pick(trade, "actual_stop_price"))
    if entry not in (None, 0) and actual_price is not None:
        actual = (actual_price - entry) / entry * 100
        source = "实际止损价"
    else:
        mae = _num(trade.get("max_adverse_excursion"))
        if mae is None:
            return None, "还没有实际止损或最大浮亏，止损先不计分。亏损本身不计成止损失败。"
        actual = mae
        source = "最大浮亏"
    adverse = abs(min(actual, 0))
    limit = abs(planned)
    if limit == 0:
        score = 100 if adverse == 0 else 0
    elif adverse <= limit * 1.15:
        score = 100
    elif adverse <= limit * 1.5:
        score = 25
    else:
        score = 0
    return score, f"计划止损约 {planned:+.2f}%，{source}约 {actual:+.2f}%。看有没有把止损放大，不看最终赚亏。"


def _execution(trade: Dict[str, Any]) -> Tuple[Optional[int], str]:
    if _num(trade.get("executed_price")) is None:
        return None, "还没有实际买入，执行先不计分。"
    followed = trade.get("followed_plan")
    issues = 0
    severe = False
    notes: List[str] = []
    if followed is False:
        issues += 1
        notes.append("没有按计划执行")
    plan = _num(trade.get("planned_entry_price"))
    buy = _num(trade.get("executed_price"))
    if plan not in (None, 0) and buy is not None:
        slip = (buy - plan) / plan * 100
        if abs(slip) >= 0.5:
            issues += 1
            notes.append(f"买入偏离 {slip:+.2f}%")
        if slip > 3:
            severe = True
    planned_pos = _num(trade.get("planned_position_pct"))
    actual_pos = _num(trade.get("actual_position_pct"))
    if planned_pos is not None and actual_pos is not None:
        gap = abs(actual_pos - planned_pos)
        if gap > 5:
            issues += 1
            notes.append(f"仓位相差 {gap:.1f} 个百分点")
        if gap > 20:
            severe = True
    entry = buy or plan
    planned_dist = _stop_distance(entry, _num(trade.get("stop_price")), _num(trade.get("expected_risk")))
    actual_stop = _num(_pick(trade, "actual_stop_price"))
    if planned_dist not in (None, 0) and entry not in (None, 0) and actual_stop is not None:
        actual_dist = (actual_stop - entry) / entry * 100
        if abs(min(actual_dist, 0)) > abs(planned_dist) * 1.5:
            issues += 1
            severe = True
            notes.append("实际止损明显宽于计划")
    style = str(_pick(trade, "exit_style") or "")
    if style in ("提前退出", "延迟退出"):
        issues += 1
        notes.append(style)
    if severe or issues >= 3:
        score = 0
    elif followed is False or issues >= 2:
        score = 25
    elif issues == 1:
        score = 75
    elif followed is True:
        score = 100
    else:
        score = 75
    detail = "、".join(notes) if notes else "计划与实际基本一致"
    return score, f"执行对照：{detail}。比的是计划偏差，不是盈亏。"


_SCORERS = {
    "direction": _direction,
    "sector": _sector,
    "stock": _stock,
    "entry": _entry,
    "exit": _exit,
    "position": _position,
    "stop": _stop,
    "execution": _execution,
}


def score_trade(trade: Dict[str, Any]) -> Dict[str, Any]:
    scores: Dict[str, Optional[int]] = {}
    reasons: Dict[str, str] = {}
    for key, _label in SKILLS:
        value, reason = _SCORERS[key](trade)
        scores[key] = value
        reasons[key] = reason
    return {"thesis_id": str(trade.get("id") or ""), "scores": scores, "reasons": reasons}


class SkillProfileService:
    def __init__(self) -> None:
        self._engine = None
        self._ready = False

    @property
    def engine(self):
        if self._engine is None:
            self._engine = storage_manager.get_postgres_engine()
        if self._engine is None:
            raise RuntimeError("PostgreSQL 未连接")
        return self._engine

    def ensure(self) -> None:
        if self._ready:
            return
        with self.engine.begin() as conn:
            conn.execute(text(_CREATE))
        self._ready = True

    def save_trade(self, trade: Dict[str, Any]) -> Dict[str, Any]:
        self.ensure()
        packed = score_trade(trade)
        thesis_id = packed["thesis_id"]
        if not thesis_id:
            return packed
        scores = packed["scores"]
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO trade_skill_evaluation (
                        thesis_id, direction_score, sector_score, stock_score, entry_score,
                        exit_score, position_score, stop_score, execution_score, reasons, updated_at
                    ) VALUES (
                        CAST(:id AS uuid), :direction, :sector, :stock, :entry,
                        :exit, :position, :stop, :execution, CAST(:reasons AS jsonb), NOW()
                    )
                    ON CONFLICT (thesis_id) DO UPDATE SET
                        direction_score = EXCLUDED.direction_score,
                        sector_score = EXCLUDED.sector_score,
                        stock_score = EXCLUDED.stock_score,
                        entry_score = EXCLUDED.entry_score,
                        exit_score = EXCLUDED.exit_score,
                        position_score = EXCLUDED.position_score,
                        stop_score = EXCLUDED.stop_score,
                        execution_score = EXCLUDED.execution_score,
                        reasons = EXCLUDED.reasons,
                        updated_at = NOW()
                    """
                ),
                {
                    "id": thesis_id,
                    "reasons": json.dumps(packed["reasons"], ensure_ascii=False),
                    **scores,
                },
            )
        return packed

    def profile(self) -> Dict[str, Any]:
        from server.knowledge_review_service import knowledge_review_service

        packed = knowledge_review_service.list_trades()
        trades = packed.get("trades") or []
        rows = []
        for trade in trades:
            try:
                rows.append(self.save_trade(trade))
            except Exception:
                logger.exception("单笔能力评分失败")
                rows.append(score_trade(trade))
        skills = []
        order = {key: index for index, (key, _label) in enumerate(SKILLS)}
        for key, label in SKILLS:
            vals = [row["scores"][key] for row in rows if row["scores"].get(key) is not None]
            n = len(vals)
            if not n:
                skills.append({"key": key, "label": label, "score": None, "raw_score": None, "sample_size": 0})
                continue
            raw = sum(vals) / n
            weight = n / (n + 20)
            adjusted = raw * weight + 50 * (1 - weight)
            skills.append({
                "key": key,
                "label": label,
                "score": int(adjusted + 0.5),
                "raw_score": round(raw, 1),
                "sample_size": n,
            })
        scored = [item for item in skills if item["score"] is not None]
        strongest = weakness = second = None
        if scored:
            hi = max(item["score"] for item in scored)
            lo = min(item["score"] for item in scored)
            if hi - lo >= 1:
                strongest = max(scored, key=lambda item: (item["score"], -order[item["key"]]))
                weak_sorted = sorted(scored, key=lambda item: (item["score"], order[item["key"]]))
                weakness = weak_sorted[0]
                if len(weak_sorted) > 1 and weak_sorted[1]["score"] < strongest["score"]:
                    second = weak_sorted[1]
                if weakness["key"] == strongest["key"]:
                    weakness = None
                    second = None
        sample_size = len(trades)
        return {
            "ok": True,
            "sample_size": sample_size,
            "early": sample_size < 20,
            "note": "能力分来自计划、实际执行和结果的对照，不来自盈亏。交易少于 20 笔时，分数会向 50 分收缩。",
            "skills": skills,
            "strongest": strongest["label"] if strongest else None,
            "weakness": weakness["label"] if weakness else None,
            "second_weakness": second["label"] if second else None,
            "gap": bool(strongest),
        }


skill_profile_service = SkillProfileService()

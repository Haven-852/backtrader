"""高龄交易员仓位阶梯：相对开仓价加减仓；策略出场则全清。"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Sequence, Set, Tuple

# 相对开仓价：下跌加仓、上涨减仓。每档只触发一次。
ADD_LEVELS: Sequence[Tuple[float, float, str]] = (
    (-0.15, 0.10, "下跌15%增持10%"),
    (-0.25, 0.25, "下跌25%增持25%"),
)
SELL_LEVELS: Sequence[Tuple[float, float, str]] = (
    (0.25, 0.10, "上涨25%卖出10%"),
    (0.35, 0.20, "上涨35%卖出20%"),
    (0.45, 0.30, "上涨45%卖出30%"),
    (0.60, 0.40, "上涨60%卖出40%"),
    (1.00, 1.00, "上涨100%清仓"),
)


def round_lot(shares: float) -> int:
    return int(shares // 100 * 100) if shares >= 100 else 0


def plan_ladder_actions(
    *,
    position: float,
    initial_shares: float,
    ref_px: float,
    px: float,
    fired_add: Iterable[float],
    fired_sell: Iterable[float],
) -> Tuple[List[Dict[str, Any]], Set[float], Set[float]]:
    """持仓且策略仍看多时，按相对开仓价规划当日加减仓。"""
    actions: List[Dict[str, Any]] = []
    fired_a = set(fired_add)
    fired_s = set(fired_sell)
    if position < 100 or ref_px <= 0 or px <= 0 or initial_shares < 100:
        return actions, fired_a, fired_s

    ret = px / ref_px - 1.0
    pos = float(position)

    for thresh, pct, reason in ADD_LEVELS:
        if ret > thresh or thresh in fired_a:
            continue
        fired_a.add(thresh)
        qty = round_lot(initial_shares * pct)
        if qty >= 100:
            actions.append({"side": "买入", "qty": qty, "reason": reason, "ret": ret})

    for thresh, pct, reason in SELL_LEVELS:
        if pos < 100:
            break
        if ret < thresh or thresh in fired_s:
            continue
        fired_s.add(thresh)
        if pct >= 1.0:
            qty = int(pos)
        else:
            qty = min(int(pos), round_lot(initial_shares * pct))
        if qty >= 100:
            actions.append({"side": "卖出", "qty": qty, "reason": reason, "ret": ret})
            pos -= qty
    return actions, fired_a, fired_s

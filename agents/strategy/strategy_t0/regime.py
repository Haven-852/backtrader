"""用当日之前的日线判断上升 / 下跌 / 横盘。不含测试日当天。"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence

REGIMES = ("up", "down", "range", "unknown")
REGIME_TITLE = {
    "up": "上升",
    "down": "下跌",
    "range": "横盘震荡",
    "unknown": "样本不足",
}


def _f(xs: Sequence[Any]) -> List[float]:
    out: List[float] = []
    for x in xs:
        try:
            v = float(x)
        except (TypeError, ValueError):
            continue
        if math.isnan(v):
            continue
        out.append(v)
    return out


def _mean(xs: Sequence[float]) -> float:
    vals = list(xs)
    return sum(vals) / len(vals) if vals else 0.0


def _std(xs: Sequence[float]) -> float:
    vals = list(xs)
    if len(vals) < 2:
        return 0.0
    m = _mean(vals)
    var = sum((x - m) ** 2 for x in vals) / (len(vals) - 1)
    return math.sqrt(max(var, 0.0))


def hist_sigma_1m_from_daily(rows: List[Dict[str, Any]]) -> float:
    """把日波动折成约 1 分钟波动，只用来种子化方法 A 的阈值。"""
    closes = _f(r.get("close") for r in rows)
    if len(closes) < 5:
        return 0.002
    rets = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0:
            rets.append((closes[i] - closes[i - 1]) / closes[i - 1])
    daily = _std(rets) if rets else 0.02
    return max(daily / math.sqrt(240.0), 1e-4)


def classify_regime(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """rows 必须按日期升序，且全部 < 测试日。"""
    closes = _f(r.get("close") for r in rows)
    highs = _f(r.get("high") if r.get("high") is not None else r.get("close") for r in rows)
    lows = _f(r.get("low") if r.get("low") is not None else r.get("close") for r in rows)
    n = len(closes)
    if n < 6:
        return {
            "regime": "unknown",
            "title": REGIME_TITLE["unknown"],
            "n_days": n,
            "reason": "日线不足 6 天，无法判断态势",
        }

    last = closes[-1]
    w = min(20, n)
    win = closes[-w:]
    ma5 = _mean(closes[-min(5, n):])
    ma20 = _mean(win)
    ret_5 = (last / closes[-6] - 1.0) if n >= 6 and closes[-6] > 0 else None
    ret_20 = (last / closes[-w] - 1.0) if closes[-w] > 0 else None
    hh = max(highs[-w:] or [last])
    ll = min(lows[-w:] or [last])
    span = hh - ll
    range_pos = ((last - ll) / span) if span > 1e-9 else 0.5

    rets = []
    for i in range(1, n):
        if closes[i - 1] > 0:
            rets.append((closes[i] - closes[i - 1]) / closes[i - 1])
    path = sum(abs(x) for x in rets[-w + 1:]) if rets else 0.0
    net = abs(ret_20 or 0.0)
    efficiency = (net / path) if path > 1e-9 else 0.0

    trs = []
    for i, r in enumerate(rows[-w:]):
        h = float(r.get("high") if r.get("high") is not None else r.get("close") or 0)
        l = float(r.get("low") if r.get("low") is not None else r.get("close") or 0)
        c = float(r.get("close") or 0)
        prev = float(rows[-w + i - 1]["close"]) if (-w + i - 1) >= -len(rows) and i > 0 else c
        trs.append(max(h - l, abs(h - prev), abs(l - prev)))
    atr = _mean(trs) if trs else 0.0
    atr_pct = (atr / last) if last else 0.0

    ret20 = ret_20 or 0.0
    above = last > ma20
    ma_up = ma5 >= ma20

    if ret20 >= 0.05 and above and ma_up:
        regime = "up"
        reason = f"近{w}日涨{ret20*100:.1f}%，均线多头"
    elif ret20 <= -0.05 and (not above) and (not ma_up):
        regime = "down"
        reason = f"近{w}日跌{ret20*100:.1f}%，均线空头"
    elif ret20 >= 0.10 and above:
        regime = "up"
        reason = f"近{w}日涨{ret20*100:.1f}%，收盘在均线上方"
    elif ret20 <= -0.10 and (not above):
        regime = "down"
        reason = f"近{w}日跌{ret20*100:.1f}%，收盘在均线下方"
    else:
        regime = "range"
        if abs(ret20) < 0.05:
            reason = f"近{w}日涨跌仅 {ret20*100:.1f}%，视为横盘"
        else:
            reason = f"近{w}日涨跌 {ret20*100:.1f}% 但均线未对齐（效率 {efficiency:.2f}），视为震荡"

    return {
        "regime": regime,
        "title": REGIME_TITLE[regime],
        "n_days": n,
        "last_close": round(last, 4),
        "ma5": round(ma5, 4),
        "ma20": round(ma20, 4),
        "ret_5": None if ret_5 is None else round(ret_5 * 100.0, 3),
        "ret_20": None if ret_20 is None else round(ret_20 * 100.0, 3),
        "range_pos": round(range_pos, 4),
        "efficiency": round(efficiency, 4),
        "atr_pct": round(atr_pct * 100.0, 3),
        "reason": reason,
        "asof_trade_date": str(rows[-1].get("trade_date") or ""),
    }

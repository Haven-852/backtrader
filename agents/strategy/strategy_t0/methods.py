"""无盘口四法：A 回撤+时间、B 冲击衰竭、C U 型量时钟、D 峰岭谷。

全部只用截至当前分钟的分时，不用盘口，不用当日收盘后高低做特征。
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .data import limit_pct_band
from .session import remaining_minutes

WINDOW = 5
SURGE_STREAK = 2
ETA_DROP = 0.45
SCORE_SELL = 0.65


def _median(xs: Sequence[float]) -> float:
    vals = [float(x) for x in xs if x is not None]
    if not vals:
        return 0.0
    vals = sorted(vals)
    n = len(vals)
    mid = n // 2
    if n % 2:
        return vals[mid]
    return 0.5 * (vals[mid - 1] + vals[mid])


def _std(xs: Sequence[float]) -> float:
    vals = [float(x) for x in xs if x is not None]
    if len(vals) < 2:
        return 0.0
    m = sum(vals) / len(vals)
    var = sum((x - m) ** 2 for x in vals) / (len(vals) - 1)
    return math.sqrt(var)


def build_clock_stats(lookback_vols: Dict[str, List[float]]) -> Dict[str, Dict[str, float]]:
    """方法 C：每个钟点的 20 日中位与标准差。不含当天。"""
    out: Dict[str, Dict[str, float]] = {}
    for clock, vols in (lookback_vols or {}).items():
        med = _median(vols)
        sd = _std(vols)
        out[clock] = {
            "median": med,
            "std": sd,
            "n": float(len(vols)),
            "spray": med + sd,
        }
    return out


def _side(close: float, prev_close: Optional[float], open_: Optional[float]) -> int:
    if open_ is not None and close != open_:
        return 1 if close > open_ else -1
    if prev_close is None:
        return 0
    if close > prev_close:
        return 1
    if close < prev_close:
        return -1
    return 0


def annotate_session(
    bars: List[Dict[str, Any]],
    clock_stats: Optional[Dict[str, Dict[str, float]]] = None,
    pre_close: Optional[float] = None,
    ts_code: str = "",
    use_clock_for_b: bool = False,
    hist_sigma_1m: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """对连续竞价分钟做因果标注。竞价 K 线跳过信号，只带过去。

    hist_sigma_1m 只允许来自当日之前的日线/历史分时，禁止用当天剩余分钟。
    """
    stats = clock_stats or {}
    session = [b for b in bars if b.get("session")]
    if not session:
        return []

    seed_sigma = float(hist_sigma_1m) if hist_sigma_1m and hist_sigma_1m > 0 else 0.002
    seed_sigma = max(seed_sigma, 1e-4)
    running_rets: List[float] = []

    band = limit_pct_band(ts_code)
    annotated: List[Dict[str, Any]] = []
    session_high: Optional[float] = None
    session_low: Optional[float] = None
    cum_amt = 0.0
    cum_vol = 0.0
    prev_close = pre_close
    buy_streak = 0
    sell_streak = 0
    flow = "none"
    down_flow = "none"
    had_surge = False
    had_down_surge = False
    prev_vol_state = "valley"
    stall_bars = 0
    down_stall_bars = 0
    no_new_high = 0
    no_new_low = 0

    q_window: List[float] = []
    q_down_window: List[float] = []
    high_window: List[float] = []
    low_window: List[float] = []

    for i, raw in enumerate(session):
        clock = raw.get("clock")
        close = float(raw["close"])
        high = float(raw["high"] if raw.get("high") is not None else close)
        low = float(raw["low"] if raw.get("low") is not None else close)
        open_ = raw.get("open")
        vol = int(raw.get("vol") or 0)
        amount = raw.get("amount")
        avg_field = raw.get("avg_price")

        session_high = high if session_high is None else max(session_high, high)
        session_low = low if session_low is None else min(session_low, low)
        cum_vol += max(vol, 0)
        if amount is not None:
            cum_amt += max(float(amount), 0.0)
        vwap = None
        if avg_field is not None:
            vwap = float(avg_field)
        elif cum_vol > 0 and cum_amt > 0:
            vwap = cum_amt / cum_vol / 100.0 if cum_amt > cum_vol * close * 10 else cum_amt / cum_vol

        side = _side(close, prev_close, float(open_) if open_ is not None else None)
        signed_vol = side * vol
        q_window.append(float(max(signed_vol, 0)))
        q_down_window.append(float(max(-signed_vol, 0)))
        high_window.append(high)
        low_window.append(low)
        if len(q_window) > WINDOW:
            q_window.pop(0)
            q_down_window.pop(0)
            high_window.pop(0)
            low_window.pop(0)
        q_plus = sum(q_window)
        q_minus = sum(q_down_window)
        d_high = high_window[-1] - high_window[0] if len(high_window) >= 2 else 0.0
        d_low = low_window[-1] - low_window[0] if len(low_window) >= 2 else 0.0
        eta = d_high / (q_plus + 1.0)
        eta_down = (-d_low) / (q_minus + 1.0)

        clk = stats.get(clock) or {}
        med = float(clk.get("median") or 0.0)
        x = (vol / med) if med > 0 else None
        spray_line = float(clk.get("spray") or 0.0)

        if side > 0:
            buy_streak += 1
            sell_streak = 0
        elif side < 0:
            sell_streak += 1
            buy_streak = 0
        else:
            buy_streak = 0
            sell_streak = 0

        vol_large = False
        if use_clock_for_b and x is not None:
            vol_large = x >= 1.0
        else:
            recent = [annotated[j]["vol"] for j in range(max(0, i - 5), i)]
            base = _median(recent) if recent else float(vol)
            vol_large = vol >= max(base, 1.0) * 1.15 if base > 0 else vol > 0

        surge_ok = buy_streak >= SURGE_STREAK and vol_large
        if surge_ok:
            had_surge = True
            flow = "surge"
            stall_bars = 0

        at_high = session_high is not None and high >= session_high - 1e-9
        if at_high:
            no_new_high = 0
        else:
            no_new_high += 1

        if flow in ("surge", "stall") and had_surge:
            pushed = d_high <= max(session_high * 1e-4, 1e-6) if session_high else True
            if (q_plus > 0 and eta <= 0) or (at_high is False and vol_large and pushed):
                flow = "stall"
                stall_bars += 1
            if side < 0 or (vwap is not None and close < vwap and side <= 0):
                flow = "flip"

        if flow == "flip" and surge_ok:
            flow = "surge"
            stall_bars = 0

        down_surge_ok = sell_streak >= SURGE_STREAK and vol_large
        if down_surge_ok:
            had_down_surge = True
            down_flow = "surge"
            down_stall_bars = 0

        at_low = session_low is not None and low <= session_low + 1e-9
        if at_low:
            no_new_low = 0
        else:
            no_new_low += 1

        if down_flow in ("surge", "stall") and had_down_surge:
            pushed_down = d_low >= -max(abs(session_low or 0) * 1e-4, 1e-6)
            if (q_minus > 0 and eta_down <= 0) or (at_low is False and vol_large and pushed_down):
                down_flow = "stall"
                down_stall_bars += 1
            if side > 0 or (vwap is not None and close > vwap and side >= 0):
                down_flow = "flip"

        if down_flow == "flip" and down_surge_ok:
            down_flow = "surge"
            down_stall_bars = 0

        spray = bool(spray_line > 0 and vol > spray_line)
        if spray and prev_vol_state in ("ridge", "peak"):
            vol_state = "ridge"
        elif spray:
            vol_state = "peak"
        else:
            vol_state = "valley"
        prev_vol_state = vol_state

        tau = remaining_minutes(clock)
        if len(running_rets) >= 15:
            live = max(_std(running_rets), 1e-4)
            sigma_1m = 0.7 * live + 0.3 * seed_sigma
        else:
            sigma_1m = seed_sigma
        sigma_1m = max(sigma_1m, 1e-4)
        drawdown = 0.0
        if session_high and session_high > 0:
            drawdown = (session_high - close) / session_high
        thresh = max(0.002, min(0.04, 2.2 * sigma_1m * math.sqrt(max(tau, 1))))
        score_a = 0.0
        if drawdown > 0:
            score_a = min(1.0, drawdown / thresh)
        if tau <= 15 and no_new_high >= 3:
            score_a = min(1.0, max(score_a, 0.55 + (15 - tau) / 30.0))
        if tau <= 5:
            score_a = max(score_a, 0.9)
        if at_high and tau > 15:
            score_a = min(score_a, 0.15)

        pct = None
        if pre_close and pre_close > 0:
            pct = (close - pre_close) / pre_close
        limit_up = bool(pct is not None and pct >= band - 0.005)
        limit_down = bool(pct is not None and pct <= -(band - 0.005))

        score_b = 0.0
        reason_b = "无涌入"
        if flow == "surge":
            score_b = 0.15
            reason_b = "买盘涌入中"
        elif flow == "stall":
            score_b = 0.55 if stall_bars == 1 else 0.72
            reason_b = "量价受阻"
        elif flow == "flip":
            score_b = 0.88
            reason_b = "方向翻面"
        if limit_up:
            score_b = min(score_b, 0.2)
            reason_b = "涨停附近不提前卖"

        score_c = 0.0
        if x is None:
            reason_c = "无同时点历史"
        else:
            score_c = min(1.5, x) / 1.5
            if x >= 1.2:
                reason_c = "相对该分钟放量"
            elif x <= 0.7:
                reason_c = "相对该分钟缩量"
            else:
                reason_c = "量能接近同时点中位"

        score_d = 0.0
        reason_d = "量谷"
        if vol_state == "ridge" and at_high:
            score_d = 0.2
            reason_d = "量岭创新高，冲击可能未结束"
        elif vol_state == "ridge" and not at_high and stall_bars >= 1:
            score_d = 0.8
            reason_d = "量岭推不动，吸收"
        elif vol_state == "peak" and (at_high or drawdown < 0.008):
            score_d = 0.78
            reason_d = "冲高后孤立量峰"
        elif vol_state == "valley" and drawdown > thresh:
            score_d = 0.35
            reason_d = "量谷回撤，交给方法A"
        if limit_up:
            score_d = min(score_d, 0.2)
            reason_d = "涨停附近不提前卖"

        sell_a = (not limit_down) and score_a >= SCORE_SELL
        sell_b = (not limit_up) and score_b >= SCORE_SELL
        sell_d = (not limit_up) and score_d >= SCORE_SELL

        bounce = 0.0
        if session_low and session_low > 0:
            bounce = (close - session_low) / session_low
        score_a_buy = 0.0
        if bounce > 0:
            score_a_buy = min(1.0, bounce / thresh)
        if tau <= 15 and no_new_low >= 3:
            score_a_buy = min(1.0, max(score_a_buy, 0.55 + (15 - tau) / 30.0))
        if tau <= 5:
            score_a_buy = max(score_a_buy, 0.9)
        if at_low and tau > 15:
            score_a_buy = min(score_a_buy, 0.15)

        score_b_buy = 0.0
        reason_b_buy = "无抛压涌入"
        if down_flow == "surge":
            score_b_buy = 0.15
            reason_b_buy = "卖盘涌入中"
        elif down_flow == "stall":
            score_b_buy = 0.55 if down_stall_bars == 1 else 0.72
            reason_b_buy = "下跌受阻"
        elif down_flow == "flip":
            score_b_buy = 0.88
            reason_b_buy = "空翻多"
        if limit_down:
            score_b_buy = min(score_b_buy, 0.2)
            reason_b_buy = "跌停附近不提前买"

        score_d_buy = 0.0
        reason_d_buy = "量谷"
        if vol_state == "ridge" and at_low:
            score_d_buy = 0.2
            reason_d_buy = "量岭创新低，抛压可能未结束"
        elif vol_state == "ridge" and not at_low and down_stall_bars >= 1:
            score_d_buy = 0.8
            reason_d_buy = "量岭砸不动，吸收"
        elif vol_state == "peak" and (at_low or bounce < 0.008):
            score_d_buy = 0.78
            reason_d_buy = "探底后孤立量峰"
        elif vol_state == "valley" and bounce > thresh:
            score_d_buy = 0.35
            reason_d_buy = "量谷反弹，交给方法A"
        if limit_down:
            score_d_buy = min(score_d_buy, 0.2)
            reason_d_buy = "跌停附近不提前买"

        buy_a = (not limit_up) and score_a_buy >= SCORE_SELL
        buy_b = (not limit_down) and score_b_buy >= SCORE_SELL
        buy_d = (not limit_down) and score_d_buy >= SCORE_SELL

        row = {
            **{k: raw.get(k) for k in (
                "bar_time", "clock", "trade_date", "open", "high", "low",
                "close", "vol", "amount", "avg_price",
            )},
            "side": side,
            "signed_vol": signed_vol,
            "session_high": session_high,
            "session_low": session_low,
            "vwap": vwap,
            "tau": tau,
            "drawdown": round(drawdown, 6),
            "bounce": round(bounce, 6),
            "thresh_a": round(thresh, 6),
            "eta": round(eta, 8),
            "eta_down": round(eta_down, 8),
            "q_plus": q_plus,
            "q_minus": q_minus,
            "x": None if x is None else round(x, 4),
            "vol_median": med or None,
            "vol_state": vol_state,
            "flow_state": flow,
            "down_flow": down_flow,
            "score_a": round(score_a, 4),
            "score_b": round(score_b, 4),
            "score_c": round(score_c, 4),
            "score_d": round(score_d, 4),
            "score_a_buy": round(score_a_buy, 4),
            "score_b_buy": round(score_b_buy, 4),
            "score_d_buy": round(score_d_buy, 4),
            "sell_a": sell_a,
            "sell_b": sell_b,
            "sell_d": sell_d,
            "buy_a": buy_a,
            "buy_b": buy_b,
            "buy_d": buy_d,
            "reason_a": "回撤达标" if sell_a else ("仍在新高附近" if at_high else "回撤未够"),
            "reason_b": reason_b,
            "reason_c": reason_c if x is not None else "无同时点历史",
            "reason_d": reason_d,
            "reason_a_buy": "反弹达标" if buy_a else ("仍在新低附近" if at_low else "反弹未够"),
            "reason_b_buy": reason_b_buy,
            "reason_d_buy": reason_d_buy,
            "limit_up": limit_up,
            "limit_down": limit_down,
            "at_session_high": at_high,
            "at_session_low": at_low,
            "lookback_n": int(clk.get("n") or 0),
        }
        annotated.append(row)
        if prev_close and prev_close > 0:
            running_rets.append((close - float(prev_close)) / float(prev_close))
        prev_close = close

    return annotated


def last_and_events(
    rows: List[Dict[str, Any]],
    method: str,
    max_events: int = 12,
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    if not rows:
        return None, []
    last = rows[-1]
    key_sell = {"a": "sell_a", "b": "sell_b", "d": "sell_d"}.get(method)
    events: List[Dict[str, Any]] = []
    for r in rows:
        interesting = False
        if method == "a" and (r.get("sell_a") or r.get("at_session_high")):
            interesting = r.get("sell_a") or False
        elif method == "b" and r.get("flow_state") in ("stall", "flip"):
            interesting = True
        elif method == "c" and r.get("x") is not None and r["x"] >= 1.2:
            interesting = True
        elif method == "d" and r.get("vol_state") in ("peak", "ridge"):
            interesting = True
        if key_sell and r.get(key_sell):
            interesting = True
        if interesting:
            events.append(_event_view(r, method))
    if len(events) > max_events:
        events = events[-max_events:]
    return _event_view(last, method), events


def _event_view(r: Dict[str, Any], method: str) -> Dict[str, Any]:
    base = {
        "time": r.get("bar_time"),
        "clock": r.get("clock"),
        "close": r.get("close"),
        "vol": r.get("vol"),
        "session_high": r.get("session_high"),
        "tau": r.get("tau"),
    }
    if method == "a":
        base.update({
            "drawdown": r.get("drawdown"),
            "score_a": r.get("score_a"),
            "sell_now": r.get("sell_a"),
            "reason": r.get("reason_a"),
        })
    elif method == "b":
        base.update({
            "flow_state": r.get("flow_state"),
            "eta": r.get("eta"),
            "score_b": r.get("score_b"),
            "sell_now": r.get("sell_b"),
            "reason": r.get("reason_b"),
            "x": r.get("x"),
        })
    elif method == "c":
        base.update({
            "x": r.get("x"),
            "vol_median": r.get("vol_median"),
            "score_c": r.get("score_c"),
            "reason": r.get("reason_c"),
        })
    elif method == "d":
        base.update({
            "vol_state": r.get("vol_state"),
            "flow_state": r.get("flow_state"),
            "score_d": r.get("score_d"),
            "sell_now": r.get("sell_d"),
            "reason": r.get("reason_d"),
            "x": r.get("x"),
        })
    return base

"""MA60 均线判断买入点和卖出点（同一策略、两套机制）。

机制 A：收盘价低于 MA60 —— 折价/缩量/深折价放量买入，反弹短线卖出。
机制 B：收盘价高于 MA60，且近 10 日里至少 8 日均线斜率为正 ——
        关闭「远超均线就卖」；相对 60 日量能中位数放量 + 最高价超 MA5 + 相对 MA60 已到高位的十字星当天卖。
趋势回踩必须站上 MA5/MA20，避免跌破途中抄在半山腰。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from server.a_share_fees import affordable_buy_shares, calc_fees, fee_summary
from server.structure_pullback import lot_shares

MA_FAST = 5
MA_MID = 20
MA_SLOW = 60
MIN_BARS = 80


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def _f(params: Optional[Dict[str, Any]], key: str, default: float) -> float:
    try:
        return float((params or {}).get(key, default) or default)
    except (TypeError, ValueError):
        return default


def add_features(df: pd.DataFrame, params: Optional[Dict[str, Any]] = None) -> pd.DataFrame:
    """日线 → MA5/20/60、折价、缩量、放量、主买点标记。"""
    p = params or {}
    g = df.sort_values("trade_date").reset_index(drop=True).copy()
    for col in ("open", "high", "low", "close"):
        g[col] = _num(g[col])
    vol = g["volume"] if "volume" in g.columns else g.get("vol")
    g["volume"] = _num(vol).fillna(0.0)

    close = g["close"]
    g["ma5"] = close.rolling(MA_FAST, min_periods=MA_FAST).mean()
    g["ma20"] = close.rolling(MA_MID, min_periods=MA_MID).mean()
    g["ma60"] = close.rolling(MA_SLOW, min_periods=MA_SLOW).mean()

    slope_days = max(1, int(_f(p, "slope_days", 5)))
    g["ma60_slope"] = g["ma60"] - g["ma60"].shift(slope_days)
    g["dev"] = (g["ma60"] - close) / g["ma60"].replace(0, np.nan)
    g["body"] = (g["open"] - close).abs() / g["open"].replace(0, np.nan)

    prev4_min = g["volume"].shift(1).rolling(4, min_periods=4).min()
    g["vol_today_min5"] = g["volume"] <= prev4_min
    recent10 = g["volume"].rolling(10, min_periods=10).mean()
    prior10 = g["volume"].shift(10).rolling(10, min_periods=10).mean()
    g["vol_shrunk"] = recent10 < prior10
    g["vol_prev10"] = g["volume"].shift(1).rolling(10, min_periods=10).mean()
    expand = _f(p, "vol_expand", 1.5)
    g["vol_expand"] = g["volume"] >= g["vol_prev10"] * expand
    prev5_max = g["volume"].shift(1).rolling(5, min_periods=5).max()
    prev5_mean = g["volume"].shift(1).rolling(5, min_periods=5).mean()
    g["vol_spike5"] = g["volume"] >= prev5_max * expand
    g["vol_gt_prev5"] = g["volume"] > prev5_mean
    yin_pct = _f(p, "long_yin_pct", 3) / 100.0
    g["long_yin"] = (close < g["open"]) & ((g["open"] - close) / g["open"].replace(0, np.nan) >= yin_pct)

    min_dev = _f(p, "min_dev", 3) / 100.0
    body_pct = _f(p, "body_pct", 3) / 100.0
    g["entry_raw"] = (
        g["ma60"].notna()
        & g["ma60_slope"].notna()
        & (g["ma60_slope"] < 0)
        & (close < g["ma60"])
        & (g["dev"] >= min_dev)
        & (g["body"] <= body_pct)
        & g["vol_today_min5"].fillna(False)
        & g["vol_shrunk"].fillna(False)
    )
    prev_slope = g["ma60_slope"].shift(1)
    g["slope_turn_up"] = (
        g["ma60"].notna()
        & g["ma60_slope"].notna()
        & prev_slope.notna()
        & (g["ma60_slope"] > 0)
        & (prev_slope <= 0)
    )
    crash_dev = _f(p, "crash_dev", 15) / 100.0
    g["crash_buy"] = (
        g["ma60"].notna()
        & (close < g["ma60"])
        & (g["dev"] >= crash_dev)
        & g["vol_spike5"].fillna(False)
    )
    slope_window = max(2, int(_f(p, "slope_window", 10)))
    slope_hits = max(1, int(_f(p, "slope_hits", 8)))
    slope_hits = min(slope_hits, slope_window)
    g["slope_up_day"] = g["ma60_slope"] > 0
    g["slope_up_n"] = g["slope_up_day"].rolling(slope_window, min_periods=slope_window).sum()
    g["regime_below"] = g["ma60"].notna() & (close < g["ma60"])
    g["regime_above"] = (
        g["ma60"].notna()
        & (close > g["ma60"])
        & g["slope_up_day"].fillna(False)
        & (g["slope_up_n"] >= slope_hits)
    )
    doji_body = _f(p, "doji_body_pct", 1.5) / 100.0
    rng = (g["high"] - g["low"]).clip(lower=1e-9)
    body = (g["open"] - close).abs()
    upper = g["high"] - np.maximum(g["open"], close)
    lower = np.minimum(g["open"], close) - g["low"]
    g["doji"] = (
        (body / g["open"].replace(0, np.nan) <= doji_body)
        & (rng / g["open"].replace(0, np.nan) >= doji_body)
        & (upper > 0)
        & (lower > 0)
    )
    excess = _f(p, "ma5_doji_excess", 4) / 100.0
    high_ma60 = _f(p, "b_ma60_high", 35) / 100.0
    g["high_over_ma5"] = (g["high"] - g["ma5"]) / g["ma5"].replace(0, np.nan)
    g["high_over_ma60"] = (g["high"] - g["ma60"]) / g["ma60"].replace(0, np.nan)
    g["vol_med60"] = g["volume"].shift(1).rolling(60, min_periods=20).median()
    g["vol_expand_base"] = g["volume"] >= g["vol_med60"] * expand
    g["b_doji_sell"] = (
        g["vol_expand_base"].fillna(False)
        & (g["high_over_ma5"] >= excess)
        & (g["high_over_ma60"] >= high_ma60)
        & g["doji"].fillna(False)
    )
    return g


def earlier_shrink_note(g: pd.DataFrame, i: int, lookback: int = 30) -> str:
    """对照更早一次缩量日的 MA60 折价，给买入反馈。"""
    if i < MA_SLOW + 5:
        return ""
    start = max(MA_SLOW, i - lookback)
    window = g.iloc[start:i]
    if window.empty:
        return ""
    hits = window[
        window["vol_today_min5"].fillna(False)
        & window["ma60"].notna()
        & (window["close"] < window["ma60"])
    ]
    if hits.empty:
        return ""
    old = float(hits["dev"].iloc[-1])
    today = float(g.iloc[i]["dev"])
    if not np.isfinite(old) or not np.isfinite(today):
        return ""
    if today > old + 1e-6:
        return f"早前萎靡偏离{old * 100:.1f}%，今日更深{today * 100:.1f}%"
    return f"早前萎靡偏离{old * 100:.1f}%，今日{today * 100:.1f}%（折价未加深）"


def _downsample(values: List[Any], dates: List[str], max_n: int) -> Tuple[List[Any], List[str]]:
    if len(values) <= max_n:
        return values, dates
    idx = np.linspace(0, len(values) - 1, max_n).astype(int)
    return [values[i] for i in idx], [dates[i] for i in idx]


def simulate(
    df: pd.DataFrame,
    cash: float = 1_000_000,
    params: Optional[Dict[str, Any]] = None,
    exec_price: str = "next_open",
) -> Dict[str, Any]:
    """按规则仿真：缩量买、斜率转正当天买、远超 MA60 放量卖。"""
    p = dict(params or {})
    p["position_mode"] = "percent"
    g = add_features(df, p)
    n = len(g)
    empty = {
        "total_return": 0.0,
        "max_drawdown": 0.0,
        "sharpe": 0.0,
        "win_rate": 0.0,
        "trade_count": 0,
        "cash": float(cash),
        "final_equity": float(cash),
        "equity": [],
        "equity_cash": [],
        "equity_dates": [],
        "trades": [],
        "fees": {
            **fee_summary(p),
            "commission_total": 0.0,
            "stamp_total": 0.0,
            "fee_total": 0.0,
        },
    }
    if n < 2:
        return empty

    close = g["close"].astype(float).values
    open_ = g["open"].astype(float).values
    dates = [str(d)[:10] for d in g["trade_date"].tolist()]
    ma5 = g["ma5"].astype(float).values
    ma20 = g["ma20"].astype(float).values
    ma60 = g["ma60"].astype(float).values
    entry_raw = g["entry_raw"].fillna(False).astype(bool).values
    slope_turn_up = g["slope_turn_up"].fillna(False).astype(bool).values
    crash_buy = g["crash_buy"].fillna(False).astype(bool).values
    regime_above = g["regime_above"].fillna(False).astype(bool).values
    regime_below = g["regime_below"].fillna(False).astype(bool).values
    vol_expand = g["vol_expand"].fillna(False).astype(bool).values
    vol_gt_prev5 = g["vol_gt_prev5"].fillna(False).astype(bool).values
    long_yin = g["long_yin"].fillna(False).astype(bool).values
    b_doji_sell = g["b_doji_sell"].fillna(False).astype(bool).values

    ma60_near = _f(p, "ma60_near", 2) / 100.0
    ma60_stretch = _f(p, "ma60_stretch", 8) / 100.0
    ma5_stretch = _f(p, "ma5_stretch", 5) / 100.0
    add_pct = _f(p, "add_pct", 15)
    max_adds = max(0, int(_f(p, "max_adds", 2)))
    next_open = exec_price == "next_open"

    position = 0.0
    cash_left = float(cash)
    entry_price = 0.0
    last_fill_i = -99
    mode = ""  # "" | short | long | crash
    trend_hold = False
    a_locked = False
    long_bars = 0
    seen_below_ma20 = False
    adds = 0
    pending: Optional[Dict[str, Any]] = None
    trades: List[Dict[str, Any]] = []
    equity: List[float] = []
    equity_cash: List[float] = []
    equity_dates: List[str] = []
    wins = 0
    closed = 0
    commission_total = 0.0
    stamp_total = 0.0

    def _fill(side: str, qty: float, px: float, day: str, reason: str) -> bool:
        nonlocal position, cash_left, entry_price, wins, closed, commission_total, stamp_total
        qty = float(qty)
        if qty < 1 or px <= 0 or np.isnan(px):
            return False
        commission = 0.0
        stamp = 0.0
        pnl = None
        if side == "买入":
            affordable = float(affordable_buy_shares(cash_left, px, p))
            qty = min(qty, affordable)
            qty = float(int(qty // 100) * 100)
            if qty < 100:
                return False
            amount = qty * px
            commission, stamp = calc_fees("买入", amount, p)
            if amount + commission > cash_left + 1e-6:
                qty = float(affordable_buy_shares(cash_left, px, p))
                if qty < 100:
                    return False
                amount = qty * px
                commission, stamp = calc_fees("买入", amount, p)
            if position <= 0:
                entry_price = px
            else:
                entry_price = (entry_price * position + px * qty) / (position + qty)
            cash_left -= amount + commission
            position += qty
        else:
            qty = min(qty, position)
            qty = float(int(qty // 100) * 100) if qty < position else qty
            if qty < 1:
                return False
            amount = qty * px
            commission, stamp = calc_fees("卖出", amount, p)
            cash_left += amount - commission - stamp
            pnl = ((px - entry_price) * qty if entry_price > 0 else 0.0) - commission - stamp
            closed += 1
            if pnl > 0:
                wins += 1
            position -= qty
            if position < 1:
                position = 0.0
                entry_price = 0.0
        commission_total += commission
        stamp_total += stamp
        trades.append(
            {
                "date": day,
                "side": side,
                "price": round(float(px), 4),
                "size": int(abs(qty)),
                "pnl": None if pnl is None else round(float(pnl), 2),
                "reason": reason,
                "commission": round(float(commission), 2),
                "stamp_tax": round(float(stamp), 2),
                "fee": round(float(commission + stamp), 2),
                "cash_after": round(float(cash_left), 2),
                "position_after": int(position),
            }
        )
        return True

    def _exec_px(i: int) -> float:
        px = float(open_[i] if next_open else close[i])
        if px <= 0 or np.isnan(px):
            px = float(close[i]) if close[i] > 0 else 1.0
        return px

    def _run_pending(i: int) -> None:
        nonlocal pending, last_fill_i, mode, seen_below_ma20, adds, long_bars, trend_hold
        if not pending:
            return
        px = _exec_px(i)
        day = dates[i]
        kind = pending.get("kind")
        if pending["side"] == "买入":
            add_params = dict(p)
            if kind == "add":
                add_params["position_pct"] = add_pct
            equity_now = cash_left + position * px
            wanted = float(lot_shares(equity_now, px, add_params, cash_left=cash_left))
            if _fill("买入", wanted, px, day, pending["reason"]):
                last_fill_i = i
                if kind == "add":
                    adds += 1
                else:
                    mode = "short"
                    trend_hold = False
                    adds = 0
                    long_bars = 0
                    m20 = ma20[i]
                    seen_below_ma20 = bool(np.isfinite(m20) and close[i] < m20)
        else:
            if _fill("卖出", position, px, day, pending["reason"]):
                last_fill_i = i
                mode = ""
                trend_hold = False
                adds = 0
                long_bars = 0
                seen_below_ma20 = False
        pending = None

    def _queue(side: str, reason: str, kind: str = "") -> None:
        nonlocal pending
        if pending is None:
            pending = {"side": side, "reason": reason, "kind": kind}

    def _buy_at_close(i: int, reason: str, new_mode: str) -> bool:
        nonlocal last_fill_i, mode, adds, long_bars, seen_below_ma20, trend_hold, a_locked
        px = float(close[i])
        if px <= 0 or not np.isfinite(px):
            px = _exec_px(i)
        wanted = float(lot_shares(cash_left + position * px, px, p, cash_left=cash_left))
        if not _fill("买入", wanted, px, dates[i], reason):
            return False
        last_fill_i = i
        mode = new_mode
        trend_hold = new_mode == "long"
        if new_mode == "long":
            a_locked = False
        adds = 0
        long_bars = 0
        seen_below_ma20 = bool(np.isfinite(ma20[i]) and close[i] < ma20[i])
        return True

    def _sell_at_close(i: int, reason: str) -> bool:
        nonlocal last_fill_i, mode, adds, long_bars, seen_below_ma20, pending, trend_hold, a_locked
        px = float(close[i])
        if px <= 0 or not np.isfinite(px):
            px = _exec_px(i)
        if not _fill("卖出", position, px, dates[i], reason):
            return False
        last_fill_i = i
        mode = ""
        trend_hold = False
        if "十字星" in reason:
            a_locked = True
        adds = 0
        long_bars = 0
        seen_below_ma20 = False
        pending = None
        return True

    for i in range(n):
        if next_open:
            _run_pending(i)

        c = float(close[i])
        m5, m20, m60 = ma5[i], ma20[i], ma60[i]
        if position >= 100:
            if np.isfinite(m20) and c < m20:
                seen_below_ma20 = True
            if mode == "crash" and np.isfinite(m60) and c > m60:
                mode = "long"
                trend_hold = True
                long_bars = 0
            elif bool(regime_above[i]) and mode == "crash":
                mode = "long"
                trend_hold = True
                long_bars = 0
            if (bool(regime_above[i]) or mode == "long") and np.isfinite(m60) and c > m60:
                trend_hold = True
            if mode != "crash" and np.isfinite(m60) and c > m60:
                if mode != "long":
                    mode = "long"
                    long_bars = 0
                else:
                    long_bars += 1
            can_signal = i > last_fill_i
            near_ma60 = (
                np.isfinite(m60)
                and m60 > 0
                and c > m60
                and (c - m60) / m60 <= ma60_near
            )
            if can_signal and (bool(regime_above[i]) or trend_hold):
                # 机制 B / 斜率转正后的趋势持有：远超均线与轻触跌破都不卖
                if bool(b_doji_sell[i]):
                    kind = "阳" if c >= float(open_[i]) else "阴"
                    _sell_at_close(i, f"高位{kind}十字星放量卖出")
                elif (
                    pending is None
                    and near_ma60
                    and np.isfinite(m5)
                    and np.isfinite(m20)
                    and c > m5
                    and c > m20
                    and not bool(vol_expand[i])
                    and adds < max_adds
                    and long_bars >= 1
                ):
                    note = f"贴近MA60 {(c - m60) / m60 * 100:.1f}%"
                    _queue("买入", f"趋势回踩补仓 {note}", "add")
            elif can_signal and mode == "crash":
                above_ma5 = np.isfinite(m5) and c > m5
                if above_ma5 and bool(vol_gt_prev5[i]) and bool(long_yin[i]):
                    _sell_at_close(i, "站上MA5放量长阴卖出")
            elif can_signal and pending is None:
                # 机制 A / 未确认升势：远超均线放量仍卖
                stretched = (
                    np.isfinite(m5)
                    and m5 > 0
                    and (c - m5) / m5 >= ma5_stretch
                )
                far_ma60 = (
                    np.isfinite(m60)
                    and m60 > 0
                    and (c - m60) / m60 >= ma60_stretch
                )
                if far_ma60 and bool(vol_expand[i]):
                    _queue("卖出", "远超MA60放量卖出")
                if pending is None and mode == "short":
                    if np.isfinite(m60) and c >= m60:
                        mode = "long"
                        long_bars = 0
                    elif (
                        np.isfinite(m20)
                        and c >= m20
                        and bool(vol_expand[i])
                        and seen_below_ma20
                    ):
                        _queue("卖出", "MA20放量兑现")
                if pending is None and mode == "long":
                    if np.isfinite(m60) and c < m60:
                        _queue("卖出", "跌破MA60离场")
                    elif stretched and bool(vol_expand[i]):
                        _queue("卖出", "远离MA5放量清仓")
        elif position < 100:
            allow_a = not a_locked
            if allow_a and bool(crash_buy[i]) and pending is None:
                _buy_at_close(i, "MA60折价15%放量买", "crash")
            elif bool(slope_turn_up[i]) and pending is None and (allow_a or bool(regime_above[i])):
                _buy_at_close(i, "MA60斜率转正当天买", "long")
            elif (
                pending is None
                and bool(regime_above[i])
                and np.isfinite(m60)
                and np.isfinite(m5)
                and np.isfinite(m20)
                and m60 > 0
                and c > m60
                and c > m5
                and c > m20
                and (c - m60) / m60 <= ma60_near
                and not bool(vol_expand[i])
            ):
                _buy_at_close(i, "趋势回踩开仓", "long")
            elif allow_a and pending is None and bool(entry_raw[i]) and bool(regime_below[i]):
                note = earlier_shrink_note(g, i)
                reason = "MA60缩量折价买"
                if note:
                    reason = f"{reason}（{note}）"
                _queue("买入", reason, "entry")

        if not next_open:
            if pending and pending["side"] == "卖出" and last_fill_i == i:
                pending = None
            else:
                _run_pending(i)

        nav = cash_left + position * c
        equity.append(round(nav / cash if cash else 1.0, 6))
        equity_cash.append(round(nav, 2))
        equity_dates.append(dates[i])

    eq = np.array(equity, dtype=float)
    if len(eq) < 2:
        return empty
    total_return = float(eq[-1] / eq[0] - 1)
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak) / np.where(peak == 0, 1, peak)
    max_drawdown = float(dd.min())
    rets = np.diff(eq) / np.where(eq[:-1] == 0, 1, eq[:-1])
    rets = rets[np.isfinite(rets)]
    if len(rets) > 1 and rets.std() > 1e-12:
        sharpe = float(rets.mean() / rets.std() * np.sqrt(252))
    else:
        sharpe = 0.0
    equity_out, dates_out = _downsample(equity, equity_dates, 800)
    cash_out, _ = _downsample(equity_cash, equity_dates, 800)
    return {
        "total_return": round(total_return, 4),
        "max_drawdown": round(max_drawdown, 4),
        "sharpe": round(sharpe, 2),
        "win_rate": round(float(wins / closed) if closed else 0.0, 4),
        "trade_count": len(trades),
        "cash": float(cash),
        "final_equity": round(float(equity_cash[-1]), 2) if equity_cash else float(cash),
        "equity": equity_out,
        "equity_cash": cash_out,
        "equity_dates": dates_out,
        "trades": trades[-200:],
        "fees": {
            **fee_summary(p),
            "commission_total": round(commission_total, 2),
            "stamp_total": round(stamp_total, 2),
            "fee_total": round(commission_total + stamp_total, 2),
        },
    }

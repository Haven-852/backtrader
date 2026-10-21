"""
C 类：趋势回踩结构止损（短线）。

收盘后出信号，次日开盘买；A 股 T+1，进场当天不能卖。
赚钱来自升势里缩量回踩后的反弹，用结构低点限制单笔亏损，趋势走坏或到期离场。
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

MA10 = 10
MA20 = 20
ATR_N = 14
STRUCT_N = 10
VMA_N = 5
MAX_RISK = 0.04
GAP_CHASE = 1.01


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def add_features(g: pd.DataFrame) -> pd.DataFrame:
    """单票日线 → ma10/ma20/atr14/结构低点/缩量回踩/candidate。"""
    g = g.sort_values("trade_date").reset_index(drop=True).copy()
    for col in ("open", "high", "low", "close"):
        g[col] = _num(g[col])
    vol = g["volume"] if "volume" in g.columns else g.get("vol")
    g["volume"] = _num(vol).fillna(0.0)

    g["ma10"] = g["close"].rolling(MA10, min_periods=MA10).mean()
    g["ma20"] = g["close"].rolling(MA20, min_periods=MA20).mean()
    prev = g["close"].shift(1)
    tr = pd.concat(
        [
            (g["high"] - g["low"]).abs(),
            (g["high"] - prev).abs(),
            (g["low"] - prev).abs(),
        ],
        axis=1,
    ).max(axis=1)
    g["atr14"] = tr.rolling(ATR_N, min_periods=ATR_N).mean()
    g["vma5"] = g["volume"].rolling(VMA_N, min_periods=VMA_N).mean()
    g["vma5_prev"] = g["vma5"].shift(1)

    g["structure_low"] = g["low"].rolling(STRUCT_N, min_periods=STRUCT_N).min()
    g["signal_stop"] = g["structure_low"]
    g["risk_fraction_signal"] = (g["close"] - g["signal_stop"]) / g["close"].replace(0, pd.NA)
    g["vol_pullback"] = g["volume"] / g["vma5_prev"].replace(0, pd.NA)

    uptrend = (g["ma10"] > g["ma20"]) & (g["close"] > g["ma20"])
    touch_ma10 = (g["low"] <= g["ma10"] * 1.01) & (g["close"] >= g["ma10"] * 0.985)
    quiet = g["vol_pullback"] <= 1.0
    risk_ok = (g["risk_fraction_signal"] > 0) & (g["risk_fraction_signal"] <= MAX_RISK)
    g["candidate"] = (
        uptrend.fillna(False)
        & touch_ma10.fillna(False)
        & quiet.fillna(False)
        & risk_ok.fillna(False)
        & g["atr14"].notna()
    )
    return g


def classify_last(g: pd.DataFrame, asof: date) -> Optional[Dict[str, Any]]:
    """asof 当日是否为 C 类信号。"""
    feat = add_features(g)
    if feat.empty:
        return None
    last = feat.iloc[-1]
    try:
        last_day = pd.Timestamp(last["trade_date"]).date()
    except Exception:
        return None
    if last_day != asof:
        return None
    if not bool(last["candidate"]):
        return None

    close = float(last["close"])
    ma10 = float(last["ma10"])
    risk = float(last["risk_fraction_signal"])
    vol_pb = float(last["vol_pullback"]) if pd.notna(last["vol_pullback"]) else 1.0
    atr = float(last["atr14"]) if pd.notna(last["atr14"]) else 0.0
    stop = float(last["signal_stop"])
    structure_low = float(last["structure_low"])

    risk_s = max(0.0, min(1.0, 1.0 - risk / MAX_RISK))
    vol_s = max(0.0, min(1.0, (1.0 - vol_pb) / 0.5)) if vol_pb <= 1.0 else 0.0
    prox = abs(close - ma10) / ma10 if ma10 > 0 else 1.0
    prox_s = max(0.0, min(1.0, 1.0 - prox / 0.02))
    score = 50.0 * risk_s + 25.0 * vol_s + 25.0 * prox_s

    below = (ma10 - close) / ma10 if ma10 else 0.0
    prev_close = float(feat.iloc[-2]["close"]) if len(feat) >= 2 and feat.iloc[-2]["close"] else close
    pct0 = close / prev_close - 1.0 if prev_close else 0.0
    reason = (
        f"C回踩·MA10>{float(last['ma20']):.2f}·贴MA10·"
        f"缩量{vol_pb:.2f}·风险{risk*100:.2f}%·"
        f"结构低{structure_low:.2f}·止损{stop:.2f}·ATR{atr:.3f}·"
        f"次日开盘买·T+1才可卖·最多持5日"
    )
    return {
        "setup_type": "C",
        "score": round(float(score), 2),
        "label": "趋势回踩",
        "reason": reason,
        "close": close,
        "ma5": ma10,
        "below_pct": round(float(below * 100), 3),
        "pct_chg": round(float(pct0 * 100), 3),
        "decline_days": None,
        "expect_up_days": 5,
        "ma10": round(ma10, 4),
        "ma20": round(float(last["ma20"]), 4) if pd.notna(last["ma20"]) else None,
        "atr14": round(atr, 4),
        "structure_low": round(structure_low, 4),
        "signal_stop": round(stop, 4),
        "risk_fraction_signal": round(risk, 5),
        "vol_pullback": round(vol_pb, 4),
    }


def backtest(
    df: pd.DataFrame,
    max_holding: int = 5,
    atr_multiple: float = 2.5,
    trigger_r: float = 2.0,
    slippage_bps: float = 5.0,
) -> pd.DataFrame:
    """
    每只股票独立交易，不共享资金。
    信号在 t 日收盘确认，t+1 开盘买；进场当日不能卖（T+1）。
    移动止损只用前一日信息；到期用收盘近似集合竞价卖出。
    """
    if df is None or df.empty:
        return pd.DataFrame()
    work = df.copy()
    if "symbol" not in work.columns:
        if "ts_code" in work.columns:
            work["symbol"] = work["ts_code"]
        else:
            work["symbol"] = "ONE"
    if "volume" not in work.columns and "vol" in work.columns:
        work["volume"] = work["vol"]

    trades: List[Dict[str, Any]] = []
    buy_factor = 1 + slippage_bps / 10000
    sell_factor = 1 - slippage_bps / 10000
    for symbol, g0 in work.groupby("symbol", sort=False):
        g = add_features(g0)
        n = len(g)
        j = 0
        while j < n - 1:
            sig = g.iloc[j]
            if not bool(sig["candidate"]):
                j += 1
                continue
            entry_i = j + 1
            ent = g.iloc[entry_i]
            if float(ent["volume"] or 0) <= 0 or pd.isna(ent["open"]) or float(ent["open"]) <= 0:
                j += 1
                continue
            if float(ent["open"]) > float(sig["close"]) * GAP_CHASE:
                j += 1
                continue
            entry_price = float(ent["open"]) * buy_factor
            initial_stop = float(sig["signal_stop"])
            risk = entry_price - initial_stop
            if not (0 < risk / entry_price <= MAX_RISK):
                j += 1
                continue
            peak = float(ent["high"])
            active_stop = initial_stop
            pending_trend_exit = False
            final_i = min(n - 1, entry_i + max_holding - 1)
            exit_i, exit_price, reason = None, None, None
            for i in range(entry_i + 1, final_i + 1):
                bar = g.iloc[i]
                prevbar = g.iloc[i - 1]
                if peak >= entry_price + trigger_r * risk and pd.notna(prevbar["atr14"]):
                    active_stop = max(
                        active_stop,
                        peak - atr_multiple * float(prevbar["atr14"]),
                    )
                if pending_trend_exit:
                    exit_i, exit_price, reason = i, float(bar["open"]) * sell_factor, "ma10_high_volume"
                    break
                if float(bar["open"]) <= active_stop:
                    exit_i, exit_price, reason = i, float(bar["open"]) * sell_factor, "gap_below_stop"
                    break
                if float(bar["low"]) <= active_stop:
                    exit_i, exit_price, reason = i, active_stop * sell_factor, "stop_or_trail"
                    break
                if i == final_i:
                    exit_i, exit_price, reason = i, float(bar["close"]) * sell_factor, "time_exit_close_proxy"
                    break
                if (
                    pd.notna(bar["ma10"])
                    and pd.notna(bar["vma5_prev"])
                    and float(bar["close"]) < float(bar["ma10"])
                    and float(bar["volume"]) > 1.5 * float(bar["vma5_prev"])
                ):
                    pending_trend_exit = True
                peak = max(peak, float(bar["high"]))
            if exit_i is None:
                j = n
                continue
            trades.append(
                {
                    "symbol": symbol,
                    "signal_date": sig["trade_date"],
                    "entry_date": ent["trade_date"],
                    "exit_date": g.iloc[exit_i]["trade_date"],
                    "entry_price": round(entry_price, 4),
                    "exit_price": round(float(exit_price), 4),
                    "holding_sessions": int(exit_i - entry_i + 1),
                    "initial_stop": round(initial_stop, 4),
                    "exit_reason": reason,
                    "gross_return_before_fees": float(exit_price) / entry_price - 1,
                }
            )
            j = exit_i + 1
    return pd.DataFrame(trades)


def trades_to_sim_result(trades: pd.DataFrame, cash: float, dates: List[str]) -> Dict[str, Any]:
    """把逐笔交易转成策略分析回测面板要用的权益曲线。"""
    if trades is None or trades.empty:
        eq = [1.0] * max(1, len(dates))
        return {
            "total_return": 0.0,
            "max_drawdown": 0.0,
            "sharpe": 0.0,
            "win_rate": 0.0,
            "trade_count": 0,
            "equity": eq[:120],
            "equity_dates": dates[:120],
            "trades": [],
        }

    nav = float(cash)
    curve_map: Dict[str, float] = {}
    ui_trades: List[Dict[str, Any]] = []
    wins = 0
    for _, t in trades.iterrows():
        ret = float(t["gross_return_before_fees"])
        pnl = nav * ret
        nav *= 1.0 + ret
        if ret > 0:
            wins += 1
        entry_d = str(t["entry_date"])[:10]
        exit_d = str(t["exit_date"])[:10]
        curve_map[exit_d] = nav / cash
        ui_trades.append(
            {
                "date": entry_d,
                "side": "买入",
                "price": float(t["entry_price"]),
                "size": 100,
                "pnl": None,
            }
        )
        ui_trades.append(
            {
                "date": exit_d,
                "side": "卖出",
                "price": float(t["exit_price"]),
                "size": 100,
                "pnl": round(pnl, 2),
                "reason": t.get("exit_reason"),
            }
        )

    equity: List[float] = []
    last = 1.0
    for d in dates:
        key = str(d)[:10]
        if key in curve_map:
            last = curve_map[key]
        equity.append(round(last, 6))

    eq = np.array(equity, dtype=float)
    total_return = float(eq[-1] / eq[0] - 1) if len(eq) else 0.0
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak) / np.where(peak == 0, 1, peak)
    max_drawdown = float(dd.min()) if len(dd) else 0.0
    rets = np.diff(eq) / np.where(eq[:-1] == 0, 1, eq[:-1]) if len(eq) > 1 else np.array([])
    rets = rets[np.isfinite(rets)]
    if len(rets) > 1 and rets.std() > 1e-12:
        sharpe = float(rets.mean() / rets.std() * (252 ** 0.5))
    else:
        sharpe = 0.0
    n = len(trades)
    if len(equity) > 120:
        idx = np.linspace(0, len(equity) - 1, 120).astype(int)
        equity_out = [equity[i] for i in idx]
        dates_out = [str(dates[i])[:10] for i in idx]
    else:
        equity_out, dates_out = equity, [str(d)[:10] for d in dates]
    return {
        "total_return": round(total_return, 4),
        "max_drawdown": round(max_drawdown, 4),
        "sharpe": round(sharpe, 2),
        "win_rate": round(wins / n, 4) if n else 0.0,
        "trade_count": n,
        "equity": equity_out,
        "equity_dates": dates_out,
        "trades": ui_trades[-30:],
    }

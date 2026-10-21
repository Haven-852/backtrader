"""A股日内高低点预判 V1（INTRADAY_TURNING_POINT）。

只做 5 分钟 K 线、未来 3 根（15 分钟）、三分类 LightGBM。
标签可以用未来行情，特征只能用当前 bar 及更早的数据。
预期涨跌空间是 ATR 障碍幅度，不是回归模型给出的点位。
回测只读库内分钟线，信号在 K 线完成后产生，最早下一根开盘价成交。
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import text

EXPERIMENT_ID = "INTRADAY_TURNING_POINT"
FEATURE_VERSION = "lgbm_5m_h3_v1"
LABEL_VERSION = "triple_barrier_v1"
HORIZON = 3
BARRIER_MULT = 0.5
ATR_WINDOW = 14
MIN_DAYS = 2
MIN_BARS = 400
MIN_TRAIN_ROWS = 300
MIN_TEST_ROWS = 200
MIN_TEST_DAYS = 8
COMMISSION = 0.00025
STAMP = 0.0005
SLIPPAGE = 0.0002
SHARES = 100
CLASS_NAMES = ("UP_FIRST", "DOWN_FIRST", "NEUTRAL")
FEATURE_COLUMNS = [
    "ret_1", "ret_3", "ret_5", "ret_10", "ret_20",
    "ret_from_open", "pos_in_day", "body_ratio", "upper_shadow", "lower_shadow",
    "vwap_dev", "vol_ratio", "amount_chg", "pv_dir", "atr_pct", "hv_20",
    "rsi_6", "rsi_14", "ema5_dev", "ema10_dev", "ema20_dev", "boll_pos",
    "index_ret_prev", "sector_ret_prev", "excess_vs_sector", "regime_num",
]
LGB_PARAMS = {
    "objective": "multiclass",
    "num_class": 3,
    "metric": "multi_logloss",
    "learning_rate": 0.05,
    "num_leaves": 31,
    "min_data_in_leaf": 40,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "verbosity": -1,
    "seed": 42,
    "num_threads": 4,
}
REGIME_NUM = {"牛": 1.0, "熊": -1.0, "修复": 0.5, "震荡": 0.0}
_CODE_RE = re.compile(r"^\d{6}\.(?:SZ|SH|BJ)$")
_ROOT = Path(__file__).resolve().parents[1]
_MODEL_DIR = _ROOT / "data" / "intraday_models"


def _engine():
    from server.storage_service import storage_manager
    return storage_manager.get_postgres_engine()


def _jsonable(obj):
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (np.floating, float)):
        v = float(obj)
        return v if np.isfinite(v) else None
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (pd.Timestamp, datetime)):
        return obj.isoformat()
    if obj is pd.NaT:
        return None
    return obj


def _code_version() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(_ROOT),
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return out.decode().strip() or "nogit"
    except Exception:
        return "nogit"


def _safe_codes(codes) -> list[str]:
    out = []
    for c in codes or []:
        c = str(c or "").strip().upper()
        if _CODE_RE.fullmatch(c) and c not in out:
            out.append(c)
    return out


def _as_shanghai(series: pd.Series) -> pd.Series:
    t = pd.to_datetime(series, utc=True, errors="coerce")
    return t.dt.tz_convert("Asia/Shanghai")


def resample_5min(minute_df: pd.DataFrame) -> pd.DataFrame:
    """把 1 分钟线收成 5 分钟。桶的时间是该 5 分钟的结束时刻，午休不连在一起。"""
    if minute_df is None or minute_df.empty:
        return pd.DataFrame()
    df = minute_df.copy()
    df["bar_time"] = _as_shanghai(df["bar_time"])
    df = df.dropna(subset=["bar_time", "close"]).sort_values(["ts_code", "bar_time"])
    for col in ("open", "high", "low", "close", "vol", "amount"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    mins = df["bar_time"].dt.hour * 60 + df["bar_time"].dt.minute
    morning = (mins >= 570) & (mins <= 690)
    afternoon = (mins >= 780) & (mins <= 900)
    base = np.where(morning, 570, np.where(afternoon, 780, -1))
    off = mins.to_numpy() - base
    exact = (off > 0) & (off % 5 == 0)
    end_off = np.where(exact, off, (off // 5 + 1) * 5)
    end_mins = base + end_off
    valid = base >= 0
    valid &= ~((base == 570) & (end_mins > 690))
    valid &= ~((base == 780) & (end_mins > 900))
    bucket = df["bar_time"].dt.normalize() + pd.to_timedelta(end_mins, unit="m")
    df = df.loc[valid].copy()
    df["bucket"] = bucket[valid]
    df = df.dropna(subset=["bucket"])

    def _sum_present(s: pd.Series):
        return s.sum(min_count=1)

    out = (
        df.groupby(["ts_code", "bucket"], sort=True)
        .agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            vol=("vol", _sum_present),
            amount=("amount", _sum_present),
        )
        .reset_index()
        .rename(columns={"bucket": "bar_time"})
    )
    out = out.dropna(subset=["open", "high", "low", "close"])
    out = out[(out["close"] > 0) & (out["high"] >= out["low"])]
    out["day"] = out["bar_time"].dt.date
    return out.reset_index(drop=True)


def _rsi(close: pd.Series, n: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(n).mean()
    loss = (-delta.clip(upper=0)).rolling(n).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _ema_dev(close: pd.Series, span: int) -> pd.Series:
    ema = close.ewm(span=span, adjust=False).mean()
    return close / ema - 1


def add_price_features(bars: pd.DataFrame) -> pd.DataFrame:
    """只使用当前及历史 bar。缺失留空，不用未来值去填。"""
    if bars.empty:
        return bars
    df = bars.sort_values(["ts_code", "bar_time"]).copy()
    g = df.groupby("ts_code", sort=False)
    df["ret_1"] = g["close"].pct_change(1)
    df["ret_3"] = g["close"].pct_change(3)
    df["ret_5"] = g["close"].pct_change(5)
    df["ret_10"] = g["close"].pct_change(10)
    df["ret_20"] = g["close"].pct_change(20)
    prev_close = g["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    df["atr"] = tr.groupby(df["ts_code"]).transform(lambda s: s.rolling(ATR_WINDOW).mean())
    day_g = df.groupby(["ts_code", "day"], sort=False)
    df["day_open"] = day_g["open"].transform("first")
    df["day_high"] = day_g["high"].cummax()
    df["day_low"] = day_g["low"].cummin()
    df["cum_amt"] = day_g["amount"].cumsum()
    df["cum_vol"] = day_g["vol"].cumsum()
    raw_vwap = df["cum_amt"] / df["cum_vol"].replace(0, np.nan)
    # 成交量有的接口按股、有的按手。只根据当前及历史的量级判断，不回看未来。
    scale = raw_vwap / df["close"].replace(0, np.nan)
    med = scale.groupby(df["ts_code"]).transform(lambda s: s.expanding(min_periods=5).median())
    vol_mult = np.where(med > 20, 100.0, np.where(med < 0.05, 0.001, 1.0))
    df["vwap"] = df["cum_amt"] / (df["cum_vol"] * vol_mult).replace(0, np.nan)
    span = (df["day_high"] - df["day_low"]).replace(0, np.nan)
    df["ret_from_open"] = df["close"] / df["day_open"] - 1
    df["pos_in_day"] = (df["close"] - df["day_low"]) / span
    bar_span = (df["high"] - df["low"]).replace(0, np.nan)
    body_top = df[["open", "close"]].max(axis=1)
    body_bot = df[["open", "close"]].min(axis=1)
    df["body_ratio"] = (df["close"] - df["open"]) / bar_span
    df["upper_shadow"] = (df["high"] - body_top) / bar_span
    df["lower_shadow"] = (body_bot - df["low"]) / bar_span
    df["vwap_dev"] = df["close"] / df["vwap"] - 1
    vol_ma = g["vol"].transform(lambda s: s.rolling(20).mean())
    df["vol_ratio"] = df["vol"] / vol_ma.replace(0, np.nan)
    df["amount_chg"] = g["amount"].pct_change(1)
    vol_chg = g["vol"].pct_change(1)
    df["pv_dir"] = np.sign(df["ret_1"]) * np.sign(vol_chg)
    df["atr_pct"] = df["atr"] / df["close"]
    df["hv_20"] = g["ret_1"].transform(lambda s: s.rolling(20).std())
    df["rsi_6"] = g["close"].transform(lambda s: _rsi(s, 6))
    df["rsi_14"] = g["close"].transform(lambda s: _rsi(s, 14))
    df["ema5_dev"] = g["close"].transform(lambda s: _ema_dev(s, 5))
    df["ema10_dev"] = g["close"].transform(lambda s: _ema_dev(s, 10))
    df["ema20_dev"] = g["close"].transform(lambda s: _ema_dev(s, 20))
    mid = g["close"].transform(lambda s: s.rolling(20).mean())
    std = g["close"].transform(lambda s: s.rolling(20).std())
    df["boll_pos"] = (df["close"] - mid) / (2 * std.replace(0, np.nan))
    return df


def add_labels(df: pd.DataFrame, horizon: int = HORIZON, mult: float = BARRIER_MULT) -> pd.DataFrame:
    """以 t 的收盘价为参考，看未来 horizon 根里先碰到哪一侧障碍。"""
    if df.empty:
        return df
    parts = []
    for _, g in df.groupby("ts_code", sort=False):
        g = g.sort_values("bar_time").copy()
        high = g["high"].to_numpy(dtype=float)
        low = g["low"].to_numpy(dtype=float)
        close = g["close"].to_numpy(dtype=float)
        atr = g["atr"].to_numpy(dtype=float)
        times = g["bar_time"].to_list()
        n = len(g)
        labels = [None] * n
        end_times = [pd.NaT] * n
        upside = np.full(n, np.nan)
        downside = np.full(n, np.nan)
        for i in range(n):
            if not np.isfinite(atr[i]) or atr[i] <= 0 or not np.isfinite(close[i]) or close[i] <= 0:
                continue
            if i + horizon >= n:
                continue
            barrier = mult * atr[i]
            up = close[i] + barrier
            down = close[i] - barrier
            upside[i] = barrier / close[i] * 100.0
            downside[i] = barrier / close[i] * 100.0
            lab = "NEUTRAL"
            for j in range(i + 1, i + horizon + 1):
                hit_up = high[j] >= up
                hit_dn = low[j] <= down
                if hit_up and hit_dn:
                    lab = "AMBIGUOUS"
                    break
                if hit_up:
                    lab = "UP_FIRST"
                    break
                if hit_dn:
                    lab = "DOWN_FIRST"
                    break
            labels[i] = lab
            end_times[i] = times[i + horizon]
        g["label"] = labels
        g["label_end_time"] = end_times
        g["expected_upside_pct"] = upside
        g["expected_downside_pct"] = downside
        parts.append(g)
    return pd.concat(parts, ignore_index=True) if parts else df


def assign_splits(df: pd.DataFrame) -> pd.DataFrame:
    """按时间 70/15/15 切开，并丢掉标签窗口跨过切分边界的样本。"""
    out = df.copy()
    times = sorted(pd.Series(out["bar_time"].unique()).dropna())
    n = len(times)
    out["time_split"] = "unlabeled"
    out.attrs["split_bounds"] = None
    if n < 10:
        return out
    i1 = min(max(1, int(n * 0.70)), n - 2)
    i2 = min(max(i1 + 1, int(n * 0.85)), n - 1)
    t_val = times[i1]
    t_test = times[i2]
    bt = out["bar_time"]
    split = np.where(bt < t_val, "train", np.where(bt < t_test, "validation", "test"))
    out["time_split"] = split
    end = out["label_end_time"]
    train_leak = (out["time_split"] == "train") & end.notna() & (end >= t_val)
    val_leak = (out["time_split"] == "validation") & end.notna() & (end >= t_test)
    out.loc[train_leak | val_leak, "time_split"] = "embargo"
    out.attrs["split_bounds"] = {
        "train_start": times[0],
        "train_end": times[i1 - 1],
        "validation_start": t_val,
        "validation_end": times[i2 - 1],
        "test_start": t_test,
        "test_end": times[-1],
    }
    return out


def leak_probe(bars: pd.DataFrame) -> None:
    """改写未来一根 K 线后，当前特征必须不变，标签允许变化。"""
    if bars.empty:
        raise RuntimeError("没有 5 分钟样本，无法做泄漏检查")
    counts = bars.groupby("ts_code").size()
    code = counts.sort_values(ascending=False).index[0]
    base = bars[bars["ts_code"] == code].sort_values("bar_time").reset_index(drop=True)
    if len(base) < ATR_WINDOW + HORIZON + 5:
        raise RuntimeError("单只股票 5 分钟样本太短，无法做泄漏检查")
    before = add_labels(add_price_features(base))
    i = None
    for k in range(ATR_WINDOW + 1, len(before) - HORIZON - 1):
        if np.isfinite(before["ret_1"].iloc[k]) and np.isfinite(before["atr"].iloc[k]):
            i = k
            break
    if i is None:
        raise RuntimeError("泄漏检查找不到可用样本")
    mutated = base.copy()
    spike = float(mutated["close"].iloc[i]) * 3.0
    mutated.loc[i + 1, "high"] = spike
    mutated.loc[i + 1, "close"] = spike
    after = add_labels(add_price_features(mutated))
    for col in ("ret_1", "ret_5", "day_high", "vwap_dev", "rsi_14"):
        b = before[col].iloc[i]
        a = after[col].iloc[i]
        if pd.isna(b) and pd.isna(a):
            continue
        if not np.isfinite(b) or not np.isfinite(a) or abs(float(a) - float(b)) > 1e-8:
            raise RuntimeError(f"特征泄漏：改未来 K 线后 {col} 变了")
    if after["label"].iloc[i] not in ("UP_FIRST", "AMBIGUOUS"):
        raise RuntimeError("标签没有使用未来 K 线，三重障碍定义失效")


def _limit_ratio(code: str, name: str) -> float:
    name = (name or "").upper()
    if "ST" in name:
        return 0.05
    if code.startswith(("300", "301", "688", "689")):
        return 0.20
    if code.startswith(("8", "4", "92")):
        return 0.30
    return 0.10


def _round_trip_cost_frac(price: float) -> float:
    if not np.isfinite(price) or price <= 0:
        return 1.0
    buy_px = price * (1 + SLIPPAGE)
    sell_px = price * (1 - SLIPPAGE)
    buy_amt = buy_px * SHARES
    sell_amt = sell_px * SHARES
    fee = max(buy_amt * COMMISSION, 5.0) + max(sell_amt * COMMISSION, 5.0) + sell_amt * STAMP
    slip_loss = (buy_px - price) * SHARES + (price - sell_px) * SHARES
    return float((fee + slip_loss) / buy_amt)


def _decide(row, pos, th_up, th_down, ratio) -> str:
    if pos is not None:
        sellable = pos["buy_date"] < row.day
        p_down = row.p_down if np.isfinite(getattr(row, "p_down", np.nan)) else np.nan
        if sellable and np.isfinite(p_down) and p_down >= th_down:
            return "SELL_CANDIDATE"
        return "HOLD"
    p_up = getattr(row, "p_up", np.nan)
    p_down = getattr(row, "p_down", np.nan)
    upside = getattr(row, "expected_upside_pct", np.nan)
    if not (np.isfinite(p_up) and np.isfinite(p_down) and np.isfinite(upside)):
        return "WAIT"
    if p_up >= th_up and (upside / 100.0) > _round_trip_cost_frac(float(row.close)) and p_up >= p_down * ratio:
        return "BUY_CANDIDATE"
    return "WAIT"


def _decide_rsi(row, pos) -> str:
    rsi = getattr(row, "rsi_14", np.nan)
    if pos is not None:
        if pos["buy_date"] < row.day and np.isfinite(rsi) and rsi > 65:
            return "SELL_CANDIDATE"
        return "HOLD"
    if np.isfinite(rsi) and rsi < 35:
        return "BUY_CANDIDATE"
    return "WAIT"


def _decide_vwap(row, pos) -> str:
    if pos is not None:
        if pos["buy_date"] < row.day and np.isfinite(row.vwap) and row.close > row.vwap:
            return "SELL_CANDIDATE"
        return "HOLD"
    if np.isfinite(getattr(row, "vwap", np.nan)) and row.close < row.vwap * (1 - 0.002):
        return "BUY_CANDIDATE"
    return "WAIT"


def _try_fill(side: str, nxt, pre_close, ratio: float):
    if nxt is None:
        return None, "no_next_bar"
    px_raw = nxt["open"]
    vol_raw = nxt["vol"]
    px = float(px_raw) if pd.notna(px_raw) else np.nan
    vol = float(vol_raw) if pd.notna(vol_raw) else np.nan
    if not np.isfinite(px) or px <= 0 or (np.isfinite(vol) and vol <= 0):
        return None, "halt_or_empty"
    if not np.isfinite(pre_close) or pre_close <= 0:
        return None, "no_pre_close"
    if side == "BUY":
        limit_up = round(pre_close * (1 + ratio) + 1e-8, 2)
        if px >= limit_up - 1e-6:
            return None, "limit_up"
        fill = px * (1 + SLIPPAGE)
        amt = fill * SHARES
        fee = max(amt * COMMISSION, 5.0)
        return {"px": fill, "amt": amt, "fee": fee, "day": nxt.day, "time": nxt.bar_time}, "filled"
    limit_dn = round(pre_close * (1 - ratio) + 1e-8, 2)
    if px <= limit_dn + 1e-6:
        return None, "limit_down"
    fill = px * (1 - SLIPPAGE)
    amt = fill * SHARES
    fee = max(amt * COMMISSION, 5.0) + amt * STAMP
    return {"px": fill, "amt": amt, "fee": fee, "day": nxt.day, "time": nxt.bar_time}, "filled"


def simulate(df: pd.DataFrame, target_split: str, mode: str, names: dict,
             th_up: float, th_down: float, ratio: float, test_start=None) -> dict:
    """下一根开盘价成交。当天买入的仓位当天不能卖。成交失败不记成成功。"""
    trades = []
    signals = []
    unfilled = 0
    if df.empty:
        return _trade_stats(trades, unfilled, signals)
    work = df.sort_values(["ts_code", "bar_time"])
    for code, g in work.groupby("ts_code", sort=False):
        g = g.reset_index(drop=True)
        day_last = g.groupby("day")["close"].last()
        days = list(day_last.index)
        prev_map = {days[i]: float(day_last.iloc[i - 1]) if i else np.nan for i in range(len(days))}
        pos = None
        ratio_lim = _limit_ratio(code, names.get(code, ""))
        for i, row in g.iterrows():
            if row.time_split != target_split:
                continue
            if mode == "model":
                signal = _decide(row, pos, th_up, th_down, ratio)
            elif mode == "rsi":
                signal = _decide_rsi(row, pos)
            else:
                signal = _decide_vwap(row, pos)
            signals.append({
                "stock_code": code,
                "prediction_time": row.bar_time,
                "signal": signal,
                "p_up": getattr(row, "p_up", None),
                "p_down": getattr(row, "p_down", None),
                "p_neutral": getattr(row, "p_neutral", None),
                "expected_upside_pct": getattr(row, "expected_upside_pct", None),
                "expected_downside_pct": getattr(row, "expected_downside_pct", None),
                "dataset_type": target_split,
            })
            nxt = g.iloc[i + 1] if i + 1 < len(g) else None
            if nxt is not None and target_split == "validation" and test_start is not None and nxt.bar_time >= test_start:
                nxt = None
            if signal == "BUY_CANDIDATE" and pos is None:
                pre = prev_map.get(nxt.day, np.nan) if nxt is not None else np.nan
                fill, reason = _try_fill("BUY", nxt, pre, ratio_lim)
                if fill is None:
                    unfilled += 1
                else:
                    pos = {"buy_date": fill["day"], "buy_time": fill["time"], "buy_amt": fill["amt"], "buy_fee": fill["fee"],
                           "year": int(str(fill["day"])[:4]), "regime": getattr(row, "regime_prev", None)}
            elif signal == "SELL_CANDIDATE" and pos is not None:
                pre = prev_map.get(nxt.day, np.nan) if nxt is not None else np.nan
                fill, reason = _try_fill("SELL", nxt, pre, ratio_lim)
                if fill is None:
                    unfilled += 1
                else:
                    net = fill["amt"] - fill["fee"] - pos["buy_amt"] - pos["buy_fee"]
                    base = pos["buy_amt"] + pos["buy_fee"]
                    trades.append({
                        "stock_code": code,
                        "ret": net / base if base else 0.0,
                        "year": pos["year"],
                        "regime": pos["regime"] or "未知",
                    })
                    pos = None
    return _trade_stats(trades, unfilled, signals)


def _trade_stats(trades, unfilled, signals) -> dict:
    if not trades:
        return {
            "trade_count": 0, "unfilled": unfilled, "win_rate": None, "payoff": None,
            "expectancy": None, "net_return": None, "max_drawdown": None,
            "by_year": {}, "by_regime": {}, "signals": signals,
        }
    rets = np.array([t["ret"] for t in trades], dtype=float)
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for r in rets:
        equity *= 1 + r
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak)
    wins = rets[rets > 0]
    losses = rets[rets <= 0]
    payoff = None
    if len(wins) and len(losses) and abs(losses.mean()) > 0:
        payoff = float(wins.mean() / abs(losses.mean()))
    def _bucket(key):
        out = {}
        for t in trades:
            k = str(t[key])
            out.setdefault(k, []).append(t["ret"])
        return {k: {"n": len(v), "expectancy": float(np.mean(v))} for k, v in out.items()}
    return {
        "trade_count": int(len(rets)),
        "unfilled": int(unfilled),
        "win_rate": float((rets > 0).mean()),
        "payoff": payoff,
        "expectancy": float(rets.mean()),
        "net_return": float(equity - 1),
        "max_drawdown": float(max_dd),
        "by_year": _bucket("year"),
        "by_regime": _bucket("regime"),
        "signals": signals,
    }


def _nll(y, proba) -> float:
    p = np.clip(proba[np.arange(len(y)), y], 1e-8, 1)
    return float(-np.mean(np.log(p)))


def _apply_temperature(proba: np.ndarray, temperature: float) -> np.ndarray:
    logp = np.log(np.clip(proba, 1e-8, 1))
    z = logp / temperature
    z = z - z.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=1, keepdims=True)


def _class_metrics(y: np.ndarray, proba: np.ndarray) -> dict:
    pred = proba.argmax(axis=1)
    per = {}
    cm = []
    for k, name in enumerate(CLASS_NAMES):
        tp = int(((pred == k) & (y == k)).sum())
        fp = int(((pred == k) & (y != k)).sum())
        fn = int(((pred != k) & (y == k)).sum())
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        per[name] = {"precision": prec, "recall": rec, "f1": f1, "support": int((y == k).sum())}
        cm.append([int(((y == k) & (pred == j)).sum()) for j in range(3)])
    onehot = np.eye(3)[y]
    brier = float(np.mean(np.sum((proba - onehot) ** 2, axis=1)))
    conf = proba.max(axis=1)
    correct = (pred == y).astype(float)
    bins = []
    for lo, hi in ((0.0, 0.4), (0.4, 0.5), (0.5, 0.6), (0.6, 0.75), (0.75, 1.01)):
        m = (conf >= lo) & (conf < hi)
        if m.sum() == 0:
            continue
        bins.append({"lo": lo, "hi": hi, "n": int(m.sum()), "mean_p": float(conf[m].mean()), "freq": float(correct[m].mean())})
    return {"per_class": per, "confusion_matrix": cm, "brier": brier, "calibration": bins, "accuracy": float(correct.mean()), "n": int(len(y))}


def _xy(df: pd.DataFrame):
    m = df["label"].isin(CLASS_NAMES)
    sub = df.loc[m]
    x = sub[FEATURE_COLUMNS].to_numpy(dtype=float)
    y = sub["label"].map({n: i for i, n in enumerate(CLASS_NAMES)}).to_numpy(dtype=int)
    return sub, x, y


def _label_share(df: pd.DataFrame) -> dict:
    labeled = df[df["label"].notna()]
    n = int(len(labeled))
    if n == 0:
        return {"n": 0}
    vc = labeled["label"].value_counts(dropna=False).to_dict()
    out = {k: int(v) for k, v in vc.items()}
    out["n"] = n
    out["ambiguous_ratio"] = float(vc.get("AMBIGUOUS", 0) / n)
    return out


class IntradayTurningService:
    def ensure_tables(self) -> None:
        ddls = [
            """
            CREATE TABLE IF NOT EXISTS intraday_prediction (
                id BIGSERIAL PRIMARY KEY,
                experiment_id VARCHAR(64) NOT NULL,
                stock_code VARCHAR(16) NOT NULL,
                prediction_time TIMESTAMPTZ NOT NULL,
                p_up_first DOUBLE PRECISION,
                p_down_first DOUBLE PRECISION,
                p_neutral DOUBLE PRECISION,
                expected_upside_pct DOUBLE PRECISION,
                expected_downside_pct DOUBLE PRECISION,
                signal VARCHAR(32),
                model_version VARCHAR(80) NOT NULL,
                feature_version VARCHAR(40) NOT NULL,
                data_snapshot_id VARCHAR(80),
                dataset_type VARCHAR(20),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS intraday_model_run (
                id BIGSERIAL PRIMARY KEY,
                experiment_id VARCHAR(64) NOT NULL,
                model_version VARCHAR(80) NOT NULL,
                train_start TIMESTAMPTZ,
                train_end TIMESTAMPTZ,
                validation_start TIMESTAMPTZ,
                validation_end TIMESTAMPTZ,
                test_start TIMESTAMPTZ,
                test_end TIMESTAMPTZ,
                parameters JSONB,
                metrics JSONB,
                code_version VARCHAR(40),
                status VARCHAR(40) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS intraday_label (
                id BIGSERIAL PRIMARY KEY,
                experiment_id VARCHAR(64) NOT NULL,
                stock_code VARCHAR(16) NOT NULL,
                bar_time TIMESTAMPTZ NOT NULL,
                label VARCHAR(20) NOT NULL,
                label_version VARCHAR(40) NOT NULL,
                horizon INT NOT NULL,
                barrier_atr_mult DOUBLE PRECISION NOT NULL,
                atr_window INT NOT NULL,
                dataset_type VARCHAR(20),
                generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_intraday_prediction_model ON intraday_prediction (model_version, stock_code, prediction_time)",
            "CREATE INDEX IF NOT EXISTS idx_intraday_model_run_exp ON intraday_model_run (experiment_id, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_intraday_label_exp ON intraday_label (experiment_id, label_version, stock_code, bar_time)",
        ]
        eng = _engine()
        with eng.begin() as conn:
            for ddl in ddls:
                conn.execute(text(ddl))

    def coverage(self) -> dict:
        self.ensure_tables()
        eng = _engine()
        with eng.connect() as conn:
            total = conn.execute(text(
                "SELECT COUNT(DISTINCT ts_code), COUNT(*) FROM stock_intraday_mins"
            )).one()
            rows = conn.execute(text(
                """
                SELECT m.ts_code, b.name,
                       COUNT(*) AS bars,
                       COUNT(DISTINCT m.trade_date) AS days,
                       MIN(m.trade_date) AS d0,
                       MAX(m.trade_date) AS d1
                FROM stock_intraday_mins m
                LEFT JOIN ref_stock_basic b ON b.ts_code = m.ts_code
                GROUP BY m.ts_code, b.name
                HAVING COUNT(DISTINCT m.trade_date) >= :min_days AND COUNT(*) >= :min_bars
                ORDER BY days DESC, bars DESC
                LIMIT 80
                """
            ), {"min_days": MIN_DAYS, "min_bars": MIN_BARS}).fetchall()
            codes = [r.ts_code for r in rows]
            dates = []
            if codes:
                safe = _safe_codes(codes)
                if safe:
                    in_list = ",".join(f"'{c}'" for c in safe)
                    dates = conn.execute(text(
                        f"""
                        SELECT trade_date, COUNT(DISTINCT ts_code) AS stocks, COUNT(*) AS bars
                        FROM stock_intraday_mins
                        WHERE ts_code IN ({in_list})
                        GROUP BY trade_date
                        ORDER BY trade_date
                        """
                    )).fetchall()
        recommended = []
        for r in rows:
            tier = "优先" if r.days >= 4 and r.bars >= 800 else "可用"
            recommended.append({
                "ts_code": r.ts_code,
                "name": r.name or "",
                "bars": int(r.bars),
                "days": int(r.days),
                "start": str(r.d0),
                "end": str(r.d1),
                "tier": tier,
            })
        return {
            "ok": True,
            "summary": {
                "stocks_with_minutes": int(total[0] or 0),
                "minute_bars": int(total[1] or 0),
                "recommended": len(recommended),
                "rule": f"至少 {MIN_DAYS} 个交易日且至少 {MIN_BARS} 根 1 分钟 K。其余股票天数太少，不进这版训练集。",
                "price_note": "使用库内 stock_intraday_mins 原始价，未做复权，训练和回测都不临时抓行情。",
            },
            "recommended": recommended,
            "dates": [{"trade_date": str(d.trade_date), "stocks": int(d.stocks), "bars": int(d.bars)} for d in dates],
        }

    def _load_minutes(self, codes: list[str]) -> pd.DataFrame:
        safe = _safe_codes(codes)
        if not safe:
            return pd.DataFrame()
        in_list = ",".join(f"'{c}'" for c in safe)
        eng = _engine()
        return pd.read_sql(text(
            f"""
            SELECT ts_code, bar_time, trade_date, open, high, low, close, vol, amount
            FROM stock_intraday_mins
            WHERE ts_code IN ({in_list})
            ORDER BY ts_code, bar_time
            """
        ), eng)

    def _context(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out["index_ret_prev"] = np.nan
        out["sector_ret_prev"] = np.nan
        out["excess_vs_sector"] = np.nan
        out["regime_prev"] = None
        out["regime_num"] = np.nan
        if out.empty:
            return out
        d0 = min(out["day"])
        d1 = max(out["day"])
        codes = _safe_codes(out["ts_code"].unique())
        eng = _engine()
        try:
            ix = pd.read_sql(text(
                """
                SELECT trade_date AS day, pct_chg
                FROM index_daily
                WHERE ts_code = '000300.SH' AND trade_date <= :d1
                ORDER BY trade_date
                """
            ), eng, params={"d1": d1})
            if not ix.empty:
                ix["day"] = pd.to_datetime(ix["day"]).dt.date
                ix["index_ret_prev"] = ix["pct_chg"].shift(1)
                med = ix["index_ret_prev"].abs().median()
                if pd.notna(med) and med > 0.5:
                    ix["index_ret_prev"] = ix["index_ret_prev"] / 100.0
                out["index_ret_prev"] = out["day"].map(dict(zip(ix["day"], ix["index_ret_prev"])))
        except Exception:
            pass
        try:
            rg = pd.read_sql(text(
                """
                SELECT DISTINCT ON (trade_date) trade_date AS day, regime_label
                FROM market_regime
                WHERE trade_date <= :d1
                ORDER BY trade_date, created_at DESC
                """
            ), eng, params={"d1": d1})
            if not rg.empty:
                rg["day"] = pd.to_datetime(rg["day"]).dt.date
                rg["regime_prev"] = rg["regime_label"].shift(1)
                mp = dict(zip(rg["day"], rg["regime_prev"]))
                out["regime_prev"] = out["day"].map(mp)
                out["regime_num"] = out["regime_prev"].map(REGIME_NUM)
        except Exception:
            pass
        if codes:
            try:
                in_list = ",".join(f"'{c}'" for c in codes)
                mem = pd.read_sql(text(
                    f"""
                    SELECT m.con_code AS ts_code, m.trade_date AS day, MIN(m.ts_code) AS sector_code
                    FROM sector_dc_member m
                    JOIN sector_dc_index_daily s
                      ON s.ts_code = m.ts_code AND s.industry_level = '东财二级'
                    WHERE m.con_code IN ({in_list}) AND m.trade_date <= :d1
                    GROUP BY m.con_code, m.trade_date
                    """
                ), eng, params={"d1": d1})
                sec = pd.read_sql(text(
                    """
                    SELECT ts_code AS sector_code, trade_date AS day, pct_change
                    FROM sector_dc_index_daily
                    WHERE industry_level = '东财二级' AND trade_date <= :d1
                    """
                ), eng, params={"d1": d1})
                if not mem.empty and not sec.empty:
                    sec["day"] = pd.to_datetime(sec["day"]).dt.date
                    sec = sec.sort_values(["sector_code", "day"])
                    sec["sector_ret_prev"] = sec.groupby("sector_code")["pct_change"].shift(1)
                    med = sec["sector_ret_prev"].abs().median()
                    if pd.notna(med) and med > 0.5:
                        sec["sector_ret_prev"] = sec["sector_ret_prev"] / 100.0
                    mem["day"] = pd.to_datetime(mem["day"])
                    keys = out[["ts_code", "day"]].drop_duplicates().copy()
                    keys["day_ts"] = pd.to_datetime(keys["day"])
                    keys = keys.sort_values("day_ts")
                    mem = mem.sort_values("day")
                    linked = pd.merge_asof(
                        keys, mem.rename(columns={"day": "day_ts"}),
                        on="day_ts", by="ts_code", direction="backward",
                    )
                    linked["day"] = pd.to_datetime(linked["day"]).dt.date
                    sec_map = sec.set_index(["sector_code", "day"])["sector_ret_prev"]
                    linked["sector_ret_prev"] = [
                        sec_map.get((r.sector_code, r.day), np.nan) for r in linked.itertuples()
                    ]
                    m = linked.set_index(["ts_code", "day"])["sector_ret_prev"]
                    out["sector_ret_prev"] = [
                        m.get((r.ts_code, r.day), np.nan) for r in out.itertuples()
                    ]
            except Exception:
                pass
        out["excess_vs_sector"] = out["ret_from_open"] - out["sector_ret_prev"]
        return out

    def _names(self, codes: list[str]) -> dict:
        safe = _safe_codes(codes)
        if not safe:
            return {}
        in_list = ",".join(f"'{c}'" for c in safe)
        eng = _engine()
        with eng.connect() as conn:
            rows = conn.execute(text(
                f"SELECT ts_code, name FROM ref_stock_basic WHERE ts_code IN ({in_list})"
            )).fetchall()
        return {r.ts_code: r.name or "" for r in rows}

    def train(self, experiment_id: str = EXPERIMENT_ID, config: dict | None = None) -> dict:
        cfg = dict(config or {})
        horizon = int(cfg.get("horizon") or HORIZON)
        mult = float(cfg.get("barrier_mult") or BARRIER_MULT)
        atr_window = int(cfg.get("atr_window") or ATR_WINDOW)
        if horizon != HORIZON or abs(mult - BARRIER_MULT) > 1e-9 or atr_window != ATR_WINDOW:
            return {"ok": False, "error": "第一版只允许 5 分钟、H=3、障碍 0.5×ATR(14)。这些参数不能拿到测试集上去挑。"}
        self.ensure_tables()
        cov = self.coverage()
        codes = _safe_codes(cfg.get("ts_codes") or [r["ts_code"] for r in cov["recommended"]])
        if not codes:
            return {"ok": False, "status": "NEED_MORE_DATA", "error": "没有足够的分钟线可以训练", "coverage": cov["summary"]}
        raw = self._load_minutes(codes)
        bars = resample_5min(raw)
        if bars.empty:
            return {"ok": False, "status": "NEED_MORE_DATA", "error": "分钟线无法收成 5 分钟 K 线", "coverage": cov["summary"]}
        try:
            leak_probe(bars)
        except Exception as e:
            return {"ok": False, "error": f"未来数据泄漏检查失败：{e}"}
        feat = add_price_features(bars)
        feat = self._context(feat)
        labeled = add_labels(feat, horizon=horizon, mult=mult)
        labeled = assign_splits(labeled)
        bounds = labeled.attrs.get("split_bounds")
        shares = {
            "all": _label_share(labeled),
            "train": _label_share(labeled[labeled["time_split"] == "train"]),
            "validation": _label_share(labeled[labeled["time_split"] == "validation"]),
            "test": _label_share(labeled[labeled["time_split"] == "test"]),
            "embargo": int((labeled["time_split"] == "embargo").sum()),
        }
        train_df, xtr, ytr = _xy(labeled[labeled["time_split"] == "train"])
        val_df, xva, yva = _xy(labeled[labeled["time_split"] == "validation"])
        test_df, xte, yte = _xy(labeled[labeled["time_split"] == "test"])
        if len(ytr) < MIN_TRAIN_ROWS or len(set(ytr.tolist())) < 3:
            return {
                "ok": True, "status": "NEED_MORE_DATA",
                "error": "训练集不足或三类没有齐，不能训练。没有降低门槛。",
                "label_share": _jsonable(shares), "coverage": cov["summary"],
            }
        try:
            import lightgbm as lgb
        except Exception as e:
            return {"ok": False, "error": f"未安装 lightgbm：{e}"}
        dtrain = lgb.Dataset(xtr, label=ytr, feature_name=FEATURE_COLUMNS, free_raw_data=False)
        callbacks = []
        valid_sets = None
        if len(yva) >= 50 and len(set(yva.tolist())) >= 2:
            dval = lgb.Dataset(xva, label=yva, feature_name=FEATURE_COLUMNS, reference=dtrain, free_raw_data=False)
            valid_sets = [dval]
            callbacks = [lgb.early_stopping(30, verbose=False)]
        booster = lgb.train(
            LGB_PARAMS, dtrain, num_boost_round=300,
            valid_sets=valid_sets, callbacks=callbacks or None,
        )
        temperature = 1.0
        val_proba = booster.predict(xva) if len(yva) else np.zeros((0, 3))
        test_proba = booster.predict(xte) if len(yte) else np.zeros((0, 3))
        if len(yva) >= 50:
            best_nll = None
            for t in (0.7, 0.85, 1.0, 1.15, 1.3):
                nll = _nll(yva, _apply_temperature(val_proba, t))
                if best_nll is None or nll < best_nll:
                    best_nll = nll
                    temperature = t
            val_proba = _apply_temperature(val_proba, temperature)
        if len(yte):
            test_proba = _apply_temperature(test_proba, temperature)
        names = self._names(codes)
        test_start = bounds["test_start"] if bounds else None
        frame = labeled.copy()
        frame["p_up"] = np.nan
        frame["p_down"] = np.nan
        frame["p_neutral"] = np.nan
        for part, proba in ((val_df, val_proba), (test_df, test_proba)):
            if part.empty:
                continue
            frame.loc[part.index, "p_up"] = proba[:, 0]
            frame.loc[part.index, "p_down"] = proba[:, 1]
            frame.loc[part.index, "p_neutral"] = proba[:, 2]
        th_up, th_down, th_ratio = 0.5, 0.5, 1.1
        threshold_source = "default_validation_too_small"
        val_trade = None
        if len(yva) >= 50:
            best = None
            for up in (0.40, 0.45, 0.50, 0.55, 0.60):
                for down in (0.40, 0.45, 0.50, 0.55):
                    for ratio in (1.0, 1.2):
                        stats = simulate(frame, "validation", "model", names, up, down, ratio, test_start)
                        score = stats["expectancy"]
                        ntr = stats["trade_count"]
                        if score is None:
                            continue
                        rank = (ntr >= 15, score, ntr)
                        if best is None or rank > best[0]:
                            best = (rank, up, down, ratio, stats)
            if best is not None:
                th_up, th_down, th_ratio = best[1], best[2], best[3]
                val_trade = best[4]
                threshold_source = "validation_expectancy"
        test_trade = simulate(frame, "test", "model", names, th_up, th_down, th_ratio, None)
        rsi_trade = simulate(frame, "test", "rsi", names, th_up, th_down, th_ratio, None)
        vwap_trade = simulate(frame, "test", "vwap", names, th_up, th_down, th_ratio, None)
        class_test = _class_metrics(yte, test_proba) if len(yte) else None
        class_val = _class_metrics(yva, val_proba) if len(yva) else None
        by_stock = []
        if len(yte):
            tmp = test_df.copy()
            tmp["pred"] = test_proba.argmax(axis=1)
            tmp["y"] = yte
            for code, g in tmp.groupby("ts_code"):
                if len(g) < 20:
                    continue
                by_stock.append({
                    "ts_code": code,
                    "name": names.get(code, ""),
                    "n": int(len(g)),
                    "accuracy": float((g["pred"].to_numpy() == g["y"].to_numpy()).mean()),
                })
        by_year = {}
        if len(yte):
            years = pd.to_datetime(test_df["bar_time"]).dt.year.to_numpy()
            pred = test_proba.argmax(axis=1)
            for year in sorted(set(years.tolist())):
                m = years == year
                by_year[str(year)] = {"n": int(m.sum()), "accuracy": float((pred[m] == yte[m]).mean())}
        test_days = int(test_df["day"].nunique()) if len(test_df) else 0
        status = "CANDIDATE"
        reason = "测试净收益为正，且高于 RSI、VWAP 两条固定基线。这仍只是候选，不是生产规则。"
        exp = test_trade.get("expectancy")
        if len(yte) < MIN_TEST_ROWS or test_days < MIN_TEST_DAYS:
            status = "NEED_MORE_DATA"
            reason = (
                f"测试集只有 {test_days} 个交易日、{len(yte)} 条样本。"
                "交易日不够，结果只能展示，不能当成策略通过。"
            )
            if exp is None or exp <= 0 or (test_trade.get("net_return") or 0) <= 0:
                reason += "扣费后的测试净收益也不是正的。"
        elif exp is None or exp <= 0 or (test_trade.get("net_return") or 0) <= 0:
            status = "NOT_EFFECTIVE"
            reason = "分类可以计算，但扣费后的测试净收益不为正，不能判定策略有效。"
        else:
            base_ok = True
            for base in (rsi_trade, vwap_trade):
                b = base.get("expectancy")
                if b is not None and exp <= b:
                    base_ok = False
            if not base_ok:
                status = "NOT_EFFECTIVE"
                reason = "测试净收益没有同时高于 RSI 和 VWAP 基线，不能判定策略有效。"
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        model_version = f"lgbm-{_code_version()}-{stamp}"
        dmin = str(labeled["bar_time"].min())
        dmax = str(labeled["bar_time"].max())
        snapshot = "mins-" + hashlib.sha1(f"{','.join(codes)}|{dmin}|{dmax}|{len(raw)}".encode()).hexdigest()[:16]
        params = {
            "freq": "5min", "horizon_bars": horizon, "horizon_minutes": horizon * 5,
            "barrier_atr_mult": mult, "atr_window": atr_window,
            "feature_version": FEATURE_VERSION, "label_version": LABEL_VERSION,
            "lgb_params": LGB_PARAMS, "best_iteration": int(booster.best_iteration or booster.current_iteration()),
            "temperature": temperature, "threshold_source": threshold_source,
            "p_up_threshold": th_up, "p_down_threshold": th_down, "prob_ratio": th_ratio,
            "commission": COMMISSION, "stamp": STAMP, "slippage": SLIPPAGE, "shares": SHARES,
            "min_commission_cny": 5, "fill": "next_bar_open", "t_plus_1": True,
            "expected_move": "atr_barrier_width_not_regression",
            "adjustment": "raw_stock_intraday_mins_no_adjust",
            "codes": codes,
        }
        metrics = {
            "reason": reason,
            "label_share": shares,
            "classification_validation": class_val,
            "classification_test": class_test,
            "trading_validation": {k: v for k, v in (val_trade or {}).items() if k != "signals"},
            "trading_test": {k: v for k, v in test_trade.items() if k != "signals"},
            "baseline_rsi": {k: v for k, v in rsi_trade.items() if k != "signals"},
            "baseline_vwap": {k: v for k, v in vwap_trade.items() if k != "signals"},
            "by_stock": by_stock,
            "by_year": by_year,
            "coverage": cov["summary"],
            "test_days": test_days,
            "train_rows": int(len(ytr)),
            "validation_rows": int(len(yva)),
            "test_rows": int(len(yte)),
        }
        _MODEL_DIR.mkdir(parents=True, exist_ok=True)
        model_path = _MODEL_DIR / f"{model_version}.txt"
        booster.save_model(str(model_path))
        meta = {
            "model_version": model_version, "feature_version": FEATURE_VERSION,
            "temperature": temperature, "th_up": th_up, "th_down": th_down, "ratio": th_ratio,
            "feature_columns": FEATURE_COLUMNS, "snapshot": snapshot, "status": status,
        }
        model_path.with_suffix(".json").write_text(json.dumps(_jsonable(meta), ensure_ascii=False), encoding="utf-8")
        self._persist(experiment_id, model_version, bounds, params, metrics, status, snapshot, labeled, test_trade, val_trade)
        return {
            "ok": True, "status": status, "reason": reason,
            "model_version": model_version, "feature_version": FEATURE_VERSION,
            "data_snapshot_id": snapshot, "parameters": _jsonable(params),
            "metrics": _jsonable(metrics), "coverage": cov["summary"],
        }

    def _persist(self, experiment_id, model_version, bounds, params, metrics, status, snapshot, labeled, test_trade, val_trade):
        def _ts(key):
            if not bounds or bounds.get(key) is None:
                return None
            v = bounds[key]
            return pd.Timestamp(v).to_pydatetime()
        eng = _engine()
        with eng.begin() as conn:
            conn.execute(text(
                """
                INSERT INTO intraday_model_run (
                    experiment_id, model_version, train_start, train_end,
                    validation_start, validation_end, test_start, test_end,
                    parameters, metrics, code_version, status
                ) VALUES (
                    :experiment_id, :model_version, :train_start, :train_end,
                    :validation_start, :validation_end, :test_start, :test_end,
                    CAST(:parameters AS jsonb), CAST(:metrics AS jsonb), :code_version, :status
                )
                """
            ), {
                "experiment_id": experiment_id, "model_version": model_version,
                "train_start": _ts("train_start"), "train_end": _ts("train_end"),
                "validation_start": _ts("validation_start"), "validation_end": _ts("validation_end"),
                "test_start": _ts("test_start"), "test_end": _ts("test_end"),
                "parameters": json.dumps(_jsonable(params), ensure_ascii=False),
                "metrics": json.dumps(_jsonable(metrics), ensure_ascii=False),
                "code_version": _code_version(), "status": status,
            })
            labels = labeled[labeled["label"].notna()]
            label_rows = []
            for r in labels.itertuples(index=False):
                label_rows.append({
                    "experiment_id": experiment_id, "stock_code": r.ts_code,
                    "bar_time": pd.Timestamp(r.bar_time).to_pydatetime(), "label": r.label,
                    "label_version": LABEL_VERSION, "horizon": HORIZON,
                    "barrier_atr_mult": BARRIER_MULT, "atr_window": ATR_WINDOW,
                    "dataset_type": r.time_split,
                })
            for i in range(0, len(label_rows), 2000):
                conn.execute(text(
                    """
                    INSERT INTO intraday_label (
                        experiment_id, stock_code, bar_time, label, label_version,
                        horizon, barrier_atr_mult, atr_window, dataset_type
                    ) VALUES (
                        :experiment_id, :stock_code, :bar_time, :label, :label_version,
                        :horizon, :barrier_atr_mult, :atr_window, :dataset_type
                    )
                    """
                ), label_rows[i:i + 2000])
            pred_rows = []
            for pack in (val_trade, test_trade):
                if not pack:
                    continue
                for s in pack.get("signals") or []:
                    pred_rows.append({
                        "experiment_id": experiment_id,
                        "stock_code": s["stock_code"],
                        "prediction_time": pd.Timestamp(s["prediction_time"]).to_pydatetime(),
                        "p_up_first": None if s["p_up"] is None or not np.isfinite(s["p_up"]) else float(s["p_up"]),
                        "p_down_first": None if s["p_down"] is None or not np.isfinite(s["p_down"]) else float(s["p_down"]),
                        "p_neutral": None if s["p_neutral"] is None or not np.isfinite(s["p_neutral"]) else float(s["p_neutral"]),
                        "expected_upside_pct": None if s["expected_upside_pct"] is None or not np.isfinite(s["expected_upside_pct"]) else float(s["expected_upside_pct"]),
                        "expected_downside_pct": None if s["expected_downside_pct"] is None or not np.isfinite(s["expected_downside_pct"]) else float(s["expected_downside_pct"]),
                        "signal": s["signal"], "model_version": model_version,
                        "feature_version": FEATURE_VERSION, "data_snapshot_id": snapshot,
                        "dataset_type": s["dataset_type"],
                    })
            for i in range(0, len(pred_rows), 2000):
                conn.execute(text(
                    """
                    INSERT INTO intraday_prediction (
                        experiment_id, stock_code, prediction_time, p_up_first, p_down_first, p_neutral,
                        expected_upside_pct, expected_downside_pct, signal, model_version, feature_version,
                        data_snapshot_id, dataset_type
                    ) VALUES (
                        :experiment_id, :stock_code, :prediction_time, :p_up_first, :p_down_first, :p_neutral,
                        :expected_upside_pct, :expected_downside_pct, :signal, :model_version, :feature_version,
                        :data_snapshot_id, :dataset_type
                    )
                    """
                ), pred_rows[i:i + 2000])

    def latest(self, experiment_id: str = EXPERIMENT_ID) -> dict:
        self.ensure_tables()
        eng = _engine()
        with eng.connect() as conn:
            row = conn.execute(text(
                """
                SELECT model_version, status, parameters, metrics, code_version, created_at,
                       train_start, train_end, validation_start, validation_end, test_start, test_end
                FROM intraday_model_run
                WHERE experiment_id = :e
                ORDER BY created_at DESC
                LIMIT 1
                """
            ), {"e": experiment_id}).fetchone()
        if row is None:
            return {"ok": True, "run": None}
        return {"ok": True, "run": {
            "model_version": row.model_version, "status": row.status,
            "parameters": row.parameters, "metrics": row.metrics,
            "code_version": row.code_version, "created_at": str(row.created_at),
            "train_start": str(row.train_start) if row.train_start else None,
            "train_end": str(row.train_end) if row.train_end else None,
            "validation_start": str(row.validation_start) if row.validation_start else None,
            "validation_end": str(row.validation_end) if row.validation_end else None,
            "test_start": str(row.test_start) if row.test_start else None,
            "test_end": str(row.test_end) if row.test_end else None,
        }}

    def evaluate(self, experiment_id: str, model_version: str, dataset_type: str) -> dict:
        self.ensure_tables()
        dataset_type = (dataset_type or "test").lower()
        if dataset_type not in ("test", "validation", "train"):
            return {"ok": False, "error": "dataset_type 只能是 train、validation、test"}
        eng = _engine()
        with eng.connect() as conn:
            row = conn.execute(text(
                """
                SELECT status, metrics, parameters, created_at
                FROM intraday_model_run
                WHERE experiment_id = :e AND model_version = :m
                ORDER BY created_at DESC LIMIT 1
                """
            ), {"e": experiment_id, "m": model_version}).fetchone()
        if row is None:
            return {"ok": False, "error": "没有这版模型"}
        metrics = row.metrics or {}
        if dataset_type == "test":
            payload = metrics.get("classification_test")
            trading = metrics.get("trading_test")
        elif dataset_type == "validation":
            payload = metrics.get("classification_validation")
            trading = metrics.get("trading_validation")
        else:
            payload = {"label_share": (metrics.get("label_share") or {}).get("train"), "train_rows": metrics.get("train_rows")}
            trading = None
        return {
            "ok": True, "locked": dataset_type == "test", "status": row.status,
            "dataset_type": dataset_type, "classification": payload, "trading": trading,
            "note": "测试集只在训练结束时评估一次，这里返回当时写入的结果。" if dataset_type == "test" else "",
        }

    def _load_meta(self, model_version: str) -> dict:
        path = _MODEL_DIR / f"{model_version}.json"
        if not path.exists():
            raise FileNotFoundError(model_version)
        return json.loads(path.read_text(encoding="utf-8"))

    def predict(self, model_version: str, as_of_time: str | None, stock_code: str, holding_buy_date: str | None = None) -> dict:
        code = _safe_codes([stock_code])
        if not code:
            return {"ok": False, "error": "股票代码不正确"}
        code = code[0]
        try:
            meta = self._load_meta(model_version)
        except FileNotFoundError:
            return {"ok": False, "error": "模型文件不在 data/intraday_models，请先训练"}
        import lightgbm as lgb
        booster = lgb.Booster(model_file=str(_MODEL_DIR / f"{model_version}.txt"))
        raw = self._load_minutes([code])
        if as_of_time:
            as_of = pd.Timestamp(as_of_time)
            if as_of.tzinfo is None:
                as_of = as_of.tz_localize("Asia/Shanghai")
            else:
                as_of = as_of.tz_convert("Asia/Shanghai")
            raw = raw.copy()
            raw["bar_time"] = _as_shanghai(raw["bar_time"])
            raw = raw[raw["bar_time"] <= as_of]
        bars = resample_5min(raw)
        if bars.empty:
            return {"ok": False, "error": "该股票在这个时点没有可用的 5 分钟数据"}
        feat = self._context(add_price_features(bars))
        feat = add_labels(feat)
        row = feat.dropna(subset=["atr"]).iloc[-1]
        x = feat.loc[[row.name], FEATURE_COLUMNS].to_numpy(dtype=float)
        proba = _apply_temperature(booster.predict(x), float(meta.get("temperature") or 1))[0]
        pos = None
        if holding_buy_date:
            pos = {"buy_date": pd.Timestamp(holding_buy_date).date()}
        view = row.copy()
        view["p_up"] = float(proba[0])
        view["p_down"] = float(proba[1])
        view["p_neutral"] = float(proba[2])
        signal = _decide(view, pos, float(meta["th_up"]), float(meta["th_down"]), float(meta["ratio"]))
        snapshot = meta.get("snapshot")
        eng = _engine()
        with eng.begin() as conn:
            conn.execute(text(
                """
                INSERT INTO intraday_prediction (
                    experiment_id, stock_code, prediction_time, p_up_first, p_down_first, p_neutral,
                    expected_upside_pct, expected_downside_pct, signal, model_version, feature_version,
                    data_snapshot_id, dataset_type
                ) VALUES (
                    :experiment_id, :stock_code, :prediction_time, :p_up_first, :p_down_first, :p_neutral,
                    :expected_upside_pct, :expected_downside_pct, :signal, :model_version, :feature_version,
                    :data_snapshot_id, 'predict'
                )
                """
            ), {
                "experiment_id": EXPERIMENT_ID, "stock_code": code,
                "prediction_time": pd.Timestamp(row.bar_time).to_pydatetime(),
                "p_up_first": float(proba[0]), "p_down_first": float(proba[1]), "p_neutral": float(proba[2]),
                "expected_upside_pct": None if not np.isfinite(row.expected_upside_pct) else float(row.expected_upside_pct),
                "expected_downside_pct": None if not np.isfinite(row.expected_downside_pct) else float(row.expected_downside_pct),
                "signal": signal, "model_version": model_version, "feature_version": FEATURE_VERSION,
                "data_snapshot_id": snapshot,
            })
        return {
            "ok": True, "stock_code": code, "prediction_time": str(row.bar_time),
            "p_up_first": float(proba[0]), "p_down_first": float(proba[1]), "p_neutral": float(proba[2]),
            "expected_upside_pct": None if not np.isfinite(row.expected_upside_pct) else float(row.expected_upside_pct),
            "expected_downside_pct": None if not np.isfinite(row.expected_downside_pct) else float(row.expected_downside_pct),
            "expected_move_note": "这两个数是 0.5×ATR 障碍的百分比，不是模型回归出的精确涨跌幅。",
            "signal": signal, "model_version": model_version, "feature_version": FEATURE_VERSION,
            "data_snapshot_id": snapshot, "status": meta.get("status"),
        }

    def backtest(self, experiment_id: str, model_version: str, start_date: str, end_date: str, config: dict | None = None) -> dict:
        """测试区间与已锁定测试集重叠时，只返回首次评估，不重新算。"""
        self.ensure_tables()
        eng = _engine()
        with eng.connect() as conn:
            row = conn.execute(text(
                """
                SELECT status, metrics, test_start, test_end
                FROM intraday_model_run
                WHERE experiment_id = :e AND model_version = :m
                ORDER BY created_at DESC LIMIT 1
                """
            ), {"e": experiment_id, "m": model_version}).fetchone()
        if row is None:
            return {"ok": False, "error": "没有这版模型"}
        try:
            start = pd.Timestamp(start_date).date()
            end = pd.Timestamp(end_date).date()
        except Exception:
            return {"ok": False, "error": "日期格式不正确"}
        if row.test_start is not None and row.test_end is not None:
            t0 = pd.Timestamp(row.test_start).date()
            t1 = pd.Timestamp(row.test_end).date()
            if start <= t1 and end >= t0:
                return {
                    "ok": True, "locked": True, "status": row.status,
                    "trading": (row.metrics or {}).get("trading_test"),
                    "classification": (row.metrics or {}).get("classification_test"),
                    "note": "请求区间碰到已锁定的测试集，返回训练时的那一次评估，没有重算。",
                }
        return {
            "ok": True, "locked": False, "status": row.status,
            "note": "第一版不在锁定测试集之外另开一段回测。当前分钟线几乎都落在同一次切分里。",
        }


intraday_turning_service = IntradayTurningService()

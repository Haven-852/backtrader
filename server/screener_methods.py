"""选股方法目录：阶段 × 流程 × 条件，以及按方法所需窗口做当日扫描。

短窗口方法只读近 40 根日线；MA60 类方法读近 90 根。流水线仍走 A/B/C 旧入口。
"""

from __future__ import annotations

from datetime import date
from typing import Any, Callable, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from server.ma60_shrink_strategy import add_features as add_ma60_features
from server.structure_pullback import classify_last as classify_structure_pullback

RegimeId = str
StepId = str

REGIMES: List[Dict[str, str]] = [
    {"id": "up", "name": "上升期", "hint": "收盘 > MA20 > MA60"},
    {"id": "range", "name": "震荡期", "hint": "均线纠缠"},
    {"id": "down", "name": "下降期", "hint": "收盘 < MA20 < MA60"},
]

STEPS: List[Dict[str, str]] = [
    {"id": "select", "name": "入选", "hint": "进池，不是买入"},
    {"id": "watch", "name": "观察", "hint": "等条件，可剔除"},
    {"id": "buy", "name": "买入", "hint": "名单 + 阶段 + 条件"},
    {"id": "hold", "name": "持有/调仓", "hint": "无卖点才走阶梯"},
    {"id": "sell", "name": "卖出", "hint": "按机制离场"},
]

# 短线方法 / MA60 方法：决定补数窗口
LOOKBACK_SHORT = 40
LOOKBACK_MA60 = 90
VOL_HALT_STD = 0.035
MA60_NEAR = 0.02
ELDER_LOOK = 21

METHODS: List[Dict[str, Any]] = [
    {
        "id": "b-ma5",
        "name": "升势回踩 MA5",
        "regimes": ["up"],
        "steps": ["select", "watch", "buy"],
        "buyable": True,
        "legacy_setup": "B",
        "lookback_bars": LOOKBACK_SHORT,
        "need": "stock_daily 约 40 根（MA5、量能）",
        "when": "上升期，价格在均线上方",
        "rule": "升势未坏，回踩 MA5 且未放量砸破。入选后观察 1–3 日，回踩质量合格再买。",
    },
    {
        "id": "c-struct",
        "name": "趋势回踩结构止损",
        "regimes": ["up"],
        "steps": ["select", "buy", "hold", "sell"],
        "buyable": True,
        "legacy_setup": "C",
        "lookback_bars": LOOKBACK_SHORT,
        "need": "stock_daily 约 40 根（MA10/MA20、ATR、结构低点）",
        "when": "上升期，MA10 > MA20",
        "rule": "缩量回踩 MA10，风险 ≤4%。收盘出信号，次日开盘买；高开超过 1% 放弃。最多持 5 日。",
    },
    {
        "id": "ma60-slope",
        "name": "MA60 斜率转正",
        "regimes": ["up"],
        "steps": ["buy"],
        "buyable": True,
        "lookback_bars": LOOKBACK_MA60,
        "need": "stock_daily 约 90 根（MA5/20/60 与斜率）",
        "when": "上升期，或个股刚确认升势",
        "rule": "斜率由负转正当天买。扫描全市场当日转正标的。",
    },
    {
        "id": "ma60-add",
        "name": "贴近 MA60 补仓",
        "regimes": ["up"],
        "steps": ["hold"],
        "buyable": False,
        "lookback_bars": LOOKBACK_MA60,
        "need": "stock_daily 约 90 根（MA60 贴近、量能）",
        "when": "已持仓，机制 B",
        "rule": "贴近 MA60 约 2%、站上 MA5/MA20、不放量。全市场扫描今日符合回踩带的票。",
    },
    {
        "id": "doji-sell",
        "name": "高位十字星放量卖",
        "regimes": ["up"],
        "steps": ["sell"],
        "buyable": False,
        "lookback_bars": LOOKBACK_MA60,
        "need": "stock_daily 约 90 根（60 日量能中位数、十字星）",
        "when": "上升期主升浪后",
        "rule": "相对 60 日量能中位数放量，最高价超 MA5，相对 MA60 已到高位，收十字星。",
    },
    {
        "id": "elder",
        "name": "高龄阶梯调仓",
        "regimes": ["up", "range", "down"],
        "steps": ["hold"],
        "buyable": False,
        "lookback_bars": LOOKBACK_SHORT,
        "need": "stock_daily 约 21 根（相对约 20 日前收盘的涨跌幅）",
        "when": "已持仓，且没有策略卖点",
        "rule": "以约 20 日前收盘作虚拟开仓价：跌 15%/25% 加仓带，涨 25% 起减仓带。",
    },
    {
        "id": "ma60-shrink",
        "name": "MA60 缩量折价",
        "regimes": ["range", "down"],
        "steps": ["select", "watch", "buy", "sell"],
        "buyable": True,
        "lookback_bars": LOOKBACK_MA60,
        "need": "stock_daily 约 90 根（MA60 折价、缩量）",
        "when": "震荡或下降，收盘低于 MA60",
        "rule": "缩量折价。反弹到 MA20 放量则短线卖。禁止跌破途中抄半山腰。",
    },
    {
        "id": "ma20-cash",
        "name": "MA20 放量兑现",
        "regimes": ["range", "down"],
        "steps": ["sell"],
        "buyable": False,
        "lookback_bars": LOOKBACK_MA60,
        "need": "stock_daily 约 90 根（MA20、放量）",
        "when": "机制 A 短线仓",
        "rule": "收盘回到 MA20 附近且放量，作为短线卖出扫描。",
    },
    {
        "id": "base-watch",
        "name": "缩量筑底观察",
        "regimes": ["down"],
        "steps": ["select", "watch"],
        "buyable": False,
        "legacy_setup": "A",
        "lookback_bars": LOOKBACK_SHORT,
        "need": "stock_daily 约 40 根（20 日回撤、缩量、MA5 走平）",
        "when": "下降期，大跌后缩量",
        "rule": "靠近 20 日低点、MA5 走平。只入选观察，不买入。",
    },
    {
        "id": "crash-buy",
        "name": "深折价 15% 放量买",
        "regimes": ["down"],
        "steps": ["buy", "sell"],
        "buyable": True,
        "lookback_bars": LOOKBACK_MA60,
        "need": "stock_daily 约 90 根（MA60 偏离、5 日放量）",
        "when": "下降期深折价",
        "rule": "偏离 MA60 ≥15% 且量远超前 5 日。站上 MA5 放量长阴则卖。",
    },
    {
        "id": "long-yin",
        "name": "站上 MA5 长阴卖",
        "regimes": ["down", "range"],
        "steps": ["sell"],
        "buyable": False,
        "lookback_bars": LOOKBACK_SHORT,
        "need": "stock_daily 约 40 根（MA5、长阴、量能）",
        "when": "深折价仓反弹后",
        "rule": "站上 MA5、量超前几日、收长阴。",
    },
    {
        "id": "vol-halt",
        "name": "高波动禁止开仓",
        "regimes": ["up", "range", "down"],
        "steps": ["watch"],
        "buyable": False,
        "lookback_bars": LOOKBACK_SHORT,
        "need": "stock_daily 约 25 根（近 20 日收益率标准差）",
        "when": "近 20 日波动过高",
        "rule": "近 20 日收益波动过高则禁止新开仓。扫描今日高波动名单。",
    },
]

METHOD_BY_ID: Dict[str, Dict[str, Any]] = {m["id"]: m for m in METHODS}
MA60_METHOD_IDS = {
    "ma60-slope",
    "ma60-add",
    "doji-sell",
    "ma60-shrink",
    "ma20-cash",
    "crash-buy",
    "long-yin",
}


def method_catalog() -> Dict[str, Any]:
    return {
        "ok": True,
        "regimes": REGIMES,
        "steps": STEPS,
        "methods": METHODS,
        "lookback_short": LOOKBACK_SHORT,
        "lookback_ma60": LOOKBACK_MA60,
    }


def resolve_methods(
    methods: Optional[Sequence[str]] = None,
    include_a: bool = False,
    include_b: bool = False,
    include_c: bool = False,
) -> List[str]:
    if methods:
        out: List[str] = []
        seen = set()
        for mid in methods:
            key = str(mid or "").strip()
            if key in METHOD_BY_ID and key not in seen:
                out.append(key)
                seen.add(key)
        return out
    mapped: List[str] = []
    if include_a:
        mapped.append("base-watch")
    if include_b:
        mapped.append("b-ma5")
    if include_c:
        mapped.append("c-struct")
    return mapped


def max_lookback_bars(method_ids: Sequence[str]) -> int:
    if not method_ids:
        return LOOKBACK_SHORT
    return max(int(METHOD_BY_ID[m]["lookback_bars"]) for m in method_ids if m in METHOD_BY_ID)


def needs_ma60(method_ids: Sequence[str]) -> bool:
    return any(m in MA60_METHOD_IDS for m in method_ids)


def _finite(*vals: Any) -> bool:
    for v in vals:
        try:
            f = float(v)
        except (TypeError, ValueError):
            return False
        if f != f:
            return False
    return True


def _asof_last(df: pd.DataFrame, asof: date) -> Optional[pd.Series]:
    if df is None or df.empty:
        return None
    last = df.iloc[-1]
    try:
        last_day = pd.Timestamp(last["trade_date"]).date()
    except Exception:
        return None
    if last_day != asof:
        return None
    return last


def stock_regime_of(close: Any, ma20: Any, ma60: Any) -> str:
    if not _finite(close, ma20, ma60):
        return "unknown"
    c, m20, m60 = float(close), float(ma20), float(ma60)
    if c > m20 > m60:
        return "up"
    if c < m20 < m60:
        return "down"
    return "range"


def _pct_chg(last: pd.Series, prev: Optional[pd.Series]) -> float:
    try:
        c0 = float(last["close"])
        c1 = float(prev["close"]) if prev is not None else c0
        if c1 > 0:
            return (c0 / c1 - 1.0) * 100.0
    except Exception:
        pass
    return 0.0


def _ma60_feat(g: pd.DataFrame, feat60: Optional[pd.DataFrame]) -> pd.DataFrame:
    if feat60 is not None and not feat60.empty:
        return feat60
    return add_ma60_features(g)


def _hit_base(
    method: Dict[str, Any],
    last: pd.Series,
    *,
    prev: Optional[pd.Series] = None,
    reason: str,
    score: float,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    close = float(last["close"]) if _finite(last.get("close")) else None
    ma5 = float(last["ma5"]) if "ma5" in last.index and _finite(last.get("ma5")) else None
    ma20 = float(last["ma20"]) if "ma20" in last.index and _finite(last.get("ma20")) else None
    ma60 = float(last["ma60"]) if "ma60" in last.index and _finite(last.get("ma60")) else None
    below = None
    if ma5 and ma5 > 0 and close:
        below = round((ma5 - close) / ma5 * 100.0, 3)
    out: Dict[str, Any] = {
        "setup_type": method.get("legacy_setup") or method["id"],
        "method_id": method["id"],
        "score": round(float(score), 2),
        "label": method["name"],
        "reason": reason,
        "close": close,
        "ma5": ma5,
        "ma20": ma20,
        "ma60": ma60,
        "below_pct": below,
        "pct_chg": round(_pct_chg(last, prev), 3),
        "buyable": bool(method["buyable"]),
        "steps": list(method["steps"]),
        "regimes": list(method["regimes"]),
        "stock_regime": stock_regime_of(close, ma20, ma60),
    }
    if extra:
        out.update(extra)
    return out


def classify_ma60_slope(g: pd.DataFrame, asof: date, feat60: Optional[pd.DataFrame] = None) -> Optional[Dict[str, Any]]:
    feat = _ma60_feat(g, feat60)
    last = _asof_last(feat, asof)
    if last is None or not bool(last.get("slope_turn_up")):
        return None
    method = METHOD_BY_ID["ma60-slope"]
    prev = feat.iloc[-2] if len(feat) >= 2 else None
    slope = float(last["ma60_slope"]) if _finite(last.get("ma60_slope")) else 0.0
    return _hit_base(
        method,
        last,
        prev=prev,
        reason=f"MA60斜率转正 {slope:.4f}",
        score=75.0 + min(20.0, max(0.0, slope / max(abs(float(last.get("ma60") or 1)), 1e-6) * 1000)),
    )


def classify_ma60_add(g: pd.DataFrame, asof: date, feat60: Optional[pd.DataFrame] = None) -> Optional[Dict[str, Any]]:
    feat = _ma60_feat(g, feat60)
    last = _asof_last(feat, asof)
    if last is None:
        return None
    c, m5, m20, m60 = last.get("close"), last.get("ma5"), last.get("ma20"), last.get("ma60")
    if not _finite(c, m5, m20, m60) or float(m60) <= 0:
        return None
    close, ma5, ma20, ma60 = float(c), float(m5), float(m20), float(m60)
    if not (close > ma60 and close > ma5 and close > ma20):
        return None
    dist = (close - ma60) / ma60
    if dist < 0 or dist > MA60_NEAR:
        return None
    if bool(last.get("vol_expand")):
        return None
    method = METHOD_BY_ID["ma60-add"]
    prev = feat.iloc[-2] if len(feat) >= 2 else None
    score = 80.0 * (1.0 - dist / MA60_NEAR)
    return _hit_base(
        method,
        last,
        prev=prev,
        reason=f"贴近MA60 {dist * 100:.2f}% · 站上MA5/MA20 · 不放量",
        score=score,
    )


def classify_doji_sell(g: pd.DataFrame, asof: date, feat60: Optional[pd.DataFrame] = None) -> Optional[Dict[str, Any]]:
    feat = _ma60_feat(g, feat60)
    last = _asof_last(feat, asof)
    if last is None or not bool(last.get("b_doji_sell")):
        return None
    method = METHOD_BY_ID["doji-sell"]
    prev = feat.iloc[-2] if len(feat) >= 2 else None
    over5 = float(last["high_over_ma5"]) if _finite(last.get("high_over_ma5")) else 0.0
    over60 = float(last["high_over_ma60"]) if _finite(last.get("high_over_ma60")) else 0.0
    return _hit_base(
        method,
        last,
        prev=prev,
        reason=f"高位十字星 · 高点超MA5 {over5 * 100:.1f}% · 超MA60 {over60 * 100:.1f}%",
        score=70.0 + min(25.0, over60 * 50.0),
    )


def classify_ma60_shrink(g: pd.DataFrame, asof: date, feat60: Optional[pd.DataFrame] = None) -> Optional[Dict[str, Any]]:
    feat = _ma60_feat(g, feat60)
    last = _asof_last(feat, asof)
    if last is None or not bool(last.get("entry_raw")):
        return None
    method = METHOD_BY_ID["ma60-shrink"]
    prev = feat.iloc[-2] if len(feat) >= 2 else None
    dev = float(last["dev"]) if _finite(last.get("dev")) else 0.0
    return _hit_base(
        method,
        last,
        prev=prev,
        reason=f"MA60缩量折价 {dev * 100:.1f}%",
        score=60.0 + min(30.0, max(0.0, dev) * 200.0),
    )


def classify_ma20_cash(g: pd.DataFrame, asof: date, feat60: Optional[pd.DataFrame] = None) -> Optional[Dict[str, Any]]:
    feat = _ma60_feat(g, feat60)
    last = _asof_last(feat, asof)
    if last is None:
        return None
    c, m20, m60 = last.get("close"), last.get("ma20"), last.get("ma60")
    if not _finite(c, m20) or not bool(last.get("vol_expand")):
        return None
    close, ma20 = float(c), float(m20)
    if ma20 <= 0:
        return None
    if abs(close / ma20 - 1.0) > 0.03:
        return None
    below_ma60 = _finite(m60) and close < float(m60)
    was_below = False
    if "ma20" in feat.columns and len(feat) >= 6:
        tail = feat.iloc[-6:-1]
        was_below = bool((tail["close"] < tail["ma20"]).any())
    if not (below_ma60 or was_below):
        return None
    method = METHOD_BY_ID["ma20-cash"]
    prev = feat.iloc[-2] if len(feat) >= 2 else None
    return _hit_base(
        method,
        last,
        prev=prev,
        reason=f"回到MA20放量兑现 · 距MA20 {(close / ma20 - 1) * 100:.2f}%",
        score=68.0,
    )


def classify_crash_buy(g: pd.DataFrame, asof: date, feat60: Optional[pd.DataFrame] = None) -> Optional[Dict[str, Any]]:
    feat = _ma60_feat(g, feat60)
    last = _asof_last(feat, asof)
    if last is None or not bool(last.get("crash_buy")):
        return None
    method = METHOD_BY_ID["crash-buy"]
    prev = feat.iloc[-2] if len(feat) >= 2 else None
    dev = float(last["dev"]) if _finite(last.get("dev")) else 0.0
    return _hit_base(
        method,
        last,
        prev=prev,
        reason=f"偏离MA60 {dev * 100:.1f}% 放量买",
        score=70.0 + min(25.0, max(0.0, dev - 0.15) * 200.0),
    )


def classify_long_yin(g: pd.DataFrame, asof: date, feat60: Optional[pd.DataFrame] = None) -> Optional[Dict[str, Any]]:
    feat = _ma60_feat(g, feat60)
    last = _asof_last(feat, asof)
    if last is None:
        return None
    if not (bool(last.get("long_yin")) and bool(last.get("vol_gt_prev5"))):
        return None
    c, m5 = last.get("close"), last.get("ma5")
    if not _finite(c, m5) or float(c) <= float(m5):
        return None
    method = METHOD_BY_ID["long-yin"]
    prev = feat.iloc[-2] if len(feat) >= 2 else None
    return _hit_base(
        method,
        last,
        prev=prev,
        reason="站上MA5 · 放量长阴",
        score=72.0,
    )


def classify_elder(g: pd.DataFrame, asof: date, feat60: Optional[pd.DataFrame] = None) -> Optional[Dict[str, Any]]:
    use = g.sort_values("trade_date").copy()
    last = _asof_last(use, asof)
    if last is None or len(use) < ELDER_LOOK:
        return None
    for col in ("close", "open", "high", "low"):
        if col in use.columns:
            use[col] = pd.to_numeric(use[col], errors="coerce")
    ref = float(use["close"].iloc[-ELDER_LOOK])
    close = float(last["close"])
    if ref <= 0 or close <= 0:
        return None
    ret = close / ref - 1.0
    band = None
    score = 0.0
    if ret <= -0.25:
        band, score = "下跌25%增持带", 78.0
    elif ret <= -0.15:
        band, score = "下跌15%增持带", 70.0
    elif ret >= 1.0:
        band, score = "上涨100%清仓带", 90.0
    elif ret >= 0.60:
        band, score = "上涨60%减仓带", 82.0
    elif ret >= 0.45:
        band, score = "上涨45%减仓带", 78.0
    elif ret >= 0.35:
        band, score = "上涨35%减仓带", 74.0
    elif ret >= 0.25:
        band, score = "上涨25%减仓带", 70.0
    else:
        return None
    method = METHOD_BY_ID["elder"]
    row = last.copy()
    if "ma5" not in row.index:
        ma5 = float(use["close"].tail(5).mean()) if len(use) >= 5 else close
        row["ma5"] = ma5
    prev = use.iloc[-2] if len(use) >= 2 else None
    return _hit_base(
        method,
        row,
        prev=prev,
        reason=f"相对{ELDER_LOOK - 1}日前 {ret * 100:.1f}% · {band}",
        score=score,
        extra={"elder_ret": round(ret * 100.0, 2), "elder_band": band},
    )


def classify_vol_halt(g: pd.DataFrame, asof: date, feat60: Optional[pd.DataFrame] = None) -> Optional[Dict[str, Any]]:
    use = g.sort_values("trade_date").copy()
    last = _asof_last(use, asof)
    if last is None or len(use) < 22:
        return None
    close = pd.to_numeric(use["close"], errors="coerce")
    rets = close.pct_change().iloc[-20:]
    vol = float(rets.std()) if rets.notna().sum() >= 15 else None
    if vol is None or vol < VOL_HALT_STD:
        return None
    method = METHOD_BY_ID["vol-halt"]
    row = last.copy()
    if "ma5" not in row.index:
        row["ma5"] = float(close.tail(5).mean())
    prev = use.iloc[-2] if len(use) >= 2 else None
    return _hit_base(
        method,
        row,
        prev=prev,
        reason=f"近20日收益波动 {vol * 100:.2f}% ≥ {VOL_HALT_STD * 100:.1f}%，禁止开仓",
        score=min(99.0, 50.0 + vol / VOL_HALT_STD * 20.0),
        extra={"vol_20": round(vol, 5)},
    )


def classify_c_struct(g: pd.DataFrame, asof: date, feat60: Optional[pd.DataFrame] = None) -> Optional[Dict[str, Any]]:
    hit = classify_structure_pullback(g, asof)
    if not hit:
        return None
    method = METHOD_BY_ID["c-struct"]
    hit["method_id"] = method["id"]
    hit["setup_type"] = "C"
    hit["buyable"] = True
    hit["steps"] = list(method["steps"])
    hit["regimes"] = list(method["regimes"])
    hit["stock_regime"] = stock_regime_of(hit.get("close"), hit.get("ma20"), hit.get("ma60"))
    return hit


Classifier = Callable[..., Optional[Dict[str, Any]]]

CLASSIFIERS: Dict[str, Classifier] = {
    "c-struct": classify_c_struct,
    "ma60-slope": classify_ma60_slope,
    "ma60-add": classify_ma60_add,
    "doji-sell": classify_doji_sell,
    "ma60-shrink": classify_ma60_shrink,
    "ma20-cash": classify_ma20_cash,
    "crash-buy": classify_crash_buy,
    "long-yin": classify_long_yin,
    "elder": classify_elder,
    "vol-halt": classify_vol_halt,
}


def classify_method(
    method_id: str,
    g: pd.DataFrame,
    asof: date,
    *,
    feat60: Optional[pd.DataFrame] = None,
    classify_b: Optional[Callable[[pd.DataFrame, date], Optional[Dict[str, Any]]]] = None,
    classify_a: Optional[Callable[[pd.DataFrame, date], Optional[Dict[str, Any]]]] = None,
) -> Optional[Dict[str, Any]]:
    if method_id == "b-ma5":
        if not classify_b:
            return None
        hit = classify_b(g, asof)
        if not hit:
            return None
        method = METHOD_BY_ID["b-ma5"]
        hit["method_id"] = "b-ma5"
        hit["setup_type"] = "B"
        hit["buyable"] = True
        hit["steps"] = list(method["steps"])
        hit["regimes"] = list(method["regimes"])
        hit["stock_regime"] = hit.get("stock_regime") or "up"
        return hit
    if method_id == "base-watch":
        if not classify_a:
            return None
        hit = classify_a(g, asof)
        if not hit:
            return None
        method = METHOD_BY_ID["base-watch"]
        hit["method_id"] = "base-watch"
        hit["setup_type"] = "A"
        hit["buyable"] = False
        hit["steps"] = list(method["steps"])
        hit["regimes"] = list(method["regimes"])
        hit["stock_regime"] = hit.get("stock_regime") or "down"
        return hit
    fn = CLASSIFIERS.get(method_id)
    if not fn:
        return None
    return fn(g, asof, feat60)

"""A 股回测费用：佣金万二、最低 5 元；卖出另收印花税万五。"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

DEFAULT_COMMISSION_RATE = 0.0002
DEFAULT_COMMISSION_MIN = 5.0
DEFAULT_STAMP_RATE = 0.0005


def _rate(params: Optional[Dict[str, Any]], key: str, default: float) -> float:
    p = params or {}
    try:
        v = float(p.get(key, default))
    except (TypeError, ValueError):
        return default
    if v < 0:
        return 0.0
    # 填 2 表示万分之二
    if v >= 1:
        return v / 10000.0
    return v


def _min_cny(params: Optional[Dict[str, Any]]) -> float:
    p = params or {}
    try:
        v = float(p.get("commission_min", DEFAULT_COMMISSION_MIN))
    except (TypeError, ValueError):
        return DEFAULT_COMMISSION_MIN
    return max(0.0, v)


def calc_fees(
    side: str,
    amount: float,
    params: Optional[Dict[str, Any]] = None,
) -> Tuple[float, float]:
    """返回 (佣金, 印花税)。买入印花税为 0。"""
    amt = max(float(amount or 0), 0.0)
    if amt <= 0:
        return 0.0, 0.0
    rate = _rate(params, "commission_rate", DEFAULT_COMMISSION_RATE)
    min_cny = _min_cny(params)
    if rate <= 0:
        commission = 0.0
    else:
        commission = max(round(amt * rate, 4), min_cny)
    stamp = 0.0
    s = (side or "").strip()
    if s in ("卖出", "sell", "SELL"):
        stamp = round(amt * _rate(params, "stamp_rate", DEFAULT_STAMP_RATE), 4)
    return commission, stamp


def affordable_buy_shares(
    cash: float,
    price: float,
    params: Optional[Dict[str, Any]] = None,
) -> int:
    """扣佣金后能买的整手数。"""
    px = float(price or 0)
    cash = float(cash or 0)
    if px <= 0 or cash <= _min_cny(params):
        return 0
    qty = int(cash / px // 100) * 100
    while qty >= 100:
        amount = qty * px
        commission, _stamp = calc_fees("买入", amount, params)
        if amount + commission <= cash + 1e-6:
            return qty
        qty -= 100
    return 0


def fee_summary(params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    rate = _rate(params, "commission_rate", DEFAULT_COMMISSION_RATE)
    stamp = _rate(params, "stamp_rate", DEFAULT_STAMP_RATE)
    return {
        "commission_rate": rate,
        "commission_min": _min_cny(params),
        "stamp_rate": stamp,
        "label": "费用",
        "status": f"佣金万{rate * 10000:g}·最低{_min_cny(params):g}元 · 印花税万{stamp * 10000:g}(卖)",
        "detail": "买入/卖出都收佣金（最低5元）；印花税仅卖出。已计入回测现金与收益。",
    }

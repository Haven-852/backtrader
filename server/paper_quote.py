"""模拟账户盘中报价：只使用真实 1 分钟分时末价，不用日线收盘或估算冒充。"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)


def _to_date(v: Any) -> Optional[date]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip().replace("/", "-")
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except Exception:
        return None


def _clock(v: Any) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.strftime("%H:%M")
    s = str(v).strip()
    if "T" in s:
        s = s.replace("T", " ")
    if " " in s:
        s = s.split(" ", 1)[1]
    s = s[:5]
    return s if len(s) >= 4 and s[2] == ":" else None


def _px(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x or x <= 0:
        return None
    return x


def _missing(ts_code: str, trade_date: Optional[date], error: str, phase: Optional[str] = None) -> Dict[str, Any]:
    return {
        "ok": False,
        "ts_code": ts_code,
        "trade_date": trade_date.isoformat() if trade_date else None,
        "price": None,
        "bar_time": None,
        "clock": None,
        "source": None,
        "session_phase": phase,
        "pre_close": None,
        "pct_chg": None,
        "quote_source": "missing",
        "error": error,
    }


def session_context() -> Dict[str, Any]:
    from data_layer.collectors.datahubco.intraday import intraday_fetcher

    phase = intraday_fetcher.session_phase()
    d = phase.get("trade_date")
    return {
        "phase": phase.get("phase"),
        "trade_date": d,
        "today": phase.get("today"),
        "now": phase.get("now"),
        "asof": d.isoformat() if isinstance(d, date) else None,
    }


def last_intraday_quote(
    ts_code: str,
    trade_date: Optional[str] = None,
) -> Dict[str, Any]:
    """
    取对应交易日最后一根有效分时收盘价。
    盘中 = 截至当前分钟；收盘后 = 当日最后一根；开盘前 = 上一交易日最后一根。
    没有分时则 ok=False，绝不回退到日线或成本价。
    """
    from tools.datahubco_toolkit import datahubco_toolkit

    ctx = session_context()
    d = _to_date(trade_date) or ctx.get("trade_date")
    code = (ts_code or "").strip().upper()
    if not code:
        return _missing("", d, "缺少 ts_code", ctx.get("phase"))
    if d is None:
        return _missing(code, None, "无法确定分时交易日", ctx.get("phase"))

    try:
        mins = datahubco_toolkit.get_mins(
            code,
            trade_date=d.isoformat(),
            source="auto",
            write_back=True,
            include_auction=False,
        )
    except Exception as e:
        logger.warning("拉取分时报价失败 %s %s: %s", code, d, e)
        return _missing(code, d, f"分时接口异常: {e}", ctx.get("phase"))

    if not mins.get("ok"):
        return _missing(
            code,
            d,
            str(mins.get("error") or "无当日分钟线"),
            mins.get("session_phase") or ctx.get("phase"),
        )

    bars = list(mins.get("data") or [])
    latest = mins.get("latest")
    if isinstance(latest, dict):
        bars.append(latest)
    session_bars = [
        b
        for b in bars
        if isinstance(b, dict)
        and str(b.get("phase") or "session") != "auction"
        and str(b.get("freq") or "1min") != "auction"
        and _px(b.get("close")) is not None
    ]
    last_bar = session_bars[-1] if session_bars else None
    px = _px(last_bar.get("close") if last_bar else None) or _px(mins.get("last"))
    if px is None:
        return _missing(
            code,
            d,
            "分时有记录但没有有效收盘价",
            mins.get("session_phase") or ctx.get("phase"),
        )

    bar_time = None
    if last_bar:
        bar_time = last_bar.get("time") or last_bar.get("bar_time")
    if bar_time and " " not in str(bar_time) and "T" not in str(bar_time):
        bar_time = f"{d.isoformat()} {str(bar_time)[:8]}"
    elif bar_time is not None:
        bar_time = str(bar_time)
    pre = _px(mins.get("pre_close"))
    pct = mins.get("pct_chg")
    if pct is None and pre:
        pct = round((px / pre - 1.0) * 100, 4)
    phase = mins.get("session_phase") or ctx.get("phase")
    src = mins.get("source_used") or mins.get("source")
    return {
        "ok": True,
        "ts_code": str(mins.get("ts_code") or code),
        "name": mins.get("name"),
        "trade_date": str(mins.get("trade_date") or d.isoformat()),
        "price": round(px, 4),
        "bar_time": str(bar_time) if bar_time is not None else None,
        "clock": _clock(bar_time),
        "source": src,
        "session_phase": phase,
        "pre_close": round(pre, 4) if pre is not None else None,
        "pct_chg": round(float(pct), 4) if pct is not None else None,
        "mins_count": mins.get("count"),
        "quote_source": "intraday",
        "error": None,
    }


def last_intraday_quotes(
    ts_codes: Iterable[str],
    trade_date: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    seen: List[str] = []
    for raw in ts_codes:
        code = (raw or "").strip().upper()
        if not code or code in out:
            continue
        seen.append(code)
        out[code] = last_intraday_quote(code, trade_date=trade_date)
    logger.info("分时报价 %s 只，成功 %s", len(seen), sum(1 for q in out.values() if q.get("ok")))
    return out

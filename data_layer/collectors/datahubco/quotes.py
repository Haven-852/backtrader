"""腾讯行情快照：收盘后补当天日线（Datahubco 经常滞后到上一交易日）。"""

from __future__ import annotations

import logging
import urllib.request
from datetime import date, datetime
from typing import Dict, Iterable, List, Optional
from zoneinfo import ZoneInfo

import pandas as pd

logger = logging.getLogger("collector.datahubco.quotes")

TZ_SH = ZoneInfo("Asia/Shanghai")


def qq_symbol(ts_code: str) -> str:
    code, _, exch = (ts_code or "").partition(".")
    prefix = {"SH": "sh", "SZ": "sz", "BJ": "bj"}.get(exch.upper(), "sz")
    return f"{prefix}{code}"


def _http_text(url: str, timeout: int = 12) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Referer": "https://gu.qq.com/",
            "Connection": "close",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("gbk", errors="replace")


def _parse_quote(ts_code: str, body: str) -> Optional[Dict]:
    parts = body.strip().strip('"').split("~")
    if len(parts) < 35:
        return None
    stamp = str(parts[30] or "").replace("-", "")[:8]
    if len(stamp) < 8:
        return None
    try:
        trade_date = datetime.strptime(stamp[:8], "%Y%m%d").date()
        close = float(parts[3])
        pre_close = float(parts[4])
        open_px = float(parts[5])
        high = float(parts[33])
        low = float(parts[34])
        vol = int(float(parts[6] or 0))
    except (TypeError, ValueError):
        return None
    amount = None
    if len(parts) > 57 and parts[57]:
        try:
            # 腾讯 57 为万元，库内日线与 Tushare 一样用千元
            amount = float(parts[57]) * 10.0
        except (TypeError, ValueError):
            amount = None
    if amount is None and len(parts) > 35 and "/" in parts[35]:
        try:
            amount = float(parts[35].split("/")[-1]) / 1000.0
        except (TypeError, ValueError):
            amount = None
    change = close - pre_close
    pct = (change / pre_close * 100) if pre_close else None
    try:
        if parts[31]:
            change = float(parts[31])
        if parts[32]:
            pct = float(parts[32])
    except (TypeError, ValueError):
        pass
    return {
        "ts_code": ts_code,
        "trade_date": trade_date,
        "open": open_px,
        "high": high,
        "low": low,
        "close": close,
        "pre_close": pre_close,
        "change": change,
        "pct_chg": round(pct, 4) if pct is not None else None,
        "vol": vol,
        "amount": amount,
        "name": parts[1] if len(parts) > 1 else ts_code,
    }


def fetch_tencent_eod(ts_codes: Iterable[str]) -> pd.DataFrame:
    codes = [c.strip().upper() for c in ts_codes if c and str(c).strip()]
    if not codes:
        return pd.DataFrame()
    symbols = [qq_symbol(c) for c in codes]
    mapping = {qq_symbol(c): c for c in codes}
    url = "http://qt.gtimg.cn/q=" + ",".join(symbols)
    try:
        raw = _http_text(url)
    except Exception as e:
        logger.warning("腾讯收盘快照失败: %s", e)
        return pd.DataFrame()
    rows: List[Dict] = []
    for chunk in raw.strip().split(";"):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        key, body = chunk.split("=", 1)
        symbol = key.replace("v_", "").strip()
        ts_code = mapping.get(symbol)
        if not ts_code:
            continue
        parsed = _parse_quote(ts_code, body)
        if parsed:
            rows.append(parsed)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def fetch_tencent_eod_on(ts_codes: Iterable[str], trade_date: date) -> pd.DataFrame:
    df = fetch_tencent_eod(ts_codes)
    if df.empty:
        return df
    return df.loc[df["trade_date"] == trade_date].copy()

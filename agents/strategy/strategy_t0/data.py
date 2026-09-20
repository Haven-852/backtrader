"""从 Timescale ``stock_intraday_mins`` 读取 1 分钟分时，严格因果截断。"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import pandas as pd
from sqlalchemy import text as sa_text

from data_layer.db_manager import storage_manager

from .session import TZ_SH, clock_key, is_auction_clock, is_session_clock, parse_as_of, parse_date, to_sh

logger = logging.getLogger("agent.strategy.t0.data")

MAX_CODES = 15
MAX_DAYS = 15


def normalize_ts_code(code: str) -> str:
    c = (code or "").strip().upper()
    if not c:
        return c
    if "." in c:
        return c
    if c[0] in "69":
        return f"{c}.SH"
    if c.startswith(("8", "4")):
        return f"{c}.BJ"
    return f"{c}.SZ"


def parse_ts_codes(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        parts = [str(x) for x in raw]
    else:
        parts = str(raw).replace("，", ",").replace(" ", ",").split(",")
    out: List[str] = []
    seen = set()
    for p in parts:
        code = normalize_ts_code(p)
        if code and code not in seen:
            seen.add(code)
            out.append(code)
        if len(out) >= MAX_CODES:
            break
    return out


def _engine():
    eng = storage_manager.get_postgres_engine()
    if eng is None:
        raise RuntimeError("PostgreSQL 未连接，无法读取分时")
    return eng


def trading_days_before(d: date, n: int) -> List[date]:
    if n <= 0:
        return []
    eng = _engine()
    sql = sa_text(
        """
        SELECT cal_date
        FROM ref_trade_cal
        WHERE exchange = 'SSE' AND is_open = 1 AND cal_date < :d
        ORDER BY cal_date DESC
        LIMIT :n
        """
    )
    try:
        df = pd.read_sql(sql, eng, params={"d": d, "n": int(n)})
    except Exception as e:
        logger.warning("读交易日历失败，改用分时表日期: %s", e)
        df = pd.DataFrame()
    if df is None or df.empty:
        sql2 = sa_text(
            """
            SELECT DISTINCT trade_date AS cal_date
            FROM stock_intraday_mins
            WHERE trade_date < :d
            ORDER BY trade_date DESC
            LIMIT :n
            """
        )
        df = pd.read_sql(sql2, eng, params={"d": d, "n": int(n)})
    if df is None or df.empty:
        return []
    days = [parse_date(x) for x in df["cal_date"].tolist()]
    return [x for x in days if x is not None]


def trading_days_between(start: date, end: date) -> List[date]:
    if start > end:
        start, end = end, start
    eng = _engine()
    sql = sa_text(
        """
        SELECT cal_date
        FROM ref_trade_cal
        WHERE exchange = 'SSE' AND is_open = 1
          AND cal_date >= :a AND cal_date <= :b
        ORDER BY cal_date
        """
    )
    try:
        df = pd.read_sql(sql, eng, params={"a": start, "b": end})
    except Exception:
        df = pd.DataFrame()
    if df is None or df.empty:
        sql2 = sa_text(
            """
            SELECT DISTINCT trade_date AS cal_date
            FROM stock_intraday_mins
            WHERE trade_date >= :a AND trade_date <= :b
            ORDER BY trade_date
            """
        )
        df = pd.read_sql(sql2, eng, params={"a": start, "b": end})
    days = [parse_date(x) for x in (df["cal_date"].tolist() if df is not None and not df.empty else [])]
    days = [x for x in days if x is not None]
    if len(days) > MAX_DAYS:
        days = days[-MAX_DAYS:]
    return days


def load_pre_close(ts_code: str, trade_date: date) -> Optional[float]:
    eng = _engine()
    sql = sa_text(
        """
        SELECT pre_close, close
        FROM stock_daily
        WHERE ts_code = :c AND trade_date = :d
        LIMIT 1
        """
    )
    df = pd.read_sql(sql, eng, params={"c": ts_code, "d": trade_date})
    if df is not None and not df.empty:
        pre = df.iloc[0].get("pre_close")
        if pd.notna(pre) and float(pre) > 0:
            return float(pre)
    prev = trading_days_before(trade_date, 1)
    if not prev:
        return None
    sql2 = sa_text(
        """
        SELECT close FROM stock_daily
        WHERE ts_code = :c AND trade_date = :d
        LIMIT 1
        """
    )
    df2 = pd.read_sql(sql2, eng, params={"c": ts_code, "d": prev[0]})
    if df2 is None or df2.empty or pd.isna(df2.iloc[0]["close"]):
        return None
    return float(df2.iloc[0]["close"])


def _rows_to_bars(df: pd.DataFrame) -> List[Dict[str, Any]]:
    if df is None or df.empty:
        return []
    out: List[Dict[str, Any]] = []
    for _, r in df.iterrows():
        bt = to_sh(r.get("bar_time"))
        clock = clock_key(bt)
        close = r.get("close")
        if close is None or (isinstance(close, float) and pd.isna(close)):
            continue
        vol = r.get("vol")
        amount = r.get("amount")
        avg = r.get("avg_price")
        bar = {
            "bar_time": bt.isoformat(sep=" ", timespec="seconds") if bt else None,
            "clock": clock,
            "trade_date": str(parse_date(r.get("trade_date")) or ""),
            "open": _f(r.get("open")),
            "high": _f(r.get("high")),
            "low": _f(r.get("low")),
            "close": float(close),
            "vol": int(vol) if pd.notna(vol) else 0,
            "amount": _f(amount),
            "avg_price": _f(avg),
            "auction": is_auction_clock(clock),
            "session": is_session_clock(clock),
        }
        out.append(bar)
    return out


def _f(v: Any) -> Optional[float]:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_mins(
    ts_code: str,
    start: date,
    end: date,
    as_of: Optional[datetime] = None,
) -> pd.DataFrame:
    eng = _engine()
    sql = sa_text(
        """
        SELECT ts_code, bar_time, trade_date, open, high, low, close, vol, amount, avg_price
        FROM stock_intraday_mins
        WHERE ts_code = :c AND trade_date >= :a AND trade_date <= :b
        ORDER BY bar_time
        """
    )
    df = pd.read_sql(sql, eng, params={"c": ts_code, "a": start, "b": end})
    if df is None or df.empty:
        return pd.DataFrame()
    df["bar_time"] = pd.to_datetime(df["bar_time"], utc=True, errors="coerce")
    try:
        df["bar_time"] = df["bar_time"].dt.tz_convert(TZ_SH)
    except Exception:
        df["bar_time"] = pd.to_datetime(df["bar_time"], errors="coerce")
    if as_of is not None:
        cutoff = to_sh(as_of)
        if cutoff is not None:
            df = df[df["bar_time"] <= cutoff]
    return df


def load_day_bars(
    ts_code: str,
    trade_date: date,
    as_of: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    df = load_mins(ts_code, trade_date, trade_date, as_of=as_of)
    return _rows_to_bars(df)


def load_lookback_session_vols(
    ts_code: str,
    trade_date: date,
    lookback_days: int = 20,
) -> Dict[str, List[float]]:
    """不含当天。返回 clock -> 历史成交量列表。"""
    days = trading_days_before(trade_date, lookback_days)
    if not days:
        return {}
    start, end = days[-1], days[0]
    df = load_mins(ts_code, start, end, as_of=None)
    buckets: Dict[str, List[float]] = {}
    if df is None or df.empty:
        return buckets
    for _, r in df.iterrows():
        clock = clock_key(r.get("bar_time"))
        if not is_session_clock(clock):
            continue
        vol = r.get("vol")
        v = float(vol) if pd.notna(vol) else 0.0
        buckets.setdefault(clock, []).append(v)
    return buckets


def limit_pct_band(ts_code: str) -> float:
    code = (ts_code or "").split(".")[0]
    if code.startswith(("300", "301", "688", "689")):
        return 0.20
    if code.startswith(("8", "4")):
        return 0.30
    return 0.10


def load_listed_codes() -> List[str]:
    eng = _engine()
    df = pd.read_sql(
        sa_text(
            """
            SELECT ts_code
            FROM ref_stock_basic
            WHERE COALESCE(list_status, 'L') = 'L'
              AND (ts_code LIKE '%.SH' OR ts_code LIKE '%.SZ')
            ORDER BY ts_code
            """
        ),
        eng,
    )
    if df is None or df.empty:
        return []
    return [normalize_ts_code(x) for x in df["ts_code"].tolist() if x]


def load_stock_names(codes: Optional[List[str]] = None) -> Dict[str, str]:
    eng = _engine()
    sql = "SELECT ts_code, name FROM ref_stock_basic"
    df = pd.read_sql(sa_text(sql), eng)
    if df is None or df.empty:
        return {}
    out = {str(r.ts_code): str(r.name or "") for r in df.itertuples(index=False)}
    if codes:
        want = set(codes)
        return {k: v for k, v in out.items() if k in want}
    return out


def list_minute_codes(trade_date: date, min_bars: int = 180) -> List[str]:
    eng = _engine()
    df = pd.read_sql(
        sa_text(
            """
            SELECT ts_code, COUNT(*) AS n
            FROM stock_intraday_mins
            WHERE trade_date = :d
            GROUP BY ts_code
            HAVING COUNT(*) >= :n
            ORDER BY ts_code
            """
        ),
        eng,
        params={"d": trade_date, "n": int(min_bars)},
    )
    if df is None or df.empty:
        return []
    return [str(x) for x in df["ts_code"].tolist()]


def load_mins_by_date(
    trade_date: date,
    as_of: Optional[datetime] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    eng = _engine()
    df = pd.read_sql(
        sa_text(
            """
            SELECT ts_code, bar_time, trade_date, open, high, low, close, vol, amount, avg_price
            FROM stock_intraday_mins
            WHERE trade_date = :d
            ORDER BY ts_code, bar_time
            """
        ),
        eng,
        params={"d": trade_date},
    )
    if df is None or df.empty:
        return {}
    df["bar_time"] = pd.to_datetime(df["bar_time"], utc=True, errors="coerce")
    try:
        df["bar_time"] = df["bar_time"].dt.tz_convert(TZ_SH)
    except Exception:
        df["bar_time"] = pd.to_datetime(df["bar_time"], errors="coerce")
    if as_of is not None:
        cutoff = to_sh(as_of)
        if cutoff is not None:
            df = df[df["bar_time"] <= cutoff]
    out: Dict[str, List[Dict[str, Any]]] = {}
    for code, g in df.groupby("ts_code", sort=False):
        out[str(code)] = _rows_to_bars(g)
    return out


def load_lookback_vols_bulk(
    trade_date: date,
    lookback_days: int = 20,
) -> Dict[str, Dict[str, List[float]]]:
    days = trading_days_before(trade_date, lookback_days)
    if not days:
        return {}
    start, end = days[-1], days[0]
    eng = _engine()
    df = pd.read_sql(
        sa_text(
            """
            SELECT ts_code, bar_time, vol
            FROM stock_intraday_mins
            WHERE trade_date >= :a AND trade_date <= :b
            ORDER BY ts_code, bar_time
            """
        ),
        eng,
        params={"a": start, "b": end},
    )
    out: Dict[str, Dict[str, List[float]]] = {}
    if df is None or df.empty:
        return out
    df["bar_time"] = pd.to_datetime(df["bar_time"], utc=True, errors="coerce")
    try:
        df["bar_time"] = df["bar_time"].dt.tz_convert(TZ_SH)
    except Exception:
        df["bar_time"] = pd.to_datetime(df["bar_time"], errors="coerce")
    for row in df.itertuples(index=False):
        clock = clock_key(row.bar_time)
        if not is_session_clock(clock):
            continue
        vol = float(row.vol) if pd.notna(row.vol) else 0.0
        out.setdefault(str(row.ts_code), {}).setdefault(clock, []).append(vol)
    return out


def load_daily_lookback(
    ts_code: str,
    before: date,
    n: int = 30,
) -> List[Dict[str, Any]]:
    eng = _engine()
    df = pd.read_sql(
        sa_text(
            """
            SELECT ts_code, trade_date, open, high, low, close, vol, pct_chg
            FROM stock_daily
            WHERE ts_code = :c AND trade_date < :d
            ORDER BY trade_date DESC
            LIMIT :n
            """
        ),
        eng,
        params={"c": ts_code, "d": before, "n": int(n)},
    )
    if df is None or df.empty:
        return []
    df = df.sort_values("trade_date")
    return _daily_rows(df)


def load_daily_lookback_bulk(before: date, n: int = 30) -> Dict[str, List[Dict[str, Any]]]:
    start = before - timedelta(days=max(int(n * 2), 50))
    eng = _engine()
    df = pd.read_sql(
        sa_text(
            """
            SELECT ts_code, trade_date, open, high, low, close, vol, pct_chg
            FROM stock_daily
            WHERE trade_date >= :a AND trade_date < :b
            ORDER BY ts_code, trade_date
            """
        ),
        eng,
        params={"a": start, "b": before},
    )
    out: Dict[str, List[Dict[str, Any]]] = {}
    if df is None or df.empty:
        return out
    for code, g in df.groupby("ts_code", sort=False):
        g = g.sort_values("trade_date").tail(int(n))
        out[str(code)] = _daily_rows(g)
    return out


def _daily_rows(df: pd.DataFrame) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for r in df.itertuples(index=False):
        rows.append({
            "ts_code": str(r.ts_code),
            "trade_date": str(parse_date(r.trade_date) or r.trade_date),
            "open": _f(r.open),
            "high": _f(r.high),
            "low": _f(r.low),
            "close": _f(r.close),
            "vol": None if pd.isna(r.vol) else float(r.vol),
            "pct_chg": _f(getattr(r, "pct_chg", None)),
        })
    return rows

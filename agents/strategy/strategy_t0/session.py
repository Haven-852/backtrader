"""A 股连续竞价分钟时钟。午休不计剩余时间。"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

TZ_SH = ZoneInfo("Asia/Shanghai")

AUCTION_START = time(9, 15)
AUCTION_END = time(9, 25)
SESSION_OPEN = time(9, 30)
LUNCH_END = time(11, 30)
AFTERNOON_OPEN = time(13, 0)
FORCE_FLAT = time(14, 55)
SESSION_CLOSE = time(15, 0)


def _clocks_between(start: time, end: time) -> list[str]:
    cur = datetime(2000, 1, 1, start.hour, start.minute)
    stop = datetime(2000, 1, 1, end.hour, end.minute)
    out: list[str] = []
    while cur <= stop:
        out.append(cur.strftime("%H%M"))
        cur += timedelta(minutes=1)
    return out


# 09:31–11:30 + 13:01–15:00 = 240 根已完成分钟
SESSION_CLOCKS: list[str] = (
    _clocks_between(time(9, 31), time(11, 30)) + _clocks_between(time(13, 1), time(15, 0))
)
CLOCK_INDEX = {c: i for i, c in enumerate(SESSION_CLOCKS)}
N_SESSION_MIN = len(SESSION_CLOCKS)


def to_sh(dt: Any) -> Optional[datetime]:
    if dt is None:
        return None
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            return dt.replace(tzinfo=TZ_SH)
        return dt.astimezone(TZ_SH)
    try:
        parsed = datetime.fromisoformat(str(dt).replace("Z", "+00:00"))
        return to_sh(parsed)
    except (TypeError, ValueError):
        return None


def clock_key(dt: Any) -> Optional[str]:
    sh = to_sh(dt)
    if sh is None:
        s = str(dt)
        digits = "".join(ch for ch in s if ch.isdigit())
        if len(digits) >= 12:
            return digits[8:12]
        return None
    return sh.strftime("%H%M")


def clock_time(clock: str) -> Optional[time]:
    if not clock or len(clock) < 4:
        return None
    try:
        return time(int(clock[:2]), int(clock[2:4]))
    except ValueError:
        return None


def is_auction_clock(clock: Optional[str]) -> bool:
    t = clock_time(clock or "")
    return bool(t and AUCTION_START <= t <= AUCTION_END)


def is_session_clock(clock: Optional[str]) -> bool:
    return clock in CLOCK_INDEX


def remaining_minutes(clock: Optional[str]) -> int:
    """本分钟走完后还剩多少连续竞价分钟。15:00 为 0。"""
    if not clock or clock not in CLOCK_INDEX:
        return 0
    return N_SESSION_MIN - 1 - CLOCK_INDEX[clock]


def force_flat_clock(clock: Optional[str]) -> bool:
    t = clock_time(clock or "")
    return bool(t and t >= FORCE_FLAT)


def now_sh() -> datetime:
    return datetime.now(TZ_SH)


def parse_date(value: Any) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return to_sh(value).date() if to_sh(value) else value.date()
    s = str(value).strip()[:10]
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(s.replace("-", "") if fmt == "%Y%m%d" else s, fmt).date()
        except ValueError:
            continue
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def parse_as_of(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return to_sh(value)
    s = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y%m%d %H:%M:%S", "%Y%m%d%H%M%S"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=TZ_SH)
        except ValueError:
            continue
    dt = to_sh(s)
    return dt

"""
Datahubco 挑选式采集：先查 PostgreSQL，缺/旧则拉远端并 upsert。

交易日规则（上海时区）:
  - 交易日 + 交易时段 (09:15–15:00)：只取到「上一交易日」，不拉当天
  - 交易日收盘后 / 非交易日：取到最近已收盘交易日（可含当天若已收盘）

模式:
  - watchlist: 加自选 → 最近一季度日线 + 周线 + 月线
  - fresh:     看板/策略 → 对照「应有最新日」检查，不足则补
  - extend:    滑动更早区间 → 向前续日线
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import text as sa_text

from data_layer.db_manager import storage_manager

from .client import DatahubcoClient

logger = logging.getLogger("collector.datahubco.selective")

TZ_SH = ZoneInfo("Asia/Shanghai")
# A 股：开盘集合竞价起至收盘；盘中不把「当天」当作已完成日线
SESSION_START = time(9, 15)
SESSION_END = time(15, 0)

BAR_COLS = [
    "ts_code",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "change",
    "pct_chg",
    "vol",
    "amount",
]

FREQ_TABLE = {
    "daily": "stock_daily",
    "weekly": "stock_weekly",
    "monthly": "stock_monthly",
}

WATCHLIST_DAILY_DAYS = 100
WATCHLIST_WEEKLY_DAYS = 400
WATCHLIST_MONTHLY_DAYS = 800


class SelectiveDataFetcher:
    """挑选式获取（单票按需）"""

    def __init__(self, client: Optional[DatahubcoClient] = None):
        self._client = client
        self._engine = None
        self._cal_cache: Optional[pd.DataFrame] = None

    @property
    def client(self) -> DatahubcoClient:
        if self._client is None:
            self._client = DatahubcoClient()
        return self._client

    @property
    def engine(self):
        if self._engine is None:
            self._engine = storage_manager.get_postgres_engine()
        return self._engine

    @staticmethod
    def normalize_ts_code(code: str) -> str:
        code = (code or "").strip().upper()
        if not code:
            return code
        if "." in code:
            return code
        if code.startswith("6"):
            return f"{code}.SH"
        if code.startswith(("0", "3")):
            return f"{code}.SZ"
        if code.startswith(("5", "1")):
            return f"{code}.SH" if code.startswith("5") else f"{code}.SZ"
        return code

    # ------------------------------------------------------------------ #
    # 交易日历 / 时段
    # ------------------------------------------------------------------ #
    def ensure_trade_cal(self, years_ahead: int = 1) -> Dict[str, Any]:
        """日历过期则从 Datahubco 刷新 SSE 交易日历"""
        if not self.engine:
            return {"ok": False, "error": "no engine"}
        today = datetime.now(TZ_SH).date()
        need_end = today + timedelta(days=365 * years_ahead)
        try:
            df = pd.read_sql(
                sa_text("SELECT MAX(cal_date) AS d FROM ref_trade_cal WHERE exchange = 'SSE'"),
                self.engine,
            )
            max_d = _fmt_date(df.iloc[0]["d"]) if not df.empty else None
        except Exception as e:
            max_d = None
            logger.warning("读 trade_cal 失败: %s", e)

        if max_d:
            try:
                if datetime.strptime(max_d[:10], "%Y-%m-%d").date() >= need_end:
                    self._cal_cache = None
                    return {"ok": True, "skipped": True, "max_date": max_d}
            except Exception:
                pass

        start = "20200101"
        if max_d:
            start = (datetime.strptime(max_d[:10], "%Y-%m-%d").date() - timedelta(days=30)).strftime("%Y%m%d")
        end = need_end.strftime("%Y%m%d")
        try:
            remote = self.client.query(
                "trade_cal",
                exchange="SSE",
                start_date=start,
                end_date=end,
            )
        except Exception as e:
            logger.error("拉取 trade_cal 失败: %s", e)
            return {"ok": False, "error": str(e), "max_date": max_d}

        if remote is None or remote.empty:
            return {"ok": False, "error": "empty trade_cal", "max_date": max_d}

        remote = remote.copy()
        remote.columns = [str(c).lower() for c in remote.columns]
        if "exchange" not in remote.columns:
            remote["exchange"] = "SSE"
        remote["cal_date"] = pd.to_datetime(remote["cal_date"].astype(str), errors="coerce").dt.date
        if "pretrade_date" in remote.columns:
            remote["pretrade_date"] = pd.to_datetime(
                remote["pretrade_date"].astype(str), errors="coerce"
            ).dt.date
        else:
            remote["pretrade_date"] = None
        remote["is_open"] = pd.to_numeric(remote.get("is_open"), errors="coerce").fillna(0).astype(int)
        remote = remote.dropna(subset=["cal_date"]).drop_duplicates(subset=["exchange", "cal_date"])

        sql = """
            INSERT INTO ref_trade_cal (exchange, cal_date, is_open, pretrade_date)
            VALUES (:exchange, :cal_date, :is_open, :pretrade_date)
            ON CONFLICT (exchange, cal_date) DO UPDATE SET
                is_open = EXCLUDED.is_open,
                pretrade_date = EXCLUDED.pretrade_date
        """
        rows = [
            {
                "exchange": str(r["exchange"]),
                "cal_date": r["cal_date"],
                "is_open": int(r["is_open"]),
                "pretrade_date": r["pretrade_date"] if pd.notna(r["pretrade_date"]) else None,
            }
            for _, r in remote.iterrows()
        ]
        with self.engine.begin() as conn:
            conn.execute(sa_text(sql), rows)
        self._cal_cache = None
        logger.info("trade_cal 已刷新 %d 行", len(rows))
        return {"ok": True, "upserted": len(rows)}

    def _load_cal(self) -> pd.DataFrame:
        if self._cal_cache is not None:
            return self._cal_cache
        if not self.engine:
            return pd.DataFrame()
        try:
            df = pd.read_sql(
                sa_text(
                    "SELECT cal_date, is_open, pretrade_date FROM ref_trade_cal "
                    "WHERE exchange = 'SSE' ORDER BY cal_date"
                ),
                self.engine,
            )
            if not df.empty:
                df["cal_date"] = pd.to_datetime(df["cal_date"]).dt.date
            self._cal_cache = df
            return df
        except Exception as e:
            logger.warning("加载交易日历失败: %s", e)
            return pd.DataFrame()

    def is_trade_day(self, d: date) -> bool:
        cal = self._load_cal()
        if cal.empty:
            # 无日历时退化为周一到周五
            return d.weekday() < 5
        row = cal.loc[cal["cal_date"] == d]
        if row.empty:
            return d.weekday() < 5
        return int(row.iloc[0]["is_open"]) == 1

    def previous_trade_day(self, d: date) -> date:
        cal = self._load_cal()
        if not cal.empty:
            opened = cal.loc[(cal["is_open"] == 1) & (cal["cal_date"] < d)]
            if not opened.empty:
                return opened.iloc[-1]["cal_date"]
        # fallback
        x = d - timedelta(days=1)
        for _ in range(15):
            if x.weekday() < 5:
                return x
            x -= timedelta(days=1)
        return d - timedelta(days=1)

    def session_context(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        """
        返回当前会话上下文：
          asof: 日线允许写入/视为「最新」的截止日期
          in_trading_session: 是否盘中（此时不拉、不写当天）
        """
        now = now or datetime.now(TZ_SH)
        if now.tzinfo is None:
            now = now.replace(tzinfo=TZ_SH)
        else:
            now = now.astimezone(TZ_SH)
        today = now.date()
        t = now.timetz().replace(tzinfo=None) if False else now.time()
        trade_today = self.is_trade_day(today)
        in_session = trade_today and SESSION_START <= t < SESSION_END

        if in_session:
            asof = self.previous_trade_day(today)
            reason = "trading_session_use_prev_close"
        elif trade_today and t >= SESSION_END:
            asof = today
            reason = "after_close_include_today"
        elif trade_today and t < SESSION_START:
            asof = self.previous_trade_day(today)
            reason = "before_open_use_prev_close"
        else:
            asof = self.previous_trade_day(today)
            reason = "non_trading_day_use_prev_close"

        return {
            "now": now.isoformat(),
            "today": today.isoformat(),
            "is_trade_day": trade_today,
            "in_trading_session": in_session,
            "asof": asof.isoformat(),
            "asof_date": asof,
            "reason": reason,
        }

    # ------------------------------------------------------------------ #
    # PG 元信息
    # ------------------------------------------------------------------ #
    def get_bar_meta(self, ts_code: str, freq: str = "daily") -> Dict[str, Any]:
        table = FREQ_TABLE.get(freq, "stock_daily")
        ts_code = self.normalize_ts_code(ts_code)
        if not self.engine:
            return {"ok": False, "error": "PostgreSQL 未连接", "count": 0}
        try:
            df = pd.read_sql(
                sa_text(
                    f"SELECT MIN(trade_date) AS d0, MAX(trade_date) AS d1, COUNT(*) AS cnt "
                    f"FROM {table} WHERE ts_code = :c"
                ),
                self.engine,
                params={"c": ts_code},
            )
            row = df.iloc[0]
            cnt = int(row["cnt"] or 0)
            return {
                "ok": True,
                "ts_code": ts_code,
                "freq": freq,
                "table": table,
                "count": cnt,
                "min_date": _fmt_date(row["d0"]),
                "max_date": _fmt_date(row["d1"]),
            }
        except Exception as e:
            return {"ok": False, "error": str(e), "count": 0}

    def get_latest_bar(self, ts_code: str) -> Optional[Dict[str, Any]]:
        ts_code = self.normalize_ts_code(ts_code)
        if not self.engine:
            return None
        try:
            df = pd.read_sql(
                sa_text(
                    "SELECT ts_code, trade_date, open, high, low, close, pre_close, "
                    "change, pct_chg, vol, amount FROM stock_daily "
                    "WHERE ts_code = :c ORDER BY trade_date DESC LIMIT 1"
                ),
                self.engine,
                params={"c": ts_code},
            )
            if df.empty:
                return None
            r = df.iloc[0]
            return {
                "ts_code": str(r["ts_code"]),
                "trade_date": _fmt_date(r["trade_date"]),
                "open": _none_if_na(r["open"]),
                "high": _none_if_na(r["high"]),
                "low": _none_if_na(r["low"]),
                "close": _none_if_na(r["close"]),
                "pre_close": _none_if_na(r["pre_close"]),
                "change": _none_if_na(r["change"]),
                "pct_chg": _none_if_na(r["pct_chg"]),
                "vol": int(r["vol"]) if pd.notna(r["vol"]) else None,
                "amount": _none_if_na(r["amount"]),
                "price_type": "raw",  # 不复权；同花顺默认常为前复权
            }
        except Exception as e:
            logger.warning("get_latest_bar 失败: %s", e)
            return None

    def is_fresh(self, ts_code: str, asof: Optional[date] = None) -> bool:
        meta = self.get_bar_meta(ts_code, "daily")
        if not meta.get("count") or not meta.get("max_date"):
            return False
        if asof is None:
            asof = self.session_context()["asof_date"]
        try:
            d_x = datetime.strptime(str(meta["max_date"])[:10], "%Y-%m-%d").date()
            return d_x >= asof
        except Exception:
            return False

    def _drop_incomplete_today(self, ts_code: str, today: date) -> None:
        """盘中剔除可能不完整的「当天」日线，避免前端显示半截数据"""
        if not self.engine:
            return
        try:
            with self.engine.begin() as conn:
                for table in ("stock_daily", "stock_weekly", "stock_monthly"):
                    conn.execute(
                        sa_text(f"DELETE FROM {table} WHERE ts_code = :c AND trade_date = :d"),
                        {"c": ts_code, "d": today},
                    )
        except Exception as e:
            logger.warning("清理当日未完成 K 线失败: %s", e)

    # ------------------------------------------------------------------ #
    # 远端拉取 + 规范化
    # ------------------------------------------------------------------ #
    def fetch_remote(
        self,
        ts_code: str,
        start_date: str,
        end_date: str,
        freqs: Optional[List[str]] = None,
    ) -> Dict[str, pd.DataFrame]:
        ts_code = self.normalize_ts_code(ts_code)
        start_date = _to_ymd(start_date)
        end_date = _to_ymd(end_date)
        freqs = freqs or ["daily", "weekly", "monthly"]
        out: Dict[str, pd.DataFrame] = {}
        for api in freqs:
            try:
                df = self.client.query(
                    api,
                    ts_code=ts_code,
                    start_date=start_date,
                    end_date=end_date,
                )
                out[api] = self._normalize_bars(df, ts_code)
            except Exception as e:
                logger.error("远端 %s %s 失败: %s", api, ts_code, e)
                out[api] = pd.DataFrame(columns=BAR_COLS)
        return out

    def _normalize_bars(self, df: pd.DataFrame, ts_code: str) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame(columns=BAR_COLS)
        df = df.copy()
        df.columns = [str(c).lower() for c in df.columns]
        if "ts_code" not in df.columns:
            df["ts_code"] = ts_code
        else:
            df["ts_code"] = df["ts_code"].fillna(ts_code).astype(str).str.upper()
            # 强制统一为目标代码，避免远端偶发错码污染
            df["ts_code"] = ts_code
        if "trade_date" not in df.columns:
            return pd.DataFrame(columns=BAR_COLS)
        df["trade_date"] = pd.to_datetime(df["trade_date"].astype(str), errors="coerce").dt.date
        for col in ("open", "high", "low", "close", "pre_close", "change", "pct_chg", "amount"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            else:
                df[col] = None
        if "vol" in df.columns:
            df["vol"] = pd.to_numeric(df["vol"], errors="coerce").round().astype("Int64")
        else:
            df["vol"] = None
        df = df.dropna(subset=["trade_date"])
        keep = [c for c in BAR_COLS if c in df.columns]
        df = df[keep].drop_duplicates(subset=["ts_code", "trade_date"], keep="last")
        return df.sort_values("trade_date").reset_index(drop=True)

    def _clip_asof(self, frames: Dict[str, pd.DataFrame], asof: date) -> Dict[str, pd.DataFrame]:
        clipped = {}
        for freq, df in frames.items():
            if df is None or df.empty:
                clipped[freq] = df if df is not None else pd.DataFrame(columns=BAR_COLS)
                continue
            clipped[freq] = df.loc[df["trade_date"] <= asof].copy()
        return clipped

    def upsert_bars(self, freq: str, df: pd.DataFrame) -> int:
        table = FREQ_TABLE.get(freq)
        if not table or df is None or df.empty or not self.engine:
            return 0
        df = df.copy()
        for c in BAR_COLS:
            if c not in df.columns:
                df[c] = None
        df = df[BAR_COLS]
        sql = f"""
            INSERT INTO {table} (
                ts_code, trade_date, open, high, low, close,
                pre_close, change, pct_chg, vol, amount
            ) VALUES (
                :ts_code, :trade_date, :open, :high, :low, :close,
                :pre_close, :change, :pct_chg, :vol, :amount
            )
            ON CONFLICT (ts_code, trade_date) DO UPDATE SET
                open = EXCLUDED.open,
                high = EXCLUDED.high,
                low = EXCLUDED.low,
                close = EXCLUDED.close,
                pre_close = EXCLUDED.pre_close,
                change = EXCLUDED.change,
                pct_chg = EXCLUDED.pct_chg,
                vol = EXCLUDED.vol,
                amount = EXCLUDED.amount
        """
        rows = []
        for _, r in df.iterrows():
            rows.append(
                {
                    "ts_code": str(r["ts_code"]),
                    "trade_date": r["trade_date"],
                    "open": _none_if_na(r["open"]),
                    "high": _none_if_na(r["high"]),
                    "low": _none_if_na(r["low"]),
                    "close": _none_if_na(r["close"]),
                    "pre_close": _none_if_na(r["pre_close"]),
                    "change": _none_if_na(r["change"]),
                    "pct_chg": _none_if_na(r["pct_chg"]),
                    "vol": int(r["vol"]) if pd.notna(r["vol"]) else None,
                    "amount": _none_if_na(r["amount"]),
                }
            )
        with self.engine.begin() as conn:
            conn.execute(sa_text(sql), rows)
        logger.info("upsert %s %d 行", table, len(rows))
        return len(rows)

    def _save_frames(self, frames: Dict[str, pd.DataFrame]) -> Dict[str, int]:
        stats = {}
        for freq, df in frames.items():
            if freq in FREQ_TABLE:
                stats[freq] = self.upsert_bars(freq, df)
        return stats

    def verify_against_remote(self, ts_code: str, asof: Optional[date] = None) -> Dict[str, Any]:
        """对比 PG 最新一根与 Datahubco 同日，确认写入正确"""
        ts_code = self.normalize_ts_code(ts_code)
        ctx = self.session_context()
        asof = asof or ctx["asof_date"]
        local = self.get_latest_bar(ts_code)
        ymd = asof.strftime("%Y%m%d")
        remote = self.fetch_remote(ts_code, ymd, ymd, freqs=["daily"]).get("daily")
        remote_row = None
        if remote is not None and not remote.empty:
            hit = remote.loc[remote["trade_date"] == asof]
            if hit.empty:
                hit = remote.tail(1)
            r = hit.iloc[0]
            remote_row = {
                "trade_date": _fmt_date(r["trade_date"]),
                "close": _none_if_na(r["close"]),
                "pct_chg": _none_if_na(r["pct_chg"]),
                "vol": int(r["vol"]) if pd.notna(r["vol"]) else None,
            }
        match = False
        if local and remote_row:
            match = (
                str(local.get("trade_date"))[:10] == str(remote_row.get("trade_date"))[:10]
                and abs(float(local.get("close") or 0) - float(remote_row.get("close") or 0)) < 1e-6
            )
        return {
            "ok": True,
            "ts_code": ts_code,
            "asof": asof.isoformat(),
            "local": local,
            "remote": remote_row,
            "match": match,
            "note": "日线为不复权价；同花顺默认前复权时历史价可能不同，近期无复权时两者应接近",
        }

    # ------------------------------------------------------------------ #
    # 三种挑选模式
    # ------------------------------------------------------------------ #
    def ensure_watchlist(self, ts_code: str) -> Dict[str, Any]:
        """加自选：按 asof 拉近一季度日线 + 周/月，写入 PG，并校验最新一根"""
        ts_code = self.normalize_ts_code(ts_code)
        self.ensure_trade_cal()
        ctx = self.session_context()
        asof: date = ctx["asof_date"]
        if ctx["in_trading_session"]:
            self._drop_incomplete_today(ts_code, datetime.now(TZ_SH).date())

        ranges = {
            "daily": (asof - timedelta(days=WATCHLIST_DAILY_DAYS), asof),
            "weekly": (asof - timedelta(days=WATCHLIST_WEEKLY_DAYS), asof),
            "monthly": (asof - timedelta(days=WATCHLIST_MONTHLY_DAYS), asof),
        }
        stats: Dict[str, int] = {}
        fetched: Dict[str, int] = {}
        for freq, (s, e) in ranges.items():
            frames = self.fetch_remote(
                ts_code,
                s.strftime("%Y%m%d"),
                e.strftime("%Y%m%d"),
                freqs=[freq],
            )
            frames = self._clip_asof(frames, asof)
            fetched[freq] = int(len(frames.get(freq, pd.DataFrame())))
            stats.update(self._save_frames(frames))

        verify = self.verify_against_remote(ts_code, asof=asof)
        meta = self.get_bar_meta(ts_code, "daily")
        return {
            "ok": True,
            "mode": "watchlist",
            "ts_code": ts_code,
            "source": "datahubco",
            "session": {k: v for k, v in ctx.items() if k != "asof_date"},
            "fetched": fetched,
            "upserted": stats,
            "meta": meta,
            "latest_bar": verify.get("local"),
            "verify": verify,
        }

    def ensure_fresh(self, ts_code: str, also_week_month: bool = True) -> Dict[str, Any]:
        """看板/策略：对照 asof；已覆盖则跳过，否则续拉并校验"""
        ts_code = self.normalize_ts_code(ts_code)
        self.ensure_trade_cal()
        ctx = self.session_context()
        asof: date = ctx["asof_date"]
        if ctx["in_trading_session"]:
            self._drop_incomplete_today(ts_code, datetime.now(TZ_SH).date())

        meta = self.get_bar_meta(ts_code, "daily")
        if meta.get("count") and self.is_fresh(ts_code, asof=asof):
            verify = self.verify_against_remote(ts_code, asof=asof)
            # 即使日期够新，若与远端不一致也强制重拉
            if verify.get("match"):
                return {
                    "ok": True,
                    "mode": "fresh",
                    "ts_code": ts_code,
                    "skipped": True,
                    "reason": "already_fresh",
                    "session": {k: v for k, v in ctx.items() if k != "asof_date"},
                    "meta": meta,
                    "latest_bar": verify.get("local"),
                    "verify": verify,
                }

        if meta.get("count") and meta.get("max_date"):
            start = datetime.strptime(str(meta["max_date"])[:10], "%Y-%m-%d").date() - timedelta(days=5)
        else:
            start = asof - timedelta(days=WATCHLIST_DAILY_DAYS)

        freqs = ["daily"]
        if also_week_month:
            freqs.extend(["weekly", "monthly"])
        frames = self.fetch_remote(
            ts_code,
            start.strftime("%Y%m%d"),
            asof.strftime("%Y%m%d"),
            freqs=freqs,
        )
        frames = self._clip_asof(frames, asof)
        stats = self._save_frames(frames)
        verify = self.verify_against_remote(ts_code, asof=asof)
        return {
            "ok": True,
            "mode": "fresh",
            "ts_code": ts_code,
            "skipped": False,
            "session": {k: v for k, v in ctx.items() if k != "asof_date"},
            "range": {"start": start.isoformat(), "end": asof.isoformat()},
            "fetched": {k: int(len(v)) for k, v in frames.items()},
            "upserted": stats,
            "meta": self.get_bar_meta(ts_code, "daily"),
            "latest_bar": verify.get("local"),
            "verify": verify,
        }

    def extend_daily(
        self,
        ts_code: str,
        before_date: Optional[str] = None,
        days: int = 60,
    ) -> Dict[str, Any]:
        ts_code = self.normalize_ts_code(ts_code)
        self.ensure_trade_cal()
        ctx = self.session_context()
        asof: date = ctx["asof_date"]
        meta = self.get_bar_meta(ts_code, "daily")
        if before_date:
            end = datetime.strptime(_fmt_date(before_date), "%Y-%m-%d").date()
        elif meta.get("min_date"):
            end = datetime.strptime(str(meta["min_date"])[:10], "%Y-%m-%d").date()
        else:
            return self.ensure_watchlist(ts_code)

        end = min(end, asof)
        start = end - timedelta(days=days)
        frames = self.fetch_remote(
            ts_code,
            start.strftime("%Y%m%d"),
            end.strftime("%Y%m%d"),
            freqs=["daily", "weekly", "monthly"],
        )
        frames = self._clip_asof(frames, asof)
        stats = self._save_frames(frames)
        return {
            "ok": True,
            "mode": "extend",
            "ts_code": ts_code,
            "session": {k: v for k, v in ctx.items() if k != "asof_date"},
            "range": {"start": start.isoformat(), "end": end.isoformat()},
            "fetched": {k: int(len(v)) for k, v in frames.items()},
            "upserted": stats,
            "meta": self.get_bar_meta(ts_code, "daily"),
            "latest_bar": self.get_latest_bar(ts_code),
        }

    def ensure(
        self,
        ts_code: str,
        mode: str = "fresh",
        before_date: Optional[str] = None,
        extend_days: int = 60,
    ) -> Dict[str, Any]:
        mode = (mode or "fresh").strip().lower()
        try:
            if mode == "watchlist":
                return self.ensure_watchlist(ts_code)
            if mode == "extend":
                return self.extend_daily(ts_code, before_date=before_date, days=extend_days)
            return self.ensure_fresh(ts_code)
        except Exception as e:
            logger.exception("ensure 失败 %s mode=%s", ts_code, mode)
            return {"ok": False, "error": str(e), "ts_code": ts_code, "mode": mode}


def _fmt_date(v) -> Optional[str]:
    if v is None or (isinstance(v, float) and pd.isna(v)) or pd.isna(v):
        return None
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, date):
        return v.isoformat()
    s = str(v)
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s[:10]


def _to_ymd(v: str) -> str:
    return str(v).replace("-", "")[:8]


def _none_if_na(v):
    if v is None or (isinstance(v, float) and pd.isna(v)) or pd.isna(v):
        return None
    if hasattr(v, "item"):
        try:
            return v.item()
        except Exception:
            pass
    return v


selective_fetcher = SelectiveDataFetcher()

"""
Datahubco 挑选式采集：先查 PostgreSQL，缺/旧则拉远端并 upsert。

交易日规则（上海时区）:
  - 交易日 + 交易时段 (09:15–15:00)：只取到「上一交易日」，不拉当天
  - 交易日收盘后 / 非交易日：取到最近已收盘交易日（可含当天若已收盘）

模式:
  - watchlist: 加自选 → 最近一季度日线 + 周线 + 月线
  - fresh:     看板/策略 → 对照「应有最新日」检查，不足则补
  - extend:    滑动更早区间 → 向前续日线
  - range:     策略分析 → 对照交易日历检查 [start, end] 全日，缺口则补
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple
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

CORE_INDEX_CODES = {
    "000001.SH",
    "000016.SH",
    "000300.SH",
    "000688.SH",
    "000905.SH",
    "000852.SH",
    "399001.SZ",
    "399005.SZ",
    "399006.SZ",
    "399673.SZ",
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
    def ensure_trade_cal(
        self,
        years_ahead: int = 1,
        cover_from: Optional[date] = None,
    ) -> Dict[str, Any]:
        """日历过期或未覆盖目标起点时，从 Datahubco 刷新 SSE 交易日历"""
        if not self.engine:
            return {"ok": False, "error": "no engine"}
        today = datetime.now(TZ_SH).date()
        need_end = today + timedelta(days=365 * years_ahead)
        need_start = cover_from or date(2020, 1, 1)
        try:
            df = pd.read_sql(
                sa_text(
                    "SELECT MIN(cal_date) AS d0, MAX(cal_date) AS d1 "
                    "FROM ref_trade_cal WHERE exchange = 'SSE'"
                ),
                self.engine,
            )
            min_d = _as_date(df.iloc[0]["d0"]) if not df.empty else None
            max_d = _as_date(df.iloc[0]["d1"]) if not df.empty else None
        except Exception as e:
            min_d = None
            max_d = None
            logger.warning("读 trade_cal 失败: %s", e)

        if min_d and max_d and min_d <= need_start and max_d >= need_end:
            self._cal_cache = None
            return {
                "ok": True,
                "skipped": True,
                "min_date": min_d.isoformat(),
                "max_date": max_d.isoformat(),
            }

        start = need_start.strftime("%Y%m%d")
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
            return {
                "ok": False,
                "error": str(e),
                "min_date": min_d.isoformat() if min_d else None,
                "max_date": max_d.isoformat() if max_d else None,
            }

        if remote is None or remote.empty:
            return {
                "ok": False,
                "error": "empty trade_cal",
                "min_date": min_d.isoformat() if min_d else None,
                "max_date": max_d.isoformat() if max_d else None,
            }

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

    def upsert_bars(self, freq: str, df: pd.DataFrame, table: Optional[str] = None) -> int:
        table = table or FREQ_TABLE.get(freq)
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

    def _fill_today_eod_if_needed(
        self,
        ts_code: str,
        asof: date,
        ctx: Optional[Dict[str, Any]] = None,
    ) -> int:
        """收盘后若 Datahubco 还没有当天日线，用腾讯快照补一根。"""
        ctx = ctx or self.session_context()
        if ctx.get("in_trading_session"):
            return 0
        latest = self.get_latest_bar(ts_code)
        latest_d = None
        if latest and latest.get("trade_date"):
            try:
                latest_d = datetime.strptime(str(latest["trade_date"])[:10], "%Y-%m-%d").date()
            except (TypeError, ValueError):
                latest_d = None
        if latest_d and latest_d >= asof:
            return 0
        from .quotes import fetch_tencent_eod_on

        df = fetch_tencent_eod_on([ts_code], asof)
        if df is None or df.empty:
            logger.warning("腾讯收盘快照无 %s %s", ts_code, asof)
            return 0
        table, _ = self._daily_table_api(ts_code)
        n = self.upsert_bars("daily", df, table=table)
        logger.info("腾讯补日线 %s %s → %s %d 行", ts_code, asof, table, n)
        return n

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

        tencent_n = self._fill_today_eod_if_needed(ts_code, asof, ctx)
        if tencent_n:
            stats["daily"] = int(stats.get("daily") or 0) + tencent_n
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
        tencent_n = self._fill_today_eod_if_needed(ts_code, asof, ctx)
        if tencent_n:
            stats["daily"] = int(stats.get("daily") or 0) + tencent_n
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

    # ------------------------------------------------------------------ #
    # 策略区间：对照交易日历检查 [start, end]，缺口则 Datahubco 补入 PG
    # ------------------------------------------------------------------ #
    def _is_index_code(self, ts_code: str) -> bool:
        code = (ts_code or "").upper()
        if code in CORE_INDEX_CODES or code.startswith("399"):
            return True
        # 上证指数段 000xxx.SH；个股主板为 6xxxxx.SH
        num, _, mkt = code.partition(".")
        if mkt == "SH" and len(num) == 6 and num.startswith("000"):
            return True
        if not self.engine:
            return False
        try:
            df = pd.read_sql(
                sa_text("SELECT 1 FROM ref_index_basic WHERE ts_code = :c LIMIT 1"),
                self.engine,
                params={"c": code},
            )
            return not df.empty
        except Exception:
            return False

    def _is_etf_code(self, ts_code: str) -> bool:
        code = (ts_code or "").upper()
        num, _, mkt = code.partition(".")
        if mkt == "SH" and num.startswith(("51", "56", "58")):
            return True
        if mkt == "SZ" and num.startswith(("15", "16")):
            return True
        if not self.engine:
            return False
        try:
            df = pd.read_sql(
                sa_text("SELECT 1 FROM ref_etf_basic WHERE ts_code = :c LIMIT 1"),
                self.engine,
                params={"c": code},
            )
            return not df.empty
        except Exception:
            return False

    def _instrument_kind(self, ts_code: str) -> str:
        if self._is_index_code(ts_code):
            return "index"
        if self._is_etf_code(ts_code):
            return "etf"
        return "stock"

    def _daily_table_api(self, ts_code: str) -> Tuple[str, str]:
        kind = self._instrument_kind(ts_code)
        if kind == "index":
            return "index_daily", "index_daily"
        if kind == "etf":
            return "etf_daily", "fund_daily"
        return "stock_daily", "daily"

    def _stock_list_date(self, ts_code: str) -> Optional[date]:
        if not self.engine:
            return None
        try:
            df = pd.read_sql(
                sa_text("SELECT list_date FROM ref_stock_basic WHERE ts_code = :c LIMIT 1"),
                self.engine,
                params={"c": ts_code},
            )
            if df.empty:
                return None
            return _as_date(df.iloc[0]["list_date"])
        except Exception:
            return None

    def _expected_open_dates(self, start: date, end: date) -> List[date]:
        cal = self._load_cal()
        if cal.empty:
            days = []
            d = start
            while d <= end:
                if d.weekday() < 5:
                    days.append(d)
                d += timedelta(days=1)
            return days
        opened = cal.loc[
            (cal["is_open"] == 1) & (cal["cal_date"] >= start) & (cal["cal_date"] <= end)
        ]
        return [d for d in opened["cal_date"].tolist()]

    def _existing_trade_dates(
        self,
        table: str,
        ts_code: str,
        start: date,
        end: date,
    ) -> List[date]:
        if not self.engine:
            return []
        allowed = {"stock_daily", "index_daily", "etf_daily", "fund_daily"}
        if table not in allowed:
            return []
        try:
            df = pd.read_sql(
                sa_text(
                    f"SELECT trade_date FROM {table} "
                    "WHERE ts_code = :c AND trade_date BETWEEN :s AND :e"
                ),
                self.engine,
                params={"c": ts_code, "s": start, "e": end},
            )
            if df.empty:
                return []
            return [_as_date(v) for v in df["trade_date"].tolist() if _as_date(v)]
        except Exception as e:
            logger.warning("读 %s 覆盖失败 %s: %s", table, ts_code, e)
            return []

    @staticmethod
    def _iter_year_chunks(start: date, end: date):
        year = start.year
        while True:
            chunk_start = max(start, date(year, 1, 1))
            chunk_end = min(end, date(year, 12, 31))
            if chunk_start <= chunk_end:
                yield chunk_start, chunk_end
            if chunk_end >= end:
                break
            year += 1

    def _fetch_daily_chunks(
        self,
        ts_code: str,
        api: str,
        start: date,
        end: date,
    ) -> pd.DataFrame:
        frames: List[pd.DataFrame] = []
        for cs, ce in self._iter_year_chunks(start, end):
            try:
                raw = self.client.query(
                    api,
                    ts_code=ts_code,
                    start_date=cs.strftime("%Y%m%d"),
                    end_date=ce.strftime("%Y%m%d"),
                )
                frames.append(self._normalize_bars(raw, ts_code))
            except Exception as e:
                logger.error("远端 %s %s %s~%s 失败: %s", api, ts_code, cs, ce, e)
                frames.append(pd.DataFrame(columns=BAR_COLS))
        nonempty = [f for f in frames if f is not None and not f.empty]
        if not nonempty:
            return pd.DataFrame(columns=BAR_COLS)
        out = pd.concat(nonempty, ignore_index=True)
        return (
            out.drop_duplicates(subset=["ts_code", "trade_date"], keep="last")
            .sort_values("trade_date")
            .reset_index(drop=True)
        )

    def _coverage_payload(
        self,
        ts_code: str,
        table: str,
        start: date,
        end: date,
        list_date: Optional[date] = None,
    ) -> Dict[str, Any]:
        exp_start = start
        if list_date and list_date > exp_start:
            exp_start = list_date
        expected = self._expected_open_dates(exp_start, end)
        existing = self._existing_trade_dates(table, ts_code, exp_start, end)
        missing = sorted(set(expected) - set(existing))
        return {
            "expected": len(expected),
            "existing": len(existing),
            "missing": len(missing),
            "missing_dates": [d.isoformat() for d in missing],
        }

    def ensure_range(
        self,
        ts_code: str,
        start: str,
        end: str,
        extra_codes: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        """
        检查 [start, end] 每个交易日是否已在 PostgreSQL；
        缺口则按标的类型从 Datahubco（Tushare 兼容）拉取并 upsert。
        """
        ts_code = self.normalize_ts_code(ts_code)
        if not ts_code:
            return {"ok": False, "error": "请填写标的代码"}
        start_d = _as_date(start)
        end_d = _as_date(end)
        if not start_d or not end_d:
            return {"ok": False, "error": "开始/结束日期无效", "ts_code": ts_code}
        if start_d > end_d:
            start_d, end_d = end_d, start_d

        try:
            primary = self._ensure_range_one(ts_code, start_d, end_d)
        except Exception as e:
            logger.exception("ensure_range 失败 %s", ts_code)
            return {"ok": False, "error": str(e), "ts_code": ts_code, "mode": "range"}

        extras: List[Dict[str, Any]] = []
        seen = {ts_code}
        for raw in extra_codes or []:
            code = self.normalize_ts_code(str(raw or ""))
            if not code or code in seen:
                continue
            seen.add(code)
            try:
                extras.append(self._ensure_range_one(code, start_d, end_d))
            except Exception as e:
                logger.exception("ensure_range extra 失败 %s", code)
                extras.append({"ok": False, "error": str(e), "ts_code": code, "mode": "range"})
        if extras:
            primary["extras"] = extras
        return primary

    def _ensure_range_one(self, ts_code: str, start: date, end: date) -> Dict[str, Any]:
        if not self.engine:
            return {"ok": False, "error": "PostgreSQL 未连接", "ts_code": ts_code, "mode": "range"}

        self.ensure_trade_cal(cover_from=start)
        ctx = self.session_context()
        asof: date = ctx["asof_date"]
        clip_end = min(end, asof)
        if start > clip_end:
            return {
                "ok": False,
                "error": f"开始日 {start.isoformat()} 晚于可写入日 {clip_end.isoformat()}（盘中不含当天）",
                "ts_code": ts_code,
                "mode": "range",
                "session": {k: v for k, v in ctx.items() if k != "asof_date"},
            }

        kind = self._instrument_kind(ts_code)
        table, api = self._daily_table_api(ts_code)
        list_date = self._stock_list_date(ts_code) if kind == "stock" else None
        before = self._coverage_payload(ts_code, table, start, clip_end, list_date)

        fetched: Dict[str, int] = {}
        stats: Dict[str, int] = {}
        skipped = before["missing"] == 0
        errors: List[str] = []

        if not skipped:
            daily_df = self._fetch_daily_chunks(ts_code, api, start, clip_end)
            daily_df = daily_df.loc[daily_df["trade_date"] <= clip_end] if not daily_df.empty else daily_df
            fetched["daily"] = int(len(daily_df))
            try:
                stats["daily"] = self.upsert_bars("daily", daily_df, table=table)
            except Exception as e:
                logger.exception("upsert %s 失败 %s", table, ts_code)
                errors.append(str(e))
                stats["daily"] = 0

            if kind == "stock":
                wm = self.fetch_remote(
                    ts_code,
                    start.strftime("%Y%m%d"),
                    clip_end.strftime("%Y%m%d"),
                    freqs=["weekly", "monthly"],
                )
                wm = self._clip_asof(wm, clip_end)
                fetched.update({k: int(len(v)) for k, v in wm.items()})
                stats.update(self._save_frames(wm))

        after = self._coverage_payload(ts_code, table, start, clip_end, list_date)
        if (
            not ctx.get("in_trading_session")
            and asof.isoformat() in (after.get("missing_dates") or [])
        ):
            tencent_n = self._fill_today_eod_if_needed(ts_code, asof, ctx)
            if tencent_n:
                stats["daily"] = int(stats.get("daily") or 0) + tencent_n
                after = self._coverage_payload(ts_code, table, start, clip_end, list_date)
        still = after.get("missing_dates") or []
        note = None
        if skipped:
            note = "所选区间交易日已全部在库中"
        elif still:
            note = "补拉后仍有缺口，多为停牌或未上市日"

        return {
            "ok": True,
            "mode": "range",
            "ts_code": ts_code,
            "kind": kind,
            "table": table,
            "api": api,
            "source": "datahubco",
            "session": {k: v for k, v in ctx.items() if k != "asof_date"},
            "range": {
                "start": start.isoformat(),
                "end": clip_end.isoformat(),
                "requested_end": end.isoformat(),
                "asof": asof.isoformat(),
            },
            "list_date": list_date.isoformat() if list_date else None,
            "expected": before["expected"],
            "existing_before": before["existing"],
            "missing_before": before["missing"],
            "missing_sample": (before.get("missing_dates") or [])[:20],
            "fetched": fetched,
            "upserted": stats,
            "existing_after": after["existing"],
            "still_missing": after["missing"],
            "still_missing_sample": still[:20],
            "skipped": skipped,
            "note": note,
            "errors": errors,
            "meta": {
                "ok": True,
                "ts_code": ts_code,
                "table": table,
                "count": after["existing"],
            },
        }

    def ensure_recent(self, ts_code: str, trading_days: int = 30) -> Dict[str, Any]:
        """确保最近 N 个交易日在库中（搜索默认 30 日）"""
        ts_code = self.normalize_ts_code(ts_code)
        if not ts_code:
            return {"ok": False, "error": "请填写标的代码"}
        trading_days = max(5, min(int(trading_days or 30), 400))
        self.ensure_trade_cal()
        ctx = self.session_context()
        asof: date = ctx["asof_date"]
        days = self._expected_open_dates(asof - timedelta(days=trading_days * 3), asof)
        if len(days) >= trading_days:
            start = days[-trading_days]
        else:
            start = asof - timedelta(days=max(45, trading_days * 2))
        result = self.ensure_range(ts_code, start.isoformat(), asof.isoformat())
        if isinstance(result, dict):
            result["mode"] = "recent"
            result["trading_days"] = trading_days
        return result

    # ------------------------------------------------------------------ #
    # 看板自选股：登记后持续日更，移出即停止
    # ------------------------------------------------------------------ #
    def _ensure_watchlist_table(self) -> None:
        if not self.engine:
            return
        sql = """
            CREATE TABLE IF NOT EXISTS dash_watchlist (
                ts_code         VARCHAR(16)   PRIMARY KEY,
                name            VARCHAR(64),
                short_name      VARCHAR(64),
                kind            VARCHAR(16)   DEFAULT 'stock',
                industry        VARCHAR(64),
                continuous      BOOLEAN       DEFAULT TRUE,
                created_at      TIMESTAMPTZ   DEFAULT NOW(),
                last_synced_at  TIMESTAMPTZ,
                last_sync_ok    BOOLEAN,
                last_sync_error TEXT
            )
        """
        with self.engine.begin() as conn:
            conn.execute(sa_text(sql))

    def watchlist_add(
        self,
        ts_code: str,
        name: Optional[str] = None,
        short_name: Optional[str] = None,
        kind: str = "stock",
        industry: Optional[str] = None,
    ) -> Dict[str, Any]:
        ts_code = self.normalize_ts_code(ts_code)
        if not ts_code:
            return {"ok": False, "error": "请填写标的代码"}
        if not self.engine:
            return {"ok": False, "error": "PostgreSQL 未连接"}
        self._ensure_watchlist_table()
        kind = (kind or "stock").strip().lower()
        sql = """
            INSERT INTO dash_watchlist (
                ts_code, name, short_name, kind, industry, continuous, created_at
            ) VALUES (
                :ts_code, :name, :short_name, :kind, :industry, TRUE, NOW()
            )
            ON CONFLICT (ts_code) DO UPDATE SET
                name = EXCLUDED.name,
                short_name = EXCLUDED.short_name,
                kind = EXCLUDED.kind,
                industry = EXCLUDED.industry,
                continuous = TRUE
        """
        with self.engine.begin() as conn:
            conn.execute(
                sa_text(sql),
                {
                    "ts_code": ts_code,
                    "name": name or ts_code,
                    "short_name": short_name or name or ts_code,
                    "kind": kind,
                    "industry": industry or "",
                },
            )
        return {"ok": True, "ts_code": ts_code, "kind": kind, "continuous": True}

    def watchlist_remove(self, ts_code: str) -> Dict[str, Any]:
        ts_code = self.normalize_ts_code(ts_code)
        if not ts_code:
            return {"ok": False, "error": "请填写标的代码"}
        if not self.engine:
            return {"ok": False, "error": "PostgreSQL 未连接"}
        self._ensure_watchlist_table()
        with self.engine.begin() as conn:
            result = conn.execute(
                sa_text("DELETE FROM dash_watchlist WHERE ts_code = :c"),
                {"c": ts_code},
            )
            deleted = result.rowcount or 0
        return {"ok": True, "ts_code": ts_code, "deleted": int(deleted), "continuous": False}

    def watchlist_has(self, ts_code: str) -> bool:
        ts_code = self.normalize_ts_code(ts_code)
        if not ts_code or not self.engine:
            return False
        try:
            self._ensure_watchlist_table()
            df = pd.read_sql(
                sa_text("SELECT 1 FROM dash_watchlist WHERE ts_code = :c LIMIT 1"),
                self.engine,
                params={"c": ts_code},
            )
            return not df.empty
        except Exception:
            return False

    def watchlist_list(self) -> List[Dict[str, Any]]:
        if not self.engine:
            return []
        self._ensure_watchlist_table()
        try:
            df = pd.read_sql(
                sa_text(
                    "SELECT ts_code, name, short_name, kind, industry, continuous, "
                    "created_at, last_synced_at, last_sync_ok, last_sync_error "
                    "FROM dash_watchlist ORDER BY created_at DESC"
                ),
                self.engine,
            )
        except Exception as e:
            logger.warning("读取 dash_watchlist 失败: %s", e)
            return []
        rows: List[Dict[str, Any]] = []
        for _, r in df.iterrows():
            rows.append(
                {
                    "ts_code": str(r["ts_code"]),
                    "name": r["name"] if pd.notna(r["name"]) else str(r["ts_code"]),
                    "short_name": r["short_name"] if pd.notna(r["short_name"]) else (r["name"] if pd.notna(r["name"]) else str(r["ts_code"])),
                    "kind": r["kind"] if pd.notna(r["kind"]) else "stock",
                    "industry": r["industry"] if pd.notna(r["industry"]) else "",
                    "continuous": bool(r["continuous"]) if pd.notna(r["continuous"]) else True,
                    "created_at": str(r["created_at"]) if pd.notna(r["created_at"]) else None,
                    "last_synced_at": str(r["last_synced_at"]) if pd.notna(r["last_synced_at"]) else None,
                    "last_sync_ok": bool(r["last_sync_ok"]) if pd.notna(r["last_sync_ok"]) else None,
                    "last_sync_error": r["last_sync_error"] if pd.notna(r.get("last_sync_error")) else None,
                }
            )
        return rows

    def watchlist_mark_sync(self, ts_code: str, ok: bool, error: Optional[str] = None) -> None:
        if not self.engine:
            return
        try:
            with self.engine.begin() as conn:
                conn.execute(
                    sa_text(
                        "UPDATE dash_watchlist SET last_synced_at = NOW(), "
                        "last_sync_ok = :ok, last_sync_error = :err WHERE ts_code = :c"
                    ),
                    {"ok": bool(ok), "err": (error or None), "c": ts_code},
                )
        except Exception as e:
            logger.warning("更新自选同步状态失败 %s: %s", ts_code, e)

    def sync_watchlist_continuous(self) -> Dict[str, Any]:
        """对仍在自选中的股票做每日补数；仅交易时段运行，已移除的不会出现在名单中。"""
        self.ensure_trade_cal()
        ctx = self.session_context()
        if not ctx.get("in_trading_session"):
            return {
                "ok": True,
                "skipped": True,
                "reason": ctx.get("reason") or "not_in_session",
                "asof": ctx.get("asof"),
                "in_trading_session": False,
                "count": 0,
                "synced": [],
            }
        items = self.watchlist_list()
        synced: List[Dict[str, Any]] = []
        skipped: List[str] = []
        for item in items:
            code = item.get("ts_code") or ""
            if not self.watchlist_has(code):
                skipped.append(code)
                continue
            if (item.get("kind") or "stock") != "stock" or not item.get("continuous", True):
                skipped.append(code)
                continue
            try:
                result = self.ensure_fresh(code, also_week_month=False)
                self.watchlist_mark_sync(code, bool(result.get("ok")), result.get("error"))
                synced.append(
                    {
                        "ts_code": code,
                        "ok": result.get("ok"),
                        "skipped": result.get("skipped"),
                        "upserted": result.get("upserted"),
                    }
                )
            except Exception as e:
                logger.exception("自选持续同步失败 %s", code)
                self.watchlist_mark_sync(code, False, str(e))
                synced.append({"ts_code": code, "ok": False, "error": str(e)})
        return {
            "ok": True,
            "count": len(items),
            "synced": synced,
            "skipped": skipped,
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


def _as_date(v) -> Optional[date]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        if isinstance(v, float) and pd.isna(v):
            return None
        if pd.isna(v):
            return None
    except Exception:
        pass
    s = str(v).strip()
    if not s or s.lower() in ("none", "nat", "nan"):
        return None
    if len(s) == 8 and s.isdigit():
        return datetime.strptime(s, "%Y%m%d").date()
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except Exception:
        return None


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

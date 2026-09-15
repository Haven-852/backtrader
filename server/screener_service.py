"""
策略选股：前一交易日收盘低于 N 日均线。
缺数则从 Datahubco（Tushare 兼容）拉取日线写入 stock_daily，命中结果写入 strategy_screener_*。
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd
from sqlalchemy import text as sa_text

from data_layer.collectors.datahubco.client import DatahubcoClient
from data_layer.collectors.datahubco.selective import (
    BAR_COLS,
    SelectiveDataFetcher,
    _none_if_na,
    selective_fetcher,
)
from data_layer.db_manager import storage_manager

logger = logging.getLogger(__name__)

BATCH_CODES = 20
SLEEP = 0.16


class ScreenerService:
    def __init__(self):
        self._engine = None
        self._client = None
        self._selective: Optional[SelectiveDataFetcher] = None

    @property
    def engine(self):
        if self._engine is None:
            self._engine = storage_manager.get_postgres_engine()
        return self._engine

    @property
    def client(self) -> DatahubcoClient:
        if self._client is None:
            self._client = DatahubcoClient()
        return self._client

    @property
    def selective(self) -> SelectiveDataFetcher:
        if self._selective is None:
            self._selective = selective_fetcher
        return self._selective

    def _ensure_tables(self) -> None:
        if not self.engine:
            raise RuntimeError("PostgreSQL 未连接")
        ddl = [
            """
            CREATE TABLE IF NOT EXISTS strategy_screener_run (
                id           SERIAL PRIMARY KEY,
                asof         DATE NOT NULL,
                ma_period    INT DEFAULT 5,
                below_pct    NUMERIC(8,4) DEFAULT 0,
                filters      JSONB,
                hit_count    INT,
                filled_rows  INT,
                created_at   TIMESTAMPTZ DEFAULT NOW()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS strategy_screener_hit (
                run_id       INT NOT NULL,
                ts_code      VARCHAR(16) NOT NULL,
                name         VARCHAR(64),
                market       VARCHAR(16),
                industry     VARCHAR(50),
                is_st        BOOLEAN,
                trade_date   DATE,
                close        NUMERIC(12,4),
                ma5          NUMERIC(12,4),
                below_pct    NUMERIC(12,4),
                PRIMARY KEY (run_id, ts_code)
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_screener_hit_asof ON strategy_screener_hit (trade_date, below_pct DESC)",
            "CREATE INDEX IF NOT EXISTS idx_stock_daily_date ON stock_daily (trade_date)",
        ]
        with self.engine.begin() as conn:
            for sql in ddl:
                conn.execute(sa_text(sql))

    def _ensure_stock_basic(self) -> int:
        if not self.engine:
            return 0
        try:
            df = pd.read_sql(sa_text("SELECT COUNT(*) AS n FROM ref_stock_basic"), self.engine)
            n = int(df.iloc[0]["n"] or 0)
            if n > 100:
                return n
        except Exception:
            n = 0
        frames = []
        for ex in ("SSE", "SZSE", "BSE"):
            try:
                raw = self.client.query("stock_basic", exchange=ex, list_status="L")
            except Exception as e:
                logger.warning("stock_basic %s 失败: %s", ex, e)
                continue
            if raw is None or raw.empty:
                continue
            raw = raw.copy()
            raw.columns = [str(c).lower() for c in raw.columns]
            raw["list_status"] = raw.get("list_status", "L")
            frames.append(raw)
            time.sleep(SLEEP)
        if not frames:
            return n
        all_df = pd.concat(frames, ignore_index=True)
        sql = """
            INSERT INTO ref_stock_basic (
                ts_code, symbol, name, area, industry, cnspell, market,
                list_date, list_status
            ) VALUES (
                :ts_code, :symbol, :name, :area, :industry, :cnspell, :market,
                :list_date, :list_status
            )
            ON CONFLICT (ts_code) DO UPDATE SET
                name = EXCLUDED.name,
                market = COALESCE(EXCLUDED.market, ref_stock_basic.market),
                industry = COALESCE(EXCLUDED.industry, ref_stock_basic.industry),
                list_status = EXCLUDED.list_status
        """
        rows = []
        for _, r in all_df.iterrows():
            code = str(r.get("ts_code") or "").upper()
            if not code:
                continue
            list_date = r.get("list_date")
            if pd.notna(list_date):
                list_date = pd.to_datetime(str(list_date), errors="coerce")
                list_date = list_date.date() if pd.notna(list_date) else None
            else:
                list_date = None
            rows.append(
                {
                    "ts_code": code,
                    "symbol": str(r.get("symbol") or "")[:10],
                    "name": r.get("name"),
                    "area": r.get("area"),
                    "industry": r.get("industry"),
                    "cnspell": r.get("cnspell"),
                    "market": r.get("market"),
                    "list_date": list_date,
                    "list_status": str(r.get("list_status") or "L")[:1],
                }
            )
        if rows:
            with self.engine.begin() as conn:
                conn.execute(sa_text(sql), rows)
        return len(rows)

    def _universe_codes(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not self.engine:
            return []
        df = pd.read_sql(
            sa_text(
                "SELECT ts_code, name, market, industry, list_status "
                "FROM ref_stock_basic WHERE COALESCE(list_status, 'L') = 'L'"
            ),
            self.engine,
        )
        if df.empty:
            return []
        rows = []
        for _, r in df.iterrows():
            code = str(r["ts_code"]).upper()
            name = str(r["name"] or "")
            market = str(r["market"] or "")
            inferred = self._infer_market(code, market)
            is_st = "ST" in name.upper() or "ST" in name
            if not filters.get("include_st", False) and is_st:
                continue
            if inferred == "主板" and not filters.get("include_main", True):
                continue
            if inferred == "科创板" and not filters.get("include_star", True):
                continue
            if inferred == "创业板" and not filters.get("include_chinext", True):
                continue
            if inferred == "北交所" and not filters.get("include_bse", False):
                continue
            if inferred == "CDR" and not filters.get("include_cdr", False):
                continue
            if inferred not in ("主板", "科创板", "创业板", "北交所", "CDR"):
                if not filters.get("include_main", True):
                    continue
            rows.append(
                {
                    "ts_code": code,
                    "name": name,
                    "market": inferred,
                    "industry": r["industry"] if pd.notna(r["industry"]) else "",
                    "is_st": is_st,
                }
            )
        return rows

    @staticmethod
    def _infer_market(ts_code: str, market: str) -> str:
        m = (market or "").strip()
        if m in ("主板", "科创板", "创业板", "北交所", "CDR"):
            return m
        code, _, exch = ts_code.partition(".")
        if exch == "BJ" or code.startswith(("8", "4")) and exch == "BJ":
            return "北交所"
        if code.startswith("688"):
            return "科创板"
        if code.startswith("300") or code.startswith("301"):
            return "创业板"
        if code.startswith("8") and exch == "SH":
            return "CDR"
        return m or "主板"

    def _session_days(self, asof: date, n: int) -> List[date]:
        self.selective.ensure_trade_cal(cover_from=asof - timedelta(days=n * 3))
        days = self.selective._expected_open_dates(asof - timedelta(days=n * 3), asof)
        if len(days) >= n:
            return days[-n:]
        return days

    def _day_count(self, d: date) -> int:
        df = pd.read_sql(
            sa_text("SELECT COUNT(*) AS n FROM stock_daily WHERE trade_date = :d"),
            self.engine,
            params={"d": d},
        )
        return int(df.iloc[0]["n"] or 0)

    def _normalize_daily(self, df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame(columns=BAR_COLS)
        df = df.copy()
        df.columns = [str(c).lower() for c in df.columns]
        if "ts_code" not in df.columns or "trade_date" not in df.columns:
            return pd.DataFrame(columns=BAR_COLS)
        df["ts_code"] = df["ts_code"].astype(str).str.upper()
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
        df = df.dropna(subset=["ts_code", "trade_date"])
        keep = [c for c in BAR_COLS if c in df.columns]
        return df[keep].drop_duplicates(subset=["ts_code", "trade_date"], keep="last")

    def _fill_window(self, days: Sequence[date], codes: Sequence[str]) -> int:
        if not days or not codes:
            return 0
        start, end = days[0], days[-1]
        filled = 0
        used_batch = False
        for d in days:
            existing = self._day_count(d)
            if existing >= max(80, int(len(codes) * 0.4)):
                logger.info("screener %s 已有 %d 行，跳过拉取", d, existing)
                continue
            ymd = d.strftime("%Y%m%d")
            if not used_batch:
                try:
                    raw = self.client.query("daily", trade_date=ymd)
                    nd = self._normalize_daily(raw)
                    if not nd.empty:
                        filled += self.selective.upsert_bars("daily", nd)
                        time.sleep(SLEEP)
                        continue
                except Exception as e:
                    logger.warning("全市场 daily(%s) 失败，改分批: %s", ymd, e)
                    used_batch = True
            else:
                break
        if not used_batch:
            # 再检查窗口内覆盖不足的代码
            missing = self._missing_codes(start, end, len(days), codes)
            if not missing:
                return filled
            used_batch = True
            codes = missing

        start_s, end_s = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
        code_list = list(codes)
        n_batches = (len(code_list) + BATCH_CODES - 1) // BATCH_CODES
        for i in range(0, len(code_list), BATCH_CODES):
            chunk = code_list[i : i + BATCH_CODES]
            bno = i // BATCH_CODES + 1
            try:
                raw = self.client.query(
                    "daily",
                    ts_code=",".join(chunk),
                    start_date=start_s,
                    end_date=end_s,
                )
                nd = self._normalize_daily(raw)
                if not nd.empty:
                    filled += self.selective.upsert_bars("daily", nd)
            except Exception as e:
                logger.warning("分批 daily %s/%s 失败: %s", bno, n_batches, e)
            if bno % 20 == 0 or bno == n_batches:
                logger.info("screener 分批进度 %s/%s 累计写入 %d", bno, n_batches, filled)
            time.sleep(SLEEP)
        return filled

    def _missing_codes(
        self, start: date, end: date, need_days: int, codes: Sequence[str]
    ) -> List[str]:
        if not codes:
            return []
        df = pd.read_sql(
            sa_text(
                "SELECT ts_code, COUNT(*) AS n FROM stock_daily "
                "WHERE trade_date BETWEEN :s AND :e "
                "GROUP BY ts_code"
            ),
            self.engine,
            params={"s": start, "e": end},
        )
        have = {str(r["ts_code"]): int(r["n"]) for _, r in df.iterrows()} if not df.empty else {}
        return [c for c in codes if have.get(c, 0) < need_days]

    def _screen_sql(
        self,
        asof: date,
        ma_period: int,
        below_pct: float,
        universe: List[Dict[str, Any]],
        limit: int,
    ) -> pd.DataFrame:
        if not universe:
            return pd.DataFrame()
        info = {u["ts_code"]: u for u in universe}
        lookback = asof - timedelta(days=ma_period * 4)
        raw = pd.read_sql(
            sa_text(
                "SELECT ts_code, trade_date, close FROM stock_daily "
                "WHERE trade_date BETWEEN :s AND :e"
            ),
            self.engine,
            params={"s": lookback, "e": asof},
        )
        if raw.empty:
            return pd.DataFrame()
        code_set = set(info)
        raw = raw[raw["ts_code"].astype(str).str.upper().isin(code_set)].copy()
        if raw.empty:
            return pd.DataFrame()
        raw["ts_code"] = raw["ts_code"].astype(str).str.upper()
        raw["close"] = pd.to_numeric(raw["close"], errors="coerce")
        raw["trade_date"] = pd.to_datetime(raw["trade_date"]).dt.date
        thresh = 1.0 - float(below_pct or 0) / 100.0
        hits = []
        for code, g in raw.groupby("ts_code", sort=False):
            g = g.sort_values("trade_date")
            g["ma5"] = g["close"].rolling(ma_period, min_periods=ma_period).mean()
            row = g.loc[g["trade_date"] == asof]
            if row.empty:
                continue
            r = row.iloc[-1]
            ma5 = r["ma5"]
            close = r["close"]
            if pd.isna(ma5) or pd.isna(close) or ma5 <= 0:
                continue
            if close < ma5 * thresh:
                meta = info.get(code) or {}
                hits.append(
                    {
                        "ts_code": code,
                        "name": meta.get("name") or code,
                        "market": meta.get("market") or "",
                        "industry": meta.get("industry") or "",
                        "is_st": bool(meta.get("is_st")),
                        "trade_date": asof.isoformat(),
                        "close": close,
                        "ma5": ma5,
                        "below_pct": round(float((ma5 - close) / ma5 * 100), 3),
                    }
                )
        if not hits:
            return pd.DataFrame()
        df = pd.DataFrame(hits).sort_values("below_pct", ascending=False)
        return df.head(int(limit)).reset_index(drop=True)

    def _save_run(
        self,
        asof: date,
        ma_period: int,
        below_pct: float,
        filters: Dict[str, Any],
        hits: pd.DataFrame,
        filled_rows: int,
    ) -> int:
        self._ensure_tables()
        with self.engine.begin() as conn:
            run_id = conn.execute(
                sa_text(
                    "INSERT INTO strategy_screener_run "
                    "(asof, ma_period, below_pct, filters, hit_count, filled_rows) "
                    "VALUES (:asof, :ma, :bp, CAST(:flt AS jsonb), :hc, :fr) RETURNING id"
                ),
                {
                    "asof": asof,
                    "ma": ma_period,
                    "bp": below_pct,
                    "flt": json.dumps(filters, ensure_ascii=False),
                    "hc": int(len(hits)),
                    "fr": int(filled_rows),
                },
            ).scalar()
            if hits is not None and not hits.empty:
                rows = []
                for _, r in hits.iterrows():
                    rows.append(
                        {
                            "run_id": int(run_id),
                            "ts_code": str(r["ts_code"]),
                            "name": r.get("name"),
                            "market": r.get("market"),
                            "industry": r.get("industry"),
                            "is_st": bool(r.get("is_st")),
                            "trade_date": r["trade_date"],
                            "close": _none_if_na(r["close"]),
                            "ma5": _none_if_na(r["ma5"]),
                            "below_pct": _none_if_na(r["below_pct"]),
                        }
                    )
                conn.execute(
                    sa_text(
                        "INSERT INTO strategy_screener_hit "
                        "(run_id, ts_code, name, market, industry, is_st, trade_date, close, ma5, below_pct) "
                        "VALUES (:run_id, :ts_code, :name, :market, :industry, :is_st, :trade_date, :close, :ma5, :below_pct) "
                        "ON CONFLICT (run_id, ts_code) DO UPDATE SET "
                        "close = EXCLUDED.close, ma5 = EXCLUDED.ma5, below_pct = EXCLUDED.below_pct"
                    ),
                    rows,
                )
        return int(run_id)

    def screen_below_ma(
        self,
        asof: Optional[str] = None,
        ma_period: int = 5,
        below_pct: float = 0.0,
        include_st: bool = False,
        include_main: bool = True,
        include_star: bool = True,
        include_chinext: bool = True,
        include_bse: bool = False,
        include_cdr: bool = False,
        limit: int = 200,
        fill: bool = True,
    ) -> Dict[str, Any]:
        if not self.engine:
            return {"ok": False, "error": "PostgreSQL 未连接"}
        self._ensure_tables()
        self.selective.ensure_trade_cal()
        ctx = self.selective.session_context()
        asof_d = ctx["asof_date"]
        if asof:
            try:
                asof_d = date.fromisoformat(str(asof)[:10])
            except Exception:
                return {"ok": False, "error": "asof 日期无效"}
        asof_d = min(asof_d, ctx["asof_date"])
        ma_period = max(2, min(int(ma_period or 5), 60))
        below_pct = max(0.0, float(below_pct or 0))
        limit = max(10, min(int(limit or 200), 1000))

        filters = {
            "include_st": include_st,
            "include_main": include_main,
            "include_star": include_star,
            "include_chinext": include_chinext,
            "include_bse": include_bse,
            "include_cdr": include_cdr,
        }
        basic_n = self._ensure_stock_basic()
        universe = self._universe_codes(filters)
        if not universe:
            return {"ok": False, "error": "股票池为空，请检查过滤条件或证券主档"}

        days = self._session_days(asof_d, ma_period + 1)
        filled = 0
        if fill:
            filled = self._fill_window(days, [u["ts_code"] for u in universe])

        hits = self._screen_sql(asof_d, ma_period, below_pct, universe, limit)
        run_id = self._save_run(asof_d, ma_period, below_pct, filters, hits, filled)
        records = []
        if not hits.empty:
            for _, r in hits.iterrows():
                records.append(
                    {
                        "ts_code": str(r["ts_code"]),
                        "name": r["name"],
                        "market": r["market"],
                        "industry": r["industry"],
                        "is_st": bool(r["is_st"]),
                        "trade_date": r["trade_date"],
                        "close": None if pd.isna(r["close"]) else round(float(r["close"]), 4),
                        "ma5": None if pd.isna(r["ma5"]) else round(float(r["ma5"]), 4),
                        "below_pct": None if pd.isna(r["below_pct"]) else round(float(r["below_pct"]), 3),
                    }
                )
        return {
            "ok": True,
            "run_id": run_id,
            "asof": asof_d.isoformat(),
            "ma_period": ma_period,
            "below_pct": below_pct,
            "filters": filters,
            "universe": len(universe),
            "stock_basic": basic_n,
            "window": [d.isoformat() for d in days],
            "filled_rows": filled,
            "hit_count": len(records),
            "hits": records,
            "source": "datahubco",
            "table": "stock_daily",
            "note": "收盘价相对 MA 的跌破幅度；(MA-close)/MA×100 ≥ 阈值",
        }


screener_service = ScreenerService()

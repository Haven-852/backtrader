"""
持续获取：板块 / ETF / 大盘指数等全市场或清单式定时拉取。

已实现：
  - 东财行业板块（dc_index / dc_daily / moneyflow → PostgreSQL）
  - 核心大盘指数日线（index_daily → TimescaleDB/PG）
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd
from sqlalchemy import text as sa_text

from data_layer.db_manager import storage_manager

from .client import DatahubcoClient
from .sectors import SectorDataFetcher, sector_fetcher
from .selective import SelectiveDataFetcher

logger = logging.getLogger("collector.datahubco.continuous")

# 行情看板顶部指数条（与 dash_service.CORE_INDICES 对齐）
DEFAULT_INDICES = [
    "000001.SH",  # 上证
    "399001.SZ",  # 深证
    "000300.SH",  # 沪深300
    "399006.SZ",  # 创业板
    "000688.SH",  # 科创50
    "000905.SH",  # 中证500
    "000016.SH",  # 上证50
    "000852.SH",  # 中证1000
    "399005.SZ",  # 中小100
    "399673.SZ",  # 创50
]

INDEX_BAR_COLS = [
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


class ContinuousDataFetcher:
    """
    持续获取入口。
    行业板块 / 核心指数：调用 run_once / sync_* 按交易日 asof 写入 PG。
    """

    def __init__(
        self,
        sectors: Optional[SectorDataFetcher] = None,
        client: Optional[DatahubcoClient] = None,
    ):
        self.enabled = True
        self.sectors = sectors or sector_fetcher
        self._client = client
        self._selective = SelectiveDataFetcher(client=client)
        self._engine = None

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

    def plan(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "enabled": self.enabled,
            "targets": {
                "sectors": {
                    "tables": [
                        "sector_dc_index_daily",
                        "sector_dc_daily",
                        "sector_moneyflow_daily",
                        "sector_dc_member",
                        "sector_dc_hierarchy",
                    ],
                    "apis": ["dc_index", "dc_daily", "moneyflow_ind_dc", "dc_member"],
                    "asof_rule": "交易日闭市前=上一交易日；闭市后=当天；非交易日=上一交易日",
                },
                "indices": {
                    "codes": DEFAULT_INDICES,
                    "table": "index_daily",
                    "api": "index_daily",
                    "asof_rule": "同 sectors",
                },
                "etf": "etf_daily（待接）",
            },
            "note": "POST /data/continuous/sectors/run · POST /data/continuous/indices/run",
        }

    def sync_sectors(
        self,
        trade_date: Optional[str] = None,
        with_members: bool = True,
        member_limit: int = 40,
        with_hierarchy: bool = False,
    ) -> Dict[str, Any]:
        if not self.enabled:
            return {"ok": False, "error": "continuous fetcher 未启用", "plan": self.plan()}
        result = self.sectors.sync_day(
            trade_date=trade_date,
            member_levels=("东财一级行业",) if with_members else (),
            member_limit=member_limit if with_members else 0,
            fetch_members_for=None if with_members else [],
        )
        if with_hierarchy:
            try:
                result["hierarchy"] = self.sectors.build_hierarchy(
                    trade_date=trade_date, sync_members=True
                )
            except Exception as e:
                result["hierarchy"] = {"ok": False, "error": str(e)}
        return result

    def _index_latest_date(self, ts_code: str) -> Optional[date]:
        if not self.engine:
            return None
        try:
            with self.engine.connect() as conn:
                row = conn.execute(
                    sa_text("SELECT MAX(trade_date) FROM index_daily WHERE ts_code = :c"),
                    {"c": ts_code},
                ).fetchone()
            return row[0] if row and row[0] else None
        except Exception:
            return None

    def _normalize_index_daily(self, df: pd.DataFrame, ts_code: str) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame(columns=INDEX_BAR_COLS)
        df = df.copy()
        df.columns = [str(c).lower() for c in df.columns]
        df["ts_code"] = ts_code
        if "trade_date" not in df.columns:
            return pd.DataFrame(columns=INDEX_BAR_COLS)
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
        keep = [c for c in INDEX_BAR_COLS if c in df.columns]
        return (
            df[keep]
            .drop_duplicates(subset=["ts_code", "trade_date"], keep="last")
            .sort_values("trade_date")
            .reset_index(drop=True)
        )

    def _upsert_index_daily(self, df: pd.DataFrame) -> int:
        if df is None or df.empty or not self.engine:
            return 0
        for c in INDEX_BAR_COLS:
            if c not in df.columns:
                df[c] = None
        sql = """
            INSERT INTO index_daily (
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
            vol = r["vol"]
            rows.append({
                "ts_code": str(r["ts_code"]),
                "trade_date": r["trade_date"],
                "open": None if pd.isna(r["open"]) else float(r["open"]),
                "high": None if pd.isna(r["high"]) else float(r["high"]),
                "low": None if pd.isna(r["low"]) else float(r["low"]),
                "close": None if pd.isna(r["close"]) else float(r["close"]),
                "pre_close": None if pd.isna(r["pre_close"]) else float(r["pre_close"]),
                "change": None if pd.isna(r["change"]) else float(r["change"]),
                "pct_chg": None if pd.isna(r["pct_chg"]) else float(r["pct_chg"]),
                "vol": None if pd.isna(vol) else int(vol),
                "amount": None if pd.isna(r["amount"]) else float(r["amount"]),
            })
        with self.engine.begin() as conn:
            conn.execute(sa_text(sql), rows)
        return len(rows)

    def sync_indices(
        self,
        trade_date: Optional[str] = None,
        codes: Optional[Sequence[str]] = None,
        lookback_days: int = 40,
        force_full_window: bool = False,
    ) -> Dict[str, Any]:
        """
        持续同步核心指数日线 → index_daily。
        默认从各指数库内最新日（或缺数时 lookback）补到 asof。
        """
        if not self.enabled:
            return {"ok": False, "error": "continuous fetcher 未启用", "plan": self.plan()}
        if not self.engine:
            return {"ok": False, "error": "数据库未连接"}

        self._selective.ensure_trade_cal()
        if trade_date:
            ymd = str(trade_date).replace("-", "")[:8]
            asof = datetime.strptime(ymd, "%Y%m%d").date()
            session = {"asof_date": asof, "ymd": ymd, "reason": "explicit"}
        else:
            session = self._selective.session_context()
            asof = session["asof_date"]
            ymd = asof.strftime("%Y%m%d")

        code_list = [c.strip().upper() for c in (codes or DEFAULT_INDICES) if c]
        result: Dict[str, Any] = {
            "ok": True,
            "session": {**session, "asof": asof.isoformat(), "ymd": ymd},
            "counts": {},
            "errors": {},
            "latest": {},
        }

        for code in code_list:
            try:
                latest = None if force_full_window else self._index_latest_date(code)
                if latest and latest >= asof:
                    result["counts"][code] = 0
                    result["latest"][code] = latest.isoformat()
                    continue
                if latest:
                    start = (latest - timedelta(days=3)).strftime("%Y%m%d")
                else:
                    start = (asof - timedelta(days=lookback_days)).strftime("%Y%m%d")
                raw = self.client.query(
                    "index_daily",
                    ts_code=code,
                    start_date=start,
                    end_date=ymd,
                )
                df = self._normalize_index_daily(raw, code)
                if not df.empty:
                    df = df.loc[df["trade_date"] <= asof]
                n = self._upsert_index_daily(df)
                result["counts"][code] = n
                result["latest"][code] = (
                    df["trade_date"].max().isoformat() if not df.empty else None
                )
                logger.info("index_daily sync %s +%d · %s→%s", code, n, start, ymd)
            except Exception as e:
                logger.error("index_daily sync %s 失败: %s", code, e)
                result["errors"][code] = str(e)

        result["ok"] = bool(result["counts"]) and not (
            len(result["errors"]) == len(code_list)
        )
        result["upserted"] = int(sum(result["counts"].values()))
        return result

    def ensure_indices_fresh(self, max_lag_days: int = 0) -> Dict[str, Any]:
        """看板用：若任一核心指数落后 asof，则触发同步。"""
        self._selective.ensure_trade_cal()
        session = self._selective.session_context()
        asof: date = session["asof_date"]
        filled = 0
        if not session.get("in_trading_session"):
            filled = self._fill_indices_from_tencent(asof, session)
        stale = []
        for code in DEFAULT_INDICES:
            latest = self._index_latest_date(code)
            if latest is None:
                stale.append(code)
                continue
            lag = (asof - latest).days
            if lag > max_lag_days:
                stale.append(code)
        if not stale:
            return {
                "ok": True,
                "synced": bool(filled),
                "asof": asof.isoformat(),
                "stale": [],
                "tencent_filled": filled,
            }
        synced = self.sync_indices(codes=stale)
        filled += self._fill_indices_from_tencent(asof, session)
        return {
            "ok": synced.get("ok", False) or bool(filled),
            "synced": True,
            "asof": asof.isoformat(),
            "stale": stale,
            "result": synced,
            "tencent_filled": filled,
        }

    def _fill_indices_from_tencent(self, asof: date, session: Dict[str, Any]) -> int:
        """Datahubco 指数日线收盘后常滞后，用腾讯当日快照补 asof。"""
        if session.get("in_trading_session"):
            return 0
        missing = [
            code
            for code in DEFAULT_INDICES
            if (self._index_latest_date(code) or date.min) < asof
        ]
        if not missing:
            return 0
        from .quotes import fetch_tencent_eod_on

        df = fetch_tencent_eod_on(missing, asof)
        if df.empty:
            logger.warning("腾讯指数收盘快照无 %s", asof)
            return 0
        n = self._upsert_index_daily(df[INDEX_BAR_COLS] if set(INDEX_BAR_COLS) <= set(df.columns) else df)
        logger.info("腾讯补指数日线 %s %d 只", asof, n)
        return n

    def run_once(self, targets: Optional[List[str]] = None) -> Dict[str, Any]:
        targets = targets or ["sectors", "indices"]
        out: Dict[str, Any] = {"ok": True, "results": {}}
        if "sectors" in targets:
            r = self.sync_sectors()
            out["results"]["sectors"] = r
            if not r.get("ok"):
                out["ok"] = False
        if "indices" in targets:
            r = self.sync_indices()
            out["results"]["indices"] = r
            if not r.get("ok"):
                out["ok"] = False
        for t in targets:
            if t not in out["results"]:
                out["results"][t] = {"ok": False, "error": "not_implemented"}
        return out


continuous_fetcher = ContinuousDataFetcher()

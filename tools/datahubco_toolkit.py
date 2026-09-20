"""
Datahubco 数据工具箱（给大模型 Agent）

策略：
  - source=auto（默认）：先查 PostgreSQL；覆盖不足再拉 Datahubco → 写入 PG → 返回
  - source=pg：只读数据库
  - source=datahubco：直接读 Datahubco；可选 write_back 入库

主要入口：get_bars / get_mins / get_sector_members / filter_st_suspend /
         coverage / remote_query / list_datasets
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

# 历史日认为「够用」的最少 1 分钟根数（连续竞价约 240）
_MINS_SUFFICIENT = 60

# dataset → (pg_table, datahubco_api, default_freq_hint)
DATASETS: Dict[str, Dict[str, Any]] = {
    "stock_daily": {
        "table": "stock_daily",
        "api": "daily",
        "desc": "个股日线 OHLCV",
        "key": ["ts_code", "trade_date"],
        "kind": "bars",
    },
    "stock_weekly": {
        "table": "stock_weekly",
        "api": "weekly",
        "desc": "个股周线",
        "key": ["ts_code", "trade_date"],
        "kind": "bars",
    },
    "stock_monthly": {
        "table": "stock_monthly",
        "api": "monthly",
        "desc": "个股月线",
        "key": ["ts_code", "trade_date"],
        "kind": "bars",
    },
    "stock_mins": {
        "table": "stock_intraday_mins",
        "api": "stk_mins",
        "desc": "个股 1 分钟分时（Timescale stock_intraday_mins）",
        "key": ["ts_code", "trade_date", "bar_time"],
        "kind": "mins",
    },
    "index_daily": {
        "table": "index_daily",
        "api": "index_daily",
        "desc": "指数日线",
        "key": ["ts_code", "trade_date"],
        "kind": "bars",
    },
    "etf_daily": {
        "table": "etf_daily",
        "api": "fund_daily",
        "desc": "ETF/基金日线（Datahubco 多为 fund_daily）",
        "key": ["ts_code", "trade_date"],
        "kind": "bars",
    },
    "sector_dc_index": {
        "table": "sector_dc_index_daily",
        "api": "dc_index",
        "desc": "东财行业板块指数日（涨跌幅/层级）",
        "key": ["ts_code", "trade_date"],
        "kind": "sector",
    },
    "sector_dc_daily": {
        "table": "sector_dc_daily",
        "api": "dc_daily",
        "desc": "东财行业板块日行情（含成交额）",
        "key": ["ts_code", "trade_date"],
        "kind": "sector",
    },
}


def _ser(v: Any) -> Any:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, (date, datetime)):
        return v.isoformat()[:10] if not isinstance(v, datetime) else v.isoformat(sep=" ", timespec="seconds")
    if hasattr(v, "item"):
        try:
            return v.item()
        except Exception:
            return float(v) if pd.notna(v) else None
    if pd.isna(v):
        return None
    return v


def _df_records(df: Optional[pd.DataFrame], limit: int = 200) -> List[Dict[str, Any]]:
    if df is None or df.empty:
        return []
    use = df.head(int(limit)).copy()
    out: List[Dict[str, Any]] = []
    for _, row in use.iterrows():
        out.append({k: _ser(row[k]) for k in use.columns})
    return out


def _to_ymd(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    raw = str(s).strip().replace("-", "")[:8]
    if len(raw) != 8 or not raw.isdigit():
        return None
    return raw


def _parse_date(s: Optional[str]) -> Optional[date]:
    ymd = _to_ymd(s)
    if not ymd:
        return None
    return datetime.strptime(ymd, "%Y%m%d").date()


class DatahubcoToolkit:
    """PG 优先 + Datahubco 补齐 的 Agent 工具实现"""

    def __init__(self):
        self._fetcher = None
        self._client = None
        self._sector = None
        self._intraday = None

    @property
    def fetcher(self):
        if self._fetcher is None:
            from data_layer.collectors.datahubco.selective import selective_fetcher

            self._fetcher = selective_fetcher
        return self._fetcher

    @property
    def client(self):
        if self._client is None:
            from data_layer.collectors.datahubco.client import DatahubcoClient

            self._client = DatahubcoClient()
        return self._client

    @property
    def sector(self):
        if self._sector is None:
            from data_layer.collectors.datahubco.sectors import sector_fetcher

            self._sector = sector_fetcher
        return self._sector

    @property
    def intraday(self):
        if self._intraday is None:
            from data_layer.collectors.datahubco.intraday import intraday_fetcher

            self._intraday = intraday_fetcher
        return self._intraday

    # ─── 对外工具 ─────────────────────────────────────

    def list_datasets(self) -> Dict[str, Any]:
        items = []
        for k, meta in DATASETS.items():
            items.append({
                "dataset": k,
                "table": meta["table"],
                "api": meta["api"],
                "desc": meta["desc"],
                "kind": meta["kind"],
            })
        return {
            "ok": True,
            "datasets": items,
            "sources": ["auto", "pg", "datahubco"],
            "note": (
                "auto=库内有则直接返回；不足则 Datahubco 拉取并入库后返回。"
                "datahubco=强制远端；write_back 控制是否写入 PG。"
                "板块成分用 get_sector_members；剔除 ST/停牌用 filter_st_suspend。"
            ),
        }

    def coverage(
        self,
        ts_code: str,
        dataset: str = "stock_daily",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        trade_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """查看 PG 覆盖情况（不触发远端）。"""
        try:
            ds = self._resolve_dataset(dataset)
            code = self.fetcher.normalize_ts_code(ts_code)
            table = ds["table"]
            if ds["kind"] == "mins":
                d = _parse_date(trade_date or end_date or start_date)
                if not d:
                    phase = self.intraday.session_phase()
                    d = phase["trade_date"]
                meta = self._pg_mins_meta(code, d)
                return {
                    "ok": True,
                    "ts_code": code,
                    "dataset": ds["name"],
                    "table": table,
                    "trade_date": d.isoformat(),
                    "coverage": meta,
                    "sufficient": int(meta.get("count") or 0) >= _MINS_SUFFICIENT,
                }
            start = _parse_date(start_date)
            end = _parse_date(end_date)
            meta = self._pg_bar_meta(code, table, start, end)
            return {
                "ok": True,
                "ts_code": code,
                "dataset": ds["name"],
                "table": table,
                "coverage": meta,
                "sufficient": bool(meta.get("count")),
            }
        except Exception as e:
            logger.exception("coverage failed")
            return {"ok": False, "error": str(e)}

    def get_bars(
        self,
        ts_code: str,
        dataset: str = "stock_daily",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 120,
        source: str = "auto",
        write_back: bool = True,
        auto_sync: bool = True,
        trade_date: Optional[str] = None,
        include_auction: bool = True,
    ) -> Dict[str, Any]:
        """
        获取行情/板块 bars。

        source:
          - auto: PG 够用则只读库；否则拉 Datahubco（默认入库）再读库返回
          - pg: 只读 PostgreSQL
          - datahubco: 直接读 Datahubco；write_back=True 时写入后再返回
        """
        try:
            ds = self._resolve_dataset(dataset)
            if ds["kind"] == "mins":
                return self.get_mins(
                    ts_code=ts_code,
                    trade_date=trade_date or end_date or start_date,
                    limit=limit if limit and limit != 120 else 500,
                    source=source,
                    write_back=write_back,
                    include_auction=include_auction,
                )
            code = self.fetcher.normalize_ts_code(ts_code)
            source = (source or "auto").strip().lower()
            if source in ("remote", "datahub", "dh"):
                source = "datahubco"
            if source not in ("auto", "pg", "datahubco"):
                return {"ok": False, "error": f"无效 source={source}，应为 auto/pg/datahubco"}

            limit = max(1, min(int(limit or 120), 2000))
            start = _parse_date(start_date)
            end = _parse_date(end_date)
            table = ds["table"]

            if ds["kind"] == "sector":
                return self._get_sector_bars(
                    code=code,
                    dataset=dataset,
                    ds=ds,
                    start=start,
                    end=end,
                    limit=limit,
                    source=source,
                    write_back=write_back,
                    auto_sync=auto_sync,
                )

            # 个股 / 指数 / ETF
            if source == "pg":
                df = self._read_pg_bars(code, table, start, end, limit)
                return self._ok_bars(
                    code, dataset, table, df, source_used="pg",
                    fetched=False, upserted=0, limit=limit,
                )

            if source == "datahubco":
                remote = self._fetch_bars_remote(code, ds, start, end, limit)
                upserted = 0
                if write_back and remote is not None and not remote.empty:
                    upserted = self._upsert_bars_df(code, ds, remote)
                # 优先返回远端；若写库成功也可再读库
                if write_back and upserted:
                    df = self._read_pg_bars(code, table, start, end, limit)
                    if df is not None and not df.empty:
                        return self._ok_bars(
                            code, dataset, table, df, source_used="datahubco+pg",
                            fetched=True, upserted=upserted, limit=limit,
                        )
                return self._ok_bars(
                    code, dataset, table, remote, source_used="datahubco",
                    fetched=True, upserted=upserted, limit=limit,
                )

            # auto
            need_remote = True
            if auto_sync:
                cover = self._pg_bar_meta(code, table, start, end)
                need_remote = self._need_remote(cover, start, end, limit)
            else:
                cover = self._pg_bar_meta(code, table, start, end)
                need_remote = not bool(cover.get("count"))

            upserted = 0
            fetched = False
            if need_remote:
                # 复用挑选式 ensure：日线优先
                if dataset in ("stock_daily", "index_daily", "etf_daily") and not start and not end:
                    ens = self.fetcher.ensure_fresh(code, also_week_month=False)
                    fetched = not ens.get("skipped")
                    upserted = int((ens.get("upserted") or {}).get("daily") or 0)
                else:
                    remote = self._fetch_bars_remote(code, ds, start, end, max(limit, 60))
                    fetched = True
                    if write_back and remote is not None and not remote.empty:
                        upserted = self._upsert_bars_df(code, ds, remote)
            df = self._read_pg_bars(code, table, start, end, limit)
            if (df is None or df.empty) and fetched:
                # 入库失败时仍返回远端
                remote = self._fetch_bars_remote(code, ds, start, end, limit)
                return self._ok_bars(
                    code, dataset, table, remote, source_used="datahubco",
                    fetched=True, upserted=upserted, limit=limit,
                    note="库内仍空，已直接返回 Datahubco 结果",
                )
            return self._ok_bars(
                code, dataset, table, df,
                source_used=("datahubco+pg" if fetched else "pg"),
                fetched=fetched, upserted=upserted, limit=limit,
            )
        except Exception as e:
            logger.exception("get_bars failed")
            return {"ok": False, "error": str(e), "ts_code": ts_code, "dataset": dataset}

    def remote_query(
        self,
        api: str,
        params: Optional[Dict[str, Any]] = None,
        write_dataset: Optional[str] = None,
        limit: int = 200,
    ) -> Dict[str, Any]:
        """
        直接调用 Datahubco OpenAPI。
        若 write_dataset 指定且可识别，则尝试写入对应 PG 表。
        """
        try:
            api = (api or "").strip().strip("/")
            if not api:
                return {"ok": False, "error": "缺少 api"}
            params = dict(params or {})
            # 清理空值
            params = {k: v for k, v in params.items() if v is not None and v != ""}
            df = self.client.query(api, **params)
            upserted = 0
            if write_dataset and df is not None and not df.empty:
                ds = self._resolve_dataset(write_dataset)
                code = None
                if "ts_code" in df.columns and len(df):
                    code = str(df.iloc[0]["ts_code"])
                if code:
                    code = self.fetcher.normalize_ts_code(code)
                    upserted = self._upsert_bars_df(code, ds, df)
                elif ds["kind"] == "sector":
                    upserted = self._upsert_sector_df(ds, df)
            return {
                "ok": True,
                "api": api,
                "params": params,
                "rows": 0 if df is None else int(len(df)),
                "columns": list(df.columns) if df is not None and not df.empty else [],
                "data": _df_records(df, limit=limit),
                "upserted": upserted,
                "write_dataset": write_dataset,
            }
        except Exception as e:
            logger.exception("remote_query failed")
            return {"ok": False, "error": str(e), "api": api}

    def get_mins(
        self,
        ts_code: str,
        trade_date: Optional[str] = None,
        limit: int = 500,
        source: str = "auto",
        write_back: bool = True,
        include_auction: bool = True,
    ) -> Dict[str, Any]:
        """
        获取个股 1 分钟分时（stock_intraday_mins）。

        source:
          - auto: 库内足够则直接返回；不足则拉远端（Datahubco stk_mins / 当日东财腾讯）并默认入库
          - pg: 只读 Timescale
          - datahubco: 强制远端；write_back 控制是否写入
        """
        try:
            code = self.fetcher.normalize_ts_code(ts_code)
            if not code:
                return {"ok": False, "error": "缺少 ts_code"}
            source = (source or "auto").strip().lower()
            if source in ("remote", "datahub", "dh"):
                source = "datahubco"
            if source not in ("auto", "pg", "datahubco"):
                return {"ok": False, "error": f"无效 source={source}，应为 auto/pg/datahubco"}

            limit = max(1, min(int(limit or 500), 2500))
            phase = self.intraday.session_phase()
            d = _parse_date(trade_date) or phase["trade_date"]
            table = "stock_intraday_mins"

            if source == "pg":
                df = self.intraday.query_pg(code, d)
                return self._ok_mins(
                    code, d, df, source_used="pg", fetched=False, upserted=0,
                    limit=limit, include_auction=include_auction,
                )

            if source == "datahubco":
                live = d == phase["trade_date"] and phase.get("phase") == "live"
                remote = self.intraday.fetch_remote(code, d, live=live)
                upserted = 0
                if write_back and remote is not None and not remote.empty:
                    upserted = int(self.intraday.upsert_mins(remote))
                    if d == phase["trade_date"] and phase.get("phase") == "after_close":
                        self.intraday.upsert_daily_from_mins(code, d, remote)
                df = remote
                if write_back and upserted:
                    pg_df = self.intraday.query_pg(code, d)
                    if pg_df is not None and not pg_df.empty:
                        df = pg_df
                return self._ok_mins(
                    code, d, df,
                    source_used="datahubco+pg" if upserted else "datahubco",
                    fetched=True, upserted=upserted,
                    limit=limit, include_auction=include_auction,
                )

            # auto：复用 IntradayFetcher.get（当日 Redis + 历史 PG 优先，缺则远端入库）
            if write_back:
                packed = self.intraday.get(
                    ts_code=code,
                    trade_date=d.strftime("%Y%m%d"),
                    history=False,
                )
                if not packed.get("ok"):
                    return {
                        "ok": False,
                        "error": packed.get("error") or "无分时数据",
                        "ts_code": code,
                        "trade_date": d.isoformat(),
                        "dataset": "stock_mins",
                        "table": table,
                    }
                bars = list(packed.get("bars") or [])
                if not include_auction:
                    bars = [b for b in bars if b.get("phase") != "auction" and b.get("freq") != "auction"]
                if len(bars) > limit:
                    bars = bars[-limit:]
                src = str(packed.get("source") or "auto")
                fetched = src not in ("timescale", "redis", "pg")
                return {
                    "ok": True,
                    "ts_code": code,
                    "name": packed.get("name"),
                    "trade_date": d.isoformat(),
                    "dataset": "stock_mins",
                    "table": table,
                    "freq": "1min",
                    "source_used": src,
                    "fetched_remote": fetched,
                    "upserted": int(packed.get("pg_written") or 0),
                    "session": packed.get("session"),
                    "session_phase": packed.get("session_phase"),
                    "pre_close": packed.get("pre_close"),
                    "last": packed.get("last"),
                    "pct_chg": packed.get("pct_chg"),
                    "count": len(bars),
                    "data": bars,
                    "latest": bars[-1] if bars else None,
                    "message": packed.get("message"),
                }

            # auto + 不写回：库够用读库，否则只读远端
            meta = self._pg_mins_meta(code, d)
            if int(meta.get("count") or 0) >= _MINS_SUFFICIENT:
                df = self.intraday.query_pg(code, d)
                return self._ok_mins(
                    code, d, df, source_used="pg", fetched=False, upserted=0,
                    limit=limit, include_auction=include_auction,
                )
            live = d == phase["trade_date"] and phase.get("phase") == "live"
            remote = self.intraday.fetch_remote(code, d, live=live)
            return self._ok_mins(
                code, d, remote, source_used="datahubco", fetched=True, upserted=0,
                limit=limit, include_auction=include_auction,
                note="write_back=false，未写入数据库",
            )
        except Exception as e:
            logger.exception("get_mins failed")
            return {"ok": False, "error": str(e), "ts_code": ts_code}

    def ensure_and_get(
        self,
        ts_code: str,
        dataset: str = "stock_daily",
        limit: int = 120,
        trade_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """快捷：auto 模式确保有数据并返回最近 limit 条。"""
        try:
            ds = self._resolve_dataset(dataset)
        except Exception:
            ds = {"kind": "bars", "name": dataset}
        if ds.get("kind") == "mins":
            return self.get_mins(
                ts_code=ts_code,
                trade_date=trade_date,
                limit=limit if limit and limit != 120 else 500,
                source="auto",
                write_back=True,
            )
        return self.get_bars(
            ts_code=ts_code,
            dataset=dataset,
            limit=limit,
            source="auto",
            write_back=True,
            auto_sync=True,
            trade_date=trade_date,
        )

    # ─── 内部 ─────────────────────────────────────────

    def _resolve_dataset(self, dataset: str) -> Dict[str, Any]:
        key = (dataset or "stock_daily").strip().lower()
        aliases = {
            "daily": "stock_daily",
            "week": "stock_weekly",
            "weekly": "stock_weekly",
            "month": "stock_monthly",
            "monthly": "stock_monthly",
            "mins": "stock_mins",
            "min": "stock_mins",
            "1min": "stock_mins",
            "minute": "stock_mins",
            "intraday": "stock_mins",
            "stk_mins": "stock_mins",
            "index": "index_daily",
            "etf": "etf_daily",
            "fund_daily": "etf_daily",
            "sector": "sector_dc_index",
            "sector_index": "sector_dc_index",
            "dc_index": "sector_dc_index",
            "dc_daily": "sector_dc_daily",
        }
        key = aliases.get(key, key)
        if key not in DATASETS:
            raise ValueError(f"未知 dataset={dataset}，可选: {', '.join(DATASETS)}")
        return {"name": key, **DATASETS[key]}

    def _pg_mins_meta(self, ts_code: str, trade_date: date) -> Dict[str, Any]:
        from sqlalchemy import text as sa_text

        eng = self.intraday.engine
        if not eng:
            return {"count": 0}
        self.intraday._ensure_table()
        sql = """
            SELECT COUNT(1) AS cnt,
                   MIN(bar_time) AS min_t,
                   MAX(bar_time) AS max_t
            FROM stock_intraday_mins
            WHERE ts_code = :c AND trade_date = :d
        """
        with eng.connect() as conn:
            row = conn.execute(sa_text(sql), {"c": ts_code, "d": trade_date}).fetchone()
        if not row:
            return {"count": 0}
        return {
            "count": int(row[0] or 0),
            "min_time": _ser(row[1]),
            "max_time": _ser(row[2]),
        }

    def _mins_df_to_records(
        self,
        df: Optional[pd.DataFrame],
        limit: int,
        include_auction: bool,
    ) -> List[Dict[str, Any]]:
        if df is None or df.empty:
            return []
        use = df.copy()
        if not include_auction:
            skip = use["bar_time"].map(self.intraday._is_auction_bar_time)
            if "freq" in use.columns:
                skip = skip | use["freq"].fillna("1min").astype(str).eq("auction")
            use = use.loc[~skip]
        if use.empty:
            return []
        if len(use) > limit:
            use = use.tail(int(limit))
        out: List[Dict[str, Any]] = []
        for _, r in use.iterrows():
            bt = r.get("bar_time")
            if hasattr(bt, "isoformat"):
                bt_s = bt.isoformat(sep=" ", timespec="seconds")
            else:
                bt_s = _ser(bt)
            phase = "auction" if self.intraday._is_auction_bar_time(r.get("bar_time")) else "session"
            if str(r.get("freq") or "") == "auction":
                phase = "auction"
            out.append({
                "ts_code": _ser(r.get("ts_code")),
                "bar_time": bt_s,
                "trade_date": _ser(r.get("trade_date")),
                "freq": str(r.get("freq") or "1min"),
                "phase": phase,
                "open": _ser(r.get("open")),
                "high": _ser(r.get("high")),
                "low": _ser(r.get("low")),
                "close": _ser(r.get("close")),
                "vol": _ser(r.get("vol")),
                "amount": _ser(r.get("amount")),
                "avg_price": _ser(r.get("avg_price")),
            })
        return out

    def _ok_mins(
        self,
        code: str,
        trade_date: date,
        df: Optional[pd.DataFrame],
        source_used: str,
        fetched: bool,
        upserted: int,
        limit: int,
        include_auction: bool,
        note: Optional[str] = None,
    ) -> Dict[str, Any]:
        records = self._mins_df_to_records(df, limit=limit, include_auction=include_auction)
        return {
            "ok": True,
            "ts_code": code,
            "trade_date": trade_date.isoformat(),
            "dataset": "stock_mins",
            "table": "stock_intraday_mins",
            "freq": "1min",
            "source_used": source_used,
            "fetched_remote": fetched,
            "upserted": upserted,
            "count": len(records),
            "data": records,
            "latest": records[-1] if records else None,
            "note": note,
        }

    def _need_remote(
        self,
        cover: Dict[str, Any],
        start: Optional[date],
        end: Optional[date],
        limit: int,
    ) -> bool:
        cnt = int(cover.get("count") or 0)
        if cnt <= 0:
            return True
        if start or end:
            # 区间模式：有任意空洞就补（简化：数量明显不足则补）
            if start and end:
                # 粗略：交易日约 2/3 日历日
                expect = max(1, int((end - start).days * 0.6))
                return cnt < min(expect, limit)
            return cnt < max(5, min(limit // 2, 30))
        # 无区间：对照 asof 是否新鲜
        try:
            asof = self.fetcher.session_context()["asof_date"]
            max_d = cover.get("max_date")
            if not max_d:
                return True
            md = datetime.strptime(str(max_d)[:10], "%Y-%m-%d").date()
            return md < asof or cnt < min(limit, 20)
        except Exception:
            return cnt < min(limit, 20)

    def _pg_bar_meta(
        self,
        ts_code: str,
        table: str,
        start: Optional[date],
        end: Optional[date],
    ) -> Dict[str, Any]:
        from sqlalchemy import text as sa_text
        from data_layer.db_manager import storage_manager

        eng = storage_manager.get_postgres_engine()
        if not eng:
            return {"count": 0}
        where = ["ts_code = :c"]
        params: Dict[str, Any] = {"c": ts_code}
        if start:
            where.append("trade_date >= :s")
            params["s"] = start
        if end:
            where.append("trade_date <= :e")
            params["e"] = end
        sql = f"""
            SELECT COUNT(1) AS cnt,
                   MIN(trade_date) AS min_d,
                   MAX(trade_date) AS max_d
            FROM {table}
            WHERE {' AND '.join(where)}
        """
        with eng.connect() as conn:
            row = conn.execute(sa_text(sql), params).fetchone()
        if not row:
            return {"count": 0}
        return {
            "count": int(row[0] or 0),
            "min_date": _ser(row[1]),
            "max_date": _ser(row[2]),
        }

    def _read_pg_bars(
        self,
        ts_code: str,
        table: str,
        start: Optional[date],
        end: Optional[date],
        limit: int,
    ) -> pd.DataFrame:
        from sqlalchemy import text as sa_text
        from data_layer.db_manager import storage_manager

        eng = storage_manager.get_postgres_engine()
        if not eng:
            return pd.DataFrame()
        where = ["ts_code = :c"]
        params: Dict[str, Any] = {"c": ts_code, "lim": int(limit)}
        if start:
            where.append("trade_date >= :s")
            params["s"] = start
        if end:
            where.append("trade_date <= :e")
            params["e"] = end
        sql = f"""
            SELECT * FROM {table}
            WHERE {' AND '.join(where)}
            ORDER BY trade_date DESC
            LIMIT :lim
        """
        df = pd.read_sql(sa_text(sql), eng, params=params)
        if not df.empty and "trade_date" in df.columns:
            df = df.sort_values("trade_date").reset_index(drop=True)
        return df

    def _fetch_bars_remote(
        self,
        ts_code: str,
        ds: Dict[str, Any],
        start: Optional[date],
        end: Optional[date],
        limit: int,
    ) -> pd.DataFrame:
        ctx = self.fetcher.session_context()
        asof: date = ctx["asof_date"]
        end_d = end or asof
        if start:
            start_d = start
        else:
            # 按 limit 粗略回看日历日
            start_d = end_d - timedelta(days=max(40, int(limit * 2.2)))

        api = ds["api"]
        # 个股日/周/月走 selective 规范化
        if api in ("daily", "weekly", "monthly"):
            frames = self.fetcher.fetch_remote(
                ts_code,
                start_d.strftime("%Y%m%d"),
                end_d.strftime("%Y%m%d"),
                freqs=[api if api != "daily" else "daily"],
            )
            freq = "daily" if api == "daily" else api
            df = frames.get(freq, pd.DataFrame())
            if df is not None and not df.empty:
                df = df.loc[df["trade_date"] <= asof]
            return df if df is not None else pd.DataFrame()

        # index_daily / fund_daily
        df = self.client.query(
            api,
            ts_code=ts_code,
            start_date=start_d.strftime("%Y%m%d"),
            end_date=end_d.strftime("%Y%m%d"),
        )
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.copy()
        df.columns = [str(c).lower() for c in df.columns]
        if "trade_date" in df.columns:
            df["trade_date"] = pd.to_datetime(df["trade_date"].astype(str), errors="coerce").dt.date
            df = df.dropna(subset=["trade_date"])
            df = df.loc[df["trade_date"] <= asof]
        if "ts_code" not in df.columns:
            df["ts_code"] = ts_code
        return df.sort_values("trade_date").reset_index(drop=True) if "trade_date" in df.columns else df

    def _upsert_bars_df(self, ts_code: str, ds: Dict[str, Any], df: pd.DataFrame) -> int:
        if df is None or df.empty:
            return 0
        table = ds["table"]
        api = ds["api"]
        use = df.copy()
        use.columns = [str(c).lower() for c in use.columns]
        if "ts_code" not in use.columns:
            use["ts_code"] = ts_code
        else:
            use["ts_code"] = ts_code

        if api in ("daily", "weekly", "monthly"):
            freq = "daily" if api == "daily" else api
            # selective upsert_bars 走 FREQ_TABLE；指数/ETF 需指定 table
            if table in ("stock_daily", "stock_weekly", "stock_monthly"):
                return int(self.fetcher.upsert_bars(freq, use))
            return int(self.fetcher.upsert_bars("daily", use, table=table))

        # 通用 upsert 到指定表（index_daily / etf_daily）
        return int(self.fetcher.upsert_bars("daily", use, table=table))

    def _get_sector_bars(
        self,
        code: str,
        dataset: str,
        ds: Dict[str, Any],
        start: Optional[date],
        end: Optional[date],
        limit: int,
        source: str,
        write_back: bool,
        auto_sync: bool,
    ) -> Dict[str, Any]:
        table = ds["table"]
        ctx = self.fetcher.session_context()
        asof: date = end or ctx["asof_date"]
        ymd = asof.strftime("%Y%m%d")

        def read_pg() -> pd.DataFrame:
            return self._read_pg_bars(code, table, start, asof if not end else end, limit)

        if source == "pg":
            df = read_pg()
            return self._ok_bars(code, dataset, table, df, "pg", False, 0, limit)

        def pull_remote() -> pd.DataFrame:
            if ds["api"] == "dc_index":
                raw = self.sector.fetch_dc_index(ymd)
            else:
                raw = self.sector.fetch_dc_daily(ymd)
            if raw is None or raw.empty:
                return pd.DataFrame()
            raw = raw.copy()
            if "ts_code" in raw.columns:
                hit = raw.loc[raw["ts_code"].astype(str).str.upper() == code.upper()]
                if hit.empty and code:
                    # 无单码时返回全日（供写入），前端 get 仍按 code 过滤
                    return raw
                return hit
            return raw

        if source == "datahubco":
            remote = pull_remote()
            upserted = 0
            if write_back:
                full = (
                    self.sector.fetch_dc_index(ymd)
                    if ds["api"] == "dc_index"
                    else self.sector.fetch_dc_daily(ymd)
                )
                upserted = self._upsert_sector_df(ds, full)
                if (remote is None or remote.empty) and full is not None and not full.empty and "ts_code" in full.columns:
                    remote = full.loc[full["ts_code"].astype(str).str.upper() == code.upper()]
            df = read_pg()
            if df is None or df.empty:
                df = remote
            return self._ok_bars(
                code, dataset, table, df,
                "datahubco+pg" if upserted else "datahubco",
                True, upserted, limit,
            )

        # auto
        cover = self._pg_bar_meta(code, table, start, asof)
        fetched = False
        upserted = 0
        if auto_sync and (not cover.get("count") or str(cover.get("max_date") or "")[:10] < asof.isoformat()):
            full = self.sector.fetch_dc_index(ymd) if ds["api"] == "dc_index" else self.sector.fetch_dc_daily(ymd)
            if write_back and full is not None and not full.empty:
                upserted = self._upsert_sector_df(ds, full)
                fetched = True
        df = read_pg()
        if (df is None or df.empty) and fetched:
            remote = pull_remote()
            return self._ok_bars(code, dataset, table, remote, "datahubco", True, upserted, limit)
        return self._ok_bars(
            code, dataset, table, df,
            "datahubco+pg" if fetched else "pg",
            fetched, upserted, limit,
        )

    def _upsert_sector_df(self, ds: Dict[str, Any], df: pd.DataFrame) -> int:
        if df is None or df.empty:
            return 0
        if ds["api"] == "dc_index":
            return int(self.sector.upsert_index(df))
        return int(self.sector.upsert_daily(df))

    # ─── 东财板块成分 / ST·停牌过滤 ─────────────────────

    @staticmethod
    def _is_st_name(name: Optional[str]) -> bool:
        """名称含 ST / *ST / S*ST 等视为 ST 股"""
        if not name:
            return False
        n = str(name).upper().replace(" ", "").replace("＊", "*")
        return "ST" in n

    def _resolve_member_trade_date(self, trade_date: Optional[str]) -> Dict[str, Any]:
        meta = self.sector.resolve_asof(trade_date)
        asof: date = meta["asof_date"]
        # 若 asof 尚无日线，回退到库内最近交易日
        from sqlalchemy import text as sa_text
        from data_layer.db_manager import storage_manager

        eng = storage_manager.get_postgres_engine()
        use_date = asof
        if eng:
            with eng.connect() as conn:
                row = conn.execute(
                    sa_text("SELECT MAX(trade_date) FROM stock_daily WHERE trade_date <= :d"),
                    {"d": asof},
                ).fetchone()
                if row and row[0] and row[0] < asof:
                    use_date = row[0]
        return {
            **meta,
            "use_date": use_date,
            "use_ymd": use_date.strftime("%Y%m%d"),
            "trade_date": use_date.isoformat(),
        }

    def _load_sector_members_df(
        self,
        sector_code: str,
        use_date: date,
        ymd: str,
        source: str = "auto",
        write_back: bool = True,
    ) -> tuple[pd.DataFrame, str, int, bool]:
        """返回 (df, source_used, upserted, fetched)"""
        from sqlalchemy import text as sa_text
        from data_layer.db_manager import storage_manager

        eng = storage_manager.get_postgres_engine()
        source = (source or "auto").strip().lower()
        if source in ("remote", "datahub", "dh"):
            source = "datahubco"
        upserted = 0
        fetched = False

        def read_pg() -> pd.DataFrame:
            if not eng:
                return pd.DataFrame()
            sql = sa_text(
                """
                SELECT
                    m.con_code AS ts_code,
                    COALESCE(b.name, m.name) AS name,
                    b.industry,
                    b.market,
                    b.list_status,
                    d.close, d.open, d.high, d.low,
                    d.pre_close, d.change, d.pct_chg, d.vol, d.amount,
                    m.trade_date
                FROM sector_dc_member m
                LEFT JOIN ref_stock_basic b ON b.ts_code = m.con_code
                LEFT JOIN stock_daily d
                  ON d.ts_code = m.con_code AND d.trade_date = m.trade_date
                WHERE m.ts_code = :c AND m.trade_date = :d
                ORDER BY d.pct_chg DESC NULLS LAST, m.con_code
                """
            )
            return pd.read_sql(sql, eng, params={"c": sector_code, "d": use_date})

        def pull_and_upsert() -> int:
            nonlocal fetched
            fetched = True
            mdf = self.sector.fetch_members(sector_code, ymd)
            if mdf is None or mdf.empty:
                return 0
            if "trade_date" in mdf.columns:
                mdf = mdf.loc[mdf["trade_date"] == use_date]
            if write_back and not mdf.empty:
                return int(self.sector.upsert_members(mdf))
            return 0

        if source == "pg":
            return read_pg(), "pg", 0, False

        if source == "datahubco":
            upserted = pull_and_upsert()
            df = read_pg()
            if df is None or df.empty:
                # 写库失败时直接返回远端瘦身结果
                mdf = self.sector.fetch_members(sector_code, ymd)
                if mdf is not None and not mdf.empty:
                    out = mdf.rename(columns={"con_code": "ts_code"}) if "con_code" in mdf.columns else mdf
                    return out, "datahubco", upserted, True
            return df, ("datahubco+pg" if upserted else "datahubco"), upserted, True

        # auto
        df = read_pg()
        if df is None or df.empty:
            self.sector.ensure_tables()
            upserted = pull_and_upsert()
            df = read_pg()
            return df, ("datahubco+pg" if upserted else "datahubco"), upserted, True
        return df, "pg", 0, False

    def _annotate_st_suspend(self, df: pd.DataFrame, trade_date: date) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame()
        use = df.copy()
        if "ts_code" not in use.columns and "con_code" in use.columns:
            use = use.rename(columns={"con_code": "ts_code"})
        use["ts_code"] = use["ts_code"].astype(str).str.upper()
        if "name" not in use.columns:
            use["name"] = None
        use["is_st"] = use["name"].map(self._is_st_name)

        from sqlalchemy import text as sa_text
        from data_layer.db_manager import storage_manager

        eng = storage_manager.get_postgres_engine()
        codes = use["ts_code"].dropna().unique().tolist()
        suspended: set = set()
        suspend_meta: Dict[str, Dict[str, Any]] = {}
        if eng and codes:
            # 当日停牌记录
            sql = sa_text(
                """
                SELECT ts_code, suspend_type, suspend_reason
                FROM ref_suspend_d
                WHERE trade_date = :d AND ts_code = ANY(:codes)
                """
            )
            try:
                sdf = pd.read_sql(sql, eng, params={"d": trade_date, "codes": codes})
            except Exception:
                # 部分驱动对 ANY 绑定不友好，改 IN
                placeholders = ", ".join(f":c{i}" for i in range(len(codes)))
                params = {"d": trade_date, **{f"c{i}": c for i, c in enumerate(codes)}}
                sdf = pd.read_sql(
                    sa_text(
                        f"""
                        SELECT ts_code, suspend_type, suspend_reason
                        FROM ref_suspend_d
                        WHERE trade_date = :d AND ts_code IN ({placeholders})
                        """
                    ),
                    eng,
                    params=params,
                )
            if not sdf.empty:
                for _, r in sdf.iterrows():
                    code = str(r["ts_code"]).upper()
                    stype = str(r.get("suspend_type") or "").upper()
                    suspend_meta[code] = {
                        "suspend_type": r.get("suspend_type"),
                        "suspend_reason": r.get("suspend_reason"),
                    }
                    if stype == "S":
                        suspended.add(code)

            # 当日无日线且仍为上市状态 → 视为停牌（补 suspend 表不全）
            # 仅当同批已有成分存在有效日线时才推断，避免整表缺行情误杀
            peers_have_bar = False
            if "close" in use.columns:
                peers_have_bar = bool(use["close"].notna().any())
            if peers_have_bar and ("vol" in use.columns or "close" in use.columns):
                for _, r in use.iterrows():
                    code = str(r["ts_code"]).upper()
                    if code in suspended:
                        continue
                    list_status = str(r.get("list_status") or "L").upper()
                    if list_status and list_status not in ("L", "LISTED", ""):
                        # 退市等不按停牌处理，交由业务侧自行看 list_status
                        continue
                    no_bar = pd.isna(r.get("close")) if "close" in use.columns else True
                    zero_vol = False
                    if "vol" in use.columns and pd.notna(r.get("vol")):
                        try:
                            zero_vol = float(r.get("vol") or 0) <= 0
                        except Exception:
                            zero_vol = False
                    if no_bar or zero_vol:
                        suspended.add(code)
                        if code not in suspend_meta:
                            suspend_meta[code] = {
                                "suspend_type": "inferred",
                                "suspend_reason": "当日无有效日线/成交量，推断停牌",
                            }

        use["is_suspended"] = use["ts_code"].map(lambda c: c in suspended)
        use["suspend_type"] = use["ts_code"].map(
            lambda c: (suspend_meta.get(c) or {}).get("suspend_type")
        )
        use["suspend_reason"] = use["ts_code"].map(
            lambda c: (suspend_meta.get(c) or {}).get("suspend_reason")
        )
        return use

    def get_sector_members(
        self,
        ts_code: str,
        trade_date: Optional[str] = None,
        source: str = "auto",
        write_back: bool = True,
        limit: int = 500,
        include_flags: bool = True,
    ) -> Dict[str, Any]:
        """
        读取东财行业板块成分股（dc_member → sector_dc_member）。
        默认 source=auto：库有则直接返回，否则 Datahubco 拉取并入库。
        """
        try:
            code = (ts_code or "").strip().upper()
            if not code:
                return {"ok": False, "error": "缺少板块 ts_code，如 BK1035.DC"}
            if "." not in code and code.startswith("BK"):
                code = f"{code}.DC"

            meta = self._resolve_member_trade_date(trade_date)
            use_date: date = meta["use_date"]
            ymd = meta["use_ymd"]
            limit = max(1, min(int(limit or 500), 5000))

            df, source_used, upserted, fetched = self._load_sector_members_df(
                code, use_date, ymd, source=source, write_back=write_back
            )
            if include_flags and df is not None and not df.empty:
                df = self._annotate_st_suspend(df, use_date)

            # 板块元信息
            sector_name = code
            sector_meta: Dict[str, Any] = {}
            from sqlalchemy import text as sa_text
            from data_layer.db_manager import storage_manager

            eng = storage_manager.get_postgres_engine()
            if eng:
                with eng.connect() as conn:
                    row = conn.execute(
                        sa_text(
                            """
                            SELECT name, pct_change, leading_name, leading_code, industry_level
                            FROM sector_dc_index_daily
                            WHERE ts_code=:c AND trade_date=:d LIMIT 1
                            """
                        ),
                        {"c": code, "d": use_date},
                    ).fetchone()
                    if row:
                        sector_name = row[0] or code
                        sector_meta = {
                            "name": row[0],
                            "pct_change": _ser(row[1]),
                            "leading": row[2],
                            "leading_code": row[3],
                            "industry_level": row[4],
                        }

            records = _df_records(df, limit=limit)
            st_n = sum(1 for r in records if r.get("is_st"))
            sus_n = sum(1 for r in records if r.get("is_suspended"))
            return {
                "ok": True,
                "ts_code": code,
                "name": sector_name,
                "trade_date": use_date.isoformat(),
                "sector": sector_meta,
                "source_used": source_used,
                "fetched_remote": fetched,
                "upserted": upserted,
                "count": len(records),
                "st_count": st_n,
                "suspended_count": sus_n,
                "members": records,
                "session": {k: meta[k] for k in ("asof", "ymd", "reason", "phase") if k in meta},
                "note": "成分股含 is_st / is_suspended 标记；剔除请用 filter_st_suspend",
            }
        except Exception as e:
            logger.exception("get_sector_members failed")
            return {"ok": False, "error": str(e), "ts_code": ts_code, "members": []}

    def filter_st_suspend(
        self,
        ts_code: Optional[str] = None,
        trade_date: Optional[str] = None,
        ts_codes: Optional[List[str]] = None,
        members: Optional[List[Dict[str, Any]]] = None,
        exclude_st: bool = True,
        exclude_suspend: bool = True,
        source: str = "auto",
        write_back: bool = True,
        limit: int = 500,
        return_removed: bool = True,
    ) -> Dict[str, Any]:
        """
        剔除 ST 与停牌股票。

        输入三选一：
          - ts_code：东财板块代码，先取成分再过滤
          - ts_codes：股票代码列表
          - members：已有成分列表（含 ts_code/name）
        """
        try:
            meta = self._resolve_member_trade_date(trade_date)
            use_date: date = meta["use_date"]
            ymd = meta["use_ymd"]
            limit = max(1, min(int(limit or 500), 5000))
            sector_code = None
            source_used = "input"

            if members:
                df = pd.DataFrame(members)
            elif ts_codes:
                codes = [self.fetcher.normalize_ts_code(str(c)) for c in ts_codes if c]
                codes = [c for c in codes if c]
                if not codes:
                    return {"ok": False, "error": "ts_codes 为空", "kept": [], "removed": []}
                from sqlalchemy import text as sa_text
                from data_layer.db_manager import storage_manager

                eng = storage_manager.get_postgres_engine()
                if not eng:
                    df = pd.DataFrame({"ts_code": codes})
                else:
                    placeholders = ", ".join(f":c{i}" for i in range(len(codes)))
                    params = {"d": use_date, **{f"c{i}": c for i, c in enumerate(codes)}}
                    df = pd.read_sql(
                        sa_text(
                            f"""
                            SELECT
                                b.ts_code,
                                b.name,
                                b.industry,
                                b.market,
                                b.list_status,
                                d.close, d.open, d.high, d.low,
                                d.pre_close, d.change, d.pct_chg, d.vol, d.amount
                            FROM ref_stock_basic b
                            LEFT JOIN stock_daily d
                              ON d.ts_code = b.ts_code AND d.trade_date = :d
                            WHERE b.ts_code IN ({placeholders})
                            """
                        ),
                        eng,
                        params=params,
                    )
                    # 补上 basic 缺失的代码
                    got = set(df["ts_code"].astype(str).str.upper()) if not df.empty else set()
                    missing = [c for c in codes if c not in got]
                    if missing:
                        df = pd.concat(
                            [df, pd.DataFrame({"ts_code": missing})],
                            ignore_index=True,
                        )
                source_used = "pg"
            elif ts_code:
                sector_code = (ts_code or "").strip().upper()
                if "." not in sector_code and sector_code.startswith("BK"):
                    sector_code = f"{sector_code}.DC"
                packed = self.get_sector_members(
                    ts_code=sector_code,
                    trade_date=trade_date or use_date.isoformat(),
                    source=source,
                    write_back=write_back,
                    limit=limit,
                    include_flags=False,
                )
                if not packed.get("ok"):
                    return packed
                df = pd.DataFrame(packed.get("members") or [])
                source_used = packed.get("source_used") or "auto"
                sector_meta = {
                    "ts_code": packed.get("ts_code"),
                    "name": packed.get("name"),
                    "sector": packed.get("sector"),
                }
            else:
                return {
                    "ok": False,
                    "error": "请提供 ts_code（板块）或 ts_codes / members",
                    "kept": [],
                    "removed": [],
                }

            if sector_code is None:
                sector_meta = {"ts_code": None, "name": None, "sector": {}}

            annotated = self._annotate_st_suspend(df, use_date)
            if annotated.empty:
                return {
                    "ok": True,
                    "trade_date": use_date.isoformat(),
                    "exclude_st": exclude_st,
                    "exclude_suspend": exclude_suspend,
                    "source_used": source_used,
                    "input_count": 0,
                    "kept_count": 0,
                    "removed_count": 0,
                    "kept": [],
                    "removed": [],
                    **sector_meta,
                }

            mask_keep = pd.Series([True] * len(annotated), index=annotated.index)
            if exclude_st:
                mask_keep &= ~annotated["is_st"].fillna(False)
            if exclude_suspend:
                mask_keep &= ~annotated["is_suspended"].fillna(False)

            kept_df = annotated.loc[mask_keep]
            removed_df = annotated.loc[~mask_keep]
            kept = _df_records(kept_df, limit=limit)
            removed = _df_records(removed_df, limit=limit) if return_removed else []

            removed_st = int(removed_df["is_st"].fillna(False).sum()) if not removed_df.empty else 0
            removed_sus = (
                int(removed_df["is_suspended"].fillna(False).sum()) if not removed_df.empty else 0
            )

            return {
                "ok": True,
                "trade_date": use_date.isoformat(),
                "exclude_st": bool(exclude_st),
                "exclude_suspend": bool(exclude_suspend),
                "source_used": source_used,
                "input_count": int(len(annotated)),
                "kept_count": len(kept),
                "removed_count": int(len(removed_df)),
                "removed_st_count": removed_st,
                "removed_suspended_count": removed_sus,
                "kept": kept,
                "removed": removed,
                "ts_codes_kept": [r.get("ts_code") for r in kept if r.get("ts_code")],
                "note": (
                    "ST 按证券简称含 ST 判断；停牌优先用 ref_suspend_d(S)，"
                    "若当日无有效日线/成交量则推断为停牌。"
                ),
                **sector_meta,
            }
        except Exception as e:
            logger.exception("filter_st_suspend failed")
            return {"ok": False, "error": str(e), "kept": [], "removed": []}

    def _ok_bars(
        self,
        code: str,
        dataset: str,
        table: str,
        df: Optional[pd.DataFrame],
        source_used: str,
        fetched: bool,
        upserted: int,
        limit: int,
        note: Optional[str] = None,
    ) -> Dict[str, Any]:
        records = _df_records(df, limit=limit)
        return {
            "ok": True,
            "ts_code": code,
            "dataset": dataset,
            "table": table,
            "source_used": source_used,
            "fetched_remote": fetched,
            "upserted": upserted,
            "count": len(records),
            "columns": list(df.columns) if df is not None and not df.empty else [],
            "data": records,
            "latest": records[-1] if records else None,
            "note": note,
        }

    # async wrappers
    async def list_datasets_async(self) -> Dict[str, Any]:
        return await asyncio.to_thread(self.list_datasets)

    async def coverage_async(self, **kwargs) -> Dict[str, Any]:
        return await asyncio.to_thread(self.coverage, **kwargs)

    async def get_bars_async(self, **kwargs) -> Dict[str, Any]:
        return await asyncio.to_thread(self.get_bars, **kwargs)

    async def remote_query_async(self, **kwargs) -> Dict[str, Any]:
        return await asyncio.to_thread(self.remote_query, **kwargs)

    async def ensure_and_get_async(self, **kwargs) -> Dict[str, Any]:
        return await asyncio.to_thread(self.ensure_and_get, **kwargs)

    async def get_mins_async(self, **kwargs) -> Dict[str, Any]:
        return await asyncio.to_thread(self.get_mins, **kwargs)

    async def get_sector_members_async(self, **kwargs) -> Dict[str, Any]:
        return await asyncio.to_thread(self.get_sector_members, **kwargs)

    async def filter_st_suspend_async(self, **kwargs) -> Dict[str, Any]:
        return await asyncio.to_thread(self.filter_st_suspend, **kwargs)


datahubco_toolkit = DatahubcoToolkit()

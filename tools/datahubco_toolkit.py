"""
Datahubco 数据工具箱（给大模型 Agent）

策略：
  - 日线/周月线等 source=auto：先查 PostgreSQL；不足再拉 Datahubco → 写入 PG → 返回
  - 分钟分时 stock_mins source=auto：先查 Redis → 再 PG/Timescale → 都没有才 Datahubco 补数
    （可写回 Redis + stock_intraday_mins）
  - source=pg：只读数据库
  - source=datahubco：直接读 Datahubco；可选 write_back 入库

主要入口：get_bars / get_mins / get_sector_members / filter_st_suspend /
         get_fund_data / get_stock_sector_moneyflow /
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
        self._fund = None

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

    @property
    def fund(self):
        if self._fund is None:
            from data_layer.collectors.datahubco.fundamentals import fund_data_fetcher

            self._fund = fund_data_fetcher
        return self._fund

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
        fund_items = []
        try:
            fund_items = self.fund.list_datasets()
        except Exception as e:
            logger.debug("list fund datasets: %s", e)
        return {
            "ok": True,
            "datasets": items,
            "fund_datasets": fund_items,
            "sources": ["auto", "pg", "datahubco"],
            "note": (
                "日线等：auto=PG 有则返回，不足则 Datahubco 拉取并入库。"
                "分时 stock_mins：auto=先 Redis，再 PG/Timescale，都没有再 Datahubco 补数（可写回 Redis+PG）。"
                "datahubco=强制远端；write_back 控制是否写入。"
                "板块成分用 get_sector_members；剔除 ST/停牌用 filter_st_suspend。"
                "龙虎榜/大宗/两融/北向/资金流/利润表/资产负债/现金流/财务指标/"
                "主营构成/审计/分红/快报/披露日程/业绩预告/十大股东用 get_fund_data；"
                "指数主档/周月线/估值/行业分类/权重、基金主档/净值/分红/持仓/复权也用 get_fund_data；"
                "限售解禁/质押/增减持/筹码/研报/同花顺概念成分/可转债/期货主档也用 get_fund_data；"
                "每日指标/复权因子/涨跌停价/涨跌停榜/停复牌/龙虎榜席位/新股也用 get_fund_data；"
                "个股二级/三级板块资金用 get_stock_sector_moneyflow。"
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
                redis_n = int(self.intraday.redis_bars_count(code, d) or 0)
                meta = self._pg_mins_meta(code, d)
                meta = {
                    **meta,
                    "redis_count": redis_n,
                    "local_count": max(int(meta.get("count") or 0), redis_n),
                    "prefer": "redis" if redis_n >= _MINS_SUFFICIENT else (
                        "pg" if int(meta.get("count") or 0) >= _MINS_SUFFICIENT else "remote"
                    ),
                }
                return {
                    "ok": True,
                    "ts_code": code,
                    "dataset": ds["name"],
                    "table": table,
                    "trade_date": d.isoformat(),
                    "coverage": meta,
                    "sufficient": int(meta.get("local_count") or 0) >= _MINS_SUFFICIENT,
                    "note": "分时优先看 Redis；PG/Timescale 为持久化备份",
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

    def fill_sector_gap(self, trade_date: str, level: str = "东财二级行业") -> Dict[str, Any]:
        """补指定交易日的东财行业板块行情并写入 PostgreSQL。"""
        try:
            if not trade_date:
                return {"ok": False, "error": "缺少 trade_date"}
            return self.sector.backfill_level_day(str(trade_date), level or "东财二级行业")
        except Exception as e:
            logger.exception("fill_sector_gap failed")
            return {"ok": False, "error": str(e), "trade_date": trade_date, "level": level}

    def fill_market_history(self, start_date: str, end_date: str) -> Dict[str, Any]:
        """补全部东财二级行业在一段日期里的涨跌和上涨家数，并写入数据库。"""
        try:
            if not start_date or not end_date:
                return {"ok": False, "error": "缺少 start_date 或 end_date"}
            return self.sector.fill_market_history(str(start_date), str(end_date))
        except Exception as e:
            logger.exception("fill_market_history failed")
            return {"ok": False, "error": str(e)}

    def fill_sector_history(self, ts_code: str, start_date: str, end_date: str) -> Dict[str, Any]:
        """补一个板块一段日期的行情、上涨家数和沪深300环境，并写入数据库。"""
        try:
            if not ts_code or not start_date or not end_date:
                return {"ok": False, "error": "缺少 ts_code、start_date 或 end_date"}
            return self.sector.fill_board_history(str(ts_code), str(start_date), str(end_date))
        except Exception as e:
            logger.exception("fill_sector_history failed")
            return {"ok": False, "error": str(e), "ts_code": ts_code}

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
        获取个股 1 分钟分时。

        本地存储：当日/近期主缓存在 Redis；Timescale `stock_intraday_mins` 为持久化。
        source:
          - auto: Redis → PG → Datahubco（缺则补数；write_back 时写 Redis/PG）
          - pg: 只读 Timescale（不经 Redis）
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
                    # 与 IntradayFetcher.get 一致：当日写 Redis + PG
                    packed = self.intraday.get(
                        ts_code=code,
                        trade_date=d.strftime("%Y%m%d"),
                        history=False,
                    )
                    if packed.get("ok"):
                        bars = list(packed.get("bars") or [])
                        if not include_auction:
                            bars = [
                                b for b in bars
                                if b.get("phase") != "auction" and b.get("freq") != "auction"
                            ]
                        if len(bars) > limit:
                            bars = bars[-limit:]
                        return {
                            "ok": True,
                            "ts_code": code,
                            "name": packed.get("name"),
                            "trade_date": d.isoformat(),
                            "dataset": "stock_mins",
                            "table": table,
                            "freq": "1min",
                            "source_used": str(packed.get("source") or "datahubco"),
                            "fetched_remote": True,
                            "upserted": int(packed.get("pg_written") or 0),
                            "redis_hit": False,
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

            # auto：先 Redis，再 PG；都不够再 Datahubco 补数
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
                was_cache = (
                    (not packed.get("redis_written"))
                    and int(packed.get("pg_written") or 0) == 0
                    and src in ("redis", "timescale", "pg")
                )
                fetched = not was_cache
                return {
                    "ok": True,
                    "ts_code": code,
                    "name": packed.get("name"),
                    "trade_date": d.isoformat(),
                    "dataset": "stock_mins",
                    "table": table,
                    "freq": "1min",
                    "source_used": src if was_cache else (
                        "datahubco+redis" if packed.get("redis_written") else (
                            "datahubco+pg" if int(packed.get("pg_written") or 0) else src
                        )
                    ),
                    "fetched_remote": fetched,
                    "redis_hit": src == "redis" and was_cache,
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

            # auto + 不写回：Redis → PG → 只读远端
            cached = self.intraday.peek_redis(code, d)
            if cached and len(cached.get("bars") or []) >= _MINS_SUFFICIENT:
                bars = list(cached.get("bars") or [])
                if not include_auction:
                    bars = [
                        b for b in bars
                        if b.get("phase") != "auction" and b.get("freq") != "auction"
                    ]
                if len(bars) > limit:
                    bars = bars[-limit:]
                return {
                    "ok": True,
                    "ts_code": code,
                    "name": cached.get("name"),
                    "trade_date": d.isoformat(),
                    "dataset": "stock_mins",
                    "table": table,
                    "freq": "1min",
                    "source_used": "redis",
                    "fetched_remote": False,
                    "redis_hit": True,
                    "upserted": 0,
                    "session": cached.get("session"),
                    "session_phase": cached.get("session_phase") or phase.get("phase"),
                    "pre_close": cached.get("pre_close"),
                    "last": cached.get("last"),
                    "pct_chg": cached.get("pct_chg"),
                    "count": len(bars),
                    "data": bars,
                    "latest": bars[-1] if bars else None,
                    "message": "命中 Redis 分时缓存（未写回）",
                }

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
                note="write_back=false，未写入 Redis/数据库",
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

    # ─── 资金 / 基本面 / 板块资金 ───────────────────────

    def get_fund_data(
        self,
        dataset: str,
        ts_code: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        trade_date: Optional[str] = None,
        period: Optional[str] = None,
        source: str = "auto",
        write_back: bool = True,
        limit: int = 500,
        content_type: str = "行业",
    ) -> Dict[str, Any]:
        """
        获取并可选写入：龙虎榜 / 席位 / 大宗 / 两融 / 北向持股 / 资金流 /
        利润表 / 资产负债表 / 现金流量表 / 财务指标 / 主营构成 / 审计意见 /
        分红送转 / 业绩快报 / 财报披露日程 / 业绩预告 / 十大股东 / 板块资金 /
        指数主档 / 周月线 / 每日估值 / 行业分类 / 成分权重 /
        基金主档 / 净值 / 分红 / 持仓 / 复权因子 /
        限售解禁 / 股权质押 / 股东增减持 / 筹码摘要与明细 / 券商研报 /
        同花顺概念成分 / 可转债基础与日线 / 期货主档 /
        每日指标 / 复权因子 / 涨跌停价 / 涨跌停榜 / 停复牌 / 新股。
        """
        try:
            return self.fund.get(
                dataset=dataset,
                ts_code=ts_code,
                start_date=start_date,
                end_date=end_date,
                trade_date=trade_date,
                period=period,
                source=source,
                write_back=write_back,
                limit=limit,
                content_type=content_type,
            )
        except Exception as e:
            logger.exception("get_fund_data failed")
            return {"ok": False, "error": str(e), "dataset": dataset, "ts_code": ts_code}

    def get_stock_sector_moneyflow(
        self,
        ts_code: str,
        trade_date: Optional[str] = None,
        levels: Optional[List[str]] = None,
        source: str = "auto",
        write_back: bool = True,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """个股所属二级/三级东财行业板块的资金走向。"""
        try:
            return self.fund.get_stock_sector_moneyflow(
                ts_code=ts_code,
                trade_date=trade_date,
                levels=levels,
                source=source,
                write_back=write_back,
                limit=limit,
            )
        except Exception as e:
            logger.exception("get_stock_sector_moneyflow failed")
            return {"ok": False, "error": str(e), "ts_code": ts_code}

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

    # ------------------------------------------------------------------ #
    # 主板全市场分时批量（训练 LightGBM 用）
    # ------------------------------------------------------------------ #
    MAINBOARD_INDEX_KEY = "intraday:mainboard:{ymd}:codes"

    def _list_mainboard_codes(
        self,
        trade_date: date,
        *,
        only_traded: bool = True,
        exclude_st: bool = True,
    ) -> List[Dict[str, str]]:
        """沪主板 60xxxx.SH + 深主板 00xxxx.SZ。"""
        from data_layer.db_manager import storage_manager
        from sqlalchemy import text as sa_text

        eng = storage_manager.get_postgres_engine()
        if not eng:
            return []
        with eng.connect() as conn:
            if only_traded:
                rows = conn.execute(
                    sa_text(
                        """
                        SELECT d.ts_code, COALESCE(b.name, '') AS name
                        FROM stock_daily d
                        LEFT JOIN ref_stock_basic b ON b.ts_code = d.ts_code
                        WHERE d.trade_date = :d
                          AND (
                            d.ts_code LIKE '60%.SH'
                            OR d.ts_code LIKE '00%.SZ'
                          )
                        ORDER BY d.ts_code
                        """
                    ),
                    {"d": trade_date},
                ).fetchall()
            else:
                rows = conn.execute(
                    sa_text(
                        """
                        SELECT ts_code, COALESCE(name, '') AS name
                        FROM ref_stock_basic
                        WHERE COALESCE(list_status, 'L') = 'L'
                          AND (
                            ts_code LIKE '60%.SH'
                            OR ts_code LIKE '00%.SZ'
                          )
                        ORDER BY ts_code
                        """
                    )
                ).fetchall()
        out = []
        for r in rows:
            code = str(r.ts_code or "").upper()
            name = str(r.name or "")
            if exclude_st and ("ST" in name.upper()):
                continue
            out.append({"ts_code": code, "name": name})
        return out

    def _mins_bar_count_local(self, ts_code: str, trade_date: date) -> Dict[str, int]:
        """本地 Redis / PG 已有分钟根数。"""
        redis_n = 0
        pg_n = 0
        try:
            redis_n = int(self.intraday.redis_bars_count(ts_code, trade_date) or 0)
        except Exception:
            pass
        try:
            df = self.intraday.query_pg(ts_code, trade_date)
            pg_n = 0 if df is None else int(len(df))
        except Exception:
            pass
        return {"redis": redis_n, "pg": pg_n, "best": max(redis_n, pg_n)}

    def _save_mainboard_index(self, trade_date: date, codes: List[str]) -> bool:
        r = None
        try:
            from data_layer.db_manager import storage_manager

            r = storage_manager.get_redis_client()
        except Exception:
            return False
        if not r:
            return False
        key = self.MAINBOARD_INDEX_KEY.format(ymd=trade_date.strftime("%Y%m%d"))
        try:
            pipe = r.pipeline()
            pipe.delete(key)
            if codes:
                pipe.sadd(key, *codes)
                pipe.expire(key, 48 * 3600)
            pipe.execute()
            return True
        except Exception as e:
            logger.warning("写主板分时索引失败: %s", e)
            return False

    def _load_mainboard_index(self, trade_date: date) -> List[str]:
        try:
            from data_layer.db_manager import storage_manager

            r = storage_manager.get_redis_client()
            if not r:
                return []
            key = self.MAINBOARD_INDEX_KEY.format(ymd=trade_date.strftime("%Y%m%d"))
            members = r.smembers(key) or set()
            out = []
            for m in members:
                if isinstance(m, bytes):
                    m = m.decode("utf-8", errors="ignore")
                out.append(str(m).upper())
            return sorted(out)
        except Exception:
            return []

    def fill_mainboard_mins(
        self,
        trade_date: Optional[str] = None,
        *,
        action: str = "fill",
        source: str = "auto",
        write_back: bool = True,
        include_auction: bool = True,
        skip_existing: bool = True,
        min_bars: int = 60,
        max_workers: int = 6,
        limit: Optional[int] = None,
        offset: int = 0,
        only_traded: bool = True,
        exclude_st: bool = True,
    ) -> Dict[str, Any]:
        """
        批量拉取主板个股当日 1 分钟分时 → Redis（主缓存）+ Timescale PG。

        action:
          - fill: 批量补数（默认）
          - status: 只统计覆盖，不拉远端
          - index: 返回 Redis 训练索引里的代码列表

        训练读取：
          - Redis key 单票：intraday:{ts_code}:{YYYYMMDD}
          - Redis 索引集合：intraday:mainboard:{YYYYMMDD}:codes
        """
        try:
            phase = self.intraday.session_phase()
            d = _parse_date(trade_date) or phase.get("trade_date") or datetime.now().date()
            action = (action or "fill").strip().lower()
            if action not in ("fill", "status", "index"):
                return {"ok": False, "error": "action 应为 fill / status / index"}

            if action == "index":
                codes = self._load_mainboard_index(d)
                return {
                    "ok": True,
                    "action": "index",
                    "trade_date": d.isoformat(),
                    "count": len(codes),
                    "codes": codes,
                    "redis_index_key": self.MAINBOARD_INDEX_KEY.format(ymd=d.strftime("%Y%m%d")),
                    "redis_bar_key_pattern": "intraday:{ts_code}:" + d.strftime("%Y%m%d"),
                    "note": "用索引 codes 批量读 Redis 单票 key，即可组装 LightGBM 训练样本。",
                }

            universe = self._list_mainboard_codes(
                d, only_traded=bool(only_traded), exclude_st=bool(exclude_st)
            )
            total = len(universe)
            if total == 0:
                return {
                    "ok": False,
                    "error": (
                        f"{d.isoformat()} 主板股票池为空。"
                        "若 only_traded=true，请先确保当日 stock_daily 已入库；"
                        "或设 only_traded=false 用 ref_stock_basic 全列表。"
                    ),
                    "trade_date": d.isoformat(),
                }

            offset = max(0, int(offset or 0))
            if limit is None or int(limit) <= 0:
                slice_u = universe[offset:]
            else:
                slice_u = universe[offset : offset + int(limit)]

            # status：只扫本地覆盖
            if action == "status":
                redis_ok = pg_ok = both = 0
                samples = []
                for item in slice_u:
                    code = item["ts_code"]
                    cnt = self._mins_bar_count_local(code, d)
                    if cnt["redis"] >= int(min_bars):
                        redis_ok += 1
                    if cnt["pg"] >= int(min_bars):
                        pg_ok += 1
                    if cnt["best"] >= int(min_bars):
                        both += 1
                    if len(samples) < 8:
                        samples.append({"ts_code": code, **cnt})
                indexed = self._load_mainboard_index(d)
                return {
                    "ok": True,
                    "action": "status",
                    "trade_date": d.isoformat(),
                    "universe_total": total,
                    "checked": len(slice_u),
                    "offset": offset,
                    "min_bars": int(min_bars),
                    "redis_ready": redis_ok,
                    "pg_ready": pg_ok,
                    "local_ready": both,
                    "coverage_pct": round(100.0 * both / max(1, len(slice_u)), 2),
                    "index_count": len(indexed),
                    "samples": samples,
                    "redis_index_key": self.MAINBOARD_INDEX_KEY.format(ymd=d.strftime("%Y%m%d")),
                    "next_hint": (
                        "覆盖不足时调用 fill-mainboard-mins action=fill；"
                        "可设 limit/offset 分批，或一次拉全量（较慢）。"
                    ),
                }

            # fill
            max_workers = max(1, min(int(max_workers or 6), 12))
            min_bars = max(1, int(min_bars or 60))
            source = (source or "auto").strip().lower()
            ok_codes: List[str] = []
            skipped: List[str] = []
            failed: List[Dict[str, str]] = []
            fetched_n = 0
            redis_written_n = 0

            # 先并入已有索引，便于增量
            existing_index = set(self._load_mainboard_index(d))

            def one(item: Dict[str, str]) -> Dict[str, Any]:
                code = item["ts_code"]
                local = self._mins_bar_count_local(code, d)
                if skip_existing and local["best"] >= min_bars:
                    # 确保 Redis 也有一份（训练优先 Redis）
                    if local["redis"] < min_bars and write_back and local["pg"] >= min_bars:
                        try:
                            packed = self.intraday.get(
                                ts_code=code,
                                trade_date=d.strftime("%Y%m%d"),
                                history=False,
                            )
                            # get() 会在有数据时写入 redis
                            return {
                                "ts_code": code,
                                "status": "skipped_warm_redis",
                                "bars": int((packed or {}).get("count") or local["best"]),
                                "redis_written": bool((packed or {}).get("redis_written")),
                                "source": (packed or {}).get("source") or "local",
                            }
                        except Exception:
                            pass
                    return {
                        "ts_code": code,
                        "status": "skipped_existing",
                        "bars": local["best"],
                        "redis_written": local["redis"] >= min_bars,
                        "source": "local",
                    }
                try:
                    packed = self.get_mins(
                        ts_code=code,
                        trade_date=d.isoformat(),
                        limit=500,
                        source=source if source in ("auto", "pg", "datahubco") else "auto",
                        write_back=bool(write_back),
                        include_auction=bool(include_auction),
                    )
                    if not packed.get("ok"):
                        return {
                            "ts_code": code,
                            "status": "failed",
                            "error": str(packed.get("error") or "无分时"),
                        }
                    n = int(packed.get("count") or 0)
                    # get_mins auto 路径可能已写 Redis；再确认
                    redis_n = self._mins_bar_count_local(code, d)["redis"]
                    return {
                        "ts_code": code,
                        "status": "ok" if n >= min_bars else "thin",
                        "bars": n,
                        "redis_bars": redis_n,
                        "redis_written": redis_n > 0,
                        "source": packed.get("source_used"),
                        "fetched_remote": bool(packed.get("fetched_remote")),
                    }
                except Exception as e:
                    return {"ts_code": code, "status": "failed", "error": str(e)[:200]}

            from concurrent.futures import ThreadPoolExecutor, as_completed

            results = []
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futs = {pool.submit(one, it): it["ts_code"] for it in slice_u}
                for fut in as_completed(futs):
                    results.append(fut.result())

            for r in results:
                st = r.get("status")
                code = r.get("ts_code")
                if st in ("ok", "skipped_existing", "skipped_warm_redis", "thin"):
                    if int(r.get("bars") or 0) > 0 or int(r.get("redis_bars") or 0) > 0:
                        ok_codes.append(code)
                    if st.startswith("skipped"):
                        skipped.append(code)
                    if r.get("fetched_remote"):
                        fetched_n += 1
                    if r.get("redis_written"):
                        redis_written_n += 1
                    if st == "thin" and int(r.get("bars") or 0) < min_bars:
                        failed.append({"ts_code": code, "error": f"分钟数不足 bars={r.get('bars')}"})
                else:
                    failed.append({"ts_code": code, "error": r.get("error") or st})

            all_ready = sorted(set(existing_index) | set(ok_codes))
            index_ok = self._save_mainboard_index(d, all_ready)
            next_offset = offset + len(slice_u)
            more = next_offset < total

            return {
                "ok": True,
                "action": "fill",
                "trade_date": d.isoformat(),
                "universe_total": total,
                "batch_size": len(slice_u),
                "offset": offset,
                "next_offset": next_offset if more else None,
                "has_more": more,
                "ok_count": len(ok_codes),
                "skipped_count": len(skipped),
                "failed_count": len(failed),
                "fetched_remote": fetched_n,
                "redis_written": redis_written_n,
                "index_count": len(all_ready),
                "index_saved": index_ok,
                "redis_index_key": self.MAINBOARD_INDEX_KEY.format(ymd=d.strftime("%Y%m%d")),
                "redis_bar_key_pattern": "intraday:{ts_code}:" + d.strftime("%Y%m%d"),
                "failed_samples": failed[:20],
                "ok_samples": ok_codes[:20],
                "training_hint": (
                    "LightGBM：先 action=index 取 codes，再按 intraday:{code}:{ymd} 从 Redis "
                    "读 bars（含 open/high/low/close/vol/amount）。也可用 PG stock_intraday_mins。"
                ),
                "next_hint": (
                    f"还有剩余，请用 offset={next_offset} 继续调用。"
                    if more
                    else "本批已覆盖当前切片；可用 action=status 检查全市场覆盖。"
                ),
            }
        except Exception as e:
            logger.exception("fill_mainboard_mins failed")
            return {"ok": False, "error": str(e)}

    async def fill_mainboard_mins_async(self, **kwargs) -> Dict[str, Any]:
        return await asyncio.to_thread(self.fill_mainboard_mins, **kwargs)

    # async wrappers
    async def list_datasets_async(self) -> Dict[str, Any]:
        return await asyncio.to_thread(self.list_datasets)

    async def coverage_async(self, **kwargs) -> Dict[str, Any]:
        return await asyncio.to_thread(self.coverage, **kwargs)

    async def get_bars_async(self, **kwargs) -> Dict[str, Any]:
        return await asyncio.to_thread(self.get_bars, **kwargs)

    async def fill_sector_gap_async(self, **kwargs) -> Dict[str, Any]:
        return await asyncio.to_thread(self.fill_sector_gap, **kwargs)

    async def fill_sector_history_async(self, **kwargs) -> Dict[str, Any]:
        return await asyncio.to_thread(self.fill_sector_history, **kwargs)

    async def fill_market_history_async(self, **kwargs) -> Dict[str, Any]:
        return await asyncio.to_thread(self.fill_market_history, **kwargs)

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

    async def get_fund_data_async(self, **kwargs) -> Dict[str, Any]:
        return await asyncio.to_thread(self.get_fund_data, **kwargs)

    async def get_stock_sector_moneyflow_async(self, **kwargs) -> Dict[str, Any]:
        return await asyncio.to_thread(self.get_stock_sector_moneyflow, **kwargs)


datahubco_toolkit = DatahubcoToolkit()

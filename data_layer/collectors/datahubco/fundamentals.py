"""
Datahubco 资金 / 基本面补数（→ PostgreSQL）

覆盖：
  - stock_top_list_daily      top_list 龙虎榜
  - stock_block_trade         block_trade 大宗交易
  - margin_summary_daily      margin 两融汇总
  - margin_detail_daily       margin_detail 两融明细
  - stock_moneyflow_daily     moneyflow 个股资金流
  - stock_hk_hold             hk_hold 北向/港股通持股
  - fin_cashflow              cashflow 现金流量表
  - fin_forecast              forecast 业绩预告
  - stock_top10_holders       top10_floatholders（网关无 top10_holders 时用流通股东）
  - sector_moneyflow_daily    moneyflow_ind_dc 板块资金（含个股 L2/L3）
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd
from sqlalchemy import text as sa_text

from data_layer.db_manager import storage_manager

from .client import DatahubcoClient
from .selective import SelectiveDataFetcher

logger = logging.getLogger("collector.datahubco.fundamentals")

# dataset → 元数据
FUND_DATASETS: Dict[str, Dict[str, Any]] = {
    "top_list": {
        "table": "stock_top_list_daily",
        "api": "top_list",
        "desc": "龙虎榜",
        "conflict": "ts_code, trade_date",
        "date_col": "trade_date",
        "scope": "market_or_stock",
    },
    "block_trade": {
        "table": "stock_block_trade",
        "api": "block_trade",
        "desc": "大宗交易",
        "conflict": "ts_code, trade_date, price, vol, buyer, seller",
        "date_col": "trade_date",
        "scope": "market_or_stock",
        "replace_mode": "day_stock",  # 同日多笔：按日+股删后插
    },
    "margin": {
        "table": "margin_summary_daily",
        "api": "margin",
        "desc": "融资融券汇总（交易所）",
        "conflict": "trade_date, exchange_id",
        "date_col": "trade_date",
        "scope": "market",
    },
    "margin_detail": {
        "table": "margin_detail_daily",
        "api": "margin_detail",
        "desc": "融资融券明细（个股）",
        "conflict": "trade_date, ts_code",
        "date_col": "trade_date",
        "scope": "stock",
    },
    "moneyflow": {
        "table": "stock_moneyflow_daily",
        "api": "moneyflow",
        "desc": "个股资金流向",
        "conflict": "ts_code, trade_date",
        "date_col": "trade_date",
        "scope": "stock",
    },
    "hk_hold": {
        "table": "stock_hk_hold",
        "api": "hk_hold",
        "desc": "北向/港股通持股",
        "conflict": "ts_code, trade_date",
        "date_col": "trade_date",
        "scope": "stock_or_market",
    },
    "cashflow": {
        "table": "fin_cashflow",
        "api": "cashflow",
        "desc": "现金流量表",
        "conflict": "ts_code, end_date, report_type",
        "date_col": "end_date",
        "scope": "stock",
        "fin": True,
    },
    "forecast": {
        "table": "fin_forecast",
        "api": "forecast",
        "desc": "业绩预告",
        "conflict": "ts_code, ann_date, end_date",
        "date_col": "end_date",
        "scope": "stock",
        "fin": True,
    },
    "top10_holders": {
        "table": "stock_top10_holders",
        "api": "top10_floatholders",  # 网关暂无 top10_holders
        "api_fallback": ["top10_holders"],
        "desc": "十大股东（优先流通股东 top10_floatholders）",
        "conflict": "ts_code, end_date, holder_name",
        "date_col": "end_date",
        "scope": "stock",
        "fin": True,
    },
    "sector_moneyflow": {
        "table": "sector_moneyflow_daily",
        "api": "moneyflow_ind_dc",
        "desc": "东财行业/概念板块资金流向",
        "conflict": "ts_code, trade_date, content_type",
        "date_col": "trade_date",
        "scope": "market",
    },
}

EXTRA_DDL = [
    """
    CREATE TABLE IF NOT EXISTS stock_hk_hold (
        ts_code     VARCHAR(16)  NOT NULL,
        trade_date  DATE         NOT NULL,
        code        VARCHAR(16),
        name        VARCHAR(64),
        vol         NUMERIC(24,4),
        ratio       NUMERIC(12,6),
        exchange    VARCHAR(8),
        updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (ts_code, trade_date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS stock_top10_holders (
        ts_code            VARCHAR(16)  NOT NULL,
        ann_date           DATE,
        end_date           DATE         NOT NULL,
        holder_name        VARCHAR(200) NOT NULL,
        hold_amount        NUMERIC(24,4),
        hold_ratio         NUMERIC(12,6),
        hold_float_ratio   NUMERIC(12,6),
        hold_change        NUMERIC(24,4),
        holder_type        VARCHAR(64),
        holder_kind        VARCHAR(32)  DEFAULT 'float',
        updated_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (ts_code, end_date, holder_name)
    )
    """,
]

# 大宗交易：旧表 PK=(ts_code,trade_date) 无法存同日多笔，迁移到明细唯一键
BLOCK_TRADE_MIGRATE = [
    "ALTER TABLE stock_block_trade DROP CONSTRAINT IF EXISTS stock_block_trade_pkey",
    "ALTER TABLE stock_block_trade DROP CONSTRAINT IF EXISTS \"441_stock_block_trade_pkey\"",
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_stock_block_trade_detail
    ON stock_block_trade (ts_code, trade_date, price, vol, buyer, seller)
    """,
]


def _parse_dates(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series.astype(str), errors="coerce").dt.date


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


class FundDataFetcher:
    """资金 / 基本面：Datahubco 拉取 + PostgreSQL upsert"""

    def __init__(self, client: Optional[DatahubcoClient] = None):
        self.client = client or DatahubcoClient()
        self._engine = None
        self._tables_ready = False
        self._selective: Optional[SelectiveDataFetcher] = None

    @property
    def engine(self):
        if self._engine is None:
            self._engine = storage_manager.get_postgres_engine()
        return self._engine

    @property
    def selective(self) -> SelectiveDataFetcher:
        if self._selective is None:
            self._selective = SelectiveDataFetcher(client=self.client)
        return self._selective

    def ensure_tables(self) -> None:
        if self._tables_ready or not self.engine:
            return
        with self.engine.begin() as conn:
            for ddl in EXTRA_DDL:
                try:
                    conn.execute(sa_text(ddl))
                except Exception as e:
                    logger.debug("fund DDL skip/warn: %s", e)
            for ddl in BLOCK_TRADE_MIGRATE:
                try:
                    conn.execute(sa_text(ddl))
                except Exception as e:
                    logger.debug("block_trade migrate: %s", e)
        self._tables_ready = True

    def list_datasets(self) -> List[Dict[str, Any]]:
        out = []
        for k, meta in FUND_DATASETS.items():
            out.append({
                "dataset": k,
                "table": meta["table"],
                "api": meta["api"],
                "desc": meta["desc"],
                "scope": meta.get("scope"),
            })
        return out

    def resolve_asof(self, trade_date: Optional[str] = None) -> Dict[str, Any]:
        if trade_date:
            d = _parse_date(trade_date)
            if d:
                return {"asof_date": d, "ymd": d.strftime("%Y%m%d")}
        ctx = self.selective.session_context()
        asof: date = ctx["asof_date"]
        return {"asof_date": asof, "ymd": asof.strftime("%Y%m%d"), "session": ctx}

    # ─── 统一入口 ─────────────────────────────────────

    def get(
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
        try:
            meta = self._resolve_meta(dataset)
        except ValueError as e:
            return {"ok": False, "error": str(e)}

        source = (source or "auto").strip().lower()
        if source in ("remote", "datahub", "dh"):
            source = "datahubco"
        if source not in ("auto", "pg", "datahubco"):
            return {"ok": False, "error": f"无效 source={source}"}

        self.ensure_tables()
        asof = self.resolve_asof(trade_date or end_date)
        code = self._norm_code(ts_code) if ts_code else None
        start = _parse_date(start_date)
        end = _parse_date(end_date) or asof["asof_date"]
        if start is None and not period and meta.get("fin"):
            # 财务默认近 2 年（网关对大区间会拒）
            start = date(end.year - 1, 1, 1)
        elif start is None and not trade_date and meta.get("date_col") == "trade_date":
            start = end - timedelta(days=30)

        limit = max(1, min(int(limit or 500), 5000))
        table = meta["table"]

        if source == "pg":
            df = self._read_pg(meta, code, start, end, period, limit)
            return self._ok(meta, code, df, "pg", False, 0, limit)

        if source == "datahubco":
            remote = self._fetch_remote(
                meta, code, start, end, asof["ymd"], period, content_type
            )
            upserted = 0
            if write_back and remote is not None and not remote.empty:
                upserted = self._upsert(meta, remote)
            df = remote
            if write_back and upserted:
                pg = self._read_pg(meta, code, start, end, period, limit)
                if pg is not None and not pg.empty:
                    df = pg
            return self._ok(
                meta, code, df,
                "datahubco+pg" if upserted else "datahubco",
                True, upserted, limit,
            )

        # auto
        cover = self._pg_count(meta, code, start, end, period)
        need = cover <= 0 or (
            meta.get("date_col") == "trade_date"
            and trade_date
            and cover <= 0
        )
        # 区间模式：行数过少也补
        if not need and start and end and meta.get("date_col") == "trade_date":
            expect = max(1, int((end - start).days * 0.5))
            need = cover < min(expect, 5)

        fetched = False
        upserted = 0
        if need:
            remote = self._fetch_remote(
                meta, code, start, end, asof["ymd"], period, content_type
            )
            fetched = True
            if write_back and remote is not None and not remote.empty:
                upserted = self._upsert(meta, remote)
            df = self._read_pg(meta, code, start, end, period, limit)
            if df is None or df.empty:
                df = remote
            return self._ok(
                meta, code, df,
                "datahubco+pg" if upserted else ("datahubco" if fetched else "pg"),
                fetched, upserted, limit,
            )

        df = self._read_pg(meta, code, start, end, period, limit)
        return self._ok(meta, code, df, "pg", False, 0, limit)

    def get_stock_sector_moneyflow(
        self,
        ts_code: str,
        trade_date: Optional[str] = None,
        levels: Optional[Sequence[str]] = None,
        source: str = "auto",
        write_back: bool = True,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """
        查个股所属二级/三级行业板块的资金走向。
        依赖 sector_dc_member + sector_dc_index_daily + sector_moneyflow_daily。
        """
        code = self._norm_code(ts_code)
        if not code:
            return {"ok": False, "error": "缺少 ts_code"}
        levels = list(levels or ("东财二级行业", "东财三级行业"))
        asof = self.resolve_asof(trade_date)
        d: date = asof["asof_date"]
        ymd = asof["ymd"]
        source = (source or "auto").strip().lower()
        if source in ("remote", "datahub", "dh"):
            source = "datahubco"

        self.ensure_tables()
        upserted = 0
        notes: List[str] = []

        # 必要时补板块资金全日
        if source in ("auto", "datahubco"):
            try:
                from .sectors import sector_fetcher

                sector_fetcher.ensure_tables()
                need_mf = self._sector_mf_day_count(d) <= 0
                if source == "datahubco" or need_mf:
                    try:
                        mf = sector_fetcher.fetch_moneyflow(ymd, content_type="行业")
                        if write_back and mf is not None and not mf.empty:
                            upserted += int(sector_fetcher.upsert_moneyflow(mf))
                        elif mf is None or mf.empty:
                            notes.append("moneyflow_ind_dc 无数据或接口不可用")
                    except Exception as e:
                        notes.append(f"板块资金远端失败: {e}")
            except Exception as e:
                notes.append(f"sector_fetcher 不可用: {e}")

        sectors = self._find_stock_sectors(code, d, levels)
        if not sectors and source != "pg":
            # 尝试用当日成分表再查一遍（可能 asof 无成分）
            sectors = self._find_stock_sectors_any_recent(code, levels, lookback=30)

        if not sectors:
            return {
                "ok": True,
                "ts_code": code,
                "trade_date": d.isoformat(),
                "levels": list(levels),
                "sectors": [],
                "moneyflow": [],
                "upserted": upserted,
                "count": 0,
                "note": "未在 sector_dc_member 中找到该股所属二级/三级板块；请先同步板块成分。",
                "notes": notes,
            }

        sector_codes = [s["sector_code"] for s in sectors]
        # 成分日可能与 asof 不一致：先用 asof，再回退到成分所在日 / 最近有资金的日期
        mf_dates = sorted({
            _parse_date(s.get("trade_date")) or d for s in sectors
        } | {d})
        mf_df = pd.DataFrame()
        for md in sorted(mf_dates, reverse=True):
            mf_df = self._read_sector_moneyflow(sector_codes, md)
            if mf_df is not None and not mf_df.empty:
                d = md
                break
        if mf_df is None or mf_df.empty:
            mf_df = self._read_sector_moneyflow_latest(sector_codes)
        records = self._df_records(mf_df, limit=limit)

        # 合并层级信息
        level_map = {s["sector_code"]: s for s in sectors}
        for r in records:
            sc = str(r.get("ts_code") or "")
            info = level_map.get(sc) or {}
            r["industry_level"] = info.get("industry_level")
            r["sector_name"] = info.get("sector_name") or r.get("name")

        return {
            "ok": True,
            "ts_code": code,
            "trade_date": d.isoformat(),
            "levels": list(levels),
            "sectors": sectors,
            "moneyflow": records,
            "count": len(records),
            "upserted": upserted,
            "source_used": "datahubco+pg" if upserted else "pg",
            "notes": notes or None,
        }

    # ─── fetch ────────────────────────────────────────

    def _fetch_remote(
        self,
        meta: Dict[str, Any],
        ts_code: Optional[str],
        start: Optional[date],
        end: date,
        asof_ymd: str,
        period: Optional[str],
        content_type: str,
    ) -> pd.DataFrame:
        api = meta["api"]
        name = meta.get("name") or ""
        kwargs: Dict[str, Any] = {}

        if meta.get("fin"):
            if ts_code:
                kwargs["ts_code"] = ts_code
            if period:
                kwargs["period"] = _to_ymd(period) or period
            else:
                # 网关拒过大区间：按年切片后合并
                return self._fetch_fin_chunked(api, ts_code, start, end, meta)

        elif api == "margin":
            kwargs["trade_date"] = asof_ymd if not start else (end.strftime("%Y%m%d"))
            # 若给了区间，按日拉取较慢；优先单日
            if start and (end - start).days > 0:
                frames = []
                d = start
                while d <= end:
                    try:
                        part = self.client.query(api, trade_date=d.strftime("%Y%m%d"))
                        if part is not None and not part.empty:
                            frames.append(part)
                    except Exception as e:
                        logger.debug("margin %s: %s", d, e)
                    d += timedelta(days=1)
                df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
                return self._normalize(df, meta)

        elif api == "sector_moneyflow" or api == "moneyflow_ind_dc":
            kwargs["trade_date"] = asof_ymd
            kwargs["content_type"] = content_type or "行业"

        elif api in ("top_list", "block_trade", "hk_hold"):
            if api == "top_list":
                # 网关要求必填 trade_date；区间则按日循环
                if start and end and (end - start).days > 0:
                    frames = []
                    d = start
                    while d <= end:
                        try:
                            part = self.client.query(api, trade_date=d.strftime("%Y%m%d"))
                            if part is not None and not part.empty:
                                frames.append(part)
                        except Exception as e:
                            logger.debug("top_list %s: %s", d, e)
                        d += timedelta(days=1)
                    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
                    out = self._normalize(df, meta)
                    if ts_code and not out.empty and "ts_code" in out.columns:
                        out = out.loc[out["ts_code"].astype(str).str.upper() == ts_code.upper()]
                    return out.reset_index(drop=True)
                kwargs["trade_date"] = asof_ymd
                if ts_code:
                    # 全日拉取后过滤（单日 + 个股）
                    pass
            elif api == "hk_hold":
                if ts_code and start and end:
                    kwargs["ts_code"] = ts_code
                    kwargs["start_date"] = start.strftime("%Y%m%d")
                    kwargs["end_date"] = end.strftime("%Y%m%d")
                elif ts_code:
                    kwargs["ts_code"] = ts_code
                    kwargs["start_date"] = (end - timedelta(days=14)).strftime("%Y%m%d")
                    kwargs["end_date"] = end.strftime("%Y%m%d")
                else:
                    # 全日：优先沪深北向；空则回退不限交易所（含港股通）
                    frames = []
                    for ex in ("SH", "SZ"):
                        try:
                            part = self.client.query(
                                api, trade_date=asof_ymd, exchange=ex
                            )
                            if part is not None and not part.empty:
                                frames.append(part)
                        except Exception as e:
                            logger.debug("hk_hold %s: %s", ex, e)
                    if not frames:
                        try:
                            part = self.client.query(api, trade_date=asof_ymd)
                            if part is not None and not part.empty:
                                frames.append(part)
                        except Exception as e:
                            logger.debug("hk_hold all: %s", e)
                    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
                    return self._normalize(df, meta)
            else:  # block_trade
                if ts_code and start and end:
                    kwargs["ts_code"] = ts_code
                    kwargs["start_date"] = start.strftime("%Y%m%d")
                    kwargs["end_date"] = end.strftime("%Y%m%d")
                elif ts_code:
                    kwargs["ts_code"] = ts_code
                    kwargs["trade_date"] = asof_ymd
                else:
                    kwargs["trade_date"] = asof_ymd

        elif api in ("moneyflow", "margin_detail"):
            if not ts_code:
                raise ValueError(f"{api} 需要 ts_code")
            kwargs["ts_code"] = ts_code
            if start and end:
                kwargs["start_date"] = start.strftime("%Y%m%d")
                kwargs["end_date"] = end.strftime("%Y%m%d")
            else:
                kwargs["trade_date"] = asof_ymd

        elif api == "top10_floatholders":
            if not ts_code:
                raise ValueError("top10_holders 需要 ts_code")
            kwargs["ts_code"] = ts_code
            if period:
                kwargs["period"] = _to_ymd(period) or period
            elif start and end:
                kwargs["start_date"] = start.strftime("%Y%m%d")
                kwargs["end_date"] = end.strftime("%Y%m%d")
            else:
                # 最近年报期末
                kwargs["period"] = f"{end.year - 1}1231" if end.month < 4 else f"{end.year}0630"

        # 尝试主 API，失败则 fallback
        apis = [api] + list(meta.get("api_fallback") or [])
        # top10: 先试正式名再流通
        if name == "top10_holders" or meta.get("table") == "stock_top10_holders":
            apis = ["top10_holders", "top10_floatholders"]

        last_err = None
        df = pd.DataFrame()
        used_api = api
        for a in apis:
            try:
                if a == "moneyflow_ind_dc":
                    df = self.client.query(a, trade_date=asof_ymd, content_type=content_type or "行业")
                else:
                    use_kwargs = dict(kwargs)
                    if a == "top10_holders" and "start_date" in use_kwargs:
                        # top10_holders 常只认 period
                        use_kwargs.pop("start_date", None)
                        use_kwargs.pop("end_date", None)
                        if "period" not in use_kwargs:
                            use_kwargs["period"] = f"{end.year}1231"
                    df = self.client.query(a, **use_kwargs)
                used_api = a
                if df is not None and not df.empty:
                    break
                # 空结果也算成功（如无业绩预告）
                if df is not None:
                    break
            except Exception as e:
                last_err = e
                logger.warning("fetch %s failed: %s", a, e)
                continue

        if (df is None or df.empty) and last_err and apis and used_api not in apis:
            raise RuntimeError(str(last_err))

        out = self._normalize(df if df is not None else pd.DataFrame(), meta)
        if not out.empty and meta.get("table") == "stock_top10_holders":
            out["holder_kind"] = "float" if used_api == "top10_floatholders" else "total"
        # top_list 全日拉取后按个股过滤
        if (
            not out.empty
            and ts_code
            and meta.get("api") == "top_list"
            and "ts_code" in out.columns
        ):
            out = out.loc[out["ts_code"].astype(str).str.upper() == ts_code.upper()].reset_index(drop=True)
        return out

    def _fetch_fin_chunked(
        self,
        api: str,
        ts_code: Optional[str],
        start: Optional[date],
        end: date,
        meta: Dict[str, Any],
    ) -> pd.DataFrame:
        if not ts_code:
            return pd.DataFrame()
        start = start or date(end.year - 1, 1, 1)
        frames: List[pd.DataFrame] = []
        y = start.year
        while y <= end.year:
            s = max(start, date(y, 1, 1)).strftime("%Y%m%d")
            e = min(end, date(y, 12, 31)).strftime("%Y%m%d")
            try:
                part = self.client.query(api, ts_code=ts_code, start_date=s, end_date=e)
                if part is not None and not part.empty:
                    frames.append(part)
            except Exception as err:
                logger.warning("%s %s %s-%s: %s", api, ts_code, s, e, err)
                # 再试 period 年报/中报
                for p in (f"{y}1231", f"{y}0630", f"{y}0930", f"{y}0331"):
                    try:
                        part = self.client.query(api, ts_code=ts_code, period=p)
                        if part is not None and not part.empty:
                            frames.append(part)
                    except Exception:
                        pass
            y += 1
        if not frames:
            return pd.DataFrame()
        return self._normalize(pd.concat(frames, ignore_index=True), meta)

    def _normalize(self, df: pd.DataFrame, meta: Dict[str, Any]) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame()
        use = df.copy()
        use.columns = [str(c).lower() for c in use.columns]

        for col in ("trade_date", "ann_date", "f_ann_date", "end_date", "first_ann_date"):
            if col in use.columns:
                use[col] = _parse_dates(use[col])

        if "ts_code" in use.columns:
            use["ts_code"] = use["ts_code"].astype(str).str.upper()

        # cashflow: report_type 不可空；varchar(1) 字段清洗 nan
        if meta["table"] == "fin_cashflow":
            if "report_type" not in use.columns:
                use["report_type"] = "1"
            else:
                use["report_type"] = (
                    use["report_type"].astype(object)
                    .where(pd.notnull(use["report_type"]), "1")
                    .map(lambda x: "1" if x is None or (isinstance(x, float) and pd.isna(x)) else str(x)[:1])
                )
            for col in ("comp_type", "end_type", "update_flag"):
                if col in use.columns:
                    use[col] = use[col].astype(object).where(pd.notnull(use[col]), None)
                    use[col] = use[col].map(
                        lambda x: None if x is None or (isinstance(x, float) and pd.isna(x)) else str(x)[:1]
                    )

        # forecast: type 字段与 SQL 保留字冲突已用 type 列名
        if meta["table"] == "fin_forecast" and "type" in use.columns:
            pass

        # block_trade vol
        if "vol" in use.columns:
            use["vol"] = pd.to_numeric(use["vol"], errors="coerce")

        # hk_hold
        if meta["table"] == "stock_hk_hold":
            if "ts_code" not in use.columns and "code" in use.columns:
                # code 可能是 600519，需补后缀
                use["ts_code"] = use["code"].map(self._norm_code)
            for c in ("vol", "ratio"):
                if c in use.columns:
                    use[c] = pd.to_numeric(use[c], errors="coerce")

        # moneyflow numeric
        if meta["table"] == "stock_moneyflow_daily":
            for c in use.columns:
                if c not in ("ts_code", "trade_date"):
                    use[c] = pd.to_numeric(use[c], errors="coerce")

        # sector moneyflow
        if meta["table"] == "sector_moneyflow_daily":
            if "content_type" not in use.columns:
                use["content_type"] = "行业"
            if "rank" in use.columns and "rank_no" not in use.columns:
                use = use.rename(columns={"rank": "rank_no"})

        # 去重
        conflict = [x.strip() for x in meta["conflict"].split(",")]
        have = [c for c in conflict if c in use.columns]
        if have:
            use = use.drop_duplicates(subset=have, keep="last")

        return use.reset_index(drop=True)

    # ─── upsert / read ────────────────────────────────

    def _upsert(self, meta: Dict[str, Any], df: pd.DataFrame) -> int:
        if df is None or df.empty or not self.engine:
            return 0
        self.ensure_tables()
        table = meta["table"]
        cols = self._table_cols(table)
        use_cols = [c for c in cols if c in df.columns]
        if not use_cols:
            return 0

        # 大宗：先确保已迁移掉旧 PK，再按明细 upsert / replace
        if meta.get("replace_mode") == "day_stock" and "trade_date" in df.columns:
            return self._replace_block_trade(df)

        conflict = meta["conflict"]
        pk = [x.strip() for x in conflict.split(",")]
        # 若 conflict 列不全在 df，退化为简单 insert ignore
        if any(c not in df.columns for c in pk):
            logger.warning("upsert %s missing pk cols %s", table, pk)
            return 0

        rows = df[use_cols].where(pd.notnull(df[use_cols]), None)
        for c in rows.columns:
            if str(rows[c].dtype) == "Int64":
                rows[c] = rows[c].astype(object).where(rows[c].notna(), None)
        # 彻底清掉 float nan / pandas NA，避免写入 varchar 变成 "nan"
        records = []
        for rec in rows.to_dict(orient="records"):
            clean = {}
            for k, v in rec.items():
                if v is None:
                    clean[k] = None
                elif isinstance(v, float) and pd.isna(v):
                    clean[k] = None
                else:
                    try:
                        if pd.isna(v):
                            clean[k] = None
                            continue
                    except (ValueError, TypeError):
                        pass
                    clean[k] = v
            records.append(clean)

        placeholders = ", ".join(f":{c}" for c in use_cols)
        col_list = ", ".join(use_cols)
        updates = ", ".join(f"{c}=EXCLUDED.{c}" for c in use_cols if c not in pk)
        if "updated_at" not in use_cols and table in (
            "stock_hk_hold", "stock_top10_holders", "sector_moneyflow_daily"
        ):
            updates = (updates + ", " if updates else "") + "updated_at=CURRENT_TIMESTAMP"
        if not updates:
            sql = f"""
            INSERT INTO {table} ({col_list})
            VALUES ({placeholders})
            ON CONFLICT ({', '.join(pk)}) DO NOTHING
            """
        else:
            sql = f"""
            INSERT INTO {table} ({col_list})
            VALUES ({placeholders})
            ON CONFLICT ({', '.join(pk)}) DO UPDATE SET {updates}
            """
        try:
            with self.engine.begin() as conn:
                conn.execute(sa_text(sql), records)
            return len(records)
        except Exception as e:
            if table == "stock_block_trade":
                logger.warning("block_trade upsert fallback replace: %s", e)
                return self._replace_block_trade(df)
            logger.error("upsert %s failed: %s", table, e)
            raise

    def _replace_block_trade(self, df: pd.DataFrame) -> int:
        if df.empty or not self.engine:
            return 0
        self.ensure_tables()
        cols = ["ts_code", "trade_date", "price", "vol", "amount", "buyer", "seller"]
        use_cols = [c for c in cols if c in df.columns]
        rows = df[use_cols].copy()
        if "vol" in rows.columns:
            rows["vol"] = pd.to_numeric(rows["vol"], errors="coerce").round().astype("Int64")
        if "price" in rows.columns:
            rows["price"] = pd.to_numeric(rows["price"], errors="coerce").round(4)
        dedupe_cols = [c for c in ("ts_code", "trade_date", "price", "vol", "buyer", "seller") if c in rows.columns]
        if dedupe_cols:
            rows = rows.drop_duplicates(subset=dedupe_cols, keep="last")
        records = []
        for rec in rows.to_dict(orient="records"):
            clean = {}
            for k, v in rec.items():
                if v is None or (isinstance(v, float) and pd.isna(v)):
                    clean[k] = None
                elif hasattr(v, "item"):
                    try:
                        clean[k] = v.item()
                    except Exception:
                        clean[k] = None if pd.isna(v) else v
                else:
                    clean[k] = v
            records.append(clean)
        if not records:
            return 0
        placeholders = ", ".join(f":{c}" for c in use_cols)
        col_list = ", ".join(use_cols)
        sql = f"""
        INSERT INTO stock_block_trade ({col_list})
        VALUES ({placeholders})
        ON CONFLICT (ts_code, trade_date, price, vol, buyer, seller)
        DO UPDATE SET amount = EXCLUDED.amount
        """
        try:
            with self.engine.begin() as conn:
                conn.execute(sa_text(sql), records)
            return len(records)
        except Exception as e:
            # 唯一约束名因 hypertable 分片而不同：退化为逐行 upsert / ignore
            logger.warning("block_trade batch upsert failed, row-wise: %s", e)
            n = 0
            with self.engine.begin() as conn:
                for rec in records:
                    try:
                        conn.execute(sa_text(sql), [rec])
                        n += 1
                    except Exception:
                        try:
                            conn.execute(
                                sa_text(
                                    f"INSERT INTO stock_block_trade ({col_list}) "
                                    f"VALUES ({placeholders}) ON CONFLICT DO NOTHING"
                                ),
                                [rec],
                            )
                            n += 1
                        except Exception:
                            pass
            return n

    def _table_cols(self, table: str) -> List[str]:
        mapping = {
            "stock_top_list_daily": [
                "ts_code", "trade_date", "name", "close", "pct_change", "turnover_rate",
                "amount", "l_sell", "l_buy", "l_amount", "net_amount", "net_rate",
                "amount_rate", "float_values", "reason",
            ],
            "stock_block_trade": [
                "ts_code", "trade_date", "price", "vol", "amount", "buyer", "seller",
            ],
            "margin_summary_daily": [
                "trade_date", "exchange_id", "rzye", "rzmre", "rzche",
                "rqye", "rqmcl", "rzrqye",
            ],
            "margin_detail_daily": [
                "trade_date", "ts_code", "name", "rzye", "rqye", "rzmre",
                "rqyl", "rzche", "rqchl", "rqmcl", "rzrqye",
            ],
            "stock_moneyflow_daily": [
                "ts_code", "trade_date",
                "buy_sm_vol", "buy_sm_amount", "sell_sm_vol", "sell_sm_amount",
                "buy_md_vol", "buy_md_amount", "sell_md_vol", "sell_md_amount",
                "buy_lg_vol", "buy_lg_amount", "sell_lg_vol", "sell_lg_amount",
                "buy_elg_vol", "buy_elg_amount", "sell_elg_vol", "sell_elg_amount",
                "net_mf_vol", "net_mf_amount",
            ],
            "stock_hk_hold": [
                "ts_code", "trade_date", "code", "name", "vol", "ratio", "exchange",
            ],
            "fin_cashflow": [
                "ts_code", "ann_date", "f_ann_date", "end_date", "report_type",
                "comp_type", "end_type",
                "net_profit", "finan_exp", "c_fr_sale_sg", "recp_tax_rends",
                "n_depos_incr_fi", "n_incr_loans_cb", "n_inc_loans_oth_bank",
                "prem_fr_orig_contr", "n_incr_insured_dep", "n_reinsur_prem",
                "n_incr_disp_tfa", "ifc_cashincr", "n_incr_disp_faas",
                "n_incr_loans_oth_bank_f", "n_cap_incr_repur",
                "c_fr_oth_operate_a", "c_inf_fr_operate_a",
                "c_paid_goods_s", "c_paid_to_for_empl", "c_paid_for_taxes",
                "n_incr_clt_loan_adv", "n_incr_loans_cb_clt",
                "c_pay_claims_orig_inco", "pay_handling_chrg", "pay_comm_insur_plcy",
                "oth_cash_pay_oper_act", "st_cash_out_act", "n_cashflow_act",
                "c_recp_return_invest", "c_recp_income_invest", "c_recp_disp_fiolta",
                "c_recp_disp_sobu", "c_inf_fr_invest_act",
                "c_paid_acq_const_fiolta", "c_paid_invest", "c_paid_acq_sobu",
                "c_inf_pay_invest_act", "oth_cash_recp_ral_inv_act",
                "oth_cash_pay_ral_inv_act", "n_cashflow_inv_act",
                "c_recp_borrow", "c_proc_issue_bonds", "c_recp_cap_contrib",
                "c_incr_cash_pub", "c_recp_cap_othcash", "c_inf_fr_finance_act",
                "c_prepay_amt_borr", "c_pay_dist_dpcp_int_exp",
                "incr_cash_cash_equ_dm", "c_inf_pay_finance_act",
                "oth_cash_recp_ral_fnc_act", "oth_cash_pay_ral_fnc_act",
                "n_cashflow_fnc_act", "eff_fx_chg_cash", "n_cash",
                "n_cash_equ_beg", "n_cash_equ_end",
                "c_conv_debt_into_eq", "c_use_fund_cash", "free_cashflow",
                "update_flag",
            ],
            "fin_forecast": [
                "ts_code", "ann_date", "end_date", "type",
                "p_change_min", "p_change_max", "net_profit_min", "net_profit_max",
                "last_parent_net", "first_ann_date", "summary", "change_reason",
            ],
            "stock_top10_holders": [
                "ts_code", "ann_date", "end_date", "holder_name",
                "hold_amount", "hold_ratio", "hold_float_ratio", "hold_change",
                "holder_type", "holder_kind",
            ],
            "sector_moneyflow_daily": [
                "trade_date", "content_type", "ts_code", "name", "pct_change", "close",
                "net_amount", "net_amount_rate",
                "buy_elg_amount", "buy_elg_amount_rate",
                "buy_lg_amount", "buy_lg_amount_rate",
                "buy_md_amount", "buy_md_amount_rate",
                "buy_sm_amount", "buy_sm_amount_rate",
                "buy_sm_amount_stock", "rank_no",
            ],
        }
        return mapping.get(table, [])

    def _read_pg(
        self,
        meta: Dict[str, Any],
        ts_code: Optional[str],
        start: Optional[date],
        end: Optional[date],
        period: Optional[str],
        limit: int,
    ) -> pd.DataFrame:
        if not self.engine:
            return pd.DataFrame()
        table = meta["table"]
        date_col = meta.get("date_col") or "trade_date"
        where: List[str] = []
        params: Dict[str, Any] = {"lim": int(limit)}
        if ts_code and "ts_code" in self._table_cols(table):
            where.append("ts_code = :c")
            params["c"] = ts_code
        if period and date_col == "end_date":
            where.append("end_date = :p")
            params["p"] = _parse_date(period)
        else:
            if start:
                where.append(f"{date_col} >= :s")
                params["s"] = start
            if end:
                where.append(f"{date_col} <= :e")
                params["e"] = end
        wh = (" WHERE " + " AND ".join(where)) if where else ""
        sql = f"SELECT * FROM {table}{wh} ORDER BY {date_col} DESC LIMIT :lim"
        try:
            df = pd.read_sql(sa_text(sql), self.engine, params=params)
            if not df.empty and date_col in df.columns:
                df = df.sort_values(date_col).reset_index(drop=True)
            return df
        except Exception as e:
            logger.warning("read %s: %s", table, e)
            return pd.DataFrame()

    def _pg_count(
        self,
        meta: Dict[str, Any],
        ts_code: Optional[str],
        start: Optional[date],
        end: Optional[date],
        period: Optional[str],
    ) -> int:
        if not self.engine:
            return 0
        table = meta["table"]
        date_col = meta.get("date_col") or "trade_date"
        where: List[str] = []
        params: Dict[str, Any] = {}
        if ts_code and table not in ("margin_summary_daily",):
            where.append("ts_code = :c")
            params["c"] = ts_code
        if period and date_col == "end_date":
            where.append("end_date = :p")
            params["p"] = _parse_date(period)
        else:
            if start:
                where.append(f"{date_col} >= :s")
                params["s"] = start
            if end:
                where.append(f"{date_col} <= :e")
                params["e"] = end
        wh = (" WHERE " + " AND ".join(where)) if where else ""
        try:
            with self.engine.connect() as conn:
                return int(
                    conn.execute(sa_text(f"SELECT COUNT(1) FROM {table}{wh}"), params).scalar()
                    or 0
                )
        except Exception:
            return 0

    def _sector_mf_day_count(self, d: date) -> int:
        if not self.engine:
            return 0
        try:
            with self.engine.connect() as conn:
                return int(
                    conn.execute(
                        sa_text(
                            "SELECT COUNT(1) FROM sector_moneyflow_daily WHERE trade_date=:d"
                        ),
                        {"d": d},
                    ).scalar()
                    or 0
                )
        except Exception:
            return 0

    def _find_stock_sectors(
        self,
        ts_code: str,
        trade_date: date,
        levels: Sequence[str],
    ) -> List[Dict[str, Any]]:
        if not self.engine:
            return []
        from sqlalchemy import bindparam

        lv_list = list(levels)
        stmt = sa_text(
            """
            SELECT m.ts_code AS sector_code,
                   COALESCE(i.name, m.name) AS sector_name,
                   i.industry_level,
                   m.trade_date
            FROM sector_dc_member m
            LEFT JOIN sector_dc_index_daily i
              ON i.ts_code = m.ts_code AND i.trade_date = m.trade_date
            WHERE m.con_code = :c
              AND m.trade_date = :d
              AND i.industry_level IN :lv
            ORDER BY i.industry_level, m.ts_code
            """
        ).bindparams(bindparam("lv", expanding=True))
        try:
            with self.engine.connect() as conn:
                rows = conn.execute(
                    stmt,
                    {"c": ts_code, "d": trade_date, "lv": lv_list},
                ).fetchall()
            return [
                {
                    "sector_code": r[0],
                    "sector_name": r[1],
                    "industry_level": r[2],
                    "trade_date": r[3].isoformat() if hasattr(r[3], "isoformat") else str(r[3]),
                }
                for r in rows
            ]
        except Exception as e:
            logger.warning("find sectors: %s", e)
            return []

    def _find_stock_sectors_any_recent(
        self,
        ts_code: str,
        levels: Sequence[str],
        lookback: int = 30,
    ) -> List[Dict[str, Any]]:
        if not self.engine:
            return []
        from sqlalchemy import bindparam

        stmt = sa_text(
            """
            SELECT DISTINCT ON (m.ts_code)
                   m.ts_code AS sector_code,
                   COALESCE(i.name, m.name) AS sector_name,
                   i.industry_level,
                   m.trade_date
            FROM sector_dc_member m
            LEFT JOIN sector_dc_index_daily i
              ON i.ts_code = m.ts_code AND i.trade_date = m.trade_date
            WHERE m.con_code = :c
              AND m.trade_date >= CURRENT_DATE - :lb
              AND i.industry_level IN :lv
            ORDER BY m.ts_code, m.trade_date DESC
            """
        ).bindparams(bindparam("lv", expanding=True))
        try:
            with self.engine.connect() as conn:
                rows = conn.execute(
                    stmt,
                    {"c": ts_code, "lb": lookback, "lv": list(levels)},
                ).fetchall()
            return [
                {
                    "sector_code": r[0],
                    "sector_name": r[1],
                    "industry_level": r[2],
                    "trade_date": r[3].isoformat() if hasattr(r[3], "isoformat") else str(r[3]),
                }
                for r in rows
            ]
        except Exception as e:
            logger.warning("find sectors recent: %s", e)
            return []

    def _read_sector_moneyflow(
        self,
        sector_codes: Sequence[str],
        trade_date: date,
    ) -> pd.DataFrame:
        if not self.engine or not sector_codes:
            return pd.DataFrame()
        from sqlalchemy import bindparam

        stmt = sa_text(
            """
            SELECT * FROM sector_moneyflow_daily
            WHERE trade_date = :d AND ts_code IN :codes
            ORDER BY net_amount DESC NULLS LAST
            """
        ).bindparams(bindparam("codes", expanding=True))
        try:
            return pd.read_sql(
                stmt,
                self.engine,
                params={"d": trade_date, "codes": list(sector_codes)},
            )
        except Exception as e:
            logger.warning("read sector mf: %s", e)
            return pd.DataFrame()

    def _read_sector_moneyflow_latest(
        self,
        sector_codes: Sequence[str],
    ) -> pd.DataFrame:
        if not self.engine or not sector_codes:
            return pd.DataFrame()
        from sqlalchemy import bindparam

        stmt = sa_text(
            """
            SELECT m.*
            FROM sector_moneyflow_daily m
            INNER JOIN (
                SELECT ts_code, MAX(trade_date) AS trade_date
                FROM sector_moneyflow_daily
                WHERE ts_code IN :codes
                GROUP BY ts_code
            ) t ON m.ts_code = t.ts_code AND m.trade_date = t.trade_date
            ORDER BY m.net_amount DESC NULLS LAST
            """
        ).bindparams(bindparam("codes", expanding=True))
        try:
            return pd.read_sql(
                stmt,
                self.engine,
                params={"codes": list(sector_codes)},
            )
        except Exception as e:
            logger.warning("read sector mf latest: %s", e)
            return pd.DataFrame()

    # ─── helpers ──────────────────────────────────────

    def _resolve_meta(self, dataset: str) -> Dict[str, Any]:
        key = (dataset or "").strip().lower()
        aliases = {
            "龙虎榜": "top_list",
            "toplist": "top_list",
            "大宗": "block_trade",
            "大宗交易": "block_trade",
            "两融": "margin_detail",
            "融资融券": "margin_detail",
            "margin_summary": "margin",
            "北向": "hk_hold",
            "北向持股": "hk_hold",
            "hsgt": "hk_hold",
            "资金流": "moneyflow",
            "资金流向": "moneyflow",
            "现金流量表": "cashflow",
            "业绩预告": "forecast",
            "十大股东": "top10_holders",
            "top10": "top10_holders",
            "top10_floatholders": "top10_holders",
            "板块资金": "sector_moneyflow",
            "moneyflow_ind_dc": "sector_moneyflow",
        }
        key = aliases.get(key, key)
        if key not in FUND_DATASETS:
            raise ValueError(
                f"未知 dataset={dataset}，可选: {', '.join(FUND_DATASETS)}"
            )
        return {"name": key, **FUND_DATASETS[key]}

    def _norm_code(self, ts_code: Optional[str]) -> Optional[str]:
        if not ts_code:
            return None
        raw = str(ts_code).strip().upper()
        if not raw:
            return None
        if "." in raw:
            return raw
        if raw.startswith("BK"):
            return f"{raw}.DC"
        if raw.startswith(("5", "6", "9")):
            return f"{raw}.SH"
        if raw.startswith(("0", "1", "2", "3")):
            return f"{raw}.SZ"
        return raw

    @staticmethod
    def _df_records(df: Optional[pd.DataFrame], limit: int = 200) -> List[Dict[str, Any]]:
        if df is None or df.empty:
            return []
        use = df.head(int(limit)).copy()
        out: List[Dict[str, Any]] = []
        for _, row in use.iterrows():
            item: Dict[str, Any] = {}
            for k in use.columns:
                v = row[k]
                if v is None or (isinstance(v, float) and pd.isna(v)):
                    item[k] = None
                elif isinstance(v, (date, datetime)):
                    item[k] = v.isoformat()[:19] if isinstance(v, datetime) else v.isoformat()
                elif hasattr(v, "item"):
                    try:
                        item[k] = v.item()
                    except Exception:
                        item[k] = float(v) if pd.notna(v) else None
                elif pd.isna(v):
                    item[k] = None
                else:
                    item[k] = v
            out.append(item)
        return out

    def _ok(
        self,
        meta: Dict[str, Any],
        ts_code: Optional[str],
        df: Optional[pd.DataFrame],
        source_used: str,
        fetched: bool,
        upserted: int,
        limit: int,
    ) -> Dict[str, Any]:
        records = self._df_records(df, limit=limit)
        return {
            "ok": True,
            "dataset": meta.get("name"),
            "table": meta["table"],
            "api": meta["api"],
            "ts_code": ts_code,
            "source_used": source_used,
            "fetched_remote": fetched,
            "upserted": upserted,
            "count": len(records),
            "columns": list(df.columns) if df is not None and not df.empty else [],
            "data": records,
            "latest": records[-1] if records else None,
            "desc": meta.get("desc"),
        }


fund_data_fetcher = FundDataFetcher()

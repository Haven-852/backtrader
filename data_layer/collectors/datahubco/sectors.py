"""
东财行业板块持续采集（Datahubco → 东财行情兜底 → PostgreSQL）

表:
  - sector_dc_index_daily    涨跌幅/领涨股/涨跌家数/层级
  - sector_dc_daily          OHLC/成交额
  - sector_moneyflow_daily   资金流向
  - sector_dc_member         板块成分股
  - sector_dc_hierarchy      一级→二级→三级
  - ref_sector_dc_basic      板块目录（代码/名称/层级）

主源 Datahubco dc_*；接口不可用时改用东财 clist（最近收盘日含成交额）。
交易日 asof 规则复用 SelectiveDataFetcher.session_context：
  交易日闭市前 → 上一交易日；闭市后 → 当天；非交易日 → 上一交易日。
"""

from __future__ import annotations

import logging
import os
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Set
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from sqlalchemy import text as sa_text

from data_layer.db_manager import storage_manager

from .client import DatahubcoClient
from .selective import SelectiveDataFetcher

logger = logging.getLogger("collector.datahubco.sectors")

TZ_SH = ZoneInfo("Asia/Shanghai")
IDX_TYPE_INDUSTRY = "行业板块"
CONTENT_TYPE_INDUSTRY = "行业"

LEVELS = ("东财一级行业", "东财二级行业", "东财三级行业")

EM_CLIST_URLS = (
    "https://push2.eastmoney.com/api/qt/clist/get",
    "https://79.push2.eastmoney.com/api/qt/clist/get",
    "https://80.push2.eastmoney.com/api/qt/clist/get",
    "https://82.push2.eastmoney.com/api/qt/clist/get",
    "https://88.push2.eastmoney.com/api/qt/clist/get",
)
EM_KLINE_URLS = (
    "https://push2his.eastmoney.com/api/qt/stock/kline/get",
    "https://79.push2his.eastmoney.com/api/qt/stock/kline/get",
    "https://82.push2his.eastmoney.com/api/qt/stock/kline/get",
)
EM_CLIST_FIELDS = (
    "f12,f14,f2,f3,f4,f5,f6,f7,f8,f15,f16,f17,f18,f20,"
    "f62,f104,f105,f124,f128,f136,f140,f141"
)

DDL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS sector_dc_index_daily (
        ts_code        VARCHAR(16)  NOT NULL,
        trade_date     DATE         NOT NULL,
        name           VARCHAR(64),
        leading_name   VARCHAR(64),
        leading_code   VARCHAR(16),
        pct_change     NUMERIC(12,4),
        leading_pct    NUMERIC(12,4),
        total_mv       NUMERIC(20,4),
        turnover_rate  NUMERIC(12,4),
        up_num         INT,
        down_num       INT,
        idx_type       VARCHAR(32),
        industry_level VARCHAR(32),
        updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (ts_code, trade_date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sector_dc_daily (
        ts_code        VARCHAR(16)  NOT NULL,
        trade_date     DATE         NOT NULL,
        open           NUMERIC(16,4),
        high           NUMERIC(16,4),
        low            NUMERIC(16,4),
        close          NUMERIC(16,4),
        change         NUMERIC(16,4),
        pct_change     NUMERIC(12,4),
        vol            BIGINT,
        amount         NUMERIC(24,4),
        swing          NUMERIC(12,4),
        turnover_rate  NUMERIC(12,4),
        category       VARCHAR(32),
        updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (ts_code, trade_date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sector_moneyflow_daily (
        trade_date            DATE         NOT NULL,
        content_type          VARCHAR(16)  NOT NULL,
        ts_code               VARCHAR(16)  NOT NULL,
        name                  VARCHAR(64),
        pct_change            NUMERIC(12,4),
        close                 NUMERIC(16,4),
        net_amount            NUMERIC(24,4),
        net_amount_rate       NUMERIC(12,4),
        buy_elg_amount        NUMERIC(24,4),
        buy_elg_amount_rate   NUMERIC(12,4),
        buy_lg_amount         NUMERIC(24,4),
        buy_lg_amount_rate    NUMERIC(12,4),
        buy_md_amount         NUMERIC(24,4),
        buy_md_amount_rate    NUMERIC(12,4),
        buy_sm_amount         NUMERIC(24,4),
        buy_sm_amount_rate    NUMERIC(12,4),
        buy_sm_amount_stock   VARCHAR(64),
        rank_no               INT,
        updated_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (ts_code, trade_date, content_type)
    )
    """,
    """
        CREATE TABLE IF NOT EXISTS sector_dc_member (
            trade_date     DATE         NOT NULL,
            ts_code        VARCHAR(16)  NOT NULL,
            con_code       VARCHAR(16)  NOT NULL,
            name           VARCHAR(64),
            close          NUMERIC(16,4),
            pct_chg        NUMERIC(12,4),
            amount         NUMERIC(24,4),
            updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (ts_code, trade_date, con_code)
        )
    """,
    """
    CREATE TABLE IF NOT EXISTS sector_dc_hierarchy (
        child_code     VARCHAR(16)  NOT NULL,
        parent_code    VARCHAR(16)  NOT NULL,
        child_level    VARCHAR(32),
        parent_level   VARCHAR(32),
        child_name     VARCHAR(64),
        parent_name    VARCHAR(64),
        score          NUMERIC(8,4),
        method         VARCHAR(32)  DEFAULT 'member_containment',
        asof_date      DATE,
        updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (child_code)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ref_sector_dc_basic (
        ts_code        VARCHAR(16)  PRIMARY KEY,
        name           VARCHAR(64),
        industry_level VARCHAR(32),
        idx_type       VARCHAR(32)  DEFAULT '行业板块',
        updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
]

INDEX_DDL = [
    "CREATE INDEX IF NOT EXISTS idx_sector_dc_index_date ON sector_dc_index_daily (trade_date)",
    "CREATE INDEX IF NOT EXISTS idx_sector_dc_index_level ON sector_dc_index_daily (trade_date, industry_level)",
    "CREATE INDEX IF NOT EXISTS idx_sector_dc_daily_date ON sector_dc_daily (trade_date)",
    "CREATE INDEX IF NOT EXISTS idx_sector_dc_daily_code ON sector_dc_daily (ts_code, trade_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_sector_mf_date ON sector_moneyflow_daily (trade_date, content_type)",
    "CREATE INDEX IF NOT EXISTS idx_sector_member_code ON sector_dc_member (ts_code, trade_date)",
    "CREATE INDEX IF NOT EXISTS idx_sector_member_con ON sector_dc_member (con_code, trade_date)",
    "CREATE INDEX IF NOT EXISTS idx_sector_hierarchy_parent ON sector_dc_hierarchy (parent_code)",
    "CREATE INDEX IF NOT EXISTS idx_sector_hierarchy_levels ON sector_dc_hierarchy (parent_level, child_level)",
    "CREATE INDEX IF NOT EXISTS idx_ref_sector_dc_level ON ref_sector_dc_basic (industry_level)",
]

ALTER_DDL = [
    "ALTER TABLE sector_dc_member ADD COLUMN IF NOT EXISTS close NUMERIC(16,4)",
    "ALTER TABLE sector_dc_member ADD COLUMN IF NOT EXISTS pct_chg NUMERIC(12,4)",
    "ALTER TABLE sector_dc_member ADD COLUMN IF NOT EXISTS amount NUMERIC(24,4)",
]


def _to_ymd(d: Any) -> str:
    if isinstance(d, date):
        return d.strftime("%Y%m%d")
    s = str(d).replace("-", "")[:8]
    return s


def _parse_dates(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series.astype(str), errors="coerce").dt.date


def _norm_bk_code(code: Any) -> str:
    s = str(code or "").strip().upper()
    if s.startswith("BK") and "." not in s:
        return f"{s}.DC"
    return s


def _em_num(v: Any) -> Optional[float]:
    if v is None or v == "-" or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _em_int(v: Any) -> Optional[int]:
    n = _em_num(v)
    if n is None:
        return None
    return int(round(n))


def _em_proxies() -> Optional[Dict[str, str]]:
    """东财走公网，需保留系统代理；DatahubcoClient 会把 NO_PROXY=*。"""
    proxy = (
        os.environ.get("HTTPS_PROXY")
        or os.environ.get("https_proxy")
        or os.environ.get("HTTP_PROXY")
        or os.environ.get("http_proxy")
        or ""
    ).strip()
    if not proxy:
        try:
            from urllib.request import getproxies_registry
            regs = getproxies_registry() or {}
            proxy = (regs.get("https") or regs.get("http") or "").strip()
        except Exception:
            proxy = ""
    if proxy:
        return {"http": proxy, "https": proxy}
    return None


def _jsonable(obj: Any) -> Any:
    """把 date/datetime 转成 ISO 字符串，避免 HTTPException 二次 500。"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(x) for x in obj]
    return obj


def _em_stock_code(code: Any, market: Any) -> Optional[str]:
    if code is None or str(code).strip() in ("", "-", "nan"):
        return None
    s = str(code).strip().upper()
    if "." in s:
        return s
    if s.isdigit():
        s = s.zfill(6)
    m = str(market).strip()
    if m in ("1", "SH"):
        return f"{s}.SH"
    if m in ("2", "BJ"):
        return f"{s}.BJ"
    return f"{s}.SZ"


class SectorDataFetcher:
    """东财行业板块：按 asof 拉取并写入 PostgreSQL。"""

    def __init__(self, client: Optional[DatahubcoClient] = None):
        self._client = client
        self._engine = None
        self._selective = SelectiveDataFetcher(client=client)
        self._dc_dead = False

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

    def ensure_tables(self) -> Dict[str, Any]:
        if not self.engine:
            return {"ok": False, "error": "no engine"}
        with self.engine.begin() as conn:
            for ddl in DDL_STATEMENTS:
                conn.execute(sa_text(ddl))
            for idx in INDEX_DDL:
                conn.execute(sa_text(idx))
            for alt in ALTER_DDL:
                conn.execute(sa_text(alt))
        return {"ok": True, "tables": [
            "sector_dc_index_daily",
            "sector_dc_daily",
            "sector_moneyflow_daily",
            "sector_dc_member",
            "sector_dc_hierarchy",
            "ref_sector_dc_basic",
        ]}

    def resolve_asof(self, trade_date: Optional[str] = None) -> Dict[str, Any]:
        if trade_date:
            ymd = _to_ymd(trade_date)
            d = datetime.strptime(ymd, "%Y%m%d").date()
            return {"asof": d.isoformat(), "asof_date": d, "ymd": ymd, "reason": "explicit"}
        self._selective.ensure_trade_cal()
        ctx = self._selective.session_context()
        d: date = ctx["asof_date"]
        return {
            "asof": d.isoformat(),
            "asof_date": d,
            "ymd": d.strftime("%Y%m%d"),
            "reason": ctx.get("reason"),
            "phase": ctx.get("phase"),
            "is_trading_day": ctx.get("is_trading_day"),
        }

    def open_dates(self, start: date, end: date) -> List[date]:
        self._selective.ensure_trade_cal()
        return list(self._selective._expected_open_dates(start, end))

    # ─── 远端拉取 ─────────────────────────────────────

    def fetch_dc_index(self, ymd: str, idx_type: str = IDX_TYPE_INDUSTRY) -> pd.DataFrame:
        # Datahubco 现已强制 idx_type；个别旧网关不认该参数时再退回全日拉取。
        try:
            df = self.client.query("dc_index", trade_date=ymd, idx_type=idx_type)
        except RuntimeError as e:
            msg = str(e).lower()
            if "idx_type" in msg and "required" not in msg:
                df = self.client.query("dc_index", trade_date=ymd)
            else:
                raise
        if df.empty:
            return df
        df = df.copy()
        df.columns = [str(c).lower() for c in df.columns]
        if "idx_type" in df.columns:
            df = df.loc[df["idx_type"] == idx_type].copy()
        if "ts_code" in df.columns:
            df["ts_code"] = df["ts_code"].map(_norm_bk_code)
        df["trade_date"] = _parse_dates(df["trade_date"])
        if "leading" in df.columns and "leading_name" not in df.columns:
            df = df.rename(columns={"leading": "leading_name"})
        if "level" in df.columns and "industry_level" not in df.columns:
            df = df.rename(columns={"level": "industry_level"})
        for col in (
            "pct_change", "leading_pct", "total_mv", "turnover_rate",
        ):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        for col in ("up_num", "down_num"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
        return df

    def fetch_dc_daily(self, ymd: str, category: str = IDX_TYPE_INDUSTRY) -> pd.DataFrame:
        df = self.client.query("dc_daily", trade_date=ymd)
        if df.empty:
            return df
        df = df.copy()
        df.columns = [str(c).lower() for c in df.columns]
        if "amount" not in df.columns:
            for alt in ("amt", "amount_yuan", "turnover", "deal_amount"):
                if alt in df.columns:
                    df = df.rename(columns={alt: "amount"})
                    break
        if "ts_code" in df.columns:
            df["ts_code"] = df["ts_code"].map(_norm_bk_code)
        if "category" in df.columns:
            filtered = df.loc[df["category"] == category]
            if filtered.empty:
                aliases = {category, "行业", "行业板块", "东财行业"}
                filtered = df.loc[df["category"].isin(aliases)]
            if filtered.empty:
                logger.warning("dc_daily category=%s 无匹配，保留全部 %d 行", category, len(df))
            else:
                df = filtered.copy()
        df["trade_date"] = _parse_dates(df["trade_date"])
        for col in ("open", "high", "low", "close", "change", "pct_change", "amount", "swing", "turnover_rate"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        if "vol" in df.columns:
            df["vol"] = pd.to_numeric(df["vol"], errors="coerce").round().astype("Int64")
        return df

    def fetch_moneyflow(self, ymd: str, content_type: str = CONTENT_TYPE_INDUSTRY) -> pd.DataFrame:
        df = self.client.query(
            "moneyflow_ind_dc", trade_date=ymd, content_type=content_type
        )
        if df.empty:
            return df
        df = df.copy()
        df.columns = [str(c).lower() for c in df.columns]
        df["trade_date"] = _parse_dates(df["trade_date"])
        if "content_type" not in df.columns:
            df["content_type"] = content_type
        for col in (
            "pct_change", "close", "net_amount", "net_amount_rate",
            "buy_elg_amount", "buy_elg_amount_rate",
            "buy_lg_amount", "buy_lg_amount_rate",
            "buy_md_amount", "buy_md_amount_rate",
            "buy_sm_amount", "buy_sm_amount_rate",
        ):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        if "rank" in df.columns:
            df = df.rename(columns={"rank": "rank_no"})
            df["rank_no"] = pd.to_numeric(df["rank_no"], errors="coerce").astype("Int64")
        return df

    def fetch_eastmoney_members(self, sector_code: str, ymd: str) -> pd.DataFrame:
        """东财 clist：fs=b:BKxxxx 拉板块成分股。"""
        raw = str(sector_code or "").replace(".DC", "").strip().upper()
        asof = datetime.strptime(_to_ymd(ymd), "%Y%m%d").date()
        ts_code = _norm_bk_code(raw)
        rows: List[Dict[str, Any]] = []
        pn = 1
        last_err: Optional[Exception] = None
        while pn <= 30:
            payload = None
            for url in EM_CLIST_URLS:
                try:
                    payload = self._em_http_json(
                        url,
                        {
                            "pn": str(pn),
                            "pz": "200",
                            "po": "1",
                            "np": "1",
                            "fltt": "2",
                            "invt": "2",
                            "fid": "f3",
                            "fs": f"b:{raw}",
                            "fields": "f12,f13,f14,f2,f3,f6",
                        },
                        retries=1,
                        timeout=8.0,
                    )
                    break
                except Exception as e:
                    last_err = e
                    time.sleep(0.35)
                    continue
            if payload is None:
                if pn == 1:
                    raise RuntimeError(f"东财成分失败 {raw}: {last_err}")
                break
            data = payload.get("data") or {}
            diffs = data.get("diff") or []
            if isinstance(diffs, dict):
                diffs = list(diffs.values())
            if not diffs:
                break
            for item in diffs:
                con = _em_stock_code(item.get("f12"), item.get("f13"))
                if not con:
                    continue
                rows.append({
                    "trade_date": asof,
                    "ts_code": ts_code,
                    "con_code": con,
                    "name": item.get("f14") or con,
                    "close": _em_num(item.get("f2")),
                    "pct_chg": _em_num(item.get("f3")),
                    "amount": _em_num(item.get("f6")),
                })
            total = int(data.get("total") or len(rows))
            if len(rows) >= total:
                break
            pn += 1
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows).drop_duplicates(
            subset=["ts_code", "trade_date", "con_code"], keep="last"
        )

    def fetch_members(self, sector_code: str, ymd: str) -> pd.DataFrame:
        sector_code = _norm_bk_code(sector_code)
        asof = datetime.strptime(_to_ymd(ymd), "%Y%m%d").date()
        df = pd.DataFrame()
        if not getattr(self, "_dc_dead", False):
            try:
                df = self.client.query("dc_member", ts_code=sector_code, trade_date=ymd)
            except Exception as e:
                logger.info("dc_member %s: %s", sector_code, e)
                if self._api_unavailable(e):
                    self._dc_dead = True
                df = pd.DataFrame()
            if df is None or df.empty:
                try:
                    df = self.client.query("dc_member", ts_code=sector_code)
                except Exception:
                    df = pd.DataFrame()
        if df is not None and not df.empty:
            df = df.copy()
            df.columns = [str(c).lower() for c in df.columns]
            df["trade_date"] = asof
            df["ts_code"] = sector_code
            if "con_code" in df.columns:
                df["con_code"] = df["con_code"].astype(str).str.upper()
            return df.drop_duplicates(subset=["ts_code", "trade_date", "con_code"], keep="last")
        try:
            return self.fetch_eastmoney_members(sector_code, ymd)
        except Exception as e:
            logger.warning("东财成分 %s: %s", sector_code, e)
            return pd.DataFrame()

    # ─── Upsert ───────────────────────────────────────

    def _upsert_frame(self, table: str, df: pd.DataFrame, cols: Sequence[str], conflict: str) -> int:
        if df is None or df.empty:
            return 0
        use = [c for c in cols if c in df.columns]
        if not use:
            return 0
        rows = df[use].where(pd.notnull(df[use]), None)
        # Int64 → python int/None
        for c in rows.columns:
            if str(rows[c].dtype) == "Int64":
                rows[c] = rows[c].astype(object).where(rows[c].notna(), None)

        placeholders = ", ".join(f":{c}" for c in use)
        col_list = ", ".join(use)
        updates = ", ".join(f"{c}=EXCLUDED.{c}" for c in use if c not in conflict.replace("(", "").replace(")", "").split(", "))
        # simpler: update all non-pk
        pk = [x.strip() for x in conflict.replace("(", "").replace(")", "").split(",")]
        updates = ", ".join(f"{c}=EXCLUDED.{c}" for c in use if c not in pk)
        updates = updates + (", updated_at=CURRENT_TIMESTAMP" if "updated_at" not in use else "")
        sql = f"""
        INSERT INTO {table} ({col_list})
        VALUES ({placeholders})
        ON CONFLICT ({', '.join(pk)}) DO UPDATE SET {updates}
        """
        records = rows.to_dict(orient="records")
        with self.engine.begin() as conn:
            conn.execute(sa_text(sql), records)
        return len(records)

    def upsert_index(self, df: pd.DataFrame) -> int:
        cols = [
            "ts_code", "trade_date", "name", "leading_name", "leading_code",
            "pct_change", "leading_pct", "total_mv", "turnover_rate",
            "up_num", "down_num", "idx_type", "industry_level",
        ]
        return self._upsert_frame("sector_dc_index_daily", df, cols, "ts_code, trade_date")

    def upsert_daily(self, df: pd.DataFrame) -> int:
        cols = [
            "ts_code", "trade_date", "open", "high", "low", "close",
            "change", "pct_change", "vol", "amount", "swing",
            "turnover_rate", "category",
        ]
        return self._upsert_frame("sector_dc_daily", df, cols, "ts_code, trade_date")

    def upsert_moneyflow(self, df: pd.DataFrame) -> int:
        cols = [
            "trade_date", "content_type", "ts_code", "name", "pct_change", "close",
            "net_amount", "net_amount_rate",
            "buy_elg_amount", "buy_elg_amount_rate",
            "buy_lg_amount", "buy_lg_amount_rate",
            "buy_md_amount", "buy_md_amount_rate",
            "buy_sm_amount", "buy_sm_amount_rate",
            "buy_sm_amount_stock", "rank_no",
        ]
        return self._upsert_frame(
            "sector_moneyflow_daily", df, cols, "ts_code, trade_date, content_type"
        )

    def upsert_members(self, df: pd.DataFrame) -> int:
        cols = ["trade_date", "ts_code", "con_code", "name", "close", "pct_chg", "amount"]
        return self._upsert_frame(
            "sector_dc_member", df, cols, "ts_code, trade_date, con_code"
        )

    def refresh_catalog(self) -> int:
        """用最新指数日快照刷新板块目录。"""
        self.ensure_tables()
        if not self.engine:
            return 0
        sql = sa_text(
            """
            INSERT INTO ref_sector_dc_basic (ts_code, name, industry_level, idx_type, updated_at)
            SELECT DISTINCT ON (ts_code)
                ts_code, name, industry_level, COALESCE(idx_type, '行业板块'), CURRENT_TIMESTAMP
            FROM sector_dc_index_daily
            WHERE idx_type = '行业板块' AND ts_code IS NOT NULL
            ORDER BY ts_code, trade_date DESC
            ON CONFLICT (ts_code) DO UPDATE SET
                name = COALESCE(EXCLUDED.name, ref_sector_dc_basic.name),
                industry_level = COALESCE(EXCLUDED.industry_level, ref_sector_dc_basic.industry_level),
                idx_type = COALESCE(EXCLUDED.idx_type, ref_sector_dc_basic.idx_type),
                updated_at = CURRENT_TIMESTAMP
            """
        )
        with self.engine.begin() as conn:
            conn.execute(sql)
            n = conn.execute(sa_text("SELECT COUNT(*) FROM ref_sector_dc_basic")).scalar()
        return int(n or 0)

    def list_all_industry_boards(self) -> List[Dict[str, str]]:
        self.ensure_tables()
        if not self.engine:
            return []
        sql = sa_text(
            """
            SELECT DISTINCT ON (ts_code) ts_code, name, industry_level
            FROM (
                SELECT ts_code, name, industry_level, DATE '9999-12-31' AS trade_date
                FROM ref_sector_dc_basic
                WHERE COALESCE(idx_type, '行业板块') = '行业板块'
                UNION ALL
                SELECT ts_code, name, industry_level, trade_date
                FROM sector_dc_index_daily
                WHERE idx_type = '行业板块'
            ) t
            ORDER BY ts_code, trade_date DESC
            """
        )
        with self.engine.connect() as conn:
            rows = conn.execute(sql).fetchall()
        seen = set()
        out: List[Dict[str, str]] = []
        for row in rows:
            code = _norm_bk_code(row[0])
            if not code or code in seen:
                continue
            seen.add(code)
            out.append({
                "ts_code": code,
                "name": row[1] or code,
                "industry_level": row[2] or "",
            })
        return out

    # ─── 东财行情兜底 ─────────────────────────────────

    def _api_unavailable(self, err: Exception) -> bool:
        """缺参 400 是调用方问题，不能把整条 dc_* 标死。"""
        msg = str(err).lower()
        if "required parameter" in msg:
            return False
        return "unknown api_name" in msg or "http 404" in msg or "http 403" in msg

    def _level_lookup(self) -> Dict[str, Dict[str, str]]:
        out: Dict[str, Dict[str, str]] = {}
        if not self.engine:
            return out
        try:
            with self.engine.connect() as conn:
                rows = conn.execute(
                    sa_text(
                        """
                        SELECT DISTINCT ON (ts_code)
                            ts_code, name, industry_level, idx_type
                        FROM sector_dc_index_daily
                        WHERE idx_type = '行业板块'
                        ORDER BY ts_code, trade_date DESC
                        """
                    )
                ).fetchall()
            for r in rows:
                code = _norm_bk_code(r[0])
                out[code] = {
                    "name": r[1] or "",
                    "industry_level": r[2] or "东财三级行业",
                    "idx_type": r[3] or IDX_TYPE_INDUSTRY,
                }
        except Exception as e:
            logger.warning("读取板块层级缓存失败: %s", e)
        return out

    def _em_http_json(self, url: str, params: Dict[str, Any], retries: int = 3, timeout: float = 15.0) -> Dict[str, Any]:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Referer": "https://quote.eastmoney.com/",
        }
        last_err: Optional[Exception] = None
        for proxies in (_em_proxies(), None):
            for attempt in range(retries):
                try:
                    resp = requests.get(
                        url,
                        params=params,
                        timeout=timeout,
                        headers={**headers, "Connection": "close"},
                        proxies=proxies,
                    )
                    resp.raise_for_status()
                    payload = resp.json()
                    return payload if isinstance(payload, dict) else {}
                except Exception as e:
                    last_err = e
                    time.sleep(0.35 * (attempt + 1))
        raise RuntimeError(f"东财请求失败 {url}: {last_err}")

    def eastmoney_last_trade_date(self, bk_code: str = "BK1035") -> Optional[date]:
        ymd = datetime.now(TZ_SH).strftime("%Y%m%d")
        params = {
            "secid": f"90.{bk_code.replace('.DC', '')}",
            "klt": "101",
            "fqt": "1",
            "beg": "20260101",
            "end": ymd,
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        }
        last_err: Optional[Exception] = None
        for url in EM_KLINE_URLS:
            try:
                payload = self._em_http_json(url, params)
                klines = ((payload.get("data") or {}).get("klines") or [])
                if klines:
                    last = str(klines[-1]).split(",")[0]
                    return datetime.strptime(last.replace("-", "")[:8], "%Y%m%d").date()
            except Exception as e:
                last_err = e
                continue
        logger.warning("东财板块K线日期失败: %s", last_err)
        return None

    def fetch_eastmoney_industry(self, ymd: str) -> Dict[str, Any]:
        """东财行业 clist：最近收盘日的涨跌幅 + 成交额。"""
        requested = datetime.strptime(ymd, "%Y%m%d").date()
        last = requested
        rows: List[Dict[str, Any]] = []
        pn = 1
        total = None
        clist_url = EM_CLIST_URLS[0]
        while pn <= 20:
            payload = None
            last_err: Optional[Exception] = None
            for url in EM_CLIST_URLS:
                try:
                    payload = self._em_http_json(
                        url,
                        {
                            "pn": str(pn),
                            "pz": "100",
                            "po": "1",
                            "np": "1",
                            "fltt": "2",
                            "invt": "2",
                            "fid": "f3",
                            "fs": "m:90+t:2+f:!50",
                            "fields": EM_CLIST_FIELDS,
                        },
                    )
                    clist_url = url
                    break
                except Exception as e:
                    last_err = e
                    time.sleep(0.25)
                    continue
            if payload is None:
                raise RuntimeError(f"东财行业 clist 失败: {last_err}")
            data = payload.get("data") or {}
            diffs = data.get("diff") or []
            if isinstance(diffs, dict):
                diffs = list(diffs.values())
            if not diffs:
                break
            rows.extend(diffs)
            total = int(data.get("total") or len(rows))
            if len(rows) >= total:
                break
            pn += 1

        if not rows:
            return {"trade_date": last, "index": pd.DataFrame(), "daily": pd.DataFrame(), "moneyflow": pd.DataFrame()}

        levels = self._level_lookup()
        index_rows: List[Dict[str, Any]] = []
        daily_rows: List[Dict[str, Any]] = []
        mf_rows: List[Dict[str, Any]] = []
        for item in rows:
            raw = str(item.get("f12") or "").strip()
            if not raw:
                continue
            ts_code = _norm_bk_code(raw)
            meta = levels.get(ts_code) or {}
            name = item.get("f14") or meta.get("name") or ts_code
            leading = item.get("f128")
            if leading in ("-", None, ""):
                leading = None
            leading_code = _em_stock_code(item.get("f140"), item.get("f141"))
            pct = _em_num(item.get("f3"))
            rec_index = {
                "ts_code": ts_code,
                "trade_date": last,
                "name": name,
                "leading_name": leading,
                "leading_code": leading_code,
                "pct_change": pct,
                "leading_pct": _em_num(item.get("f136")),
                "total_mv": _em_num(item.get("f20")),
                "turnover_rate": _em_num(item.get("f8")),
                "up_num": _em_int(item.get("f104")),
                "down_num": _em_int(item.get("f105")),
                "idx_type": IDX_TYPE_INDUSTRY,
                "industry_level": meta.get("industry_level") or "东财三级行业",
            }
            rec_daily = {
                "ts_code": ts_code,
                "trade_date": last,
                "open": _em_num(item.get("f17")),
                "high": _em_num(item.get("f15")),
                "low": _em_num(item.get("f16")),
                "close": _em_num(item.get("f2")),
                "change": _em_num(item.get("f4")),
                "pct_change": pct,
                "vol": _em_int(item.get("f5")),
                "amount": _em_num(item.get("f6")),
                "swing": _em_num(item.get("f7")),
                "turnover_rate": _em_num(item.get("f8")),
                "category": IDX_TYPE_INDUSTRY,
            }
            rec_mf = {
                "trade_date": last,
                "content_type": CONTENT_TYPE_INDUSTRY,
                "ts_code": ts_code,
                "name": name,
                "pct_change": pct,
                "close": _em_num(item.get("f2")),
                "net_amount": _em_num(item.get("f62")),
            }
            index_rows.append(rec_index)
            daily_rows.append(rec_daily)
            mf_rows.append(rec_mf)

        logger.info(
            "东财行业 clist %d 行 · 行情日 %s（请求 %s）· %s",
            len(index_rows),
            last.isoformat(),
            requested.isoformat(),
            clist_url,
        )
        return {
            "trade_date": last,
            "index": pd.DataFrame(index_rows),
            "daily": pd.DataFrame(daily_rows),
            "moneyflow": pd.DataFrame(mf_rows),
        }

    def fetch_sw_overlay(self, ymd: str) -> Dict[str, Any]:
        """Datahubco 申万日行情按名称贴到东财板块代码（dc_* 不可用时的成交额来源）。"""
        df = self.client.query("sw_daily", trade_date=ymd, limit=5000)
        if df is None or df.empty:
            return {"trade_date": datetime.strptime(ymd, "%Y%m%d").date(), "index": pd.DataFrame(), "daily": pd.DataFrame()}
        df = df.copy()
        df.columns = [str(c).lower() for c in df.columns]
        asof = datetime.strptime(ymd, "%Y%m%d").date()
        if "trade_date" in df.columns:
            parsed = _parse_dates(df["trade_date"])
            if parsed.notna().any():
                asof = parsed.dropna().iloc[-1]
        levels = self._level_lookup()
        by_name: Dict[str, List[tuple]] = {}
        for code, meta in levels.items():
            nm = str(meta.get("name") or "").strip()
            if nm:
                by_name.setdefault(nm, []).append((code, meta))

        index_rows: List[Dict[str, Any]] = []
        daily_rows: List[Dict[str, Any]] = []
        for _, row in df.iterrows():
            nm = str(row.get("name") or "").strip()
            targets = by_name.get(nm) or []
            if not targets:
                continue
            amount = _em_num(row.get("amount"))
            if amount is not None:
                amount = amount * 10000.0  # 申万成交额按万元
            pct = _em_num(row.get("pct_change"))
            total_mv = _em_num(row.get("total_mv"))
            if total_mv is not None:
                total_mv = total_mv * 10000.0
            rec_daily_base = {
                "trade_date": asof,
                "open": _em_num(row.get("open")),
                "high": _em_num(row.get("high")),
                "low": _em_num(row.get("low")),
                "close": _em_num(row.get("close")),
                "change": _em_num(row.get("change")),
                "pct_change": pct,
                "vol": _em_int(row.get("vol")),
                "amount": amount,
                "category": IDX_TYPE_INDUSTRY,
            }
            for ts_code, meta in targets:
                index_rows.append(
                    {
                        "ts_code": ts_code,
                        "trade_date": asof,
                        "name": nm,
                        "pct_change": pct,
                        "total_mv": total_mv,
                        "idx_type": IDX_TYPE_INDUSTRY,
                        "industry_level": meta.get("industry_level") or "东财三级行业",
                    }
                )
                daily_rows.append({"ts_code": ts_code, **rec_daily_base})

        logger.info(
            "申万行情映射东财板块 %d 行 · %s（源 %d）",
            len(index_rows),
            asof.isoformat() if hasattr(asof, "isoformat") else asof,
            len(df),
        )
        return {
            "trade_date": asof,
            "index": pd.DataFrame(index_rows),
            "daily": pd.DataFrame(daily_rows),
        }

    def _known_boards(self, level: str) -> List[Dict[str, str]]:
        if not self.engine:
            return []
        with self.engine.connect() as conn:
            rows = conn.execute(
                sa_text(
                    """
                    SELECT DISTINCT ON (ts_code) ts_code, name, industry_level
                    FROM sector_dc_index_daily
                    WHERE industry_level = :lv AND idx_type = '行业板块'
                    ORDER BY ts_code, trade_date DESC
                    """
                ),
                {"lv": level},
            ).fetchall()
        out = []
        for row in rows:
            code = _norm_bk_code(row[0])
            if code:
                out.append({"ts_code": code, "name": row[1] or code, "industry_level": row[2] or level})
        return out

    def _em_kline_map(self, ts_code: str, beg: str, end: str, retries: int = 1) -> Dict[str, Dict[str, Any]]:
        raw = ts_code.replace(".DC", "")
        params = {
            "secid": f"90.{raw}",
            "klt": "101",
            "fqt": "1",
            "beg": beg,
            "end": end,
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        }
        payload = None
        last_err: Optional[Exception] = None
        for url in (EM_KLINE_URLS[0],):
            try:
                payload = self._em_http_json(url, params, retries=max(1, retries), timeout=15.0)
                break
            except Exception as e:
                last_err = e
                continue
        if payload is None:
            raise RuntimeError(last_err or "kline empty")
        lines = ((payload.get("data") or {}).get("klines") or [])
        found: Dict[str, Dict[str, Any]] = {}
        for line in lines:
            parts = str(line).split(",")
            if len(parts) < 10:
                continue
            ymd = parts[0].replace("-", "")[:8]
            found[ymd] = {
                "open": _em_num(parts[1]),
                "close": _em_num(parts[2]),
                "high": _em_num(parts[3]),
                "low": _em_num(parts[4]),
                "vol": _em_int(parts[5]),
                "amount": _em_num(parts[6]),
                "swing": _em_num(parts[7]),
                "pct_change": _em_num(parts[8]),
                "change": _em_num(parts[9]),
                "turnover_rate": _em_num(parts[10]) if len(parts) > 10 else None,
            }
        return found

    def _kline_bar_count(self, ts_code: str) -> int:
        if not self.engine:
            return 0
        try:
            with self.engine.connect() as conn:
                n = conn.execute(
                    sa_text("SELECT COUNT(*) FROM sector_dc_daily WHERE ts_code = :c"),
                    {"c": ts_code},
                ).scalar()
            return int(n or 0)
        except Exception:
            return 0

    def ensure_kline_history(
        self,
        ts_code: str,
        lookback_days: int = 500,
        min_bars: int = 60,
    ) -> Dict[str, Any]:
        """只补该板块日 K 到 sector_dc_daily，不拉成分股行情。"""
        self.ensure_tables()
        code = _norm_bk_code(ts_code)
        if not code:
            return {"ok": False, "error": "缺少板块代码"}
        have = self._kline_bar_count(code)
        if have >= min_bars:
            return {"ok": True, "ts_code": code, "skipped": True, "bars": have}
        name, level = code, "东财二级行业"
        if self.engine:
            with self.engine.connect() as conn:
                row = conn.execute(
                    sa_text("SELECT name, industry_level FROM ref_sector_dc_basic WHERE ts_code = :c"),
                    {"c": code},
                ).fetchone()
                if not row:
                    row = conn.execute(
                        sa_text(
                            """
                            SELECT name, industry_level FROM sector_dc_index_daily
                            WHERE ts_code = :c ORDER BY trade_date DESC LIMIT 1
                            """
                        ),
                        {"c": code},
                    ).fetchone()
                if row:
                    name = row[0] or name
                    level = row[1] or level
        end = datetime.now(TZ_SH).date()
        start = end - timedelta(days=max(120, int(lookback_days)))
        try:
            bars = self._em_kline_map(
                code, start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), retries=1
            )
        except Exception as e:
            return {"ok": False, "ts_code": code, "error": str(e)[:240], "bars": have}
        index_rows: List[Dict[str, Any]] = []
        daily_rows: List[Dict[str, Any]] = []
        for ymd, bar in bars.items():
            try:
                day = datetime.strptime(ymd, "%Y%m%d").date()
            except ValueError:
                continue
            if day < start or day > end:
                continue
            index_rows.append({
                "ts_code": code,
                "trade_date": day,
                "name": name,
                "pct_change": bar.get("pct_change"),
                "idx_type": IDX_TYPE_INDUSTRY,
                "industry_level": level,
            })
            daily_rows.append({
                "ts_code": code,
                "trade_date": day,
                "open": bar.get("open"),
                "high": bar.get("high"),
                "low": bar.get("low"),
                "close": bar.get("close"),
                "change": bar.get("change"),
                "pct_change": bar.get("pct_change"),
                "vol": bar.get("vol"),
                "amount": bar.get("amount"),
                "swing": bar.get("swing"),
                "turnover_rate": bar.get("turnover_rate"),
                "category": IDX_TYPE_INDUSTRY,
            })
        n_daily = self.upsert_daily(pd.DataFrame(daily_rows)) if daily_rows else 0
        if index_rows:
            try:
                self.upsert_index(pd.DataFrame(index_rows))
            except Exception:
                pass
        return {
            "ok": n_daily > 0 or have > 0,
            "ts_code": code,
            "name": name,
            "level": level,
            "skipped": False,
            "daily_rows": n_daily,
            "bars": self._kline_bar_count(code),
            "source": "eastmoney_kline",
        }

    def backfill_all_industry_klines(
        self,
        lookback_days: int = 500,
        min_bars: int = 60,
        max_workers: int = 1,
    ) -> Dict[str, Any]:
        """给一/二/三级行业板块补历史日 K。默认串行，避免连接池卡死。"""
        self.ensure_tables()
        self.refresh_catalog()
        boards = self.list_all_industry_boards()
        ok = 0
        skipped = 0
        failed = 0
        errors: List[str] = []

        def pull(board: Dict[str, str]) -> Dict[str, Any]:
            return self.ensure_kline_history(
                board["ts_code"], lookback_days=lookback_days, min_bars=min_bars
            )

        workers = max(1, int(max_workers or 1))
        results: List[Dict[str, Any]] = []
        if workers == 1:
            for i, board in enumerate(boards, 1):
                try:
                    got = pull(board)
                    results.append(got)
                    if i == 1 or i % 20 == 0 or i == len(boards):
                        logger.info(
                            "sector kline %d/%d %s bars=%s",
                            i, len(boards), board.get("ts_code"), got.get("bars"),
                        )
                except Exception as e:
                    failed += 1
                    errors.append(str(e)[:160])
        else:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futs = [pool.submit(pull, b) for b in boards]
                for fut in as_completed(futs):
                    try:
                        results.append(fut.result())
                    except Exception as e:
                        failed += 1
                        errors.append(str(e)[:160])
        for got in results:
            if got.get("skipped"):
                skipped += 1
                ok += 1
            elif got.get("ok"):
                ok += 1
            else:
                failed += 1
                if got.get("error"):
                    errors.append(f"{got.get('ts_code')}: {got.get('error')}")
        return {
            "ok": ok > 0,
            "boards": len(boards),
            "filled": ok,
            "skipped": skipped,
            "failed": failed,
            "errors": errors[:12],
        }

    def sync_all_members(
        self,
        trade_date: Optional[str] = None,
        sleep_s: float = 0.02,
        skip_existing: bool = True,
    ) -> Dict[str, Any]:
        """补齐一/二/三级全部板块成分股。"""
        self.ensure_tables()
        meta = self.resolve_asof(trade_date)
        asof: date = meta["asof_date"]
        ymd = meta["ymd"]
        if self.engine:
            with self.engine.connect() as conn:
                latest = conn.execute(
                    sa_text("SELECT MAX(trade_date) FROM sector_dc_index_daily")
                ).scalar()
            if latest and latest < asof:
                asof = latest
                ymd = asof.strftime("%Y%m%d")
        boards = self.list_all_industry_boards()
        if not boards:
            boards = []
            for lv in LEVELS:
                boards.extend(self.list_sectors_by_level(asof, lv))
        codes = [b["ts_code"] for b in boards]
        stats = self.ensure_members_for_codes(
            codes, ymd, asof, sleep_s=sleep_s, skip_existing=skip_existing
        )
        stats.update({"ok": True, "asof": asof.isoformat(), "boards": len(codes)})
        return stats

    def _amount_scale(self, boards: Sequence[Dict[str, str]], sample_day: date) -> float:
        """用库内已有成交额校准东财 K 线金额单位。对不上就返回 0，表示这一天不写成交额。"""
        if not self.engine or not boards:
            return 1.0
        ymd = sample_day.strftime("%Y%m%d")
        with self.engine.connect() as conn:
            for board in boards[:8]:
                row = conn.execute(
                    sa_text(
                        """
                        SELECT amount FROM sector_dc_daily
                        WHERE ts_code = :c AND trade_date = :d AND amount IS NOT NULL AND amount > 0
                        """
                    ),
                    {"c": board["ts_code"], "d": sample_day},
                ).fetchone()
                if not row:
                    continue
                try:
                    bars = self._em_kline_map(board["ts_code"], ymd, ymd)
                except Exception:
                    continue
                remote = (bars.get(ymd) or {}).get("amount")
                if not remote:
                    continue
                ratio = float(row[0]) / float(remote)
                for scale in (1.0, 1000.0, 10000.0, 0.001, 0.0001):
                    if 0.5 <= ratio / scale <= 2:
                        return scale
                return 0.0
        return 1.0

    def _backfill_from_sw(self, ymd: str, target: date, level: str, codes: Set[str]) -> Dict[str, Any]:
        try:
            packed = self.fetch_sw_overlay(ymd)
        except Exception as e:
            return {"ok": False, "error": str(e)}
        idx = packed.get("index")
        daily = packed.get("daily")
        if idx is None or idx.empty:
            return {"ok": False, "error": "申万日行情为空"}
        idx = idx.loc[idx["ts_code"].isin(codes)].copy()
        if idx.empty:
            return {"ok": False, "error": "申万行业名称没有对上这一层板块"}
        idx["industry_level"] = level
        idx["idx_type"] = IDX_TYPE_INDUSTRY
        n_idx = self.upsert_index(idx)
        n_daily = 0
        if daily is not None and not daily.empty:
            daily = daily.loc[daily["ts_code"].isin(codes)].copy()
            n_daily = self.upsert_daily(daily)
        return {
            "ok": True,
            "trade_date": target.isoformat(),
            "level": level,
            "source": "sw_daily",
            "boards": len(codes),
            "index_rows": n_idx,
            "daily_rows": n_daily,
            "moneyflow_rows": 0,
            "unmatched": len(codes) - int(idx["ts_code"].nunique()),
            "note": "东财板块接口不可用，按申万日行情里同名行业写入涨跌和成交额。上涨家数、领涨股、主力净额没有来源，留空。名称对不上的板块不补。",
        }

    def backfill_level_day(self, trade_date: str, level: str = "东财二级行业") -> Dict[str, Any]:
        """补某一天的行业板块行情。东财接口不可用时，用申万同名行业的涨跌和成交额，对不上的板块留空。"""
        self.ensure_tables()
        ymd = str(trade_date).replace("-", "")[:8]
        try:
            target = datetime.strptime(ymd, "%Y%m%d").date()
        except ValueError:
            return {"ok": False, "error": f"无法识别交易日 {trade_date}"}
        level = level or "东财二级行业"

        try:
            remote = self.fetch_dc_index(ymd)
        except Exception as e:
            remote = pd.DataFrame()
            logger.info("dc_index %s 不可用: %s", ymd, e)
        if remote is not None and not remote.empty and "industry_level" in remote.columns:
            hit = remote.loc[remote["industry_level"] == level]
            if not hit.empty:
                n_idx = self.upsert_index(hit)
                n_daily = 0
                try:
                    daily = self.fetch_dc_daily(ymd)
                    if daily is not None and not daily.empty and "ts_code" in daily.columns:
                        codes = set(hit["ts_code"].astype(str))
                        daily = daily.loc[daily["ts_code"].astype(str).isin(codes)]
                        n_daily = self.upsert_daily(daily)
                except Exception as e:
                    logger.info("dc_daily %s 不可用: %s", ymd, e)
                return {
                    "ok": True,
                    "trade_date": target.isoformat(),
                    "level": level,
                    "source": "datahubco",
                    "index_rows": n_idx,
                    "daily_rows": n_daily,
                    "moneyflow_rows": 0,
                    "failed": 0,
                }

        boards = self._known_boards(level)
        if not boards:
            return {"ok": False, "error": f"库里没有{level}代码，无法按板块补 {target.isoformat()}"}
        codes = {b["ts_code"] for b in boards}

        sw_packed = self._backfill_from_sw(ymd, target, level, codes)
        if sw_packed.get("ok"):
            return sw_packed

        probe = boards[0]["ts_code"]
        try:
            self._em_kline_map(probe, ymd, ymd)
        except Exception as e:
            return {
                "ok": False,
                "error": f"Datahubco 没有东财板块接口，东财日K也失败（{e}）。申万名称能对上的板块也没有写入。",
                "trade_date": target.isoformat(),
                "level": level,
                "sw_error": sw_packed.get("error"),
            }

        sample_day = None
        if self.engine:
            with self.engine.connect() as conn:
                sample_day = conn.execute(
                    sa_text(
                        """
                        SELECT MAX(d.trade_date) FROM sector_dc_daily d
                        JOIN sector_dc_index_daily i
                          ON i.ts_code = d.ts_code AND i.trade_date = d.trade_date
                        WHERE i.industry_level = :lv AND d.amount IS NOT NULL
                          AND d.trade_date <> :target
                        """
                    ),
                    {"lv": level, "target": target},
                ).scalar()
        scale = self._amount_scale(boards, sample_day) if isinstance(sample_day, date) else 1.0

        from concurrent.futures import ThreadPoolExecutor, as_completed

        index_rows: List[Dict[str, Any]] = []
        daily_rows: List[Dict[str, Any]] = []
        errors: List[str] = []

        def pull(board: Dict[str, str]) -> Optional[Dict[str, Any]]:
            bars = self._em_kline_map(board["ts_code"], ymd, ymd)
            bar = bars.get(ymd)
            if not bar or bar.get("pct_change") is None:
                return None
            return {"board": board, "bar": bar}

        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(pull, board) for board in boards]
            for fut in as_completed(futures):
                try:
                    got = fut.result()
                except Exception as e:
                    errors.append(str(e)[:160])
                    continue
                if not got:
                    errors.append("empty")
                    continue
                board = got["board"]
                bar = got["bar"]
                amount = bar.get("amount")
                if amount is not None and scale:
                    amount = amount * scale
                elif scale == 0:
                    amount = None
                index_rows.append({
                    "ts_code": board["ts_code"],
                    "trade_date": target,
                    "name": board["name"],
                    "pct_change": bar.get("pct_change"),
                    "idx_type": IDX_TYPE_INDUSTRY,
                    "industry_level": board["industry_level"],
                })
                daily_rows.append({
                    "ts_code": board["ts_code"],
                    "trade_date": target,
                    "open": bar.get("open"),
                    "high": bar.get("high"),
                    "low": bar.get("low"),
                    "close": bar.get("close"),
                    "change": bar.get("change"),
                    "pct_change": bar.get("pct_change"),
                    "vol": bar.get("vol"),
                    "amount": amount,
                    "swing": bar.get("swing"),
                    "turnover_rate": bar.get("turnover_rate"),
                    "category": IDX_TYPE_INDUSTRY,
                })

        if not index_rows:
            sample = errors[0] if errors else "没有返回"
            return {
                "ok": False,
                "error": f"东财日 K 没有补上 {target.isoformat()}：{sample}",
                "trade_date": target.isoformat(),
                "level": level,
                "failed": len(errors),
            }
        n_idx = self.upsert_index(pd.DataFrame(index_rows))
        n_daily = self.upsert_daily(pd.DataFrame(daily_rows))
        return {
            "ok": True,
            "trade_date": target.isoformat(),
            "level": level,
            "source": "eastmoney_kline",
            "amount_scale": scale,
            "boards": len(boards),
            "index_rows": n_idx,
            "daily_rows": n_daily,
            "moneyflow_rows": 0,
            "failed": len([e for e in errors if e]),
            "note": "上涨家数、领涨股、主力净额这天的日 K 里没有，留空，不编造。",
        }

    def fill_board_history(self, ts_code: str, start_date: str, end_date: str) -> Dict[str, Any]:
        """补一个板块区间内的涨跌、成分股日线、上涨家数，以及尚未填写的沪深300环境。"""
        code = _norm_bk_code(ts_code)
        if not code:
            return {"ok": False, "error": "缺少板块代码"}
        try:
            start = datetime.strptime(str(start_date).replace("-", "")[:8], "%Y%m%d").date()
            end = datetime.strptime(str(end_date).replace("-", "")[:8], "%Y%m%d").date()
        except ValueError:
            return {"ok": False, "error": "日期无法识别"}
        if end < start:
            return {"ok": False, "error": "结束日早于开始日"}
        with self.engine.connect() as conn:
            meta = conn.execute(
                sa_text(
                    """
                    SELECT name, industry_level FROM sector_dc_index_daily
                    WHERE ts_code = :code
                    ORDER BY trade_date DESC LIMIT 1
                    """
                ),
                {"code": code},
            ).fetchone()
        if not meta:
            return {"ok": False, "error": f"库里没有 {code} 的板块名称，无法写入"}
        source = "eastmoney_kline"
        bars: Dict[str, Dict[str, Any]] = {}
        em_error = ""
        try:
            bars = self._em_kline_map(code, start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), retries=3)
        except Exception as e:
            em_error = str(e)
        if not bars:
            try:
                bars = self._sw_history(str(meta[0] or ""), start, end)
                source = "sw_daily"
            except Exception as e:
                return {
                    "ok": False,
                    "error": f"东财日K没有返回：{em_error or '空'}；申万日行情也没有对上：{e}",
                    "ts_code": code,
                }
        index_rows = []
        daily_rows = []
        for ymd, bar in bars.items():
            if bar.get("pct_change") is None:
                continue
            try:
                day = datetime.strptime(ymd, "%Y%m%d").date()
            except ValueError:
                continue
            if day < start or day > end:
                continue
            index_rows.append({
                "ts_code": code,
                "trade_date": day,
                "name": meta[0],
                "pct_change": bar.get("pct_change"),
                "idx_type": IDX_TYPE_INDUSTRY,
                "industry_level": meta[1] or "东财二级行业",
            })
            daily_row = {
                "ts_code": code,
                "trade_date": day,
                "open": bar.get("open"),
                "high": bar.get("high"),
                "low": bar.get("low"),
                "close": bar.get("close"),
                "change": bar.get("change"),
                "pct_change": bar.get("pct_change"),
                "vol": bar.get("vol"),
                "turnover_rate": bar.get("turnover_rate"),
                "category": IDX_TYPE_INDUSTRY,
            }
            if source == "sw_daily" and bar.get("amount") is not None:
                daily_row["amount"] = bar.get("amount")
            daily_rows.append(daily_row)
        if not index_rows:
            return {"ok": False, "error": "东财日K在这个区间里没有涨跌幅", "ts_code": code}
        n_idx = self.upsert_index(pd.DataFrame(index_rows))
        n_daily = self.upsert_daily(pd.DataFrame(daily_rows))
        members = self._ashare_members(code)
        stock_rows = self._fill_member_bars(members, start, end)
        breadth_rows = self._write_breadth(code, start, end)
        index_rows_n = self._fill_benchmark(start, end)
        regime_rows = self._write_regimes(start, end)
        return {
            "ok": True,
            "ts_code": code,
            "name": meta[0],
            "source": source,
            "index_rows": n_idx,
            "daily_rows": n_daily,
            "member_count": len(members),
            "stock_rows": stock_rows,
            "breadth_rows": breadth_rows,
            "benchmark_rows": index_rows_n,
            "regime_rows": regime_rows,
            "note": (
                "板块涨跌来自东财日K。" if source == "eastmoney_kline"
                else "东财日K没有连上，板块涨跌改用申万同名行业。"
            ) + "上涨家数用成分股日线统计，B股不计入。市场环境按沪深300的均线规则补写，库里已有的判断不覆盖。",
        }

    def _sw_history(self, name: str, start: date, end: date) -> Dict[str, Dict[str, Any]]:
        """按板块名称找到申万行业，再拉取这一段日行情。"""
        wanted = (name or "").strip()
        if not wanted:
            raise RuntimeError("没有板块名称")
        lookup = pd.DataFrame()
        day = end
        for _ in range(12):
            lookup = self.client.query("sw_daily", trade_date=day.strftime("%Y%m%d"), limit=5000)
            if lookup is not None and not lookup.empty:
                break
            day = day.fromordinal(day.toordinal() - 1)
        if lookup is None or lookup.empty:
            raise RuntimeError("申万日行情没有返回行业列表")
        lookup = lookup.copy()
        lookup.columns = [str(c).lower() for c in lookup.columns]
        hits = []
        for _, row in lookup.iterrows():
            nm = str(row.get("name") or "").strip()
            if nm == wanted or nm.startswith(wanted):
                hits.append((nm, str(row.get("ts_code") or "")))
        hits.sort(key=lambda item: (0 if item[0] == wanted else 1, 1 if item[0].startswith("非") else 0, len(item[0])))
        if not hits or not hits[0][1]:
            raise RuntimeError(f"申万行业里没有「{wanted}」")
        sw_code = hits[0][1]
        df = self.client.query(
            "sw_daily",
            ts_code=sw_code,
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
        )
        if df is None or df.empty:
            raise RuntimeError(f"{sw_code} 在这个区间没有日行情")
        df = df.copy()
        df.columns = [str(c).lower() for c in df.columns]
        found: Dict[str, Dict[str, Any]] = {}
        for _, row in df.iterrows():
            raw_day = str(row.get("trade_date") or "").replace("-", "")[:8]
            if len(raw_day) != 8 or not raw_day.isdigit():
                continue
            amount = _em_num(row.get("amount"))
            if amount is not None:
                amount = amount * 10000.0
            found[raw_day] = {
                "open": _em_num(row.get("open")),
                "close": _em_num(row.get("close")),
                "high": _em_num(row.get("high")),
                "low": _em_num(row.get("low")),
                "vol": _em_int(row.get("vol")),
                "amount": amount,
                "pct_change": _em_num(row.get("pct_change")),
                "change": _em_num(row.get("change")),
            }
        return found

    def fill_market_history(self, start_date: str, end_date: str) -> Dict[str, Any]:
        """补全部东财二级行业在整段日期上的涨跌和上涨家数。已有长历史的板块不覆盖。"""
        try:
            start = datetime.strptime(str(start_date).replace("-", "")[:8], "%Y%m%d").date()
            end = datetime.strptime(str(end_date).replace("-", "")[:8], "%Y%m%d").date()
        except ValueError:
            return {"ok": False, "error": "日期无法识别"}
        with self.engine.connect() as conn:
            boards = conn.execute(
                sa_text(
                    """
                    SELECT i.ts_code, i.name, COALESCE(c.n, 0)
                    FROM (
                        SELECT DISTINCT ON (ts_code) ts_code, name, industry_level
                        FROM sector_dc_index_daily
                        WHERE industry_level = '东财二级行业' AND idx_type = '行业板块'
                        ORDER BY ts_code, trade_date DESC
                    ) i
                    LEFT JOIN (
                        SELECT ts_code, COUNT(*) AS n
                        FROM sector_dc_index_daily
                        WHERE pct_change IS NOT NULL AND trade_date BETWEEN :a AND :b
                        GROUP BY ts_code
                    ) c ON c.ts_code = i.ts_code
                    """
                ),
                {"a": start, "b": end},
            ).fetchall()
        pending = [
            {"ts_code": row[0], "name": row[1], "level": "东财二级行业"}
            for row in boards
            if int(row[2] or 0) < 200 and row[1]
        ]
        sw_map = self._sw_name_map(end)
        written = 0
        unmatched = []
        from concurrent.futures import ThreadPoolExecutor, as_completed

        jobs = []
        for board in pending:
            sw_code = sw_map.get(str(board["name"]).strip())
            if not sw_code:
                unmatched.append(board["name"])
                continue
            jobs.append((board, sw_code))

        def pull(board: Dict[str, str], sw_code: str) -> List[Dict[str, Any]]:
            df = self.client.query(
                "sw_daily",
                ts_code=sw_code,
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
            )
            if df is None or df.empty:
                return []
            df = df.copy()
            df.columns = [str(c).lower() for c in df.columns]
            rows = []
            for _, item in df.iterrows():
                raw_day = str(item.get("trade_date") or "").replace("-", "")[:8]
                pct = _em_num(item.get("pct_change"))
                if len(raw_day) != 8 or pct is None:
                    continue
                rows.append({
                    "ts_code": board["ts_code"],
                    "trade_date": datetime.strptime(raw_day, "%Y%m%d").date(),
                    "name": board["name"],
                    "pct_change": pct,
                    "idx_type": IDX_TYPE_INDUSTRY,
                    "industry_level": "东财二级行业",
                })
            return rows

        frames: List[Dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(pull, board, sw_code) for board, sw_code in jobs]
            for fut in as_completed(futures):
                try:
                    frames.extend(fut.result() or [])
                except Exception as e:
                    logger.warning("全市场板块行情没有写入: %s", e)
        if frames:
            written = self.upsert_index(pd.DataFrame(frames))
        breadth = self._write_market_breadth(start, end)
        regimes = self._write_regimes(start, end)
        return {
            "ok": True,
            "boards": len(boards),
            "fetched_boards": len(jobs),
            "unmatched": unmatched[:30],
            "unmatched_count": len(unmatched),
            "index_rows": written,
            "breadth_rows": breadth,
            "regime_rows": regimes,
            "note": "全市场板块涨跌按申万同名行业写入，已有较长历史的板块不覆盖。上涨家数用成分股日线统计。名称对不上的板块留空。",
        }

    def _sw_name_map(self, end: date) -> Dict[str, str]:
        lookup = pd.DataFrame()
        day = end
        for _ in range(12):
            lookup = self.client.query("sw_daily", trade_date=day.strftime("%Y%m%d"), limit=5000)
            if lookup is not None and not lookup.empty:
                break
            day = day.fromordinal(day.toordinal() - 1)
        found: Dict[str, str] = {}
        if lookup is None or lookup.empty:
            return found
        lookup = lookup.copy()
        lookup.columns = [str(c).lower() for c in lookup.columns]
        for _, row in lookup.iterrows():
            name = str(row.get("name") or "").strip()
            code = str(row.get("ts_code") or "").strip()
            if name and code and not name.startswith("非"):
                found.setdefault(name, code)
        return found

    def _write_market_breadth(self, start: date, end: date) -> int:
        with self.engine.begin() as conn:
            result = conn.execute(
                sa_text(
                    """
                    UPDATE sector_dc_index_daily AS i
                    SET up_num = s.up_num, down_num = s.down_num, updated_at = CURRENT_TIMESTAMP
                    FROM (
                        SELECT d.ts_code, d.trade_date,
                               COUNT(*) FILTER (WHERE sd.pct_chg > 0)::int AS up_num,
                               COUNT(*) FILTER (WHERE sd.pct_chg < 0)::int AS down_num,
                               COUNT(sd.pct_chg)::int AS n
                        FROM sector_dc_index_daily d
                        JOIN (
                            SELECT DISTINCT ON (ts_code, con_code) ts_code, con_code
                            FROM sector_dc_member
                            WHERE con_code NOT LIKE '200%' AND con_code NOT LIKE '900%'
                            ORDER BY ts_code, con_code, trade_date DESC
                        ) m ON m.ts_code = d.ts_code
                        JOIN stock_daily sd
                          ON sd.ts_code = m.con_code AND sd.trade_date = d.trade_date
                        WHERE d.industry_level = '东财二级行业'
                          AND d.trade_date BETWEEN :a AND :b
                          AND d.up_num IS NULL
                          AND d.pct_change IS NOT NULL
                        GROUP BY d.ts_code, d.trade_date
                    ) s
                    WHERE i.ts_code = s.ts_code AND i.trade_date = s.trade_date AND s.n >= 5
                    """
                ),
                {"a": start, "b": end},
            )
        return int(result.rowcount or 0)

    def _ashare_members(self, code: str) -> List[str]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                sa_text(
                    """
                    SELECT DISTINCT ON (con_code) con_code
                    FROM sector_dc_member
                    WHERE ts_code = :code AND con_code IS NOT NULL
                    ORDER BY con_code, trade_date DESC
                    """
                ),
                {"code": code},
            ).fetchall()
        codes = []
        for row in rows:
            symbol = str(row[0] or "")
            if symbol.startswith("200") or symbol.startswith("900"):
                continue
            codes.append(symbol)
        return codes

    def _fill_member_bars(self, codes: Sequence[str], start: date, end: date) -> int:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        needed = []
        with self.engine.connect() as conn:
            for symbol in codes:
                row = conn.execute(
                    sa_text(
                        """
                        SELECT COUNT(*), MIN(trade_date) FROM stock_daily
                        WHERE ts_code = :code AND trade_date BETWEEN :a AND :b
                        """
                    ),
                    {"code": symbol, "a": start, "b": end},
                ).fetchone()
                count = int(row[0] or 0)
                first = row[1]
                if count >= 200 and first is not None and first <= start:
                    continue
                needed.append(symbol)
        if not needed:
            return 0
        written = 0

        def pull(symbol: str) -> int:
            frames = self._selective.fetch_remote(
                symbol, start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), freqs=["daily"],
            )
            return int(self._selective.upsert_bars("daily", frames.get("daily")))

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(pull, symbol) for symbol in needed]
            for fut in as_completed(futures):
                try:
                    written += int(fut.result() or 0)
                except Exception as e:
                    logger.warning("成分股日线没有写入: %s", e)
        return written

    def _write_breadth(self, code: str, start: date, end: date) -> int:
        with self.engine.begin() as conn:
            result = conn.execute(
                sa_text(
                    """
                    UPDATE sector_dc_index_daily AS i
                    SET up_num = s.up_num, down_num = s.down_num, updated_at = CURRENT_TIMESTAMP
                    FROM (
                        SELECT d.trade_date,
                               COUNT(*) FILTER (WHERE sd.pct_chg > 0)::int AS up_num,
                               COUNT(*) FILTER (WHERE sd.pct_chg < 0)::int AS down_num,
                               COUNT(sd.pct_chg)::int AS n
                        FROM sector_dc_index_daily d
                        JOIN (
                            SELECT DISTINCT con_code FROM sector_dc_member
                            WHERE ts_code = :code
                              AND con_code NOT LIKE '200%'
                              AND con_code NOT LIKE '900%'
                        ) m ON TRUE
                        LEFT JOIN stock_daily sd
                          ON sd.ts_code = m.con_code AND sd.trade_date = d.trade_date
                        WHERE d.ts_code = :code
                          AND d.trade_date BETWEEN :a AND :b
                          AND d.up_num IS NULL
                        GROUP BY d.trade_date
                    ) s
                    WHERE i.ts_code = :code AND i.trade_date = s.trade_date AND s.n >= 5
                    """
                ),
                {"code": code, "a": start, "b": end},
            )
        return int(result.rowcount or 0)

    def _fill_benchmark(self, start: date, end: date) -> int:
        with self.engine.connect() as conn:
            have = conn.execute(
                sa_text(
                    """
                    SELECT COUNT(*), MIN(trade_date) FROM index_daily
                    WHERE ts_code = '000300.SH' AND trade_date BETWEEN :a AND :b
                    """
                ),
                {"a": start, "b": end},
            ).fetchone()
        if int(have[0] or 0) >= 200 and have[1] is not None and have[1] <= start:
            return 0
        df = self.client.query(
            "index_daily",
            ts_code="000300.SH",
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
        )
        normalized = self._selective._normalize_bars(df, "000300.SH")
        return int(self._selective.upsert_bars("daily", normalized, table="index_daily"))

    def _write_regimes(self, start: date, end: date) -> int:
        """用沪深300收盘和均线补没有标签的交易日。已有标签保持不变。"""
        import json

        with self.engine.connect() as conn:
            rows = conn.execute(
                sa_text(
                    """
                    SELECT trade_date, close FROM index_daily
                    WHERE ts_code = '000300.SH' AND close IS NOT NULL
                    ORDER BY trade_date
                    """
                )
            ).fetchall()
            existing = {
                row[0]
                for row in conn.execute(sa_text("SELECT DISTINCT trade_date FROM market_regime")).fetchall()
            }
        closes = [(row[0], float(row[1])) for row in rows if row[0] is not None and row[1] is not None]
        pending = []
        for index in range(60, len(closes)):
            day, close = closes[index]
            if day < start or day > end or day in existing:
                continue
            window20 = [item[1] for item in closes[index - 19:index + 1]]
            window60 = [item[1] for item in closes[index - 59:index + 1]]
            ma20 = sum(window20) / len(window20)
            ma60 = sum(window60) / len(window60)
            base20 = closes[index - 20][1]
            base60 = closes[index - 60][1]
            ret20 = close / base20 - 1 if base20 else 0
            ret60 = close / base60 - 1 if base60 else 0
            if close > ma60 and ma20 > ma60 and ret20 >= 0.03:
                label = "牛"
            elif close < ma60 and ma20 < ma60 and ret20 <= -0.03:
                label = "熊"
            elif ret20 > 0 and ret60 < 0:
                label = "修复"
            else:
                label = "震荡"
            pending.append({
                "day": day,
                "label": label,
                "evidence": json.dumps({
                    "source": "000300.SH",
                    "rule": "收盘相对20日和60日均线，已有市场环境不覆盖",
                    "close": round(close, 4),
                    "ma20": round(ma20, 4),
                    "ma60": round(ma60, 4),
                    "ret20": round(ret20, 4),
                    "ret60": round(ret60, 4),
                }, ensure_ascii=False),
            })
        if not pending:
            return 0
        with self.engine.begin() as conn:
            conn.execute(
                sa_text(
                    """
                    INSERT INTO market_regime (trade_date, regime_label, confidence, evidence)
                    VALUES (:day, :label, 0.5, CAST(:evidence AS jsonb))
                    """
                ),
                pending,
            )
        return len(pending)

    # ─── 一次完整同步 ─────────────────────────────────

    def sync_day(
        self,
        trade_date: Optional[str] = None,
        fetch_members_for: Optional[Sequence[str]] = None,
        member_levels: Sequence[str] = ("东财一级行业",),
        member_limit: int = 40,
    ) -> Dict[str, Any]:
        """同步一个 asof 日的行业板块三类主表；可选同步成分股。"""
        self._dc_dead = False
        self.ensure_tables()
        meta = self.resolve_asof(trade_date)
        ymd = meta["ymd"]
        result: Dict[str, Any] = {"ok": True, "session": meta, "counts": {}, "errors": {}, "source": "datahubco"}

        idx = pd.DataFrame()
        daily = pd.DataFrame()
        mf = pd.DataFrame()

        try:
            idx = self.fetch_dc_index(ymd)
        except Exception as e:
            result["errors"]["index"] = str(e)
            if self._api_unavailable(e):
                self._dc_dead = True
            logger.warning("dc_index 不可用: %s", e)

        if not self._dc_dead:
            try:
                daily = self.fetch_dc_daily(ymd)
            except Exception as e:
                result["errors"]["daily"] = str(e)
                if self._api_unavailable(e):
                    self._dc_dead = True
                logger.warning("dc_daily 不可用: %s", e)
            try:
                mf = self.fetch_moneyflow(ymd)
            except Exception as e:
                result["errors"]["moneyflow"] = str(e)
                if self._api_unavailable(e):
                    self._dc_dead = True
                logger.warning("moneyflow_ind_dc 不可用: %s", e)

        if idx.empty or daily.empty:
            try:
                em = self.fetch_eastmoney_industry(ymd)
                if not em["index"].empty:
                    result["source"] = "eastmoney"
                    use_date: date = em["trade_date"]
                    meta = {
                        **meta,
                        "asof": use_date.isoformat(),
                        "asof_date": use_date,
                        "ymd": use_date.strftime("%Y%m%d"),
                        "reason": f"{meta.get('reason')}|eastmoney_clist",
                    }
                    result["session"] = meta
                    idx = em["index"]
                    daily = em["daily"]
                    if mf.empty:
                        mf = em["moneyflow"]
            except Exception as e:
                logger.warning("东财 clist 暂不可用: %s", e)
                result["errors"]["eastmoney"] = str(e)

        if idx.empty or daily.empty:
            try:
                sw = self.fetch_sw_overlay(ymd)
                if not sw["index"].empty:
                    result["source"] = "sw_daily"
                    use_date = sw["trade_date"]
                    if not isinstance(use_date, date):
                        use_date = datetime.strptime(str(use_date)[:10], "%Y-%m-%d").date()
                    meta = {
                        **meta,
                        "asof": use_date.isoformat(),
                        "asof_date": use_date,
                        "ymd": use_date.strftime("%Y%m%d"),
                        "reason": f"{meta.get('reason')}|sw_daily_name_overlay",
                    }
                    result["session"] = meta
                    idx = sw["index"]
                    daily = sw["daily"]
            except Exception as e:
                logger.error("申万行业映射失败: %s", e)
                result["errors"]["sw_daily"] = str(e)

        try:
            n = self.upsert_index(idx)
            result["counts"]["sector_dc_index_daily"] = n
            logger.info("sector_dc_index_daily upsert %d · %s · %s", n, meta.get("ymd"), result["source"])
        except Exception as e:
            logger.error("dc_index 写入失败: %s", e)
            result["errors"]["index_upsert"] = str(e)

        try:
            n = self.upsert_daily(daily)
            result["counts"]["sector_dc_daily"] = n
            amt_n = int(daily["amount"].fillna(0).gt(0).sum()) if (not daily.empty and "amount" in daily.columns) else 0
            logger.info("sector_dc_daily upsert %d · amount>0 %d · %s", n, amt_n, meta.get("ymd"))
        except Exception as e:
            logger.error("dc_daily 写入失败: %s", e)
            result["errors"]["daily_upsert"] = str(e)

        try:
            n = self.upsert_moneyflow(mf)
            result["counts"]["sector_moneyflow_daily"] = n
        except Exception as e:
            logger.error("moneyflow 写入失败: %s", e)
            result["errors"]["moneyflow_upsert"] = str(e)

        try:
            result["counts"]["ref_sector_dc_basic"] = self.refresh_catalog()
        except Exception as e:
            logger.warning("刷新板块目录失败: %s", e)

        codes: List[str] = list(fetch_members_for or [])
        if not codes and result["counts"].get("sector_dc_index_daily"):
            try:
                with self.engine.connect() as conn:
                    rows = conn.execute(
                        sa_text(
                            """
                            SELECT ts_code FROM sector_dc_index_daily
                            WHERE trade_date = :d AND industry_level = :lv
                            ORDER BY pct_change DESC NULLS LAST
                            LIMIT :lim
                            """
                        ),
                        {
                            "d": meta["asof_date"],
                            "lv": list(member_levels)[0] if member_levels else "东财一级行业",
                            "lim": member_limit,
                        },
                    ).fetchall()
                    codes = [r[0] for r in rows]
            except Exception as e:
                logger.warning("选取成分股板块失败: %s", e)

        member_n = 0
        for code in codes:
            try:
                mdf = self.fetch_members(code, ymd)
                if not mdf.empty and "trade_date" in mdf.columns:
                    mdf = mdf.loc[mdf["trade_date"] == meta["asof_date"]]
                member_n += self.upsert_members(mdf)
            except Exception as e:
                logger.warning("成分 %s 失败: %s", code, e)
                if self._api_unavailable(e):
                    self._dc_dead = True
                    break
        result["counts"]["sector_dc_member"] = member_n
        result["member_sectors"] = codes
        result["ok"] = bool(result["counts"].get("sector_dc_index_daily") or result["counts"].get("sector_dc_daily"))
        return _jsonable(result)

    # ─── 层级（成分包含推断）──────────────────────────

    def list_sectors_by_level(self, trade_date: date, level: str) -> List[Dict[str, str]]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                sa_text(
                    """
                    SELECT ts_code, COALESCE(name, ts_code) AS name
                    FROM sector_dc_index_daily
                    WHERE trade_date = :d AND industry_level = :lv AND idx_type = '行业板块'
                    ORDER BY ts_code
                    """
                ),
                {"d": trade_date, "lv": level},
            ).fetchall()
        return [{"ts_code": r[0], "name": r[1]} for r in rows]

    def member_set(self, sector_code: str, trade_date: date) -> Set[str]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                sa_text(
                    "SELECT con_code FROM sector_dc_member WHERE ts_code=:c AND trade_date=:d"
                ),
                {"c": sector_code, "d": trade_date},
            ).fetchall()
        return {str(r[0]).upper() for r in rows if r[0]}

    def ensure_members_for_codes(
        self,
        codes: Sequence[str],
        ymd: str,
        asof: date,
        sleep_s: float = 0.05,
        skip_existing: bool = True,
    ) -> Dict[str, Any]:
        """批量补齐成分股；已有成分则跳过。"""
        fetched = 0
        skipped = 0
        failed = 0
        for code in codes:
            if skip_existing:
                existing = self.member_set(code, asof)
                if not existing and self.engine:
                    with self.engine.connect() as conn:
                        n = conn.execute(
                            sa_text("SELECT COUNT(*) FROM sector_dc_member WHERE ts_code=:c"),
                            {"c": code},
                        ).scalar()
                    existing = bool(n)
                if existing:
                    skipped += 1
                    continue
            try:
                mdf = self.fetch_members(code, ymd)
                if not mdf.empty and "trade_date" in mdf.columns:
                    mdf = mdf.loc[mdf["trade_date"] == asof]
                if not mdf.empty:
                    self.upsert_members(mdf)
                    fetched += 1
                else:
                    failed += 1
            except Exception as e:
                failed += 1
                logger.warning("ensure_members %s: %s", code, e)
            if sleep_s:
                time.sleep(sleep_s)
        return {"fetched": fetched, "skipped": skipped, "failed": failed, "total": len(codes)}

    def _assign_parents(
        self,
        parents: List[Dict[str, str]],
        children: List[Dict[str, str]],
        parent_level: str,
        child_level: str,
        trade_date: date,
        min_score: float,
    ) -> List[Dict[str, Any]]:
        parent_sets: Dict[str, Set[str]] = {}
        for p in parents:
            s = self.member_set(p["ts_code"], trade_date)
            if s:
                parent_sets[p["ts_code"]] = s
        parent_name = {p["ts_code"]: p["name"] for p in parents}

        edges: List[Dict[str, Any]] = []
        for ch in children:
            cset = self.member_set(ch["ts_code"], trade_date)
            if not cset:
                continue
            best_code = None
            best_score = -1.0
            for pcode, pset in parent_sets.items():
                inter = len(cset & pset)
                score = inter / len(cset)
                if score > best_score:
                    best_score = score
                    best_code = pcode
            if best_code is None or best_score < min_score:
                continue
            edges.append({
                "child_code": ch["ts_code"],
                "parent_code": best_code,
                "child_level": child_level,
                "parent_level": parent_level,
                "child_name": ch["name"],
                "parent_name": parent_name.get(best_code, best_code),
                "score": round(float(best_score), 4),
                "method": "member_containment",
                "asof_date": trade_date,
            })
        return edges

    def upsert_hierarchy(self, edges: List[Dict[str, Any]]) -> int:
        if not edges:
            return 0
        df = pd.DataFrame(edges)
        cols = [
            "child_code", "parent_code", "child_level", "parent_level",
            "child_name", "parent_name", "score", "method", "asof_date",
        ]
        return self._upsert_frame("sector_dc_hierarchy", df, cols, "child_code")

    def hierarchy_edge_count(self) -> int:
        try:
            with self.engine.connect() as conn:
                return int(conn.execute(sa_text("SELECT COUNT(1) FROM sector_dc_hierarchy")).scalar() or 0)
        except Exception:
            return 0

    def build_hierarchy(
        self,
        trade_date: Optional[str] = None,
        min_score: float = 0.45,
        sync_members: bool = True,
        sleep_s: float = 0.05,
    ) -> Dict[str, Any]:
        """
        用成分股集合包含关系推断 L1→L2、L2→L3，写入 sector_dc_hierarchy。
        每个子板块只保留得分最高的父板块。
        """
        self.ensure_tables()
        meta = self.resolve_asof(trade_date)
        asof: date = meta["asof_date"]
        ymd = meta["ymd"]

        # 若当日无 index，回退库内最新日
        with self.engine.connect() as conn:
            latest = conn.execute(
                sa_text("SELECT MAX(trade_date) FROM sector_dc_index_daily")
            ).scalar()
        if latest and latest < asof:
            asof = latest
            ymd = asof.strftime("%Y%m%d")

        result: Dict[str, Any] = {
            "ok": True,
            "asof": asof.isoformat(),
            "min_score": min_score,
            "member_sync": {},
            "counts": {},
        }

        l1 = self.list_sectors_by_level(asof, "东财一级行业")
        l2 = self.list_sectors_by_level(asof, "东财二级行业")
        l3 = self.list_sectors_by_level(asof, "东财三级行业")
        result["counts"]["l1"] = len(l1)
        result["counts"]["l2"] = len(l2)
        result["counts"]["l3"] = len(l3)

        if sync_members:
            all_codes = [x["ts_code"] for x in l1 + l2 + l3]
            result["member_sync"] = self.ensure_members_for_codes(
                all_codes, ymd, asof, sleep_s=sleep_s, skip_existing=True
            )

        edges_l2 = self._assign_parents(l1, l2, "东财一级行业", "东财二级行业", asof, min_score)
        edges_l3 = self._assign_parents(l2, l3, "东财二级行业", "东财三级行业", asof, min_score)
        edges = edges_l2 + edges_l3
        n = self.upsert_hierarchy(edges)
        result["counts"]["edges_l1_l2"] = len(edges_l2)
        result["counts"]["edges_l2_l3"] = len(edges_l3)
        result["counts"]["hierarchy_upserted"] = n
        result["ok"] = n > 0
        logger.info(
            "hierarchy built asof=%s L2=%d L3=%d",
            asof, len(edges_l2), len(edges_l3),
        )
        return result

    def ensure_hierarchy(
        self,
        trade_date: Optional[str] = None,
        min_edges: int = 80,
        sync_members: bool = True,
    ) -> Dict[str, Any]:
        """库内边过少则重建层级。"""
        self.ensure_tables()
        n = self.hierarchy_edge_count()
        if n >= min_edges:
            return {"ok": True, "rebuilt": False, "edges": n}
        built = self.build_hierarchy(trade_date=trade_date, sync_members=sync_members)
        return {"ok": built.get("ok", False), "rebuilt": True, "edges": self.hierarchy_edge_count(), "build": built}


sector_fetcher = SectorDataFetcher()

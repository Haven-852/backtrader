"""
东财行业板块持续采集（Datahubco → 东财行情兜底 → PostgreSQL）

表:
  - sector_dc_index_daily    涨跌幅/领涨股/涨跌家数/层级
  - sector_dc_daily          OHLC/成交额
  - sector_moneyflow_daily   资金流向
  - sector_dc_member         板块成分股
  - sector_dc_hierarchy      一级→二级→三级

主源 Datahubco dc_*；接口不可用时改用东财 clist（最近收盘日含成交额）。
交易日 asof 规则复用 SelectiveDataFetcher.session_context：
  交易日闭市前 → 上一交易日；闭市后 → 当天；非交易日 → 上一交易日。
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime
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
    "https://82.push2.eastmoney.com/api/qt/clist/get",
)
EM_KLINE_URLS = (
    "https://push2his.eastmoney.com/api/qt/stock/kline/get",
    "https://79.push2his.eastmoney.com/api/qt/stock/kline/get",
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
]

INDEX_DDL = [
    "CREATE INDEX IF NOT EXISTS idx_sector_dc_index_date ON sector_dc_index_daily (trade_date)",
    "CREATE INDEX IF NOT EXISTS idx_sector_dc_index_level ON sector_dc_index_daily (trade_date, industry_level)",
    "CREATE INDEX IF NOT EXISTS idx_sector_dc_daily_date ON sector_dc_daily (trade_date)",
    "CREATE INDEX IF NOT EXISTS idx_sector_mf_date ON sector_moneyflow_daily (trade_date, content_type)",
    "CREATE INDEX IF NOT EXISTS idx_sector_member_code ON sector_dc_member (ts_code, trade_date)",
    "CREATE INDEX IF NOT EXISTS idx_sector_hierarchy_parent ON sector_dc_hierarchy (parent_code)",
    "CREATE INDEX IF NOT EXISTS idx_sector_hierarchy_levels ON sector_dc_hierarchy (parent_level, child_level)",
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
        return {"ok": True, "tables": [
            "sector_dc_index_daily",
            "sector_dc_daily",
            "sector_moneyflow_daily",
            "sector_dc_member",
            "sector_dc_hierarchy",
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
        df = self.client.query("dc_index", trade_date=ymd)
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

    def fetch_members(self, sector_code: str, ymd: str) -> pd.DataFrame:
        df = self.client.query("dc_member", ts_code=sector_code, trade_date=ymd)
        if df.empty:
            # 部分网关不认 trade_date，再试仅代码
            try:
                df = self.client.query("dc_member", ts_code=sector_code)
            except Exception:
                return pd.DataFrame()
        if df.empty:
            return df
        df = df.copy()
        df.columns = [str(c).lower() for c in df.columns]
        if "trade_date" in df.columns:
            df["trade_date"] = _parse_dates(df["trade_date"])
        else:
            df["trade_date"] = datetime.strptime(ymd, "%Y%m%d").date()
        df["ts_code"] = sector_code
        if "con_code" in df.columns:
            df["con_code"] = df["con_code"].astype(str).str.upper()
        return df.drop_duplicates(subset=["ts_code", "trade_date", "con_code"], keep="last")

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
        cols = ["trade_date", "ts_code", "con_code", "name"]
        return self._upsert_frame(
            "sector_dc_member", df, cols, "ts_code, trade_date, con_code"
        )

    # ─── 东财行情兜底 ─────────────────────────────────

    def _api_unavailable(self, err: Exception) -> bool:
        msg = str(err)
        return "unknown api_name" in msg or "HTTP 400" in msg

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

    def _em_http_json(self, url: str, params: Dict[str, Any], retries: int = 1) -> Dict[str, Any]:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Referer": "https://quote.eastmoney.com/",
        }
        last_err: Optional[Exception] = None
        for attempt in range(retries):
            try:
                resp = requests.get(
                    url,
                    params=params,
                    timeout=20,
                    headers=headers,
                    proxies={"http": None, "https": None},
                )
                resp.raise_for_status()
                payload = resp.json()
                return payload if isinstance(payload, dict) else {}
            except Exception as e:
                last_err = e
                time.sleep(0.4 * (attempt + 1))
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

    # ─── 一次完整同步 ─────────────────────────────────

    def sync_day(
        self,
        trade_date: Optional[str] = None,
        fetch_members_for: Optional[Sequence[str]] = None,
        member_levels: Sequence[str] = ("东财一级行业",),
        member_limit: int = 40,
    ) -> Dict[str, Any]:
        """同步一个 asof 日的行业板块三类主表；可选同步成分股。"""
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

        codes: List[str] = list(fetch_members_for or [])
        if getattr(self, "_dc_dead", False):
            result["counts"]["sector_dc_member"] = 0
            result["member_sectors"] = []
            result["ok"] = bool(result["counts"].get("sector_dc_index_daily") or result["counts"].get("sector_dc_daily"))
            return result

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
        return result

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

"""
全市场股票清单工具

从 Datahubco `stock_basic` 分页拉取 list_status=L 的全市场代码，
写入 `ref_market_universe`（含 board_type），并同步 `ref_stock_basic`，
供 `fill_batch` 按 offset/limit 切片读取。

用法：
  action=status        看本地行数 / 按板块分布
  action=ensure_schema 建表
  action=fill          分页拉全量并 upsert（默认 list_status=L）
"""
from __future__ import annotations

import asyncio
import logging
import math
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import pandas as pd
from sqlalchemy import text as sa_text

from tools.ml_train_toolkit import classify_board_type

logger = logging.getLogger(__name__)

_PAGE_SIZE = 5000
_FETCH_RETRIES = 3


def _parse_date(v: Any) -> Optional[date]:
    if v is None or v == "" or (isinstance(v, float) and math.isnan(v)):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip().replace("/", "")
    if len(s) == 8 and s.isdigit():
        return datetime.strptime(s, "%Y%m%d").date()
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def _exchange_of(ts_code: str) -> str:
    _, _, exch = (ts_code or "").partition(".")
    return (exch or "").upper()


class MarketUniverseToolkit:
    """全市场股票清单：fill / status / ensure_schema。"""

    def __init__(self) -> None:
        self._engine = None
        self._client = None

    @property
    def engine(self):
        if self._engine is None:
            from data_layer.db_manager import storage_manager

            self._engine = storage_manager.get_postgres_engine()
        return self._engine

    @property
    def client(self):
        if self._client is None:
            from data_layer.collectors.datahubco.client import DatahubcoClient

            self._client = DatahubcoClient()
        return self._client

    def ensure_schema(self) -> Dict[str, Any]:
        ddl = """
            CREATE TABLE IF NOT EXISTS ref_market_universe (
                ts_code        VARCHAR(16)  PRIMARY KEY,
                symbol         VARCHAR(10)  NOT NULL,
                name           VARCHAR(50),
                area           VARCHAR(20),
                industry       VARCHAR(50),
                market         VARCHAR(10),
                board_type     VARCHAR(16)  NOT NULL,
                exchange       VARCHAR(8),
                list_date      DATE,
                list_status    VARCHAR(1)   NOT NULL DEFAULT 'L',
                is_hs          VARCHAR(1),
                updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_market_universe_board ON ref_market_universe (board_type, list_status)",
            "CREATE INDEX IF NOT EXISTS idx_market_universe_status ON ref_market_universe (list_status, ts_code)",
        ]
        with self.engine.begin() as conn:
            conn.execute(sa_text(ddl))
            for sql in indexes:
                conn.execute(sa_text(sql))
        return {"ok": True, "action": "ensure_schema", "table": "ref_market_universe"}

    def status(self) -> Dict[str, Any]:
        self.ensure_schema()
        with self.engine.connect() as conn:
            total = int(
                conn.execute(sa_text("SELECT COUNT(*) FROM ref_market_universe")).scalar() or 0
            )
            listed = int(
                conn.execute(
                    sa_text("SELECT COUNT(*) FROM ref_market_universe WHERE list_status = 'L'")
                ).scalar()
                or 0
            )
            by_board = conn.execute(
                sa_text(
                    """
                    SELECT board_type, COUNT(*) AS n
                    FROM ref_market_universe
                    WHERE list_status = 'L'
                    GROUP BY 1
                    ORDER BY 2 DESC
                    """
                )
            ).fetchall()
            basic_n = int(
                conn.execute(sa_text("SELECT COUNT(*) FROM ref_stock_basic")).scalar() or 0
            )
            sample = conn.execute(
                sa_text(
                    """
                    SELECT ts_code, name, board_type, market, industry
                    FROM ref_market_universe
                    WHERE list_status = 'L'
                    ORDER BY ts_code
                    LIMIT 5
                    """
                )
            ).mappings().fetchall()
        return {
            "ok": True,
            "action": "status",
            "table": "ref_market_universe",
            "total_rows": total,
            "listed_rows": listed,
            "by_board_type": [{"board_type": r[0], "rows": int(r[1])} for r in by_board],
            "ref_stock_basic_rows": basic_n,
            "sample": [dict(r) for r in sample],
            "ready_for_fill_batch": listed > 0,
            "next_hint": (
                "清单已就绪，可用 fill-ml-train-daily action=fill_batch limit/offset 采集。"
                if listed > 0
                else "请先 action=fill 拉取全市场代码。"
            ),
        }

    def _fetch_page(
        self,
        *,
        list_status: str,
        offset: int,
        limit: int,
    ) -> pd.DataFrame:
        last_err: Optional[BaseException] = None
        for attempt in range(_FETCH_RETRIES):
            try:
                df = self.client.query(
                    "stock_basic",
                    list_status=list_status,
                    offset=int(offset),
                    limit=int(limit),
                )
                return df if df is not None else pd.DataFrame()
            except Exception as e:
                last_err = e
                logger.warning(
                    "stock_basic page offset=%s 失败 (%d/%d): %s",
                    offset,
                    attempt + 1,
                    _FETCH_RETRIES,
                    e,
                )
        raise RuntimeError(f"stock_basic 分页失败 offset={offset}: {last_err}")

    def _normalize_rows(self, df: pd.DataFrame, list_status: str) -> List[Dict[str, Any]]:
        if df is None or df.empty:
            return []
        rows: List[Dict[str, Any]] = []
        for _, r in df.iterrows():
            ts_code = str(r.get("ts_code") or "").strip().upper()
            if not ts_code or "." not in ts_code:
                continue
            name = r.get("name")
            name_s = str(name).strip() if name is not None and str(name) not in ("nan", "None") else None
            market = r.get("market")
            market_s = (
                str(market).strip()
                if market is not None and str(market) not in ("nan", "None")
                else None
            )
            symbol = r.get("symbol")
            if symbol is None or str(symbol) in ("nan", "None", ""):
                symbol = ts_code.split(".", 1)[0]
            else:
                symbol = str(symbol).strip()
            status = r.get("list_status")
            status_s = (
                str(status).strip().upper()[:1]
                if status is not None and str(status) not in ("nan", "None", "")
                else list_status
            )
            board = classify_board_type(ts_code, name=name_s, market=market_s)
            area = r.get("area")
            industry = r.get("industry")
            is_hs = r.get("is_hs")
            rows.append(
                {
                    "ts_code": ts_code,
                    "symbol": symbol,
                    "name": name_s,
                    "area": (
                        str(area).strip()
                        if area is not None and str(area) not in ("nan", "None")
                        else None
                    ),
                    "industry": (
                        str(industry).strip()
                        if industry is not None and str(industry) not in ("nan", "None")
                        else None
                    ),
                    "market": market_s,
                    "board_type": board,
                    "exchange": _exchange_of(ts_code),
                    "list_date": _parse_date(r.get("list_date")),
                    "list_status": status_s or "L",
                    "is_hs": (
                        str(is_hs).strip()[:1]
                        if is_hs is not None and str(is_hs) not in ("nan", "None")
                        else None
                    ),
                }
            )
        return rows

    def _upsert_universe(self, rows: List[Dict[str, Any]]) -> int:
        if not rows:
            return 0
        sql = sa_text(
            """
            INSERT INTO ref_market_universe (
              ts_code, symbol, name, area, industry, market,
              board_type, exchange, list_date, list_status, is_hs, updated_at
            ) VALUES (
              :ts_code, :symbol, :name, :area, :industry, :market,
              :board_type, :exchange, :list_date, :list_status, :is_hs, CURRENT_TIMESTAMP
            )
            ON CONFLICT (ts_code) DO UPDATE SET
              symbol = EXCLUDED.symbol,
              name = EXCLUDED.name,
              area = EXCLUDED.area,
              industry = EXCLUDED.industry,
              market = EXCLUDED.market,
              board_type = EXCLUDED.board_type,
              exchange = EXCLUDED.exchange,
              list_date = EXCLUDED.list_date,
              list_status = EXCLUDED.list_status,
              is_hs = EXCLUDED.is_hs,
              updated_at = CURRENT_TIMESTAMP
            """
        )
        with self.engine.begin() as conn:
            conn.execute(sql, rows)
        return len(rows)

    def _upsert_stock_basic(self, rows: List[Dict[str, Any]]) -> int:
        """同步写入 ref_stock_basic，供其它工具 JOIN。"""
        if not rows:
            return 0
        sql = sa_text(
            """
            INSERT INTO ref_stock_basic (
              ts_code, symbol, name, area, industry, market,
              list_date, list_status, is_hs
            ) VALUES (
              :ts_code, :symbol, :name, :area, :industry, :market,
              :list_date, :list_status, :is_hs
            )
            ON CONFLICT (ts_code) DO UPDATE SET
              symbol = EXCLUDED.symbol,
              name = EXCLUDED.name,
              area = EXCLUDED.area,
              industry = EXCLUDED.industry,
              market = EXCLUDED.market,
              list_date = EXCLUDED.list_date,
              list_status = EXCLUDED.list_status,
              is_hs = EXCLUDED.is_hs
            """
        )
        payload = [
            {
                "ts_code": r["ts_code"],
                "symbol": r["symbol"],
                "name": r["name"],
                "area": r["area"],
                "industry": r["industry"],
                "market": r["market"],
                "list_date": r["list_date"],
                "list_status": r["list_status"],
                "is_hs": r["is_hs"],
            }
            for r in rows
        ]
        with self.engine.begin() as conn:
            conn.execute(sql, payload)
        return len(payload)

    def fill(
        self,
        *,
        list_status: str = "L",
        page_size: int = _PAGE_SIZE,
        sync_stock_basic: bool = True,
        max_pages: int = 20,
    ) -> Dict[str, Any]:
        """
        分页拉取 Datahubco stock_basic → 写入 ref_market_universe（含 board_type）。
        默认同时同步 ref_stock_basic。
        """
        self.ensure_schema()
        status = (list_status or "L").strip().upper()[:1] or "L"
        page_size = max(100, min(int(page_size or _PAGE_SIZE), 5000))
        max_pages = max(1, min(int(max_pages or 20), 50))

        pages: List[Dict[str, Any]] = []
        total_upserted = 0
        basic_upserted = 0
        offset = 0
        seen_codes: set = set()

        for page_i in range(max_pages):
            df = self._fetch_page(list_status=status, offset=offset, limit=page_size)
            n = 0 if df is None else len(df)
            rows = self._normalize_rows(df, status)
            # 去重（分页偶发重叠）
            fresh = [r for r in rows if r["ts_code"] not in seen_codes]
            for r in fresh:
                seen_codes.add(r["ts_code"])
            ups = self._upsert_universe(fresh)
            total_upserted += ups
            if sync_stock_basic and fresh:
                basic_upserted += self._upsert_stock_basic(fresh)
            pages.append(
                {
                    "page": page_i + 1,
                    "offset": offset,
                    "fetched": n,
                    "upserted": ups,
                }
            )
            logger.info(
                "market_universe page=%d offset=%d fetched=%d upserted=%d",
                page_i + 1,
                offset,
                n,
                ups,
            )
            if n < page_size:
                break
            offset += page_size
        else:
            pages.append({"note": f"已达 max_pages={max_pages}，可能还有更多"})

        st = self.status()
        return {
            "ok": True,
            "action": "fill",
            "list_status": status,
            "page_size": page_size,
            "pages": pages,
            "unique_codes": len(seen_codes),
            "universe_upserted": total_upserted,
            "stock_basic_upserted": basic_upserted if sync_stock_basic else 0,
            "listed_rows": st.get("listed_rows"),
            "by_board_type": st.get("by_board_type"),
            "ready_for_fill_batch": bool(st.get("ready_for_fill_batch")),
            "next_hint": (
                "清单已写入 ref_market_universe；"
                "接下来用 datahubco_data__fill_ml_train_daily action=fill_batch "
                "limit=50 offset=0 开始采集，按 next_offset 循环。"
            ),
        }

    def run(self, action: str = "status", **kwargs: Any) -> Dict[str, Any]:
        action = (action or "status").strip().lower()
        if action in ("ensure_schema", "schema", "ensure"):
            return self.ensure_schema()
        if action in ("status", "stat"):
            return self.status()
        if action in ("fill", "sync", "refresh"):
            return self.fill(
                list_status=str(kwargs.get("list_status") or "L"),
                page_size=int(kwargs.get("page_size") or kwargs.get("limit") or _PAGE_SIZE),
                sync_stock_basic=False if kwargs.get("sync_stock_basic") is False else True,
                max_pages=int(kwargs.get("max_pages") or 20),
            )
        return {
            "ok": False,
            "error": "action 应为 status / ensure_schema / fill",
        }

    async def run_async(self, **kwargs: Any) -> Dict[str, Any]:
        return await asyncio.to_thread(self.run, **kwargs)


market_universe_toolkit = MarketUniverseToolkit()

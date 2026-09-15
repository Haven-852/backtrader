"""
东财行业板块看板服务：气泡图数据 + 成分股当日行情
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any, Dict, List, Optional

import pandas as pd
from sqlalchemy import text as sa_text

from data_layer.db_manager import storage_manager

logger = logging.getLogger(__name__)

LEVEL_MAP = {
    "1": "东财一级行业",
    "l1": "东财一级行业",
    "一级": "东财一级行业",
    "东财一级行业": "东财一级行业",
    "2": "东财二级行业",
    "l2": "东财二级行业",
    "二级": "东财二级行业",
    "东财二级行业": "东财二级行业",
    "3": "东财三级行业",
    "l3": "东财三级行业",
    "三级": "东财三级行业",
    "东财三级行业": "东财三级行业",
}


def _ser(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, date):
        return v.isoformat()
    if hasattr(v, "item"):
        try:
            return v.item()
        except Exception:
            return float(v) if pd.notna(v) else None
    return v


class SectorService:
    def __init__(self):
        self._engine = None

    @property
    def engine(self):
        if self._engine is None:
            try:
                self._engine = storage_manager.get_postgres_engine()
            except Exception as e:
                logger.warning("sector engine: %s", e)
        return self._engine

    def _latest_trade_date(self) -> Optional[date]:
        if not self.engine:
            return None
        try:
            with self.engine.connect() as conn:
                row = conn.execute(
                    sa_text("SELECT MAX(trade_date) FROM sector_dc_index_daily")
                ).fetchone()
            return row[0] if row and row[0] else None
        except Exception:
            return None

    def ensure_data(self, trade_date: Optional[str] = None) -> Dict[str, Any]:
        """库内无 asof 日数据则触发同步。"""
        from data_layer.collectors.datahubco.sectors import sector_fetcher

        sector_fetcher.ensure_tables()
        meta = sector_fetcher.resolve_asof(trade_date)
        asof: date = meta["asof_date"]
        latest = self._latest_trade_date()
        if latest is not None and latest >= asof:
            return {
                "synced": False,
                "asof": asof.isoformat(),
                "latest": latest.isoformat(),
                "session": meta,
            }
        result = sector_fetcher.sync_day(
            trade_date=asof.strftime("%Y%m%d"),
            member_limit=31,
            member_levels=("东财一级行业",),
        )
        return {
            "synced": True,
            "asof": asof.isoformat(),
            "session": meta,
            "result": result,
        }

    def get_board(
        self,
        level: str = "东财一级行业",
        trade_date: Optional[str] = None,
        auto_sync: bool = True,
        parent_code: Optional[str] = None,
    ) -> Dict[str, Any]:
        """合并 index + daily + moneyflow，供气泡图/tooltip。

        parent_code 若传入，则返回该父板块下的子行业（忽略 level，以层级表为准）。
        """
        level_name = LEVEL_MAP.get(level, level) or "东财一级行业"
        parent_code = (parent_code or "").strip().upper() or None
        sync_info = {}
        if auto_sync:
            try:
                sync_info = self.ensure_data(trade_date)
            except Exception as e:
                logger.warning("sector auto_sync: %s", e)
                sync_info = {"synced": False, "error": str(e)}

        if not self.engine:
            return {"ok": False, "error": "数据库未连接", "sectors": [], "levels": list(LEVEL_MAP.values())}

        from data_layer.collectors.datahubco.sectors import sector_fetcher

        meta = sector_fetcher.resolve_asof(trade_date)
        asof: date = meta["asof_date"]
        # 若库内最新日更旧，用库内最新
        latest = self._latest_trade_date()
        use_date = asof
        if latest and latest < asof:
            use_date = latest

        hierarchy_info: Dict[str, Any] = {}
        if parent_code:
            try:
                # 边不足时才重建；首次会补齐成分（较慢，仅一次）
                hierarchy_info = sector_fetcher.ensure_hierarchy(
                    trade_date=use_date.strftime("%Y%m%d"),
                    min_edges=80,
                    sync_members=True,
                )
            except Exception as e:
                logger.warning("ensure_hierarchy: %s", e)
                hierarchy_info = {"ok": False, "error": str(e)}

            sql = sa_text(
                """
                SELECT
                    i.ts_code, i.trade_date, i.name, i.leading_name AS leading, i.leading_code,
                    i.pct_change, i.leading_pct, i.total_mv, i.turnover_rate,
                    i.up_num, i.down_num, i.idx_type, i.industry_level AS level,
                    d.open, d.high, d.low, d.close, d.change, d.vol, d.amount, d.swing,
                    m.net_amount, m.net_amount_rate, m.buy_sm_amount_stock, m.rank_no AS mf_rank,
                    h.score AS hierarchy_score, h.parent_code, h.parent_name,
                    COALESCE(hc.child_count, 0) AS child_count
                FROM sector_dc_hierarchy h
                JOIN sector_dc_index_daily i
                  ON i.ts_code = h.child_code AND i.trade_date = :d
                LEFT JOIN sector_dc_daily d
                  ON d.ts_code = i.ts_code AND d.trade_date = i.trade_date
                LEFT JOIN sector_moneyflow_daily m
                  ON m.ts_code = i.ts_code AND m.trade_date = i.trade_date
                 AND m.content_type = '行业'
                LEFT JOIN (
                    SELECT parent_code, COUNT(*)::int AS child_count
                    FROM sector_dc_hierarchy
                    GROUP BY parent_code
                ) hc ON hc.parent_code = i.ts_code
                WHERE h.parent_code = :p
                ORDER BY i.pct_change DESC NULLS LAST
                """
            )
            params = {"d": use_date, "p": parent_code}
        else:
            sql = sa_text(
                """
                SELECT
                    i.ts_code, i.trade_date, i.name, i.leading_name AS leading, i.leading_code,
                    i.pct_change, i.leading_pct, i.total_mv, i.turnover_rate,
                    i.up_num, i.down_num, i.idx_type, i.industry_level AS level,
                    d.open, d.high, d.low, d.close, d.change, d.vol, d.amount, d.swing,
                    m.net_amount, m.net_amount_rate, m.buy_sm_amount_stock, m.rank_no AS mf_rank,
                    COALESCE(hc.child_count, 0) AS child_count
                FROM sector_dc_index_daily i
                LEFT JOIN sector_dc_daily d
                  ON d.ts_code = i.ts_code AND d.trade_date = i.trade_date
                LEFT JOIN sector_moneyflow_daily m
                  ON m.ts_code = i.ts_code AND m.trade_date = i.trade_date
                 AND m.content_type = '行业'
                LEFT JOIN (
                    SELECT parent_code, COUNT(*)::int AS child_count
                    FROM sector_dc_hierarchy
                    GROUP BY parent_code
                ) hc ON hc.parent_code = i.ts_code
                WHERE i.trade_date = :d AND i.industry_level = :lv AND i.idx_type = '行业板块'
                ORDER BY i.pct_change DESC NULLS LAST
                """
            )
            params = {"d": use_date, "lv": level_name}

        try:
            df = pd.read_sql(sql, self.engine, params=params)
        except Exception as e:
            logger.error("sector board query: %s", e)
            return {
                "ok": False,
                "error": str(e),
                "sectors": [],
                "trade_date": use_date.isoformat(),
                "level": level_name,
                "parent_code": parent_code,
                "sync": sync_info,
                "hierarchy": hierarchy_info,
            }

        # 父子下钻：若当日 index 联不到子板块，回退到库内最新交易日再查一次
        if parent_code and df.empty and latest and latest != use_date:
            try:
                params = {**params, "d": latest}
                df = pd.read_sql(sql, self.engine, params=params)
                if not df.empty:
                    use_date = latest
            except Exception as e:
                logger.warning("sector children fallback query: %s", e)

        sectors = []
        for _, row in df.iterrows():
            pct = float(row["pct_change"]) if pd.notna(row.get("pct_change")) else 0.0
            amount = float(row["amount"]) if pd.notna(row.get("amount")) else 0.0
            net = float(row["net_amount"]) if pd.notna(row.get("net_amount")) else None
            item = {
                "ts_code": row["ts_code"],
                "name": row.get("name"),
                "trade_date": _ser(row.get("trade_date")),
                "level": row.get("level"),
                "pct_change": round(pct, 4),
                "change": _ser(row.get("change")),
                "close": _ser(row.get("close")),
                "open": _ser(row.get("open")),
                "high": _ser(row.get("high")),
                "low": _ser(row.get("low")),
                "vol": _ser(row.get("vol")),
                "amount": amount,
                "amount_yi": round(amount / 1e8, 4) if amount else 0.0,
                "swing": _ser(row.get("swing")),
                "turnover_rate": _ser(row.get("turnover_rate")),
                "total_mv": _ser(row.get("total_mv")),
                "leading": row.get("leading"),
                "leading_code": row.get("leading_code"),
                "leading_pct": _ser(row.get("leading_pct")),
                "up_num": _ser(row.get("up_num")),
                "down_num": _ser(row.get("down_num")),
                "net_amount": net,
                "net_amount_yi": round(net / 1e8, 4) if net is not None else None,
                "net_amount_rate": _ser(row.get("net_amount_rate")),
                "abs_pct_change": abs(pct),
                "child_count": int(row["child_count"]) if pd.notna(row.get("child_count")) else 0,
            }
            if parent_code:
                item["parent_code"] = row.get("parent_code") or parent_code
                item["parent_name"] = row.get("parent_name")
                item["hierarchy_score"] = _ser(row.get("hierarchy_score"))
            sectors.append(item)

        ups = sum(1 for s in sectors if (s.get("pct_change") or 0) > 0)
        downs = sum(1 for s in sectors if (s.get("pct_change") or 0) < 0)
        avg = (
            sum(s.get("pct_change") or 0 for s in sectors) / len(sectors)
            if sectors else 0
        )

        parent_meta = None
        if parent_code:
            try:
                with self.engine.connect() as conn:
                    prow = conn.execute(
                        sa_text(
                            "SELECT name, industry_level, pct_change FROM sector_dc_index_daily "
                            "WHERE ts_code=:c AND trade_date=:d LIMIT 1"
                        ),
                        {"c": parent_code, "d": use_date},
                    ).fetchone()
                if prow:
                    parent_meta = {
                        "ts_code": parent_code,
                        "name": prow[0],
                        "level": prow[1],
                        "pct_change": _ser(prow[2]),
                    }
            except Exception:
                parent_meta = {"ts_code": parent_code}

            child_level = sectors[0]["level"] if sectors else None
            status = (
                f"子板块气泡 · {parent_meta.get('name') or parent_code}"
                f" → {child_level or '下级'} · {use_date.isoformat()}"
            )
            level_out = child_level or level_name
        else:
            status = f"东财行业板块 · {level_name} · {use_date.isoformat()}"
            level_out = level_name

        return {
            "ok": True,
            "trade_date": use_date.isoformat(),
            "level": level_out,
            "levels": ["东财一级行业", "东财二级行业", "东财三级行业"],
            "parent_code": parent_code,
            "parent": parent_meta,
            "sectors": sectors,
            "summary": {
                "count": len(sectors),
                "up_count": ups,
                "down_count": downs,
                "avg_pct_change": round(avg, 2),
            },
            "session": meta,
            "sync": sync_info,
            "hierarchy": hierarchy_info,
            "status_message": status,
        }

    def rebuild_hierarchy(
        self,
        trade_date: Optional[str] = None,
        sync_members: bool = True,
    ) -> Dict[str, Any]:
        from data_layer.collectors.datahubco.sectors import sector_fetcher

        return sector_fetcher.build_hierarchy(
            trade_date=trade_date,
            sync_members=sync_members,
        )

    def get_sector_path(self, ts_code: str) -> Dict[str, Any]:
        """返回某板块向上的父链（L3→L2→L1）。"""
        ts_code = (ts_code or "").strip().upper()
        if not self.engine or not ts_code:
            return {"ok": False, "path": []}
        from data_layer.collectors.datahubco.sectors import sector_fetcher
        sector_fetcher.ensure_tables()
        path = []
        cur = ts_code
        seen = set()
        with self.engine.connect() as conn:
            while cur and cur not in seen:
                seen.add(cur)
                row = conn.execute(
                    sa_text(
                        """
                        SELECT h.child_code, h.child_name, h.child_level,
                               h.parent_code, h.parent_name, h.parent_level, h.score
                        FROM sector_dc_hierarchy h
                        WHERE h.child_code = :c
                        """
                    ),
                    {"c": cur},
                ).fetchone()
                if not row:
                    break
                path.append({
                    "ts_code": row[0],
                    "name": row[1],
                    "level": row[2],
                    "parent_code": row[3],
                    "parent_name": row[4],
                    "parent_level": row[5],
                    "score": _ser(row[6]),
                })
                cur = row[3]
        return {"ok": True, "ts_code": ts_code, "path": path}
    def get_members(
        self,
        ts_code: str,
        trade_date: Optional[str] = None,
        auto_fetch: bool = True,
    ) -> Dict[str, Any]:
        """板块成分股 + 当日个股行情（stock_daily）。"""
        ts_code = (ts_code or "").strip().upper()
        if not ts_code:
            return {"ok": False, "error": "缺少 ts_code", "members": []}

        from data_layer.collectors.datahubco.sectors import sector_fetcher

        sector_fetcher.ensure_tables()
        meta = sector_fetcher.resolve_asof(trade_date)
        asof: date = meta["asof_date"]
        latest = self._latest_trade_date()
        use_date = latest if latest and latest < asof else asof
        ymd = use_date.strftime("%Y%m%d")

        if not self.engine:
            return {"ok": False, "error": "数据库未连接", "members": []}

        # 读库；空则拉远端
        with self.engine.connect() as conn:
            cnt = conn.execute(
                sa_text(
                    "SELECT COUNT(*) FROM sector_dc_member WHERE ts_code=:c AND trade_date=:d"
                ),
                {"c": ts_code, "d": use_date},
            ).scalar()

        if (not cnt or cnt == 0) and auto_fetch:
            try:
                mdf = sector_fetcher.fetch_members(ts_code, ymd)
                if not mdf.empty:
                    mdf = mdf.loc[mdf["trade_date"] == use_date] if "trade_date" in mdf.columns else mdf
                    sector_fetcher.upsert_members(mdf)
            except Exception as e:
                logger.warning("拉取成分股失败 %s: %s", ts_code, e)

        sql = sa_text(
            """
            SELECT
                m.con_code AS ts_code,
                COALESCE(b.name, m.name) AS name,
                d.close, d.open, d.high, d.low,
                d.pre_close, d.change, d.pct_chg, d.vol, d.amount
            FROM sector_dc_member m
            LEFT JOIN ref_stock_basic b ON b.ts_code = m.con_code
            LEFT JOIN stock_daily d
              ON d.ts_code = m.con_code AND d.trade_date = m.trade_date
            WHERE m.ts_code = :c AND m.trade_date = :d
            ORDER BY d.pct_chg DESC NULLS LAST, m.con_code
            """
        )
        try:
            df = pd.read_sql(sql, self.engine, params={"c": ts_code, "d": use_date})
        except Exception as e:
            return {"ok": False, "error": str(e), "members": [], "ts_code": ts_code}

        # 板块元信息
        sector_name = ts_code
        try:
            with self.engine.connect() as conn:
                row = conn.execute(
                    sa_text(
                        "SELECT name, pct_change, leading_name, leading_code FROM sector_dc_index_daily "
                        "WHERE ts_code=:c AND trade_date=:d LIMIT 1"
                    ),
                    {"c": ts_code, "d": use_date},
                ).fetchone()
                if row:
                    sector_name = row[0] or ts_code
                    sector_meta = {
                        "name": row[0],
                        "pct_change": _ser(row[1]),
                        "leading": row[2],
                        "leading_code": row[3],
                    }
                else:
                    sector_meta = {}
        except Exception:
            sector_meta = {}

        members = []
        for _, r in df.iterrows():
            members.append({
                "ts_code": r["ts_code"],
                "name": r.get("name"),
                "close": _ser(r.get("close")),
                "open": _ser(r.get("open")),
                "high": _ser(r.get("high")),
                "low": _ser(r.get("low")),
                "pre_close": _ser(r.get("pre_close")),
                "change": _ser(r.get("change")),
                "pct_chg": _ser(r.get("pct_chg")),
                "vol": _ser(r.get("vol")),
                "amount": _ser(r.get("amount")),
            })

        return {
            "ok": True,
            "ts_code": ts_code,
            "name": sector_name,
            "trade_date": use_date.isoformat(),
            "sector": sector_meta,
            "members": members,
            "count": len(members),
            "session": meta,
        }

    async def get_board_async(self, **kwargs) -> Dict[str, Any]:
        return await asyncio.to_thread(self.get_board, **kwargs)

    async def get_members_async(self, **kwargs) -> Dict[str, Any]:
        return await asyncio.to_thread(self.get_members, **kwargs)

    async def ensure_data_async(self, **kwargs) -> Dict[str, Any]:
        return await asyncio.to_thread(self.ensure_data, **kwargs)

    async def rebuild_hierarchy_async(self, **kwargs) -> Dict[str, Any]:
        return await asyncio.to_thread(self.rebuild_hierarchy, **kwargs)


sector_service = SectorService()

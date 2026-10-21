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

    def _member_coverage(self, ts_code: str, trade_date: date) -> tuple:
        """返回 (成分数量, 已有现价数量)。"""
        if not self.engine or not trade_date:
            return 0, 0
        try:
            with self.engine.connect() as conn:
                row = conn.execute(
                    sa_text(
                        "SELECT COUNT(*), COUNT(close) "
                        "FROM sector_dc_member WHERE ts_code=:c AND trade_date=:d"
                    ),
                    {"c": ts_code, "d": trade_date},
                ).fetchone()
            return int(row[0] or 0), int(row[1] or 0)
        except Exception:
            return 0, 0

    def _count_on_date(self, table: str, trade_date: date) -> int:
        if not self.engine:
            return 0
        try:
            with self.engine.connect() as conn:
                row = conn.execute(
                    sa_text(f"SELECT COUNT(*) FROM {table} WHERE trade_date = :d"),
                    {"d": trade_date},
                ).fetchone()
            return int(row[0] or 0) if row else 0
        except Exception:
            return 0

    def _amount_filled(self, trade_date: date) -> int:
        if not self.engine:
            return 0
        try:
            with self.engine.connect() as conn:
                row = conn.execute(
                    sa_text(
                        "SELECT COUNT(*) FROM sector_dc_daily "
                        "WHERE trade_date = :d AND amount IS NOT NULL AND amount > 0"
                    ),
                    {"d": trade_date},
                ).fetchone()
            return int(row[0] or 0) if row else 0
        except Exception:
            return 0

    def _needs_amount_refresh(self, trade_date: date) -> bool:
        index_n = self._count_on_date("sector_dc_index_daily", trade_date)
        if index_n <= 0:
            return True
        amount_n = self._amount_filled(trade_date)
        return amount_n < max(10, int(index_n * 0.3))

    def ensure_data(self, trade_date: Optional[str] = None) -> Dict[str, Any]:
        """缺 asof 日指数或成交额覆盖不足时补齐到上一交易日。"""
        from data_layer.collectors.datahubco.sectors import sector_fetcher

        sector_fetcher.ensure_tables()
        meta = sector_fetcher.resolve_asof(trade_date)
        asof: date = meta["asof_date"]
        latest = self._latest_trade_date()
        need = latest is None or latest < asof or self._needs_amount_refresh(asof)
        if not need:
            return {
                "synced": False,
                "asof": asof.isoformat(),
                "latest": latest.isoformat() if latest else None,
                "session": meta,
            }

        try:
            result = sector_fetcher.sync_day(
                trade_date=asof.strftime("%Y%m%d"),
                member_limit=0,
            )
            days = [
                {
                    "date": (result.get("session") or {}).get("asof") or asof.isoformat(),
                    "ok": result.get("ok"),
                    "source": result.get("source"),
                    "counts": result.get("counts"),
                    "errors": result.get("errors"),
                }
            ]
        except Exception as e:
            logger.warning("sector sync_day %s: %s", asof, e)
            days = [{"date": asof.isoformat(), "ok": False, "error": str(e)}]

        new_latest = self._latest_trade_date()
        return {
            "synced": True,
            "asof": asof.isoformat(),
            "latest": new_latest.isoformat() if new_latest else None,
            "session": meta,
            "days": days,
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
        latest = self._latest_trade_date()
        use_date = asof
        asof_n = self._count_on_date("sector_dc_index_daily", asof)
        if asof_n == 0 and latest:
            use_date = latest
            logger.warning(
                "板块 asof=%s 暂无指数数据，回退到库内 %s",
                asof.isoformat(),
                latest.isoformat(),
            )

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

        amt_ok = sum(1 for s in sectors if (s.get("amount") or 0) > 0)
        if sectors and amt_ok < max(1, int(len(sectors) * 0.5)):
            logger.warning(
                "板块成交额覆盖不足 %d/%d · %s",
                amt_ok,
                len(sectors),
                use_date.isoformat() if hasattr(use_date, "isoformat") else use_date,
            )

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
        """板块成分股 + 行情。

        现价/涨跌幅/成交额优先级：
        1) 与成分同一天的 stock_daily
        2) 东财成分快照（sector_dc_member.close/pct_chg/amount）
        3) 该股不晚于 asof 的最近一条 stock_daily
        库里名单不全或报价覆盖不足时，点击会从东财 clist 刷新。
        """
        ts_code = (ts_code or "").strip().upper()
        if not ts_code:
            return {"ok": False, "error": "缺少 ts_code", "members": []}

        from data_layer.collectors.datahubco.sectors import sector_fetcher

        sector_fetcher.ensure_tables()
        meta = sector_fetcher.resolve_asof(trade_date)
        asof: date = meta["asof_date"]
        latest = self._latest_trade_date()
        use_date = latest if latest and latest < asof else asof
        asof_ymd = asof.strftime("%Y%m%d")

        if not self.engine:
            return {"ok": False, "error": "数据库未连接", "members": []}

        cnt, quoted = self._member_coverage(ts_code, use_date)
        if not cnt:
            with self.engine.connect() as conn:
                latest_m = conn.execute(
                    sa_text("SELECT MAX(trade_date) FROM sector_dc_member WHERE ts_code=:c"),
                    {"c": ts_code},
                ).scalar()
            if latest_m:
                use_date = latest_m
                cnt, quoted = self._member_coverage(ts_code, use_date)

        # 名单为空才拉远端。已有名单时用最近日线回填现价，避免点击被东财超时拖死。
        need_refresh = auto_fetch and cnt == 0
        if need_refresh:
            refreshed = False
            try:
                em = sector_fetcher.fetch_eastmoney_members(ts_code, asof_ymd)
                if em is not None and not em.empty:
                    sector_fetcher.upsert_members(em)
                    use_date = asof
                    refreshed = True
            except Exception as e:
                logger.warning("东财刷新成分股失败 %s: %s", ts_code, e)
            if not refreshed:
                try:
                    mdf = sector_fetcher.fetch_members(ts_code, asof_ymd)
                    if mdf is not None and not mdf.empty:
                        sector_fetcher.upsert_members(mdf)
                        use_date = asof
                except Exception as e:
                    logger.warning("拉取成分股失败 %s: %s", ts_code, e)
            cnt, quoted = self._member_coverage(ts_code, use_date)
            if not cnt:
                with self.engine.connect() as conn:
                    latest_m = conn.execute(
                        sa_text(
                            "SELECT MAX(trade_date) FROM sector_dc_member WHERE ts_code=:c"
                        ),
                        {"c": ts_code},
                    ).scalar()
                if latest_m:
                    use_date = latest_m

        sql = sa_text(
            """
            SELECT
                m.con_code AS ts_code,
                COALESCE(b.name, m.name) AS name,
                COALESCE(d.close, m.close, ld.close) AS close,
                COALESCE(d.open, ld.open) AS open,
                COALESCE(d.high, ld.high) AS high,
                COALESCE(d.low, ld.low) AS low,
                COALESCE(d.pre_close, ld.pre_close) AS pre_close,
                COALESCE(d.change, ld.change) AS change,
                COALESCE(d.pct_chg, m.pct_chg, ld.pct_chg) AS pct_chg,
                COALESCE(d.vol, ld.vol) AS vol,
                COALESCE(d.amount, m.amount, ld.amount) AS amount,
                COALESCE(d.trade_date, ld.trade_date) AS quote_date
            FROM sector_dc_member m
            LEFT JOIN ref_stock_basic b ON b.ts_code = m.con_code
            LEFT JOIN stock_daily d
              ON d.ts_code = m.con_code AND d.trade_date = m.trade_date
            LEFT JOIN LATERAL (
                SELECT close, open, high, low, pre_close, change, pct_chg, vol, amount, trade_date
                FROM stock_daily
                WHERE ts_code = m.con_code AND trade_date <= :d
                ORDER BY trade_date DESC
                LIMIT 1
            ) ld ON TRUE
            WHERE m.ts_code = :c AND m.trade_date = :d
            ORDER BY COALESCE(d.pct_chg, m.pct_chg, ld.pct_chg) DESC NULLS LAST, m.con_code
            """
        )
        try:
            df = pd.read_sql(sql, self.engine, params={"c": ts_code, "d": use_date})
        except Exception as e:
            return {"ok": False, "error": str(e), "members": [], "ts_code": ts_code}

        sector_name = ts_code
        sector_meta: Dict[str, Any] = {}
        try:
            with self.engine.connect() as conn:
                row = conn.execute(
                    sa_text(
                        "SELECT name, pct_change, leading_name, leading_code FROM sector_dc_index_daily "
                        "WHERE ts_code=:c AND trade_date=:d LIMIT 1"
                    ),
                    {"c": ts_code, "d": use_date},
                ).fetchone()
                if not row:
                    row = conn.execute(
                        sa_text(
                            "SELECT name, pct_change, leading_name, leading_code FROM sector_dc_index_daily "
                            "WHERE ts_code=:c ORDER BY trade_date DESC LIMIT 1"
                        ),
                        {"c": ts_code},
                    ).fetchone()
                if row:
                    sector_name = row[0] or ts_code
                    sector_meta = {
                        "name": row[0],
                        "pct_change": _ser(row[1]),
                        "leading": row[2],
                        "leading_code": row[3],
                    }
        except Exception:
            sector_meta = {}

        members = []
        quoted_out = 0
        for _, r in df.iterrows():
            close = _ser(r.get("close"))
            pct_chg = _ser(r.get("pct_chg"))
            amount = _ser(r.get("amount"))
            if close is not None or pct_chg is not None or amount is not None:
                quoted_out += 1
            members.append({
                "ts_code": r["ts_code"],
                "name": r.get("name"),
                "close": close,
                "open": _ser(r.get("open")),
                "high": _ser(r.get("high")),
                "low": _ser(r.get("low")),
                "pre_close": _ser(r.get("pre_close")),
                "change": _ser(r.get("change")),
                "pct_chg": pct_chg,
                "vol": _ser(r.get("vol")),
                "amount": amount,
                "quote_date": _ser(r.get("quote_date")),
            })

        return {
            "ok": True,
            "ts_code": ts_code,
            "name": sector_name,
            "trade_date": use_date.isoformat() if hasattr(use_date, "isoformat") else str(use_date),
            "sector": sector_meta,
            "members": members,
            "count": len(members),
            "quoted_count": quoted_out,
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

    def sync_all_members(self, trade_date: Optional[str] = None) -> Dict[str, Any]:
        from data_layer.collectors.datahubco.sectors import sector_fetcher

        sector_fetcher.ensure_tables()
        return sector_fetcher.sync_all_members(trade_date=trade_date)

    async def sync_all_members_async(self, **kwargs) -> Dict[str, Any]:
        return await asyncio.to_thread(self.sync_all_members, **kwargs)

    def backfill_klines(self, lookback_days: int = 500) -> Dict[str, Any]:
        from data_layer.collectors.datahubco.sectors import sector_fetcher

        sector_fetcher.ensure_tables()
        return sector_fetcher.backfill_all_industry_klines(lookback_days=lookback_days)

    async def backfill_klines_async(self, **kwargs) -> Dict[str, Any]:
        return await asyncio.to_thread(self.backfill_klines, **kwargs)


sector_service = SectorService()

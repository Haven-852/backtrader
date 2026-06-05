"""
Market Data Service - 市场数据全量查询服务
直接使用 data_layer.storage.pg_manager 从 PostgreSQL 查询全部 30 张表

数据分类：
  1. 基础参照 (ref_*): 6 tables
  2. 财务报表 (fin_*): 6 tables
  3. 日/周/月K线: 4 tables
  4. 盘前/复权/竞价: 3 tables
  5. 资金/两融/大宗/龙虎: 5 tables
  6. 指数/权重: 2 tables
  7. 基金/ETF: 3 tables
  8. 研报一致预期: 1 table
"""

import logging
from typing import Any, Optional
from datetime import datetime

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

# ─── K线表映射 ─────────────────────────────────────────

KLINE_TABLES = {
    "daily": "stock_daily",
    "weekly": "stock_weekly",
    "monthly": "stock_monthly",
}

# ─── 30张表的分类定义 ─────────────────────────────────

TABLE_CATEGORIES = {
    "基础参照": {
        "icon": "book",
        "tables": ["ref_trade_cal", "ref_stock_basic", "ref_stock_namechange",
                    "ref_new_share", "ref_stk_limit_daily", "ref_suspend_d"],
    },
    "财务报表": {
        "icon": "file-invoice",
        "tables": ["fin_income", "fin_balancesheet", "fin_cashflow",
                    "fin_fina_indicator", "fin_forecast", "fin_dividend"],
    },
    "K线行情": {
        "icon": "chart-line",
        "tables": ["stock_daily", "stock_weekly", "stock_monthly", "bars_daily"],
    },
    "盘前&复权&竞价": {
        "icon": "clock",
        "tables": ["stock_daily_basic", "stock_adj_factor", "stock_auction_daily"],
    },
    "资金&两融&大宗": {
        "icon": "money-bill-wave",
        "tables": ["stock_moneyflow_daily", "stock_top_list_daily",
                    "margin_summary_daily", "margin_detail_daily", "stock_block_trade"],
    },
    "指数&权重": {
        "icon": "chart-bar",
        "tables": ["index_daily", "index_weight"],
    },
    "基金&ETF": {
        "icon": "piggy-bank",
        "tables": ["fund_daily", "fund_nav", "etf_daily"],
    },
    "研报一致预期": {
        "icon": "file-alt",
        "tables": ["broker_report_consensus"],
    },
}

# 核心指数代码
CORE_INDEX_CODES = {
    "000001.SH": "上证指数",
    "399001.SZ": "深证成指",
    "000300.SH": "沪深300",
    "399006.SZ": "创业板指",
    "000688.SH": "科创50",
}


class MarketDataService:
    """市场数据服务 —— 直接查询 PostgreSQL"""

    def __init__(self):
        self._pg = None

    @property
    def pg(self):
        if self._pg is None:
            from data_layer.storage.manager import pg_manager
            self._pg = pg_manager
            try:
                self._pg._check_initialized()
            except RuntimeError:
                pass
        return self._pg

    # ─── 综合摘要 ───────────────────────────────────────

    async def get_summary(self) -> dict:
        """获取全部 30 张表的统计摘要"""
        categories = []
        all_ok = True
        errors = []

        try:
            self.pg._check_initialized()
        except RuntimeError as e:
            return {
                "categories": [],
                "total_tables": 30,
                "total_rows": 0,
                "db_connected": False,
                "error": str(e),
            }

        for cat_name, cat_info in TABLE_CATEGORIES.items():
            cat_tables = []
            cat_rows = 0
            for table_name in cat_info["tables"]:
                try:
                    cnt = await self.pg.count(table_name)
                    cat_tables.append({
                        "name": table_name,
                        "rows": cnt,
                        "has_data": cnt > 0,
                    })
                    cat_rows += cnt
                except Exception as e:
                    cat_tables.append({
                        "name": table_name,
                        "rows": -1,
                        "has_data": False,
                        "error": str(e),
                    })
                    errors.append(f"{table_name}: {e}")
                    all_ok = False

            categories.append({
                "category": cat_name,
                "icon": cat_info["icon"],
                "tables": cat_tables,
                "total_rows": cat_rows,
                "table_count": len(cat_tables),
            })

        total_rows = sum(c["total_rows"] for c in categories)
        return {
            "categories": categories,
            "total_tables": 30,
            "total_rows": total_rows,
            "db_connected": all_ok,
            "errors": errors if errors else None,
        }

    # ─── K线数据 (核心) ─────────────────────────────────

    async def get_kline(
        self,
        ts_code: str,
        freq: str = "daily",
        start: Optional[str] = None,
        end: Optional[str] = None,
        limit: int = 500,
    ) -> dict:
        """
        从 PostgreSQL stock_daily/stock_weekly/stock_monthly 直接查询K线

        Args:
            ts_code: 股票代码 (如 000001.SZ)
            freq: 频率 daily|weekly|monthly
            start: 起始日期
            end: 结束日期
            limit: 最大条数
        """
        table = KLINE_TABLES.get(freq)
        if not table:
            return {"error": f"不支持的K线频率: {freq}，可选: {list(KLINE_TABLES.keys())}"}

        try:
            df = await self.pg.query(
                table=table,
                ts_code=ts_code,
                start_date=start,
                end_date=end,
                limit=limit,
                order_by="trade_date",
                order_desc=True,
            )

            if df is None or df.empty:
                return {
                    "ts_code": ts_code,
                    "freq": freq,
                    "table": table,
                    "bars": [],
                    "count": 0,
                    "message": f"表 {table} 中无 {ts_code} 的数据",
                }

            # 按时间升序
            df = df.sort_values("trade_date")

            # 格式化 bars
            bars = self._format_kline_bars(df)

            # 计算简单统计
            stats = self._calc_bars_stats(df)

            return {
                "ts_code": ts_code,
                "freq": freq,
                "table": table,
                "bars": bars,
                "count": len(bars),
                "stats": stats,
            }

        except Exception as e:
            logger.error(f"查询K线失败 ({ts_code}, {freq}): {e}")
            return {"error": str(e), "bars": [], "count": 0}

    async def get_index_kline(
        self,
        ts_code: str = "000001.SH",
        start: Optional[str] = None,
        end: Optional[str] = None,
        limit: int = 500,
    ) -> dict:
        """从 index_daily 表查询指数日线"""
        try:
            df = await self.pg.query(
                table="index_daily",
                ts_code=ts_code,
                start_date=start,
                end_date=end,
                limit=limit,
                order_by="trade_date",
                order_desc=True,
            )

            if df is None or df.empty:
                return {
                    "ts_code": ts_code,
                    "bars": [],
                    "count": 0,
                    "name": CORE_INDEX_CODES.get(ts_code, ts_code),
                }

            df = df.sort_values("trade_date")
            bars = self._format_kline_bars(df)
            stats = self._calc_bars_stats(df)

            return {
                "ts_code": ts_code,
                "name": CORE_INDEX_CODES.get(ts_code, ts_code),
                "bars": bars,
                "count": len(bars),
                "stats": stats,
            }

        except Exception as e:
            logger.error(f"查询指数K线失败 ({ts_code}): {e}")
            return {"error": str(e), "bars": [], "count": 0}

    # ─── 索引快照 ───────────────────────────────────────

    async def get_indices_snapshot(self) -> dict:
        """获取核心指数最新快照"""
        codes = list(CORE_INDEX_CODES.keys())
        results = []

        for code in codes:
            try:
                df = await self.pg.query(
                    table="index_daily",
                    ts_code=code,
                    limit=1,
                    order_by="trade_date",
                    order_desc=True,
                )
                if df is not None and not df.empty:
                    row = df.iloc[0]
                    results.append({
                        "ts_code": code,
                        "name": CORE_INDEX_CODES[code],
                        "trade_date": str(row.get("trade_date", ""))[:10],
                        "close": float(row.get("close", 0) or 0),
                        "open": float(row.get("open", 0) or 0),
                        "high": float(row.get("high", 0) or 0),
                        "low": float(row.get("low", 0) or 0),
                        "pre_close": float(row.get("pre_close", 0) or 0),
                        "change": float(row.get("change", 0) or 0),
                        "pct_chg": float(row.get("pct_chg", 0) or 0),
                        "vol": int(row.get("vol", 0) or 0),
                        "amount": float(row.get("amount", 0) or 0),
                    })
                else:
                    results.append({
                        "ts_code": code,
                        "name": CORE_INDEX_CODES[code],
                        "trade_date": "",
                        "close": 0,
                        "open": 0, "high": 0, "low": 0, "pre_close": 0,
                        "change": 0, "pct_chg": 0, "vol": 0, "amount": 0,
                    })
            except Exception as e:
                logger.warning(f"查询指数 {code} 失败: {e}")
                results.append({
                    "ts_code": code,
                    "name": CORE_INDEX_CODES[code],
                    "error": str(e),
                })

        return {"indices": results, "total": len(results)}

    # ─── 任意表数据 ─────────────────────────────────────

    async def get_table_data(
        self,
        table_name: str,
        limit: int = 50,
        offset: int = 0,
        ts_code: Optional[str] = None,
    ) -> dict:
        """获取任意表的预览数据"""
        try:
            # 先获取行数
            total_rows = await self.pg.count(table_name)

            # 查询数据
            kwargs = {"table": table_name, "limit": limit, "offset": offset,
                       "order_by": "trade_date", "order_desc": True}
            if ts_code:
                kwargs["ts_code"] = ts_code

            df = await self.pg.query(**kwargs)

            columns = df.columns.tolist() if not df.empty else []
            rows = self._df_to_records(df)

            # 获取表的模型列定义
            from data_layer.storage.models import TABLE_MODEL_MAP
            model = TABLE_MODEL_MAP.get(table_name)
            column_defs = []
            if model and hasattr(model, '_cols'):
                column_defs = model._cols

            return {
                "table_name": table_name,
                "columns": column_defs,
                "rows": rows,
                "total_rows": total_rows,
                "preview_count": len(rows),
                "offset": offset,
                "limit": limit,
                "has_more": offset + limit < total_rows,
            }

        except ValueError as e:
            return {"error": str(e), "table_name": table_name}
        except Exception as e:
            logger.error(f"查询表 {table_name} 失败: {e}")
            return {"error": str(e), "table_name": table_name}

    # ─── 财务数据 ───────────────────────────────────────

    async def get_financials(self, ts_code: str) -> dict:
        """获取某只股票的财务报表数据"""
        fin_tables = {
            "income": "fin_income",
            "balance": "fin_balancesheet",
            "cashflow": "fin_cashflow",
            "indicator": "fin_fina_indicator",
            "forecast": "fin_forecast",
            "dividend": "fin_dividend",
        }

        result = {"ts_code": ts_code, "reports": {}}

        for name, table in fin_tables.items():
            try:
                df = await self.pg.query(
                    table=table,
                    ts_code=ts_code,
                    limit=20,
                    order_by="end_date",
                    order_desc=True,
                )
                if df is not None and not df.empty:
                    result["reports"][name] = {
                        "table": table,
                        "count": len(df),
                        "data": self._df_to_records(df),
                    }
                else:
                    result["reports"][name] = {
                        "table": table,
                        "count": 0,
                        "data": [],
                    }
            except Exception as e:
                result["reports"][name] = {
                    "table": table,
                    "count": 0,
                    "error": str(e),
                }

        return result

    # ─── 资金流向 ───────────────────────────────────────

    async def get_moneyflow(self, ts_code: str, limit: int = 100) -> dict:
        """获取某只股票的资金流向数据"""
        try:
            df = await self.pg.query(
                table="stock_moneyflow_daily",
                ts_code=ts_code,
                limit=limit,
                order_by="trade_date",
                order_desc=True,
            )

            if df is None or df.empty:
                return {"ts_code": ts_code, "data": [], "count": 0}

            df = df.sort_values("trade_date")
            return {
                "ts_code": ts_code,
                "data": self._df_to_records(df),
                "count": len(df),
            }
        except Exception as e:
            logger.error(f"查询资金流向失败 ({ts_code}): {e}")
            return {"error": str(e), "ts_code": ts_code}

    # ─── 融资融券 ───────────────────────────────────────

    async def get_margin_summary(self, limit: int = 30) -> dict:
        """获取融资融券汇总数据"""
        try:
            df = await self.pg.query(
                table="margin_summary_daily",
                limit=limit,
                order_by="trade_date",
                order_desc=True,
            )

            if df is None or df.empty:
                return {"data": [], "count": 0}

            df = df.sort_values("trade_date")
            return {
                "data": self._df_to_records(df),
                "count": len(df),
            }
        except Exception as e:
            logger.error(f"查询两融汇总失败: {e}")
            return {"error": str(e)}

    # ─── 研报一致预期 ───────────────────────────────────

    async def get_consensus(self, ts_code: str, limit: int = 50) -> dict:
        """获取某只股票的券商一致预期"""
        try:
            df = await self.pg.query(
                table="broker_report_consensus",
                ts_code=ts_code,
                limit=limit,
                order_by="calc_date",
                order_desc=True,
            )

            if df is None or df.empty:
                return {"ts_code": ts_code, "data": [], "count": 0}

            df = df.sort_values("calc_date")
            return {
                "ts_code": ts_code,
                "data": self._df_to_records(df),
                "count": len(df),
            }
        except Exception as e:
            logger.error(f"查询一致预期失败 ({ts_code}): {e}")
            return {"error": str(e)}

    # ─── 行业统计 (来自 stock_daily_basic) ──────────────

    async def get_industry_stats(self) -> dict:
        """获取行业统计 - 基于 stock_daily_basic 和 ref_stock_basic"""
        try:
            # 获取最近交易日期的每家股票的基本面数据
            sql = """
            SELECT b.ts_code, b.name, b.industry, b.market,
                   d.close, d.pe, d.pe_ttm, d.pb, d.total_mv, d.circ_mv,
                   d.turnover_rate, d.turnover_rate_f, d.volume_ratio
            FROM (
                SELECT DISTINCT ON (ts_code) ts_code, trade_date, close, pe, pe_ttm, pb,
                       total_mv, circ_mv, turnover_rate, turnover_rate_f, volume_ratio
                FROM stock_daily_basic
                ORDER BY ts_code, trade_date DESC
            ) d
            JOIN ref_stock_basic b ON b.ts_code = d.ts_code
            WHERE b.industry IS NOT NULL AND b.industry != ''
            ORDER BY d.total_mv DESC NULLS LAST
            LIMIT 200
            """
            df = await self.pg.query_sql(sql)

            if df is None or df.empty:
                return {"industries": [], "stocks": [], "count": 0}

            # 按行业聚合
            industry_groups = df.groupby("industry").agg(
                stock_count=("ts_code", "count"),
                total_mv=("total_mv", "sum"),
                avg_pe=("pe_ttm", "mean"),
                avg_pb=("pb", "mean"),
            ).reset_index()

            # 格式化
            industries = []
            for _, row in industry_groups.iterrows():
                industries.append({
                    "industry": row["industry"],
                    "stock_count": int(row["stock_count"]),
                    "total_mv": float(row["total_mv"]) if pd.notna(row["total_mv"]) else 0,
                    "avg_pe": round(float(row["avg_pe"]), 2) if pd.notna(row["avg_pe"]) else None,
                    "avg_pb": round(float(row["avg_pb"]), 2) if pd.notna(row["avg_pb"]) else None,
                })

            stocks = self._df_to_records(df)

            return {
                "industries": sorted(industries, key=lambda x: x["total_mv"], reverse=True),
                "stocks": stocks,
                "count": len(stocks),
                "industry_count": len(industries),
            }
        except Exception as e:
            logger.error(f"查询行业统计失败: {e}")
            return {"error": str(e), "industries": [], "stocks": []}

    # ─── 股票搜索 (从 ref_stock_basic) ──────────────────

    async def search_stocks(self, keyword: str, limit: int = 20) -> list:
        """从 ref_stock_basic 搜索股票"""
        try:
            sql = """
            SELECT ts_code, symbol, name, area, industry, market, list_status, list_date
            FROM ref_stock_basic
            WHERE (ts_code ILIKE :kw OR name ILIKE :kw OR symbol ILIKE :kw)
              AND list_status = 'L'
            ORDER BY ts_code
            LIMIT :limit
            """
            df = await self.pg.query_sql(sql, {"kw": f"%{keyword}%", "limit": limit})

            if df is None or df.empty:
                return []

            return self._df_to_records(df)
        except Exception as e:
            logger.warning(f"搜索股票失败: {e}")
            return []

    # ─── 辅助方法 ───────────────────────────────────────

    def _format_kline_bars(self, df: pd.DataFrame) -> list:
        """将K线 DataFrame 格式化为 JSON 序列化列表"""
        bars = []
        for _, row in df.iterrows():
            bar = {}
            # 时间字段
            for time_col in ["trade_date", "time"]:
                if time_col in df.columns:
                    val = row[time_col]
                    if hasattr(val, "isoformat"):
                        bar["time"] = val.isoformat()[:10]
                    else:
                        bar["time"] = str(val)[:10]
                    break

            if "time" not in bar:
                bar["time"] = ""

            # OHLCV
            for col in ["open", "high", "low", "close", "pre_close", "change", "pct_chg"]:
                val = row.get(col)
                if val is not None and not (isinstance(val, float) and np.isnan(val)):
                    bar[col] = round(float(val), 4) if not isinstance(val, (int, str)) else val
                else:
                    bar[col] = None

            for col in ["vol"]:
                val = row.get(col)
                if val is not None and not (isinstance(val, float) and np.isnan(val)):
                    bar[col] = int(val)
                else:
                    bar[col] = None

            for col in ["amount"]:
                val = row.get(col)
                if val is not None and not (isinstance(val, float) and np.isnan(val)):
                    bar[col] = round(float(val), 2)
                else:
                    bar[col] = None

            bars.append(bar)
        return bars

    def _calc_bars_stats(self, df: pd.DataFrame) -> dict:
        """计算K线统计信息"""
        if df.empty:
            return {}

        close = df["close"].astype(float)
        high = df["high"].astype(float) if "high" in df.columns else close
        low = df["low"].astype(float) if "low" in df.columns else close
        vol = df["vol"].astype(float) if "vol" in df.columns else pd.Series(dtype=float)

        first_close = close.iloc[0] if len(close) > 0 else 0
        last_close = close.iloc[-1] if len(close) > 0 else 0

        change_pct = 0
        if first_close > 0:
            change_pct = ((last_close - first_close) / first_close) * 100

        return {
            "period_start": str(df["trade_date"].iloc[0])[:10] if "trade_date" in df.columns else "",
            "period_end": str(df["trade_date"].iloc[-1])[:10] if "trade_date" in df.columns else "",
            "first_close": round(float(first_close), 2),
            "last_close": round(float(last_close), 2),
            "change_pct": round(change_pct, 2),
            "high_max": round(float(high.max()), 2) if len(high) > 0 else None,
            "low_min": round(float(low.min()), 2) if len(low) > 0 else None,
            "vol_total": int(vol.sum()) if len(vol) > 0 else 0,
            "bar_count": len(df),
        }

    def _df_to_records(self, df: pd.DataFrame) -> list:
        """DataFrame 转 records，处理日期和数字类型"""
        if df is None or df.empty:
            return []
        records = []
        for _, row in df.iterrows():
            record = {}
            for col in df.columns:
                val = row[col]
                if hasattr(val, "isoformat"):
                    record[col] = val.isoformat()[:10] if hasattr(val, "date") else val.isoformat()
                elif isinstance(val, (np.integer,)):
                    record[col] = int(val)
                elif isinstance(val, (np.floating,)):
                    if np.isnan(val):
                        record[col] = None
                    else:
                        record[col] = round(float(val), 4)
                elif pd.isna(val) if not isinstance(val, (str, int, float)) else False:
                    record[col] = None
                else:
                    record[col] = val
            records.append(record)
        return records


# 全局实例
market_data_service = MarketDataService()

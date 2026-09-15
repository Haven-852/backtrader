"""
Dash Service - 股票行情看板服务层
负责：K线数据查询（日/周/月/分钟线）、技术指标计算、股票搜索、ETF行情

数据来源：
  - TimescaleDB: stock_daily (日K), stock_weekly (周K), stock_monthly (月K)
  - TimescaleDB: etf_daily (ETF日K), index_daily (指数日K)
  - InfluxDB: stock_bar_mins (分钟K线: 1m/5m/15m/30m/60m)
  - PostgreSQL: ref_stock_basic (股票主档), ref_etf_basic (ETF主档)
"""

import sys
import os
import logging
import asyncio
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta

import pandas as pd
import numpy as np
from sqlalchemy import text as sa_text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger(__name__)

# ─── 技术指标计算工具 ──────────────────────────────────


def calc_ma(series: pd.Series, period: int) -> pd.Series:
    """简单移动平均线"""
    return series.rolling(window=period).mean()


def calc_ema(series: pd.Series, period: int) -> pd.Series:
    """指数移动平均线"""
    return series.ewm(span=period, adjust=False).mean()


def calc_macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> Dict[str, pd.Series]:
    """MACD 指标"""
    ema_fast = calc_ema(close, fast)
    ema_slow = calc_ema(close, slow)
    dif = ema_fast - ema_slow
    dea = calc_ema(dif, signal)
    macd_hist = 2 * (dif - dea)
    return {"DIF": dif, "DEA": dea, "MACD": macd_hist}


def calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """RSI 相对强弱指标"""
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def calc_bollinger(close: pd.Series, period: int = 20, std_dev: float = 2.0) -> Dict[str, pd.Series]:
    """布林带"""
    mid = calc_ma(close, period)
    std = close.rolling(window=period).std()
    upper = mid + std_dev * std
    lower = mid - std_dev * std
    return {"BOLL_MID": mid, "BOLL_UPPER": upper, "BOLL_LOWER": lower}


def calc_kdj(high: pd.Series, low: pd.Series, close: pd.Series,
             n: int = 9, m1: int = 3, m2: int = 3) -> Dict[str, pd.Series]:
    """KDJ 随机指标"""
    lowest_low = low.rolling(window=n).min()
    highest_high = high.rolling(window=n).max()
    rsv = (close - lowest_low) / (highest_high - lowest_low).replace(0, np.nan) * 100
    k = rsv.ewm(alpha=1 / m1, adjust=False).mean()
    d = k.ewm(alpha=1 / m2, adjust=False).mean()
    j = 3 * k - 2 * d
    return {"K": k, "D": d, "J": j}


def calc_volume_ma(vol: pd.Series, periods: List[int] = [5, 10, 20]) -> Dict[str, pd.Series]:
    """成交量均线"""
    return {f"VOL_MA{p}": calc_ma(vol, p) for p in periods}


# ─── Dash Service ──────────────────────────────────────


class DashService:
    """股票行情看板服务"""

    # 支持的周期及对应的数据源
    TIMEFRAME_MAP = {
        "1m":   {"table": "stock_bar_mins", "freq": "1min",  "source": "influx"},
        "5m":   {"table": "stock_bar_mins", "freq": "5min",  "source": "influx"},
        "15m":  {"table": "stock_bar_mins", "freq": "15min", "source": "influx"},
        "30m":  {"table": "stock_bar_mins", "freq": "30min", "source": "influx"},
        "60m":  {"table": "stock_bar_mins", "freq": "60min", "source": "influx"},
        "1h":   {"table": "stock_bar_mins", "freq": "60min", "source": "influx"},
        "day":  {"table": "stock_daily",   "freq": "1day",   "source": "timescale"},
        "1d":   {"table": "stock_daily",   "freq": "1day",   "source": "timescale"},
        "week": {"table": "stock_weekly",  "freq": "1week",  "source": "timescale"},
        "1w":   {"table": "stock_weekly",  "freq": "1week",  "source": "timescale"},
        "month":{"table": "stock_monthly", "freq": "1month", "source": "timescale"},
        "1M":   {"table": "stock_monthly", "freq": "1month", "source": "timescale"},
    }

    # 可用的技术指标
    AVAILABLE_INDICATORS = [
        {"id": "ma",       "name": "移动均线 MA",    "params": {"periods": [5, 10, 20, 30]}},
        {"id": "ema",      "name": "指数均线 EMA",   "params": {"periods": [12, 26]}},
        {"id": "macd",     "name": "MACD",           "params": {"fast": 12, "slow": 26, "signal": 9}},
        {"id": "rsi",      "name": "RSI 相对强弱",   "params": {"period": 14}},
        {"id": "boll",     "name": "布林带 BOLL",    "params": {"period": 20, "std_dev": 2.0}},
        {"id": "kdj",      "name": "KDJ 随机指标",   "params": {"n": 9, "m1": 3, "m2": 3}},
        {"id": "volume",   "name": "成交量均线",     "params": {"periods": [5, 10, 20]}},
    ]

    def __init__(self):
        self._storage = None
        self._pg = None

    @property
    def pg(self):
        """PostgreSQL 异步管理器（data_layer.storage）"""
        if self._pg is None:
            from data_layer.storage import pg_manager
            self._pg = pg_manager
        return self._pg

    async def _ensure_pg(self) -> bool:
        try:
            if not self.pg._initialized:
                self.pg.initialize()
            self.pg._check_initialized()
            return True
        except Exception as e:
            logger.warning(f"pg_manager 不可用，将使用 storage 回退: {e}")
            return False

    @property
    def storage(self):
        if self._storage is None:
            try:
                from data_layer.db_manager import storage_manager
                self._storage = storage_manager
            except Exception as e:
                logger.warning(f"无法加载 StorageManager: {e}")
                return None
        return self._storage

    def get_available_timeframes(self) -> List[Dict]:
        """获取可用周期列表"""
        return [
            {"id": "intraday", "name": "分时", "group": "分时"},
            {"id": "day",   "name": "日线", "group": "日线"},
            {"id": "week",  "name": "周线", "group": "周线"},
            {"id": "month", "name": "月线", "group": "月线"},
        ]

    def get_available_indicators(self) -> List[Dict]:
        """获取可用技术指标列表"""
        return self.AVAILABLE_INDICATORS

    async def search_stocks(self, keyword: str, limit: int = 20) -> List[Dict]:
        """搜索股票（ref_stock_basic）；asyncpg 不可用时回退 SQLAlchemy。"""
        if await self._ensure_pg():
            try:
                sql = """
                SELECT ts_code, symbol, name, area, industry, list_date
                FROM ref_stock_basic
                WHERE (ts_code ILIKE :kw OR name ILIKE :kw OR symbol ILIKE :kw)
                ORDER BY ts_code
                LIMIT :limit
                """
                df = await self.pg.query_sql(sql, {"kw": f"%{keyword}%", "limit": limit})
                if df is not None and not df.empty:
                    return df.to_dict("records")
            except Exception as e:
                logger.warning(f"pg 搜索股票失败，尝试 storage 回退: {e}")

        engine = self.storage.get_postgres_engine() if self.storage else None
        if engine:
            try:
                sql = """
                SELECT ts_code, symbol, name, area, industry, list_date
                FROM ref_stock_basic
                WHERE (ts_code ILIKE :kw OR name ILIKE :kw OR symbol ILIKE :kw)
                ORDER BY ts_code LIMIT :limit
                """
                df = pd.read_sql(
                    sa_text(sql), engine, params={"kw": f"%{keyword}%", "limit": limit}
                )
                return df.to_dict("records") if not df.empty else []
            except Exception as e:
                logger.warning(f"storage 搜索股票失败: {e}")
        return self._mock_stock_search(keyword)

    async def get_stock_info(self, ts_code: str) -> Optional[Dict]:
        """获取股票基本信息；asyncpg 不可用时回退 SQLAlchemy。"""
        if await self._ensure_pg():
            try:
                df = await self.pg.query_sql(
                    "SELECT ts_code, symbol, name, area, industry, list_date "
                    "FROM ref_stock_basic WHERE ts_code = :code LIMIT 1",
                    {"code": ts_code},
                )
                if df is not None and not df.empty:
                    return df.iloc[0].to_dict()
            except Exception as e:
                logger.warning(f"获取股票信息失败 ({ts_code}): {e}")

        engine = self.storage.get_postgres_engine() if self.storage else None
        if not engine:
            return None
        try:
            df = pd.read_sql(
                sa_text(
                    "SELECT ts_code, symbol, name, area, industry, list_date "
                    "FROM ref_stock_basic WHERE ts_code = :code LIMIT 1"
                ),
                engine,
                params={"code": ts_code},
            )
            if df.empty:
                return None
            return df.iloc[0].to_dict()
        except Exception as e:
            logger.warning(f"storage 获取股票信息失败 ({ts_code}): {e}")
            return None

    async def get_etf_info(self, ts_code: str) -> Optional[Dict]:
        """获取ETF基本信息"""
        # 先从白名单匹配
        for etf in self.POPULAR_ETFS:
            if etf["ts_code"] == ts_code:
                return {"ts_code": ts_code, "name": etf["name"], "fund_type": etf.get("fund_type", ""), "symbol": ts_code.split(".")[0]}

        if not self.storage:
            return None
        engine = self.storage.get_postgres_engine()
        if not engine:
            return None
        try:
            sql = """
            SELECT ts_code, name, management, fund_type, bench_index, list_date, list_status
            FROM ref_etf_basic WHERE ts_code = :code LIMIT 1
            """
            df = pd.read_sql(sa_text(sql), engine, params={"code": ts_code})
            if df.empty:
                return None
            return df.iloc[0].to_dict()
        except Exception as e:
            logger.warning(f"获取ETF信息失败 ({ts_code}): {e}")
            return None

    # ─── 指数大盘数据 ──────────────────────────────────

    # 同花顺风格核心指数定义
    CORE_INDICES = [
        {"ts_code": "000001.SH", "name": "上证指数", "short_name": "上证"},
        {"ts_code": "399001.SZ", "name": "深证成指", "short_name": "深证"},
        {"ts_code": "000300.SH", "name": "沪深300", "short_name": "沪深300"},
        {"ts_code": "399006.SZ", "name": "创业板指", "short_name": "创业板"},
        {"ts_code": "000688.SH", "name": "科创50", "short_name": "科创50"},
        {"ts_code": "000905.SH", "name": "中证500", "short_name": "中证500"},
        {"ts_code": "000016.SH", "name": "上证50", "short_name": "上证50"},
        {"ts_code": "000852.SH", "name": "中证1000", "short_name": "中证1000"},
        {"ts_code": "399005.SZ", "name": "中小100", "short_name": "中小100"},
        {"ts_code": "399673.SZ", "name": "创业板50", "short_name": "创50"},
    ]

    async def get_index_snapshots(self, auto_sync: bool = True) -> List[Dict]:
        """获取核心指数最新快照（优先 SQLAlchemy 读 index_daily；可自动持续补数）"""
        sync_info = None
        if auto_sync:
            try:
                from data_layer.collectors.datahubco import continuous_fetcher
                sync_info = await asyncio.to_thread(continuous_fetcher.ensure_indices_fresh)
            except Exception as e:
                logger.warning(f"指数持续同步跳过: {e}")
                sync_info = {"ok": False, "error": str(e)}

        # 1) SQLAlchemy（稳定，不依赖 asyncpg）
        engine = self.storage.get_postgres_engine() if self.storage else None
        if engine:
            try:
                codes = [i["ts_code"] for i in self.CORE_INDICES]
                sql = sa_text(
                    """
                    SELECT DISTINCT ON (ts_code)
                           ts_code, trade_date, close, open, high, low,
                           pre_close, change, pct_chg, vol, amount
                    FROM index_daily
                    WHERE ts_code IN :codes
                    ORDER BY ts_code, trade_date DESC
                    """
                )
                df = await asyncio.to_thread(
                    pd.read_sql, sql, engine, params={"codes": tuple(codes)}
                )
                by_code = {}
                if df is not None and not df.empty:
                    for _, row in df.iterrows():
                        by_code[str(row["ts_code"])] = row

                results = []
                for idx_info in self.CORE_INDICES:
                    code = idx_info["ts_code"]
                    row = by_code.get(code)
                    if row is None:
                        results.append({
                            "ts_code": code,
                            "name": idx_info["name"],
                            "short_name": idx_info["short_name"],
                            "trade_date": "",
                            "close": 0,
                            "pre_close": 0,
                            "change": 0,
                            "pct_chg": 0,
                            "vol": 0,
                            "amount": 0,
                        })
                        continue
                    close_val = float(row["close"]) if pd.notna(row.get("close")) else 0
                    pre_close_val = float(row["pre_close"]) if pd.notna(row.get("pre_close")) else close_val
                    change_val = float(row["change"]) if pd.notna(row.get("change")) else 0
                    pct_val = float(row["pct_chg"]) if pd.notna(row.get("pct_chg")) else 0
                    results.append({
                        "ts_code": code,
                        "name": idx_info["name"],
                        "short_name": idx_info["short_name"],
                        "trade_date": str(row["trade_date"])[:10] if pd.notna(row.get("trade_date")) else "",
                        "close": round(close_val, 2),
                        "open": round(float(row["open"]), 2) if pd.notna(row.get("open")) else None,
                        "high": round(float(row["high"]), 2) if pd.notna(row.get("high")) else None,
                        "low": round(float(row["low"]), 2) if pd.notna(row.get("low")) else None,
                        "pre_close": round(pre_close_val, 2),
                        "change": round(change_val, 2),
                        "pct_chg": round(pct_val, 2),
                        "vol": int(row["vol"]) if pd.notna(row.get("vol")) else 0,
                        "amount": float(row["amount"]) if pd.notna(row.get("amount")) else 0,
                    })
                # 若多数有真实收盘价则返回
                if any((r.get("close") or 0) > 0 for r in results):
                    return results
            except Exception as e:
                logger.warning(f"SQLAlchemy 指数快照失败: {e}")

        # 2) asyncpg 回退
        if await self._ensure_pg():
            results = []
            try:
                for idx_info in self.CORE_INDICES:
                    code = idx_info["ts_code"]
                    df = await self.pg.query(
                        table="index_daily",
                        ts_code=code,
                        limit=1,
                        order_by="trade_date",
                        order_desc=True,
                    )
                    if df is None or df.empty:
                        results.append({
                            "ts_code": code,
                            "name": idx_info["name"],
                            "short_name": idx_info["short_name"],
                            "trade_date": "",
                            "close": 0,
                            "pre_close": 0,
                            "change": 0,
                            "pct_chg": 0,
                            "vol": 0,
                            "amount": 0,
                        })
                        continue

                    row = df.iloc[0]
                    close_val = float(row["close"]) if pd.notna(row.get("close")) else 0
                    pre_close_val = float(row["pre_close"]) if pd.notna(row.get("pre_close")) else close_val
                    change_val = float(row["change"]) if pd.notna(row.get("change")) else 0
                    pct_val = float(row["pct_chg"]) if pd.notna(row.get("pct_chg")) else 0
                    results.append({
                        "ts_code": code,
                        "name": idx_info["name"],
                        "short_name": idx_info["short_name"],
                        "trade_date": str(row["trade_date"])[:10] if pd.notna(row.get("trade_date")) else "",
                        "close": round(close_val, 2),
                        "open": round(float(row["open"]), 2) if pd.notna(row.get("open")) else None,
                        "high": round(float(row["high"]), 2) if pd.notna(row.get("high")) else None,
                        "low": round(float(row["low"]), 2) if pd.notna(row.get("low")) else None,
                        "pre_close": round(pre_close_val, 2),
                        "change": round(change_val, 2),
                        "pct_chg": round(pct_val, 2),
                        "vol": int(row["vol"]) if pd.notna(row.get("vol")) else 0,
                        "amount": float(row["amount"]) if pd.notna(row.get("amount")) else 0,
                    })
                if any((r.get("close") or 0) > 0 for r in results):
                    return results
            except Exception as e:
                logger.warning(f"获取指数快照失败: {e}")

        return self._mock_index_snapshots()

    def _mock_index_snapshots(self) -> List[Dict]:
        """模拟指数快照数据（数据库不可用时）"""
        import random
        random.seed(42)
        return [
            {"ts_code": idx["ts_code"], "name": idx["name"], "short_name": idx["short_name"],
             "trade_date": datetime.now().strftime("%Y-%m-%d"),
             "close": round(random.uniform(3000, 5000), 2),
             "pre_close": round(random.uniform(3000, 5000), 2),
             "change": round(random.uniform(-50, 50), 2),
             "pct_chg": round(random.uniform(-1.5, 1.5), 2),
             "vol": random.randint(50000000, 500000000),
             "amount": random.uniform(5e8, 2e9)}
            for idx in self.CORE_INDICES
        ]

    # ─── ETF行情数据 ──────────────────────────────────

    async def get_etf_list(self) -> List[Dict]:
        """获取热门ETF列表（优先DB，降级白名单，最终模拟行情）"""
        import random
        random.seed(42)

        # 尝试从 DB 查询真实行情
        if self.storage:
            engine = self.storage.get_postgres_engine()
            if engine:
                try:
                    codes = [e["ts_code"] for e in self.POPULAR_ETFS]
                    sql = """
                    SELECT DISTINCT ON (ts_code) ts_code, trade_date, close, pre_close,
                           change, pct_chg, vol, amount
                    FROM etf_daily
                    WHERE ts_code IN :codes
                    ORDER BY ts_code, trade_date DESC
                    """
                    df = pd.read_sql(sa_text(sql), engine, params={"codes": tuple(codes)})
                    if not df.empty:
                        etf_map = {e["ts_code"]: e for e in self.POPULAR_ETFS}
                        results = []
                        for _, row in df.iterrows():
                            code = row["ts_code"]
                            info = etf_map.get(code, {"ts_code": code, "name": code, "short_name": code, "fund_type": ""})
                            close_val = float(row["close"]) if pd.notna(row["close"]) else 0
                            pre_close_val = float(row["pre_close"]) if pd.notna(row["pre_close"]) else close_val
                            change_val = float(row["change"]) if pd.notna(row["change"]) else 0
                            pct_val = float(row["pct_chg"]) if pd.notna(row["pct_chg"]) else 0
                            results.append({
                                "ts_code": code,
                                "name": info["name"],
                                "short_name": info["short_name"],
                                "fund_type": info.get("fund_type", ""),
                                "trade_date": str(row["trade_date"])[:10] if pd.notna(row["trade_date"]) else "",
                                "close": round(close_val, 2),
                                "pre_close": round(pre_close_val, 2),
                                "change": round(change_val, 2),
                                "pct_chg": round(pct_val, 2),
                                "vol": int(row["vol"]) if pd.notna(row["vol"]) else 0,
                                "amount": float(row["amount"]) if pd.notna(row["amount"]) else 0,
                            })
                        # 保持 POPULAR_ETFS 顺序
                        results.sort(key=lambda x: codes.index(x["ts_code"]) if x["ts_code"] in codes else 999)
                        return results
                except Exception as e:
                    logger.warning(f"查询 etf_daily 失败 ({e})，降级到模拟数据")

        # 降级：返回模拟行情数据（确保前端始终有数据可展示）
        return [
            {
                "ts_code": e["ts_code"],
                "name": e["name"],
                "short_name": e["short_name"],
                "fund_type": e.get("fund_type", ""),
                "trade_date": datetime.now().strftime("%Y-%m-%d"),
                "close": round(random.uniform(0.5, 8.0), 3),
                "pre_close": round(random.uniform(0.5, 8.0), 3),
                "change": round(random.uniform(-0.2, 0.2), 3),
                "pct_chg": round(random.uniform(-3.0, 3.0), 2),
                "vol": random.randint(100000, 50000000),
                "amount": random.uniform(1e5, 1e8),
            }
            for e in self.POPULAR_ETFS
        ]

    # ─── ETF 一级/二级市场看板 ───────────────────────────

    ETF_MARKET_META = {
        "primary": {
            "id": "primary",
            "label": "一级市场（上交所）",
            "exchange": "SSE",
            "suffix": ".SH",
        },
        "secondary": {
            "id": "secondary",
            "label": "二级市场（深交所）",
            "exchange": "SZSE",
            "suffix": ".SZ",
        },
    }

    async def _is_trade_day(self, date_str: str) -> bool:
        """判断是否为交易日（ref_trade_cal via pg_manager）"""
        if not await self._ensure_pg():
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            return dt.weekday() < 5
        try:
            df = await self.pg.query_sql(
                "SELECT is_open FROM ref_trade_cal WHERE cal_date = :d AND exchange = 'SSE' LIMIT 1",
                {"d": date_str},
            )
            if df is not None and not df.empty:
                return int(df.iloc[0]["is_open"]) == 1
        except Exception as e:
            logger.warning(f"查询交易日历失败: {e}")
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.weekday() < 5

    def _query_latest_etf_trade_date_sync(self) -> Optional[str]:
        """通过 storage SQLAlchemy 查询 etf_daily 最新交易日"""
        if not self.storage:
            return None
        engine = self.storage.get_postgres_engine()
        if not engine:
            return None
        try:
            df = pd.read_sql(sa_text("SELECT MAX(trade_date) AS td FROM etf_daily"), engine)
            if not df.empty and pd.notna(df.iloc[0]["td"]):
                return str(df.iloc[0]["td"])[:10]
        except Exception as e:
            logger.warning(f"storage 查询 etf_daily 最新日期失败: {e}")
        return None

    async def _get_latest_etf_trade_date(self) -> str:
        if await self._ensure_pg():
            try:
                df = await self.pg.query_sql("SELECT MAX(trade_date) AS td FROM etf_daily", {})
                if df is not None and not df.empty and pd.notna(df.iloc[0]["td"]):
                    return str(df.iloc[0]["td"])[:10]
            except Exception as e:
                logger.warning(f"pg 查询 etf_daily 最新日期失败: {e}")

        latest = self._query_latest_etf_trade_date_sync()
        if latest:
            return latest
        return datetime.now().strftime("%Y-%m-%d")

    async def _get_market_trading_status(self) -> Dict[str, Any]:
        """A股交易时段判断 + 最近行情日期"""
        from datetime import time as dt_time
        try:
            from zoneinfo import ZoneInfo
            now = datetime.now(ZoneInfo("Asia/Shanghai"))
        except Exception:
            now = datetime.now()

        today = now.strftime("%Y-%m-%d")
        is_trade_day = await self._is_trade_day(today)
        t = now.time()
        in_morning = dt_time(9, 30) <= t <= dt_time(11, 30)
        in_afternoon = dt_time(13, 0) <= t <= dt_time(15, 0)
        is_trading = is_trade_day and (in_morning or in_afternoon)

        latest_date = await self._get_latest_etf_trade_date()
        if is_trading:
            has_today = False
            try:
                if await self._ensure_pg():
                    df = await self.pg.query_sql(
                        "SELECT COUNT(*) AS cnt FROM etf_daily WHERE trade_date = :d",
                        {"d": today},
                    )
                    has_today = df is not None and not df.empty and int(df.iloc[0]["cnt"]) > 0
            except Exception:
                pass
            if not has_today and self.storage:
                engine = self.storage.get_postgres_engine()
                if engine:
                    try:
                        df = pd.read_sql(
                            sa_text("SELECT COUNT(*) AS cnt FROM etf_daily WHERE trade_date = :d"),
                            engine, params={"d": today},
                        )
                        has_today = not df.empty and int(df.iloc[0]["cnt"]) > 0
                    except Exception:
                        pass
            if has_today:
                latest_date = today

        if is_trading:
            status, message = "trading", "交易中 · 显示最新行情"
        elif is_trade_day and t < dt_time(9, 30):
            status, message = "pre_market", "开市前 · 显示上一交易日收盘"
        elif is_trade_day and dt_time(11, 30) < t < dt_time(13, 0):
            status, message = "lunch_break", "午间休市 · 显示上午收盘"
        elif is_trade_day and t > dt_time(15, 0):
            status, message = "closed", "已收盘 · 显示当日收盘"
        else:
            status, message = "halted", "非交易时段 · 显示最近交易日收盘"

        return {
            "is_trading": is_trading,
            "is_trade_day": is_trade_day,
            "status": status,
            "status_message": message,
            "trade_date": latest_date,
            "server_time": now.strftime("%Y-%m-%d %H:%M:%S"),
        }

    def _etf_meta(self, ts_code: str) -> Dict[str, str]:
        for e in self.POPULAR_ETFS:
            if e["ts_code"] == ts_code:
                return e
        symbol = ts_code.split(".")[0] if "." in ts_code else ts_code
        return {
            "ts_code": ts_code,
            "name": ts_code,
            "short_name": symbol[-6:] if len(symbol) > 6 else symbol,
            "fund_type": "其他",
        }

    def _format_etf_row(self, row: pd.Series) -> Dict[str, Any]:
        code = str(row["ts_code"])
        meta = self._etf_meta(code)
        close_val = float(row["close"]) if pd.notna(row.get("close")) else 0
        pre_close_val = float(row["pre_close"]) if pd.notna(row.get("pre_close")) else close_val
        change_val = float(row["change"]) if pd.notna(row.get("change")) else 0
        pct_val = float(row["pct_chg"]) if pd.notna(row.get("pct_chg")) else 0
        amount_val = float(row["amount"]) if pd.notna(row.get("amount")) else 0
        # Tushare amount 单位为千元 → 亿元
        amount_yi = round(amount_val / 100000.0, 2)
        abs_pct = round(abs(pct_val), 2)
        return {
            "ts_code": code,
            "name": meta["name"],
            "short_name": meta["short_name"],
            "fund_type": meta.get("fund_type", "其他"),
            "trade_date": str(row["trade_date"])[:10] if pd.notna(row.get("trade_date")) else "",
            "close": round(close_val, 4 if close_val < 10 else 2),
            "pre_close": round(pre_close_val, 4 if pre_close_val < 10 else 2),
            "change": round(change_val, 4 if abs(change_val) < 10 else 2),
            "pct_chg": round(pct_val, 2),
            "abs_pct_chg": abs_pct,
            "amount": amount_val,
            "amount_yi": amount_yi,
            "vol": int(row["vol"]) if pd.notna(row.get("vol")) else 0,
        }

    def _summarize_etfs(self, etfs: List[Dict]) -> Dict[str, Any]:
        if not etfs:
            return {"count": 0, "up_count": 0, "down_count": 0, "flat_count": 0, "avg_pct_chg": 0}
        ups = sum(1 for e in etfs if e.get("pct_chg", 0) > 0)
        downs = sum(1 for e in etfs if e.get("pct_chg", 0) < 0)
        flats = len(etfs) - ups - downs
        avg = sum(e.get("pct_chg", 0) for e in etfs) / len(etfs)
        return {
            "count": len(etfs),
            "up_count": ups,
            "down_count": downs,
            "flat_count": flats,
            "avg_pct_chg": round(avg, 2),
        }

    def _query_market_etfs_sync(
        self, trade_date: str, suffix: str, limit: int
    ) -> List[Dict]:
        """从 etf_daily 拉取指定交易所当日全部活跃 ETF（按成交额排序）"""
        if not self.storage:
            return []
        engine = self.storage.get_postgres_engine()
        if not engine:
            return []
        try:
            sql = """
            SELECT ts_code, trade_date, open, high, low, close, pre_close,
                   change, pct_chg, vol, amount
            FROM etf_daily
            WHERE trade_date = :td AND ts_code LIKE :pattern
              AND close IS NOT NULL AND close > 0
            ORDER BY amount DESC NULLS LAST
            LIMIT :lim
            """
            df = pd.read_sql(
                sa_text(sql), engine,
                params={"td": trade_date, "pattern": f"%{suffix}", "lim": limit},
            )
            if df.empty:
                return []
            return [self._format_etf_row(row) for _, row in df.iterrows()]
        except Exception as e:
            logger.warning(f"storage 查询 ETF ({suffix}) 失败: {e}")
            return []

    async def _fetch_market_etfs(
        self, market_id: str, trade_date: str, limit: int = 100
    ) -> List[Dict]:
        """按交易所后缀拉取 ETF 最新行情（etf_daily 全市场，非仅热门白名单）"""
        suffix = self.ETF_MARKET_META[market_id]["suffix"]
        results = self._query_market_etfs_sync(trade_date, suffix, limit)

        if not results and await self._ensure_pg():
            try:
                sql = """
                SELECT ts_code, trade_date, open, high, low, close, pre_close,
                       change, pct_chg, vol, amount
                FROM etf_daily
                WHERE trade_date = :td AND ts_code LIKE :pattern
                  AND close IS NOT NULL AND close > 0
                ORDER BY amount DESC NULLS LAST
                LIMIT :lim
                """
                df = await self.pg.query_sql(
                    sql, {"td": trade_date, "pattern": f"%{suffix}", "lim": limit}
                )
                if df is not None and not df.empty:
                    results = [self._format_etf_row(row) for _, row in df.iterrows()]
            except Exception as e:
                logger.warning(f"pg 查询 {market_id} ETF 失败: {e}")

        if not results:
            etf_map = {e["ts_code"]: e for e in self.POPULAR_ETFS if e["ts_code"].endswith(suffix)}
            import random
            random.seed(hash(market_id) % 2**32)
            for code, info in list(etf_map.items())[:limit]:
                base = random.uniform(0.8, 5.0)
                chg = random.uniform(-0.15, 0.15)
                pct = round(chg / base * 100, 2)
                results.append({
                    "ts_code": code,
                    "name": info["name"],
                    "short_name": info["short_name"],
                    "fund_type": info.get("fund_type", ""),
                    "trade_date": trade_date,
                    "close": round(base, 3),
                    "pre_close": round(base - chg, 3),
                    "change": round(chg, 3),
                    "pct_chg": pct,
                    "abs_pct_chg": abs(pct),
                    "amount_yi": round(random.uniform(0.5, 50), 2),
                    "vol": random.randint(100000, 5000000),
                    "amount": random.uniform(1e6, 1e8),
                })

        return results[:limit]

    async def get_etf_board(self, limit_per_market: int = 100) -> Dict[str, Any]:
        """
        ETF 看板：一级市场（上交所 .SH）与二级市场（深交所 .SZ）
        数据来源：data_layer.storage → etf_daily
        """
        status = await self._get_market_trading_status()
        trade_date = status["trade_date"]

        primary_etfs = await self._fetch_market_etfs("primary", trade_date, limit_per_market)
        secondary_etfs = await self._fetch_market_etfs("secondary", trade_date, limit_per_market)

        return {
            **status,
            "primary_market": {
                **self.ETF_MARKET_META["primary"],
                "etfs": primary_etfs,
                "summary": self._summarize_etfs(primary_etfs),
            },
            "secondary_market": {
                **self.ETF_MARKET_META["secondary"],
                "etfs": secondary_etfs,
                "summary": self._summarize_etfs(secondary_etfs),
            },
        }

    # ─── 周K/月K 自动推导（表为空时从日K聚合） ─────────

    async def _query_bars_pg(
        self, ts_code: str, table: str,
        start: Optional[str], end: Optional[str], limit: int
    ) -> Optional[pd.DataFrame]:
        """从 PostgreSQL 查询K线。

        优先 SQLAlchemy（与挑选式/策略同一引擎，稳定可用）；
        asyncpg 作补充；二者都失败才返回 None。
        """
        # 1) SQLAlchemy 同步引擎（已知可用）
        if self.storage:
            df = self._query_timescale_bars(ts_code, table, start, end, limit)
            if df is not None and not df.empty:
                return df

        # 2) asyncpg 回退（带超时，避免连接池挂死拖垮看板）
        if await self._ensure_pg():
            try:
                actual_table = table
                if self._is_etf_code(ts_code):
                    actual_table = "etf_daily"
                elif self._is_index_code(ts_code) and table in (
                    "stock_daily", "stock_weekly", "stock_monthly", "index_daily"
                ):
                    actual_table = "index_daily"
                elif table in ("stock_daily", "stock_weekly", "stock_monthly", "etf_daily"):
                    actual_table = table

                import asyncio
                df = await asyncio.wait_for(
                    self.pg.query(
                        table=actual_table,
                        ts_code=ts_code,
                        start_date=start,
                        end_date=end,
                        limit=limit,
                        order_by="trade_date",
                        order_desc=True,
                    ),
                    timeout=8.0,
                )
                if df is not None and not df.empty:
                    if "trade_date" in df.columns:
                        df["time"] = pd.to_datetime(df["trade_date"])
                    return df
            except Exception as e:
                logger.warning(f"pg_manager 查询 {table} 失败 ({ts_code}): {e}")

        return None

    async def _derive_weekly_from_daily(self, ts_code: str, limit: int, daily_table: str = "stock_daily") -> Optional[pd.DataFrame]:
        """从日K线聚合成周K线"""
        daily_df = await self._query_bars_pg(ts_code, daily_table, None, None, limit * 5)
        if daily_df is None or daily_df.empty:
            return None
        daily_df = daily_df.sort_values("time")
        daily_df["time"] = pd.to_datetime(daily_df["time"])
        daily_df = daily_df.set_index("time")

        weekly = daily_df.resample("W").agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "vol": "sum",
            "amount": "sum",
        }).dropna()
        weekly = weekly.reset_index().tail(limit)
        # 补充 pre_close / change / pct_chg
        if "pre_close" not in weekly.columns:
            weekly["pre_close"] = weekly["close"].shift(1)
        if "change" not in weekly.columns:
            weekly["change"] = weekly["close"] - weekly["pre_close"]
        if "pct_chg" not in weekly.columns:
            weekly["pct_chg"] = (weekly["change"] / weekly["pre_close"].replace(0, np.nan)) * 100
        return weekly

    async def _derive_monthly_from_daily(self, ts_code: str, limit: int, daily_table: str = "stock_daily") -> Optional[pd.DataFrame]:
        """从日K线聚合成月K线"""
        daily_df = await self._query_bars_pg(ts_code, daily_table, None, None, limit * 22)
        if daily_df is None or daily_df.empty:
            return None
        daily_df = daily_df.sort_values("time")
        daily_df["time"] = pd.to_datetime(daily_df["time"])
        daily_df = daily_df.set_index("time")

        monthly = daily_df.resample("ME").agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "vol": "sum",
            "amount": "sum",
        }).dropna()
        monthly = monthly.reset_index().tail(limit)
        if "pre_close" not in monthly.columns:
            monthly["pre_close"] = monthly["close"].shift(1)
        if "change" not in monthly.columns:
            monthly["change"] = monthly["close"] - monthly["pre_close"]
        if "pct_chg" not in monthly.columns:
            monthly["pct_chg"] = (monthly["change"] / monthly["pre_close"].replace(0, np.nan)) * 100
        return monthly

    def _derive_intraday_from_daily(self, ts_code: str, freq: str, limit: int) -> Optional[pd.DataFrame]:
        """尝试从日K线模拟分钟K线（当InfluxDB无数据时）——返回None让前端显示提示"""
        # 分钟线不能从日线推导，返回None让前端显示明确消息
        return None

    def _aggregate_df_to_weekly(self, df: pd.DataFrame, limit: int) -> pd.DataFrame:
        """将日线DataFrame聚合为周线"""
        df = df.copy()
        df["time"] = pd.to_datetime(df["time"])
        df = df.sort_values("time").set_index("time")
        weekly = df.resample("W").agg({
            "open": "first", "high": "max", "low": "min",
            "close": "last", "vol": "sum", "amount": "sum",
        }).dropna().reset_index().tail(limit)
        if "pre_close" not in weekly.columns:
            weekly["pre_close"] = weekly["close"].shift(1)
        return weekly

    def _aggregate_df_to_monthly(self, df: pd.DataFrame, limit: int) -> pd.DataFrame:
        """将日线DataFrame聚合为月线"""
        df = df.copy()
        df["time"] = pd.to_datetime(df["time"])
        df = df.sort_values("time").set_index("time")
        monthly = df.resample("ME").agg({
            "open": "first", "high": "max", "low": "min",
            "close": "last", "vol": "sum", "amount": "sum",
        }).dropna().reset_index().tail(limit)
        if "pre_close" not in monthly.columns:
            monthly["pre_close"] = monthly["close"].shift(1)
        return monthly

    async def get_kline_data(
        self,
        ts_code: str,
        timeframe: str = "day",
        start: Optional[str] = None,
        end: Optional[str] = None,
        limit: int = 500,
        indicators: Optional[List[str]] = None,
        auto_fill: bool = True,
    ) -> Dict[str, Any]:
        """
        获取K线数据并计算技术指标

        Args:
            ts_code: 股票代码 (如 000001.SZ)
            timeframe: 周期 (1m/5m/15m/30m/60m/day/week/month)
            start: 开始日期
            end: 结束日期
            limit: 最大返回条数
            indicators: 需要计算的技术指标列表

        Returns:
            {
                "ts_code": "...",
                "name": "...",
                "timeframe": "day",
                "bars": [...],
                "indicators": {...},
            }
        """
        tf_config = self.TIMEFRAME_MAP.get(timeframe)
        if not tf_config:
            return {"error": f"不支持的周期: {timeframe}", "bars": [], "indicators": {}}

        if tf_config["source"] == "influx":
            return {
                "error": f"暂不支持分钟线周期: {timeframe}",
                "ts_code": ts_code,
                "timeframe": timeframe,
                "bars": [],
                "indicators": {},
                "count": 0,
            }

        bars_df = pd.DataFrame()
        is_etf = self._is_etf_code(ts_code)
        is_index = self._is_index_code(ts_code)
        fallback_msg = ""
        if is_etf:
            daily_table = "etf_daily"
        elif is_index:
            daily_table = "index_daily"
        else:
            daily_table = "stock_daily"
            # 个股挑选式：缺/旧则 Datahubco → PG（搜索 30 日路径会关掉 auto_fill）
            if auto_fill:
                try:
                    from server.selective_data_service import selective_data_service
                    await selective_data_service.ensure_fresh_if_needed(ts_code)
                except Exception as e:
                    logger.warning(f"挑选式补数跳过 {ts_code}: {e}")

        if timeframe == "day":
            bars_df = await self._query_bars_pg(ts_code, daily_table, start, end, limit)
            # 滑动更早：库内日线不足且为个股时向前续拉
            if (
                auto_fill
                and daily_table == "stock_daily"
                and (bars_df is None or len(bars_df) < min(limit, 60))
            ):
                try:
                    from server.selective_data_service import selective_data_service
                    await selective_data_service.ensure(
                        ts_code, mode="extend", extend_days=90
                    )
                    bars_df = await self._query_bars_pg(
                        ts_code, daily_table, start, end, limit
                    )
                except Exception as e:
                    logger.warning(f"日线向前续拉失败 {ts_code}: {e}")
        elif timeframe == "week":
            bars_df = await self._query_bars_pg(ts_code, "stock_weekly", start, end, limit)
            if bars_df is None or bars_df.empty:
                logger.info(f"{ts_code} 周K表为空，从日K自动聚合")
                bars_df = await self._derive_weekly_from_daily(ts_code, limit, daily_table)
                fallback_msg = "周线数据通过日线聚合生成"
        elif timeframe == "month":
            bars_df = await self._query_bars_pg(ts_code, "stock_monthly", start, end, limit)
            if bars_df is None or bars_df.empty:
                logger.info(f"{ts_code} 月K表为空，从日K自动聚合")
                bars_df = await self._derive_monthly_from_daily(ts_code, limit, daily_table)
                fallback_msg = "月线数据通过日线聚合生成"

        if bars_df is None or bars_df.empty:
            # 仅当 asyncpg 与 SQLAlchemy 都不可用时才用模拟数据
            engine_ok = False
            try:
                engine_ok = bool(self.storage and self.storage.get_postgres_engine())
            except Exception:
                engine_ok = False
            if not await self._ensure_pg() and not engine_ok:
                bars_df = self._mock_kline(ts_code, timeframe, limit)
                fallback_msg = "数据库未连接，当前为模拟数据"
            else:
                result = {
                    "ts_code": ts_code,
                    "timeframe": timeframe,
                    "bars": [],
                    "indicators": {},
                    "count": 0,
                }
                if fallback_msg:
                    result["message"] = fallback_msg
                else:
                    result["message"] = f"无 {ts_code} 的 {timeframe} K线数据"
                return result

        # 统一列名
        bars_df = self._normalize_columns(bars_df)

        # 按时间升序
        bars_df = bars_df.sort_values("time")

        # 计算技术指标
        indicator_data = {}
        if indicators:
            indicator_data = self._compute_indicators(bars_df, indicators)

        # 转为 JSON 可序列化格式
        bars = self._df_to_bars(bars_df)

        # 股票/指数/ETF 信息
        stock_info = None
        display_name = ts_code
        if is_etf:
            stock_info = await self.get_etf_info(ts_code)
            display_name = stock_info.get("name", ts_code) if stock_info else ts_code
        else:
            stock_info = await self.get_stock_info(ts_code)
            if not stock_info and is_index:
                for idx in self.CORE_INDICES:
                    if idx["ts_code"] == ts_code:
                        stock_info = {"ts_code": ts_code, "name": idx["name"]}
                        break
            display_name = stock_info.get("name", ts_code) if stock_info else ts_code

        return {
            "ts_code": ts_code,
            "name": display_name,
            "timeframe": timeframe,
            "bars": bars,
            "indicators": indicator_data,
            "count": len(bars),
            "stock_info": stock_info,
            "is_index": is_index,
            "is_etf": is_etf,
            "message": fallback_msg or None,
        }

    # ─── 指数代码判断 ──────────────────────────────────

    # 已知核心指数代码（可根据实际情况扩展）
    _INDEX_CODES = {
        "000001.SH", "000016.SH", "000300.SH", "000688.SH", "000905.SH",
        "000852.SH", "399001.SZ", "399005.SZ", "399006.SZ", "399673.SZ",
    }

    # 热门 ETF 列表（同花顺风格）
    POPULAR_ETFS = [
        {"ts_code": "510050.SH", "name": "上证50ETF", "short_name": "50ETF", "fund_type": "股票"},
        {"ts_code": "510300.SH", "name": "沪深300ETF", "short_name": "300ETF", "fund_type": "股票"},
        {"ts_code": "510500.SH", "name": "中证500ETF", "short_name": "500ETF", "fund_type": "股票"},
        {"ts_code": "159915.SZ", "name": "创业板ETF", "short_name": "创业板ET", "fund_type": "股票"},
        {"ts_code": "588000.SH", "name": "科创50ETF", "short_name": "科创50ET", "fund_type": "股票"},
        {"ts_code": "512880.SH", "name": "证券ETF", "short_name": "证券ETF", "fund_type": "行业"},
        {"ts_code": "512010.SH", "name": "医药ETF", "short_name": "医药ETF", "fund_type": "行业"},
        {"ts_code": "512660.SH", "name": "军工ETF", "short_name": "军工ETF", "fund_type": "行业"},
        {"ts_code": "512100.SH", "name": "中证1000ETF", "short_name": "1000ETF", "fund_type": "股票"},
        {"ts_code": "159949.SZ", "name": "创业板50ETF", "short_name": "创50ETF", "fund_type": "股票"},
        {"ts_code": "518880.SH", "name": "黄金ETF", "short_name": "黄金ETF", "fund_type": "商品"},
        {"ts_code": "511880.SH", "name": "银华日利ETF", "short_name": "货币ETF", "fund_type": "货币"},
        {"ts_code": "513100.SH", "name": "纳指ETF", "short_name": "纳指ETF", "fund_type": "跨境"},
        {"ts_code": "513050.SH", "name": "中概互联ETF", "short_name": "中概互联", "fund_type": "跨境"},
        {"ts_code": "510880.SH", "name": "红利ETF", "short_name": "红利ETF", "fund_type": "股票"},
        {"ts_code": "516510.SH", "name": "芯片ETF", "short_name": "芯片ETF", "fund_type": "行业"},
        {"ts_code": "515790.SH", "name": "光伏ETF", "short_name": "光伏ETF", "fund_type": "行业"},
        {"ts_code": "159865.SZ", "name": "中证畜牧ETF", "short_name": "畜牧ETF", "fund_type": "行业"},
    ]

    # ETF代码白名单（快速检测）
    _ETF_CODES = {etf["ts_code"] for etf in POPULAR_ETFS}

    def _is_index_code(self, ts_code: str) -> bool:
        """判断是否为指数代码"""
        if ts_code in self._INDEX_CODES:
            return True
        # 也可以从 ref_index_basic 表查询，但为避免循环依赖，这里先用白名单
        engine = self.storage.get_postgres_engine() if self.storage else None
        if engine:
            try:
                sql = "SELECT 1 FROM ref_index_basic WHERE ts_code = :code LIMIT 1"
                df = pd.read_sql(sa_text(sql), engine, params={"code": ts_code})
                if not df.empty:
                    return True
            except Exception:
                pass
        return False

    def _is_etf_code(self, ts_code: str) -> bool:
        """判断是否为ETF代码"""
        if ts_code in self._ETF_CODES:
            return True
        engine = self.storage.get_postgres_engine() if self.storage else None
        if engine:
            try:
                sql = "SELECT 1 FROM ref_etf_basic WHERE ts_code = :code LIMIT 1"
                df = pd.read_sql(sa_text(sql), engine, params={"code": ts_code})
                if not df.empty:
                    return True
            except Exception:
                pass
        # 也检查 etf_daily 表是否有数据
        if engine:
            try:
                sql = "SELECT 1 FROM etf_daily WHERE ts_code = :code LIMIT 1"
                df = pd.read_sql(sa_text(sql), engine, params={"code": ts_code})
                if not df.empty:
                    return True
            except Exception:
                pass
        return False

    # ─── 私有查询方法 ──────────────────────────────────

    def _query_timescale_bars(
        self, ts_code: str, table: str,
        start: Optional[str], end: Optional[str], limit: int
    ) -> Optional[pd.DataFrame]:
        """从 TimescaleDB 查询K线"""
        engine = self.storage.get_postgres_engine()
        if not engine:
            return None

        try:
            actual_table = table

            # ETF 代码：路由到 etf_daily 表
            if self._is_etf_code(ts_code):
                if table in ("stock_daily", "stock_weekly", "stock_monthly"):
                    actual_table = "etf_daily"
                elif table == "etf_daily":
                    actual_table = "etf_daily"
                else:
                    # 周/月线 ETF 从 etf_daily 聚合
                    actual_table = "etf_daily"

            # 检测是否为指数代码，自动切换到 index_daily 表
            elif table in ("stock_daily", "stock_weekly", "stock_monthly") and self._is_index_code(ts_code):
                if table == "stock_daily":
                    actual_table = "index_daily"
                else:
                    logger.info(f"指数 {ts_code} 不支持 {table}，使用 index_daily 日线数据")
                    actual_table = "index_daily"

            sql = f"""
            SELECT trade_date, open, high, low, close, pre_close,
                   change, pct_chg, vol, amount
            FROM {actual_table}
            WHERE ts_code = :code
            """
            params = {"code": ts_code}
            if start:
                sql += " AND trade_date >= :start"
                params["start"] = start
            if end:
                sql += " AND trade_date <= :end"
                params["end"] = end
            sql += " ORDER BY trade_date DESC LIMIT :limit"
            params["limit"] = limit

            df = pd.read_sql(sa_text(sql), engine, params=params)
            if not df.empty and "trade_date" in df.columns:
                df["time"] = pd.to_datetime(df["trade_date"])
                df = df.drop(columns=["trade_date"])
            return df
        except Exception as e:
            logger.warning(f"查询 {table} 失败 ({ts_code}): {e}")
            return None

    def _query_influx_bars(
        self, ts_code: str, freq: str,
        start: Optional[str], end: Optional[str], limit: int
    ) -> Optional[pd.DataFrame]:
        """从 InfluxDB 查询分钟K线"""
        client = self.storage.get_influx_client()
        if not client:
            return None

        try:
            start_str = start if start else "-30d"
            end_str = end if end else "now()"

            query = f"""
            from(bucket: "{self.storage._connections.get('influx_bucket', 'market_data')}")
                |> range(start: {start_str}, stop: {end_str})
                |> filter(fn: (r) => r._measurement == "stock_bar_mins")
                |> filter(fn: (r) => r.ts_code == "{ts_code}")
                |> filter(fn: (r) => r.freq == "{freq}")
                |> pivot(rowKey: ["_time", "ts_code", "freq"],
                         columnKey: ["_field"],
                         valueColumn: "_value")
                |> sort(columns: ["_time"], desc: true)
                |> limit(n: {limit})
            """
            query_api = client.query_api()
            result = query_api.query_data_frame(query)

            if result is None:
                return None
            if isinstance(result, list):
                if len(result) == 0:
                    return None
                df = result[0]
            else:
                df = result

            if df.empty:
                return df

            if "_time" in df.columns:
                df["time"] = pd.to_datetime(df["_time"])
            return df
        except Exception as e:
            logger.warning(f"InfluxDB 查询分钟线失败 ({ts_code}, {freq}): {e}")
            return None

    def _normalize_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """标准化列名"""
        rename_map = {
            "trade_date": "time",
            "_time": "time",
            "ts_code": "symbol",
        }
        # 只重命名存在的列
        rename_map = {k: v for k, v in rename_map.items() if k in df.columns}
        df = df.rename(columns=rename_map)

        # 确保关键列存在
        for col in ["open", "high", "low", "close", "vol"]:
            if col not in df.columns:
                df[col] = np.nan

        return df

    def _compute_indicators(self, df: pd.DataFrame, indicators: List[str]) -> Dict[str, Any]:
        """计算技术指标"""
        result = {}
        close = df["close"].astype(float)
        high = df["high"].astype(float)
        low = df["low"].astype(float)
        vol = df["vol"].astype(float) if "vol" in df.columns else pd.Series(dtype=float)

        for ind in indicators:
            try:
                if ind == "ma":
                    mas = {}
                    for p in [5, 10, 20, 30]:
                        ma_val = calc_ma(close, p)
                        mas[f"MA{p}"] = [round(v, 2) if not pd.isna(v) else None for v in ma_val.values]
                    result["ma"] = mas

                elif ind == "ema":
                    emas = {}
                    for p in [12, 26]:
                        ema_val = calc_ema(close, p)
                        emas[f"EMA{p}"] = [round(v, 2) if not pd.isna(v) else None for v in ema_val.values]
                    result["ema"] = emas

                elif ind == "macd":
                    macd_data = calc_macd(close)
                    for key, series in macd_data.items():
                        result[f"macd_{key}"] = [round(v, 4) if not pd.isna(v) else None for v in series.values]

                elif ind == "rsi":
                    rsi_val = calc_rsi(close)
                    result["rsi"] = [round(v, 2) if not pd.isna(v) else None for v in rsi_val.values]

                elif ind == "boll":
                    boll_data = calc_bollinger(close)
                    for key, series in boll_data.items():
                        result[f"boll_{key}"] = [round(v, 2) if not pd.isna(v) else None for v in series.values]

                elif ind == "kdj":
                    kdj_data = calc_kdj(high, low, close)
                    for key, series in kdj_data.items():
                        result[f"kdj_{key}"] = [round(v, 2) if not pd.isna(v) else None for v in series.values]

                elif ind == "volume":
                    if not vol.empty:
                        vol_mas = calc_volume_ma(vol)
                        for key, series in vol_mas.items():
                            result[key] = [int(v) if not pd.isna(v) else None for v in series.values]

            except Exception as e:
                logger.warning(f"计算指标 {ind} 失败: {e}")
                continue

        return result

    def _df_to_bars(self, df: pd.DataFrame) -> List[Dict]:
        """将 DataFrame 转为可 JSON 序列化的 bar 列表"""
        bars = []
        for _, row in df.iterrows():
            bar = {
                "time": str(row.get("time", "")),
            }
            for col in ["open", "high", "low", "close", "vol", "amount", "pre_close", "change", "pct_chg"]:
                val = row.get(col)
                if val is None or (isinstance(val, float) and np.isnan(val)):
                    continue
                if isinstance(val, (np.integer, np.floating)):
                    if col == "vol":
                        bar[col] = int(val)
                    elif col in ("change", "pct_chg"):
                        bar[col] = round(float(val), 4)
                    else:
                        bar[col] = round(float(val), 4)
                else:
                    try:
                        # Decimal / str from SQLAlchemy
                        num = float(val)
                        if col == "vol":
                            bar[col] = int(num)
                        elif col in ("change", "pct_chg"):
                            bar[col] = round(num, 4)
                        else:
                            bar[col] = round(num, 4)
                    except (TypeError, ValueError):
                        bar[col] = val
            bars.append(bar)
        return bars

    # ─── 模拟数据 (fallback) ────────────────────────────

    def _mock_stock_search(self, keyword: str) -> List[Dict]:
        """模拟股票搜索"""
        mock_stocks = [
            {"ts_code": "000001.SZ", "symbol": "000001", "name": "平安银行", "area": "深圳", "industry": "银行", "market": "主板", "list_status": "L"},
            {"ts_code": "600036.SH", "symbol": "600036", "name": "招商银行", "area": "深圳", "industry": "银行", "market": "主板", "list_status": "L"},
            {"ts_code": "000858.SZ", "symbol": "000858", "name": "五粮液", "area": "宜宾", "industry": "白酒", "market": "主板", "list_status": "L"},
            {"ts_code": "600519.SH", "symbol": "600519", "name": "贵州茅台", "area": "贵州", "industry": "白酒", "market": "主板", "list_status": "L"},
            {"ts_code": "300750.SZ", "symbol": "300750", "name": "宁德时代", "area": "福建", "industry": "电气设备", "market": "创业板", "list_status": "L"},
            {"ts_code": "000333.SZ", "symbol": "000333", "name": "美的集团", "area": "佛山", "industry": "家电", "market": "主板", "list_status": "L"},
            {"ts_code": "601318.SH", "symbol": "601318", "name": "中国平安", "area": "深圳", "industry": "保险", "market": "主板", "list_status": "L"},
            {"ts_code": "600030.SH", "symbol": "600030", "name": "中信证券", "area": "深圳", "industry": "证券", "market": "主板", "list_status": "L"},
            {"ts_code": "000725.SZ", "symbol": "000725", "name": "京东方A", "area": "北京", "industry": "元器件", "market": "主板", "list_status": "L"},
            {"ts_code": "002415.SZ", "symbol": "002415", "name": "海康威视", "area": "杭州", "industry": "计算机", "market": "中小板", "list_status": "L"},
        ]
        kw = keyword.lower()
        return [s for s in mock_stocks if kw in s["ts_code"].lower() or kw in s["name"].lower() or kw in s["symbol"].lower()]

    def _mock_kline(self, ts_code: str, timeframe: str, limit: int) -> pd.DataFrame:
        """生成模拟K线数据（支持最多 1500 根K线）"""
        np.random.seed(hash(ts_code) % 2**32)

        # ETF 价格较小；股票中等；指数较大
        if self._is_etf_code(ts_code):
            base_price = np.random.uniform(0.8, 6.0)
        elif self._is_index_code(ts_code):
            base_price = np.random.uniform(1000, 5000)
        else:
            base_price = np.random.uniform(5, 200)

        freq_map = {
            "1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min", "60m": "1h",
            "day": "1D", "week": "1W", "month": "1M",
        }
        freq = freq_map.get(timeframe, "1D")
        dates = pd.date_range(end=datetime.now(), periods=min(limit, 1500), freq=freq)

        price = base_price
        bars = []
        for d in dates:
            change = np.random.randn() * price * 0.02
            open_p = price
            close_p = price + change
            high_p = max(open_p, close_p) + abs(np.random.randn() * price * 0.01)
            low_p = min(open_p, close_p) - abs(np.random.randn() * price * 0.01)
            vol = int(abs(np.random.randn()) * 10000000 + 5000000)
            bars.append({
                "time": d,
                "open": round(open_p, 3 if base_price < 10 else 2),
                "high": round(high_p, 3 if base_price < 10 else 2),
                "low": round(low_p, 3 if base_price < 10 else 2),
                "close": round(close_p, 3 if base_price < 10 else 2),
                "vol": vol,
                "amount": round(close_p * vol, 2),
            })
            # ETF波动较小
            if self._is_etf_code(ts_code):
                price = close_p * (1 + np.random.uniform(-0.005, 0.005))
            else:
                price = close_p

        return pd.DataFrame(bars)


# 全局实例
dash_service = DashService()

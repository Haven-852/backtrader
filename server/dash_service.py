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
        {"id": "ma",       "name": "移动均线 MA",    "params": {"periods": [5, 10, 20, 60]}},
        {"id": "ema",      "name": "指数均线 EMA",   "params": {"periods": [12, 26]}},
        {"id": "macd",     "name": "MACD",           "params": {"fast": 12, "slow": 26, "signal": 9}},
        {"id": "rsi",      "name": "RSI 相对强弱",   "params": {"period": 14}},
        {"id": "boll",     "name": "布林带 BOLL",    "params": {"period": 20, "std_dev": 2.0}},
        {"id": "kdj",      "name": "KDJ 随机指标",   "params": {"n": 9, "m1": 3, "m2": 3}},
        {"id": "volume",   "name": "成交量均线",     "params": {"periods": [5, 10, 20]}},
    ]

    def __init__(self):
        self._storage = None

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
            {"id": "1m",   "name": "1分钟",   "group": "分钟"},
            {"id": "5m",   "name": "5分钟",   "group": "分钟"},
            {"id": "15m",  "name": "15分钟",  "group": "分钟"},
            {"id": "30m",  "name": "30分钟",  "group": "分钟"},
            {"id": "60m",  "name": "60分钟",  "group": "分钟"},
            {"id": "day",  "name": "日线",    "group": "日线"},
            {"id": "week", "name": "周线",    "group": "周线"},
            {"id": "month","name": "月线",    "group": "月线"},
        ]

    def get_available_indicators(self) -> List[Dict]:
        """获取可用技术指标列表"""
        return self.AVAILABLE_INDICATORS

    async def search_stocks(self, keyword: str, limit: int = 20) -> List[Dict]:
        """搜索股票"""
        if not self.storage:
            return self._mock_stock_search(keyword)

        engine = self.storage.get_postgres_engine()
        if not engine:
            return self._mock_stock_search(keyword)

        try:
            sql = """
            SELECT ts_code, symbol, name, area, industry, market, list_status
            FROM ref_stock_basic
            WHERE (ts_code ILIKE :kw OR name ILIKE :kw OR symbol ILIKE :kw)
              AND list_status = 'L'
            ORDER BY ts_code
            LIMIT :limit
            """
            df = pd.read_sql(sa_text(sql), engine, params={"kw": f"%{keyword}%", "limit": limit})
            return df.to_dict("records")
        except Exception as e:
            logger.warning(f"搜索股票失败: {e}")
            return self._mock_stock_search(keyword)

    async def get_stock_info(self, ts_code: str) -> Optional[Dict]:
        """获取股票基本信息"""
        if not self.storage:
            return None

        engine = self.storage.get_postgres_engine()
        if not engine:
            return None

        try:
            sql = """
            SELECT ts_code, symbol, name, area, industry, market, list_status, list_date
            FROM ref_stock_basic WHERE ts_code = :code LIMIT 1
            """
            df = pd.read_sql(sa_text(sql), engine, params={"code": ts_code})
            if df.empty:
                return None
            return df.iloc[0].to_dict()
        except Exception as e:
            logger.warning(f"获取股票信息失败 ({ts_code}): {e}")
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

    async def get_index_snapshots(self) -> List[Dict]:
        """获取核心指数最新快照（Redis 缓存 60 秒）"""
        if not self.storage:
            return self._mock_index_snapshots()

        # 尝试从 Redis 读取缓存
        cache_key = "dash:index_snapshots"
        redis_client = self.storage.get_redis_client() if hasattr(self.storage, 'get_redis_client') else None
        if redis_client:
            try:
                import json
                cached = redis_client.get(cache_key)
                if cached:
                    return json.loads(cached)
            except Exception:
                pass

        engine = self.storage.get_postgres_engine()
        if not engine:
            return self._mock_index_snapshots()

        try:
            codes = [idx["ts_code"] for idx in self.CORE_INDICES]
            sql = """
            SELECT DISTINCT ON (ts_code) ts_code, trade_date, open, high, low, close,
                   pre_close, change, pct_chg, vol, amount
            FROM index_daily
            WHERE ts_code IN :codes
            ORDER BY ts_code, trade_date DESC
            """
            df = pd.read_sql(sa_text(sql), engine, params={"codes": tuple(codes)})

            name_map = {idx["ts_code"]: idx for idx in self.CORE_INDICES}
            results = []
            for _, row in df.iterrows():
                code = row["ts_code"]
                idx_info = name_map.get(code, {"name": code, "short_name": code})
                close_val = float(row["close"]) if pd.notna(row["close"]) else 0
                pre_close_val = float(row["pre_close"]) if pd.notna(row["pre_close"]) else close_val
                change_val = float(row["change"]) if pd.notna(row["change"]) else 0
                pct_val = float(row["pct_chg"]) if pd.notna(row["pct_chg"]) else 0
                results.append({
                    "ts_code": code,
                    "name": idx_info["name"],
                    "short_name": idx_info["short_name"],
                    "trade_date": str(row["trade_date"])[:10] if pd.notna(row["trade_date"]) else "",
                    "close": round(close_val, 2),
                    "open": round(float(row["open"]), 2) if pd.notna(row["open"]) else None,
                    "high": round(float(row["high"]), 2) if pd.notna(row["high"]) else None,
                    "low": round(float(row["low"]), 2) if pd.notna(row["low"]) else None,
                    "pre_close": round(pre_close_val, 2),
                    "change": round(change_val, 2),
                    "pct_chg": round(pct_val, 2),
                    "vol": int(row["vol"]) if pd.notna(row["vol"]) else 0,
                    "amount": float(row["amount"]) if pd.notna(row["amount"]) else 0,
                })

            # 保持 CORE_INDICES 的顺序
            results.sort(key=lambda x: codes.index(x["ts_code"]) if x["ts_code"] in codes else 999)

            # 写入 Redis 缓存（60 秒过期）
            if redis_client:
                try:
                    import json
                    redis_client.setex(cache_key, 60, json.dumps(results, default=str))
                except Exception:
                    pass

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

    # ─── 周K/月K 自动推导（表为空时从日K聚合） ─────────

    def _derive_weekly_from_daily(self, ts_code: str, limit: int) -> Optional[pd.DataFrame]:
        """从日K线聚合成周K线"""
        daily_df = self._query_timescale_bars(ts_code, "stock_daily", None, None, limit * 5)
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

    def _derive_monthly_from_daily(self, ts_code: str, limit: int) -> Optional[pd.DataFrame]:
        """从日K线聚合成月K线"""
        daily_df = self._query_timescale_bars(ts_code, "stock_daily", None, None, limit * 22)
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

        bars_df = pd.DataFrame()
        is_etf = self._is_etf_code(ts_code)
        is_index = self._is_index_code(ts_code)
        fallback_msg = ""

        if not self.storage:
            bars_df = self._mock_kline(ts_code, timeframe, limit)
        elif tf_config["source"] == "influx":
            bars_df = self._query_influx_bars(ts_code, tf_config["freq"], start, end, limit)
            if (bars_df is None or bars_df.empty) and not is_index:
                # 分钟线无数据，尝试从 InfluxDB 查更低级别再聚合
                logger.info(f"{ts_code} 的 {tf_config['freq']} 分钟线数据为空，当前不可用")
                fallback_msg = f"{tf_config['freq']} 分钟线数据未采集，请先运行数据采集任务"
        elif tf_config["source"] == "timescale":
            actual_table = tf_config["table"]
            # ETF 只支持日线（周/月需要从日线聚合）
            if is_etf and actual_table in ("stock_weekly", "stock_monthly"):
                logger.info(f"ETF {ts_code} 不支持 {actual_table}，将从 etf_daily 聚合")
                daily_bars = self._query_timescale_bars(ts_code, "etf_daily", start, end, limit * 22 if actual_table == "stock_monthly" else limit * 5)
                if daily_bars is not None and not daily_bars.empty:
                    if actual_table == "stock_weekly":
                        bars_df = self._aggregate_df_to_weekly(daily_bars, limit)
                    elif actual_table == "stock_monthly":
                        bars_df = self._aggregate_df_to_monthly(daily_bars, limit)
                fallback_msg = "ETF周/月数据通过日线聚合生成"
            else:
                bars_df = self._query_timescale_bars(ts_code, actual_table, start, end, limit)

            # 周/月K线为空时：从日K线自动聚合（非指数、非ETF）
            if (bars_df is None or bars_df.empty) and not is_index and not is_etf:
                actual_table = tf_config.get("table", "")
                if actual_table == "stock_weekly":
                    logger.info(f"{ts_code} 周K表为空，从日K自动聚合")
                    bars_df = self._derive_weekly_from_daily(ts_code, limit)
                    fallback_msg = "周线数据通过日线聚合生成"
                elif actual_table == "stock_monthly":
                    logger.info(f"{ts_code} 月K表为空，从日K自动聚合")
                    bars_df = self._derive_monthly_from_daily(ts_code, limit)
                    fallback_msg = "月线数据通过日线聚合生成"

        if bars_df is None or bars_df.empty:
            # ETF 日线为空时使用模拟数据（确保前端有内容可看）
            if is_etf and timeframe == "day":
                logger.info(f"ETF {ts_code} 日线数据为空，使用模拟K线")
                bars_df = self._mock_kline(ts_code, "day", limit)
                fallback_msg = "ETF日线数据暂未采集，当前为模拟数据"
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

        # 股票/ETF信息
        stock_info = None
        if is_etf:
            stock_info = await self.get_etf_info(ts_code)
        else:
            stock_info = await self.get_stock_info(ts_code)

        return {
            "ts_code": ts_code,
            "name": stock_info.get("name", ts_code) if stock_info else ts_code,
            "timeframe": timeframe,
            "bars": bars,
            "indicators": indicator_data,
            "count": len(bars),
            "stock_info": stock_info,
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
                    for p in [5, 10, 20, 60]:
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
            for col in ["open", "high", "low", "close", "vol", "amount"]:
                val = row.get(col)
                if isinstance(val, (np.integer, np.floating)):
                    bar[col] = round(float(val), 4) if col != "vol" else int(val)
                elif val is None or (isinstance(val, float) and np.isnan(val)):
                    bar[col] = None
                else:
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

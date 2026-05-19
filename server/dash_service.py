"""
Dash Service - 股票行情看板服务层
负责：K线数据查询（日/周/月/分钟线）、技术指标计算、股票搜索

数据来源：
  - TimescaleDB: stock_daily (日K), stock_weekly (周K), stock_monthly (月K)
  - InfluxDB: stock_bar_mins (分钟K线: 1m/5m/15m/30m/60m)
  - PostgreSQL: ref_stock_basic (股票主档)
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
        """获取核心指数最新快照"""
        if not self.storage:
            return self._mock_index_snapshots()

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

        if not self.storage:
            bars_df = self._mock_kline(ts_code, timeframe, limit)
        elif tf_config["source"] == "influx":
            bars_df = self._query_influx_bars(ts_code, tf_config["freq"], start, end, limit)
        elif tf_config["source"] == "timescale":
            bars_df = self._query_timescale_bars(ts_code, tf_config["table"], start, end, limit)

        if bars_df is None or bars_df.empty:
            return {
                "ts_code": ts_code,
                "timeframe": timeframe,
                "bars": [],
                "indicators": {},
                "count": 0,
            }

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

        # 股票信息
        stock_info = await self.get_stock_info(ts_code)

        return {
            "ts_code": ts_code,
            "name": stock_info.get("name", ts_code) if stock_info else ts_code,
            "timeframe": timeframe,
            "bars": bars,
            "indicators": indicator_data,
            "count": len(bars),
            "stock_info": stock_info,
        }

    # ─── 指数代码判断 ──────────────────────────────────

    # 已知核心指数代码（可根据实际情况扩展）
    _INDEX_CODES = {
        "000001.SH", "000016.SH", "000300.SH", "000688.SH", "000905.SH",
        "000852.SH", "399001.SZ", "399005.SZ", "399006.SZ", "399673.SZ",
    }

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
            # 检测是否为指数代码，自动切换到 index_daily 表
            actual_table = table
            if table in ("stock_daily", "stock_weekly", "stock_monthly") and self._is_index_code(ts_code):
                # 指数只有日线，周线/月线也回退到日线聚合
                if table == "stock_daily":
                    actual_table = "index_daily"
                else:
                    # 周线/月线按指数日线做聚合
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
        """生成模拟K线数据"""
        np.random.seed(hash(ts_code) % 2**32)
        periods_map = {
            "1m": 240, "5m": 48, "15m": 16, "30m": 8, "60m": 4,
            "day": 1, "week": 1 / 5, "month": 1 / 22,
        }
        periods_per_day = periods_map.get(timeframe, 1)
        days_back = max(1, int(limit / max(periods_per_day, 1)))

        freq_map = {
            "1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min", "60m": "1h",
            "day": "1D", "week": "1W", "month": "1M",
        }

        freq = freq_map.get(timeframe, "1D")
        dates = pd.date_range(end=datetime.now(), periods=min(limit, 500), freq=freq)

        price = 50.0
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
                "open": round(open_p, 2),
                "high": round(high_p, 2),
                "low": round(low_p, 2),
                "close": round(close_p, 2),
                "vol": vol,
                "amount": round(close_p * vol, 2),
            })
            price = close_p

        return pd.DataFrame(bars)


# 全局实例
dash_service = DashService()

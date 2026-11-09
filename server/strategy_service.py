"""
Strategy Service - 策略分析数据与感知
从 PostgreSQL stock_daily / index_daily 读取真实行情，计算特征与市况 regime
策略工具箱目录：STRATEGY_CATALOG（前端 GET /strategy/catalog 拉取）
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from sqlalchemy import text as sa_text

from data_layer.db_manager import storage_manager
from server.dash_service import calc_ma, calc_ema, calc_rsi, calc_macd, calc_bollinger

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 策略工具箱目录（前端 GET /strategy/catalog 拉取）
# 新增策略：在本列表追加一条 dict 即可，无需改前端。
# ---------------------------------------------------------------------------
STRATEGY_CATALOG: List[Dict[str, Any]] = [
    {
        "id": "dual_ma",
        "name": "双均线动量",
        "category": "动量",
        "desc": "快线上穿慢线买入，下穿卖出。",
        "suitable_regime": "trend_up / trend_down",
        "suitable_regimes": ["trend_up", "trend_down"],
        "logic": "信号：MA_fast 上穿 MA_slow → 多；下穿 → 平仓。适合趋势市。",
        "params": [
            {"name": "fast", "label": "快线周期", "min": 2, "max": 60, "step": 1, "default": 5},
            {"name": "slow", "label": "慢线周期", "min": 5, "max": 200, "step": 1, "default": 20},
        ],
    },
    {
        "id": "mean_reversion",
        "name": "均值回归",
        "category": "均值回归",
        "desc": "价格偏离均线过大后反向交易。",
        "suitable_regime": "range",
        "suitable_regimes": ["range"],
        "logic": "偏离度 = (close-MA)/MA；低于 -z 买入，高于 +z 卖出。",
        "params": [
            {"name": "window", "label": "均线窗口", "min": 5, "max": 120, "default": 20},
            {"name": "z", "label": "偏离阈值%", "min": 1, "max": 15, "step": 0.5, "default": 3},
        ],
    },
    {
        "id": "rsi_revert",
        "name": "RSI 超买超卖",
        "category": "均值回归",
        "desc": "RSI 超卖买入、超买卖出。",
        "suitable_regime": "range",
        "suitable_regimes": ["range"],
        "logic": "RSI < oversold 开多；RSI > overbought 平仓。",
        "params": [
            {"name": "period", "label": "RSI 周期", "min": 5, "max": 30, "default": 14},
            {"name": "oversold", "label": "超卖", "min": 10, "max": 40, "default": 30},
            {"name": "overbought", "label": "超买", "min": 60, "max": 90, "default": 70},
        ],
    },
    {
        "id": "breakout",
        "name": "通道突破",
        "category": "突破",
        "desc": "突破 N 日最高价做多。",
        "suitable_regime": "trend_up",
        "suitable_regimes": ["trend_up"],
        "logic": "收盘价 > 近 N 日最高 → 买入；跌破近 M 日最低 → 卖出。",
        "params": [
            {"name": "entry_n", "label": "突破窗口", "min": 5, "max": 60, "default": 20},
            {"name": "exit_n", "label": "退出窗口", "min": 3, "max": 40, "default": 10},
        ],
    },
    {
        "id": "vol_filter",
        "name": "波动率过滤",
        "category": "过滤",
        "desc": "高波动时降仓或空仓，可与主策略组合。",
        "suitable_regime": "high_vol（规避）",
        "suitable_regimes": [],
        "avoid_regimes": ["high_vol"],
        "logic": "波动分位 > 阈值则禁止开仓，仅允许减仓。",
        "params": [
            {"name": "vol_window", "label": "波动窗口", "min": 10, "max": 60, "default": 20},
            {"name": "pctile", "label": "分位阈值", "min": 50, "max": 95, "default": 80},
        ],
    },
    {
        "id": "short_swing",
        "name": "短线心得规则包",
        "category": "规则",
        "desc": "基于短线炒股心得的轮动与风控规则（示意）。",
        "suitable_regime": "轮动市",
        "suitable_regimes": ["range", "trend_up"],
        "logic": "隔夜浮盈减仓、均线上方卖出、中间时段只卖不买等规则组合（演示版用简化信号）。",
        "params": [
            {"name": "take_half", "label": "隔夜减仓%", "min": 30, "max": 70, "default": 50},
            {"name": "max_hold_days", "label": "最长持仓天", "min": 1, "max": 10, "default": 3},
        ],
    },
    {
        "id": "macd_cross",
        "name": "MACD 金叉死叉",
        "category": "动量",
        "desc": "DIF 上穿 DEA 做多，下穿平仓。",
        "suitable_regime": "trend_up / trend_down",
        "suitable_regimes": ["trend_up", "trend_down"],
        "logic": "MACD 柱由负转正（金叉）开多；由正转负（死叉）平仓。",
        "params": [
            {"name": "fast", "label": "快线 EMA", "min": 5, "max": 20, "default": 12},
            {"name": "slow", "label": "慢线 EMA", "min": 15, "max": 40, "default": 26},
            {"name": "signal", "label": "信号线", "min": 5, "max": 15, "default": 9},
        ],
    },
    {
        "id": "boll_revert",
        "name": "布林带回归",
        "category": "均值回归",
        "desc": "触及下轨买入、上轨卖出。",
        "suitable_regime": "range",
        "suitable_regimes": ["range"],
        "logic": "收盘 < 下轨开多；收盘 > 上轨或回到中轨平仓。",
        "params": [
            {"name": "window", "label": "窗口", "min": 10, "max": 40, "default": 20},
            {"name": "nstd", "label": "标准差倍数", "min": 1, "max": 3, "step": 0.1, "default": 2},
        ],
    },
    {
        "id": "structure_pullback",
        "name": "趋势回踩结构止损",
        "category": "短线",
        "desc": "升势里缩量回踩 MA10，次日开盘买，结构低点止损，最多持 5 日。",
        "suitable_regime": "trend_up",
        "suitable_regimes": ["trend_up"],
        "logic": (
            "收盘确认信号：MA10>MA20 且收盘在 MA20 上方（升势未坏），"
            "当日低点碰到 MA10（回踩）、成交量不超过前 5 日均量（缩量）、"
            "收盘相对近 10 日结构低点的风险不超过 4%。"
            "次日开盘买入；开盘相对信号收盘高开超过 1% 则放弃追价。"
            "A 股 T+1：进场当天不能卖，止损最早从进场后第二个交易日开始。"
            "浮盈达到 2R 后，止损上移为「持仓期最高价 − 2.5×前一日 ATR」。"
            "收盘跌破 MA10 且放量（>1.5×前 5 日均量）则次日开盘离场；"
            "否则持满 5 个交易日按收盘近似集合竞价卖出。"
            "同一标的同时只持一笔。"
        ),
        "profit": (
            "怎么赚钱：只在已经走升势的票里等缩量回踩，买的是趋势延续而不是抄底。"
            "结构低点把单笔亏损卡在约 4% 以内；涨起来后用 ATR 移动止损锁利润；"
            "趋势走坏（跌破 MA10 且放量）立刻认错。盈亏来自「小亏快砍 + 趋势反弹吃一段」，"
            "不是靠猜涨停。回测已扣佣金和印花税；不含涨跌停无法成交和停牌，只作参考。"
        ),
        "params": [
            {"name": "max_holding", "label": "最长持仓日", "min": 2, "max": 10, "step": 1, "default": 5},
            {"name": "atr_multiple", "label": "移动止损 ATR 倍数", "min": 1, "max": 5, "step": 0.1, "default": 2.5},
            {"name": "trigger_r", "label": "启动移动止损的 R", "min": 1, "max": 4, "step": 0.1, "default": 2.0},
            {"name": "slippage_bps", "label": "滑点(bp)", "min": 0, "max": 50, "step": 1, "default": 5},
        ],
    },
    {
        "id": "ma60_shrink",
        "name": "MA60均线判断买入点和卖出点",
        "category": "短线",
        "desc": "同一策略两套机制：价在MA60下方做折价短线；价在上方确认升势后拿主升浪，高位放量十字星再兑现。",
        "suitable_regime": "trend_down / trend_up",
        "suitable_regimes": ["trend_down", "trend_up", "range"],
        "logic": (
            "机制A（收盘价 < MA60）："
            "缩量折价买（次日开盘）；深折价≥15%且量远超前5日则当天买；"
            "反弹到MA20且放量则短线卖；深折价仓在站上MA5、量超前几日且收长阴时卖。"
            "此机制下，若尚未确认升势，价格一旦远离均线并放量仍按远超规则兑现。"
            "机制B（收盘价 > MA60，且近10日中至少8日MA60斜率为正）："
            "关闭「远超MA60放量卖」和「远离MA5放量清仓」。"
            "斜率刚转正当天可买；贴近MA60、站上MA5/MA20且不放量可开仓/补仓。"
            "卖出：相对近60日成交量中位数放量，且最高价超过MA5，并相对MA60已到高位，"
            "同时收出十字星（阳十字、阴十字都算），当天卖出。"
            "高位十字星兑现后，在升势再次确认前不再用机制A去接主升浪后的阴跌。"
            "A股T+1：买入当天不能卖。同一标的同时只做这一套仓位。"
        ),
        "profit": (
            "怎么赚钱：均线下方低吸短线波段；均线上方确认升势后拿趋势，"
            "不再因为加速远离均线而卖掉主升浪；高位相对60日量能放量十字星再兑现，回踩贴近均线再补。"
            "回测已扣佣金和印花税；不含涨跌停无法成交和停牌，只作参考。"
        ),
        "params": [
            {"name": "min_dev", "label": "MA60折价阈值%", "min": 1, "max": 15, "step": 0.5, "default": 3},
            {"name": "crash_dev", "label": "深折价放量买%", "min": 8, "max": 30, "step": 1, "default": 15},
            {"name": "long_yin_pct", "label": "长阴实体%", "min": 1, "max": 10, "step": 0.5, "default": 3},
            {"name": "body_pct", "label": "开收盘最大差%", "min": 0.5, "max": 8, "step": 0.5, "default": 3},
            {"name": "vol_expand", "label": "放量倍数", "min": 1.1, "max": 4, "step": 0.1, "default": 1.8},
            {"name": "ma60_stretch", "label": "机制A远超MA60卖%", "min": 3, "max": 40, "step": 0.5, "default": 25},
            {"name": "ma5_stretch", "label": "机制A远离MA5%", "min": 2, "max": 30, "step": 0.5, "default": 20},
            {"name": "ma60_near", "label": "机制B贴近MA60%", "min": 0.5, "max": 5, "step": 0.5, "default": 2},
            {"name": "add_pct", "label": "补仓比例%", "min": 5, "max": 50, "step": 1, "default": 15},
            {"name": "max_adds", "label": "最多补仓次数", "min": 0, "max": 5, "step": 1, "default": 2},
            {"name": "position_pct", "label": "开仓仓位%", "min": 10, "max": 100, "step": 5, "default": 100},
            {"name": "slope_days", "label": "MA60斜率回看日", "min": 3, "max": 20, "step": 1, "default": 5},
            {"name": "slope_window", "label": "升势确认窗口", "min": 5, "max": 20, "step": 1, "default": 10},
            {"name": "slope_hits", "label": "窗口内斜率为正天数", "min": 5, "max": 20, "step": 1, "default": 8},
            {"name": "ma5_doji_excess", "label": "机制B高点超MA5点数%", "min": 3, "max": 20, "step": 0.5, "default": 4},
            {"name": "b_ma60_high", "label": "机制B高位相对MA60%", "min": 10, "max": 80, "step": 5, "default": 35},
            {"name": "doji_body_pct", "label": "十字星实体上限%", "min": 0.3, "max": 3, "step": 0.1, "default": 1.8},
        ],
    },
]


class StrategyService:
    def __init__(self):
        self._engine = None

    @property
    def engine(self):
        if self._engine is None:
            self._engine = storage_manager.get_postgres_engine()
        return self._engine

    def get_catalog(self, category: Optional[str] = None) -> Dict[str, Any]:
        """返回策略工具箱目录（可按 category 过滤）"""
        items = list(STRATEGY_CATALOG)
        if category:
            cat = category.strip()
            items = [s for s in items if s.get("category") == cat]
        categories = sorted({s.get("category") or "未分类" for s in STRATEGY_CATALOG})
        return {
            "ok": True,
            "count": len(items),
            "categories": categories,
            "strategies": items,
        }

    def get_strategy(self, strategy_id: str) -> Optional[Dict[str, Any]]:
        sid = (strategy_id or "").strip()
        for s in STRATEGY_CATALOG:
            if s.get("id") == sid:
                return s
        return None

    def _read_bars(
        self,
        ts_code: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
        limit: int = 500,
        table: str = "stock_daily",
    ) -> pd.DataFrame:
        if not self.engine:
            return pd.DataFrame()

        if table == "stock_daily" and (
            ts_code in ("000001.SH", "000300.SH", "000016.SH", "000905.SH", "000852.SH", "000688.SH")
            or ts_code.startswith("399")
        ):
            table = "index_daily"

        sql = f"""
            SELECT ts_code, trade_date, open, high, low, close, pre_close,
                   change, pct_chg, vol, amount
            FROM {table}
            WHERE ts_code = :code
        """
        params: Dict[str, Any] = {"code": ts_code}
        if start:
            sql += " AND trade_date >= :start"
            params["start"] = start
        if end:
            sql += " AND trade_date <= :end"
            params["end"] = end
        sql += " ORDER BY trade_date DESC LIMIT :lim"
        params["lim"] = limit

        try:
            df = pd.read_sql(sa_text(sql), self.engine, params=params)
        except Exception as e:
            logger.warning(f"查询 {table} 失败: {e}")
            return pd.DataFrame()

        if df.empty:
            return df
        df = df.sort_values("trade_date").reset_index(drop=True)
        for col in ("open", "high", "low", "close", "pre_close", "change", "pct_chg", "vol", "amount"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d")
        return df

    def _detect_regime(self, close: pd.Series, vol_window: int = 20) -> Dict[str, Any]:
        if len(close) < max(60, vol_window + 5):
            return {"regime": "unknown", "label": "样本不足", "detail": f"仅 {len(close)} 根K线"}

        ma20 = calc_ma(close, 20)
        ma60 = calc_ma(close, 60)
        ret = close.pct_change()
        vol = ret.rolling(vol_window).std() * np.sqrt(252)
        vol_pctile = float(vol.rank(pct=True).iloc[-1]) if vol.notna().sum() > 5 else 0.5

        last = float(close.iloc[-1])
        m20 = float(ma20.iloc[-1]) if pd.notna(ma20.iloc[-1]) else last
        m60 = float(ma60.iloc[-1]) if pd.notna(ma60.iloc[-1]) else last

        if vol_pctile >= 0.8:
            regime, label = "high_vol", "高波动"
        elif last > m20 > m60:
            regime, label = "trend_up", "上升趋势"
        elif last < m20 < m60:
            regime, label = "trend_down", "下降趋势"
        else:
            regime, label = "range", "震荡市"

        return {
            "regime": regime,
            "label": label,
            "vol_percentile": round(vol_pctile, 4),
            "ma20": round(m20, 4),
            "ma60": round(m60, 4),
            "last_close": round(last, 4),
            "detail": f"收盘{last:.3f} / MA20={m20:.3f} / MA60={m60:.3f} / 波动分位={vol_pctile:.1%}",
        }

    async def perceive(
        self,
        ts_code: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
        limit: int = 500,
        vol_window: int = 20,
        features: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """读取真实日线并生成感知快照"""
        features = features or ["ma", "rsi", "vol_pctile"]
        if not self.engine:
            return {"ok": False, "error": "PostgreSQL 未连接", "source": "postgres"}

        # 挑选式：策略标的缺/旧则先补 Datahubco
        try:
            from server.selective_data_service import selective_data_service
            await selective_data_service.ensure_fresh_if_needed(ts_code)
        except Exception as e:
            logger.warning("perceive 前补数失败 %s: %s", ts_code, e)

        df = self._read_bars(ts_code, start=start, end=end, limit=limit)
        if df.empty:
            return {
                "ok": False,
                "error": f"未找到 {ts_code} 的日线数据（请检查代码或日期区间）",
                "ts_code": ts_code,
                "source": "postgres",
            }

        close = df["close"]
        regime_info = self._detect_regime(close, vol_window=vol_window)

        feature_snapshot: Dict[str, Any] = {}
        if "ma" in features:
            feature_snapshot["ma5"] = _last(calc_ma(close, 5))
            feature_snapshot["ma10"] = _last(calc_ma(close, 10))
            feature_snapshot["ma20"] = _last(calc_ma(close, 20))
            feature_snapshot["ma60"] = _last(calc_ma(close, 60))
        if "ema" in features:
            feature_snapshot["ema12"] = _last(calc_ema(close, 12))
            feature_snapshot["ema26"] = _last(calc_ema(close, 26))
        if "rsi" in features:
            feature_snapshot["rsi14"] = _last(calc_rsi(close, 14))
        if "macd" in features:
            macd = calc_macd(close)
            feature_snapshot["macd_dif"] = _last(macd["DIF"])
            feature_snapshot["macd_dea"] = _last(macd["DEA"])
            feature_snapshot["macd_hist"] = _last(macd["MACD"])
        if "boll" in features:
            boll = calc_bollinger(close)
            feature_snapshot["boll_mid"] = _last(boll["BOLL_MID"])
            feature_snapshot["boll_upper"] = _last(boll["BOLL_UPPER"])
            feature_snapshot["boll_lower"] = _last(boll["BOLL_LOWER"])
        if "vol_pctile" in features:
            feature_snapshot["vol_percentile"] = regime_info.get("vol_percentile")

        name = ts_code
        try:
            ndf = pd.read_sql(
                sa_text("SELECT name FROM ref_stock_basic WHERE ts_code = :c LIMIT 1"),
                self.engine,
                params={"c": ts_code},
            )
            if not ndf.empty and pd.notna(ndf.iloc[0]["name"]):
                name = str(ndf.iloc[0]["name"])
        except Exception:
            pass

        recent = df.tail(10)[["trade_date", "open", "high", "low", "close", "pct_chg", "vol"]].copy()
        recent = recent.replace({np.nan: None})
        bars_preview = recent.to_dict(orient="records")
        for row in bars_preview:
            for k, v in list(row.items()):
                if hasattr(v, "item"):
                    row[k] = v.item()
                elif isinstance(v, float):
                    row[k] = round(v, 4)

        last = df.iloc[-1]
        return {
            "ok": True,
            "source": "postgres",
            "table": "stock_daily/index_daily",
            "ts_code": ts_code,
            "name": name,
            "bar_count": int(len(df)),
            "date_range": {
                "start": str(df["trade_date"].iloc[0]),
                "end": str(df["trade_date"].iloc[-1]),
            },
            "latest": {
                "trade_date": str(last["trade_date"]),
                "close": _num(last["close"]),
                "pct_chg": _num(last.get("pct_chg")),
                "vol": _num(last.get("vol")),
                "amount": _num(last.get("amount")),
            },
            "regime": regime_info,
            "features": feature_snapshot,
            "bars_preview": bars_preview,
        }

    async def get_data_meta(self, ts_code: str) -> Dict[str, Any]:
        """快速查看某标的在库中的数据覆盖范围"""
        if not self.engine:
            return {"ok": False, "error": "PostgreSQL 未连接"}
        try:
            df = pd.read_sql(
                sa_text(
                    "SELECT MIN(trade_date) AS d0, MAX(trade_date) AS d1, COUNT(*) AS cnt "
                    "FROM stock_daily WHERE ts_code = :c"
                ),
                self.engine,
                params={"c": ts_code},
            )
            if df.empty or int(df.iloc[0]["cnt"] or 0) == 0:
                df = pd.read_sql(
                    sa_text(
                        "SELECT MIN(trade_date) AS d0, MAX(trade_date) AS d1, COUNT(*) AS cnt "
                        "FROM index_daily WHERE ts_code = :c"
                    ),
                    self.engine,
                    params={"c": ts_code},
                )
                table = "index_daily"
            else:
                table = "stock_daily"
            row = df.iloc[0]
            return {
                "ok": True,
                "ts_code": ts_code,
                "table": table,
                "count": int(row["cnt"] or 0),
                "min_date": str(row["d0"])[:10] if pd.notna(row["d0"]) else None,
                "max_date": str(row["d1"])[:10] if pd.notna(row["d1"]) else None,
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}


def _last(series: pd.Series):
    if series is None or series.empty:
        return None
    v = series.iloc[-1]
    return _num(v)


def _num(v):
    if v is None or (isinstance(v, float) and np.isnan(v)) or pd.isna(v):
        return None
    if hasattr(v, "item"):
        v = v.item()
    if isinstance(v, float):
        return round(v, 4)
    return v


strategy_service = StrategyService()

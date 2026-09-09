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

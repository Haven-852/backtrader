"""
Strategy Cycle Service - 假设 / 回测 / 反思
与 strategy_service（感知、目录）分离：本文件负责分析循环后半段逻辑。
回测使用 PostgreSQL 真实日线做简化向量回测（非完整 backtrader 引擎）。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from server.dash_service import calc_ma, calc_ema, calc_rsi, calc_macd, calc_bollinger
from server.strategy_service import strategy_service

logger = logging.getLogger(__name__)

REGIME_LABELS = {
    "trend_up": "上升趋势",
    "trend_down": "下降趋势",
    "range": "震荡市",
    "high_vol": "高波动",
    "unknown": "未知",
}

# 回测开关：前端顶栏勾选框由此接口拉取（GET /strategy/options）
BACKTEST_OPTIONS: List[Dict[str, Any]] = [
    {
        "id": "lookback_guard",
        "key": "lookback_guard",
        "label": "前视保护",
        "label_en": "lookback_guard",
        "default": True,
        "recommended": True,
        "description": (
            "开启后：信号在当日收盘生成，次日开盘成交，避免用到未来信息。"
            "关闭后：当日收盘信号按当日收盘价成交，结果更乐观但存在前视偏差。"
        ),
        "on_exec": "next_open",
        "off_exec": "same_close",
        "ui_hint": "建议保持开启，贴近实盘可成交时点",
    },
    {
        "id": "walk_forward",
        "key": "walk_forward",
        "label": "Walk-Forward",
        "label_en": "walk_forward",
        "default": False,
        "recommended": False,
        "description": (
            "开启后：将行情切成多段，分别做训练/测试样本外回测，检验策略稳健性；"
            "结果中返回 walk_forward_rows。关闭则仅做整段一次回测。"
        ),
        "folds": 3,
        "ui_hint": "用于稳健性检验，计算稍慢",
    },
]


class StrategyCycleService:
    """策略循环：假设 → 回测 → 反思"""

    def get_options(self) -> Dict[str, Any]:
        """返回回测开关定义（前视保护 / Walk-Forward），供前端渲染勾选框"""
        return {
            "ok": True,
            "count": len(BACKTEST_OPTIONS),
            "options": list(BACKTEST_OPTIONS),
            "defaults": {o["key"]: o["default"] for o in BACKTEST_OPTIONS},
        }

    # ------------------------------------------------------------------ #
    # 假设
    # ------------------------------------------------------------------ #
    def hypothesize(
        self,
        strategy_id: str,
        regime: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        strat = strategy_service.get_strategy(strategy_id)
        if not strat:
            return {"ok": False, "error": f"未知策略: {strategy_id}"}

        regime = regime or "unknown"
        fit = self._fits_regime(strat, regime)
        label = REGIME_LABELS.get(regime, regime)
        name = strat["name"]
        params = params or {}

        if fit:
            hypothesis = f"市况 {label} 与「{name}」匹配，采用当前参数验证。"
            recommendation = "proceed"
        else:
            avoid = strat.get("avoid_regimes") or []
            if regime in avoid:
                hypothesis = f"市况 {label} 属于「{name}」规避区间，建议换策略或仅做风控过滤。"
                recommendation = "switch"
            else:
                hypothesis = f"市况 {label} 与「{name}」不完全匹配，仍回测以对比记忆库。"
                recommendation = "proceed_with_caution"

        # Agent 可切换的候选
        alt = None
        if recommendation != "proceed":
            for s in strategy_service.get_catalog()["strategies"]:
                if s["id"] != strategy_id and self._fits_regime(s, regime):
                    alt = {"id": s["id"], "name": s["name"]}
                    break

        return {
            "ok": True,
            "strategy_id": strategy_id,
            "strategy_name": name,
            "regime": regime,
            "regime_label": label,
            "fit": fit,
            "recommendation": recommendation,
            "hypothesis": hypothesis,
            "params": params,
            "suggested_strategy": alt,
        }

    @staticmethod
    def _fits_regime(strat: Dict[str, Any], regime: str) -> bool:
        if not regime or regime == "unknown":
            return False
        lst = strat.get("suitable_regimes")
        if isinstance(lst, list) and lst:
            return regime in lst
        text = str(strat.get("suitable_regime") or "")
        return regime in text

    # ------------------------------------------------------------------ #
    # 回测（真实日线 + 简化信号）
    # ------------------------------------------------------------------ #
    async def backtest(
        self,
        strategy_id: str,
        ts_code: str,
        params: Optional[Dict[str, Any]] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
        cash: float = 1_000_000,
        lookback_guard: bool = True,
        walk_forward: bool = False,
        benchmark: str = "000300.SH",
        limit: int = 800,
    ) -> Dict[str, Any]:
        strat = strategy_service.get_strategy(strategy_id)
        if not strat:
            return {"ok": False, "error": f"未知策略: {strategy_id}"}

        params = self._merge_params(strat, params or {})
        df = strategy_service._read_bars(ts_code, start=start, end=end, limit=limit)
        if df.empty or len(df) < 30:
            return {
                "ok": False,
                "error": f"标的 {ts_code} 可用日线不足（需≥30），请检查代码或日期",
            }

        if lookback_guard:
            # 防止用到未来：信号在当日收盘生成，次日开盘成交（用次日 open）
            exec_price = "next_open"
        else:
            exec_price = "same_close"  # 演示用，存在前视

        signals = self._generate_signals(strategy_id, df, params)
        result = self._simulate(
            df,
            signals,
            cash=cash,
            exec_price=exec_price,
            params=params,
        )

        # 基准简单 buy&hold
        bench_ret = 0.0
        bdf = strategy_service._read_bars(benchmark, start=df["trade_date"].iloc[0], end=df["trade_date"].iloc[-1], limit=limit)
        if not bdf.empty and len(bdf) >= 2:
            bench_ret = float(bdf["close"].iloc[-1] / bdf["close"].iloc[0] - 1)

        alpha = float(result["total_return"] - bench_ret)

        walk_rows: List[Dict[str, Any]] = []
        if walk_forward:
            walk_rows = self._walk_forward(df, strategy_id, params, cash, exec_price)

        # 开关执行摘要（前端回测结果区展示）
        guard_meta = next((o for o in BACKTEST_OPTIONS if o["key"] == "lookback_guard"), {})
        wf_meta = next((o for o in BACKTEST_OPTIONS if o["key"] == "walk_forward"), {})
        options_applied = {
            "lookback_guard": {
                "enabled": lookback_guard,
                "label": guard_meta.get("label", "前视保护"),
                "exec_price": exec_price,
                "status": "通过" if lookback_guard else "已关闭（存在前视风险）",
                "detail": (
                    "次日开盘成交，无前视"
                    if lookback_guard
                    else "同日收盘成交，存在前视偏差"
                ),
            },
            "walk_forward": {
                "enabled": walk_forward,
                "label": wf_meta.get("label", "Walk-Forward"),
                "folds": len(walk_rows) if walk_forward else 0,
                "status": "已启用" if walk_forward else "未启用",
                "detail": (
                    f"已完成 {len(walk_rows)} 段滚动样本外检验"
                    if walk_forward
                    else "仅整段回测"
                ),
            },
        }

        return {
            "ok": True,
            "source": "postgres",
            "engine": "vector_sim",
            "strategy_id": strategy_id,
            "strategy_name": strat["name"],
            "ts_code": ts_code,
            "benchmark": benchmark,
            "date_range": {
                "start": str(df["trade_date"].iloc[0]),
                "end": str(df["trade_date"].iloc[-1]),
            },
            "bar_count": int(len(df)),
            "total_return": result["total_return"],
            "max_drawdown": result["max_drawdown"],
            "sharpe": result["sharpe"],
            "win_rate": result["win_rate"],
            "trade_count": result["trade_count"],
            "alpha": round(alpha, 4),
            "benchmark_return": round(bench_ret, 4),
            "equity": result["equity"],
            "equity_dates": result["equity_dates"],
            "trades": result["trades"],
            "lookback_ok": lookback_guard,
            "exec_price": exec_price,
            "walk_forward": walk_forward,
            "walk_forward_rows": walk_rows,
            "options_applied": options_applied,
            "params": params,
        }

    def _merge_params(self, strat: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for p in strat.get("params") or []:
            name = p["name"]
            out[name] = override.get(name, p.get("default"))
        for k, v in override.items():
            if k not in out:
                out[k] = v
        return out

    def _generate_signals(self, strategy_id: str, df: pd.DataFrame, params: Dict[str, Any]) -> pd.Series:
        """返回目标仓位 0/1（日频，收盘后信号）"""
        close = df["close"].astype(float)
        high = df["high"].astype(float) if "high" in df.columns else close
        low = df["low"].astype(float) if "low" in df.columns else close
        n = len(df)
        pos = pd.Series(0.0, index=df.index)

        if strategy_id == "dual_ma":
            fast = int(params.get("fast", 5))
            slow = int(params.get("slow", 20))
            ma_f = calc_ma(close, fast)
            ma_s = calc_ma(close, slow)
            pos = (ma_f > ma_s).astype(float)

        elif strategy_id == "mean_reversion":
            window = int(params.get("window", 20))
            z = float(params.get("z", 3)) / 100.0
            ma = calc_ma(close, window)
            dev = (close - ma) / ma.replace(0, np.nan)
            # 低买高卖：偏离过低持仓，回到均线附近平仓
            hold = False
            vals = []
            for i in range(n):
                d = dev.iloc[i]
                if pd.isna(d) or pd.isna(ma.iloc[i]):
                    vals.append(0.0)
                    continue
                if d < -z:
                    hold = True
                elif d > z or (hold and d >= 0):
                    hold = False
                vals.append(1.0 if hold else 0.0)
            pos = pd.Series(vals, index=df.index)

        elif strategy_id == "rsi_revert":
            period = int(params.get("period", 14))
            oversold = float(params.get("oversold", 30))
            overbought = float(params.get("overbought", 70))
            rsi = calc_rsi(close, period)
            hold = False
            vals = []
            for i in range(n):
                r = rsi.iloc[i]
                if pd.isna(r):
                    vals.append(0.0)
                    continue
                if r < oversold:
                    hold = True
                elif r > overbought:
                    hold = False
                vals.append(1.0 if hold else 0.0)
            pos = pd.Series(vals, index=df.index)

        elif strategy_id == "breakout":
            entry_n = int(params.get("entry_n", 20))
            exit_n = int(params.get("exit_n", 10))
            hold = False
            vals = []
            for i in range(n):
                if i < max(entry_n, exit_n):
                    vals.append(0.0)
                    continue
                hh = high.iloc[i - entry_n : i].max()
                ll = low.iloc[i - exit_n : i].min()
                if close.iloc[i] > hh:
                    hold = True
                elif close.iloc[i] < ll:
                    hold = False
                vals.append(1.0 if hold else 0.0)
            pos = pd.Series(vals, index=df.index)

        elif strategy_id == "vol_filter":
            # 单独作为过滤：低波动才允许满仓（相对均线之上）
            vol_window = int(params.get("vol_window", 20))
            pctile = float(params.get("pctile", 80)) / 100.0
            ret = close.pct_change()
            vol = ret.rolling(vol_window).std()
            rank = vol.rank(pct=True)
            ma20 = calc_ma(close, 20)
            pos = ((rank < pctile) & (close > ma20)).astype(float)

        elif strategy_id == "short_swing":
            max_hold = int(params.get("max_hold_days", 3))
            ma10 = calc_ma(close, 10)
            hold_days = 0
            vals = []
            for i in range(n):
                if i < 10 or pd.isna(ma10.iloc[i]):
                    vals.append(0.0)
                    continue
                # 简化：价格在均线上方开仓，持仓不超过 max_hold
                if hold_days > 0:
                    hold_days += 1
                    if hold_days > max_hold or close.iloc[i] < ma10.iloc[i]:
                        hold_days = 0
                        vals.append(0.0)
                    else:
                        vals.append(1.0)
                else:
                    if close.iloc[i] > ma10.iloc[i] and close.iloc[i] > close.iloc[i - 1]:
                        hold_days = 1
                        vals.append(1.0)
                    else:
                        vals.append(0.0)
            pos = pd.Series(vals, index=df.index)

        elif strategy_id == "macd_cross":
            fast = int(params.get("fast", 12))
            slow = int(params.get("slow", 26))
            signal = int(params.get("signal", 9))
            macd = calc_macd(close, fast=fast, slow=slow, signal=signal)
            dif, dea = macd["DIF"], macd["DEA"]
            pos = (dif > dea).astype(float)

        elif strategy_id == "boll_revert":
            window = int(params.get("window", 20))
            nstd = float(params.get("nstd", 2))
            boll = calc_bollinger(close, period=window, std_dev=nstd)
            mid, upper, lower = boll["BOLL_MID"], boll["BOLL_UPPER"], boll["BOLL_LOWER"]
            hold = False
            vals = []
            for i in range(n):
                if any(pd.isna(x.iloc[i]) for x in (mid, upper, lower)):
                    vals.append(0.0)
                    continue
                c = close.iloc[i]
                if c < lower.iloc[i]:
                    hold = True
                elif c > upper.iloc[i] or (hold and c >= mid.iloc[i]):
                    hold = False
                vals.append(1.0 if hold else 0.0)
            pos = pd.Series(vals, index=df.index)

        else:
            # 未知策略：默认双均线 5/20
            ma_f = calc_ma(close, 5)
            ma_s = calc_ma(close, 20)
            pos = (ma_f > ma_s).astype(float)

        return pos.fillna(0.0)

    def _simulate(
        self,
        df: pd.DataFrame,
        target_pos: pd.Series,
        cash: float,
        exec_price: str,
        params: Dict[str, Any],
    ) -> Dict[str, Any]:
        close = df["close"].astype(float).values
        open_ = df["open"].astype(float).values if "open" in df.columns else close
        dates = df["trade_date"].astype(str).values
        signal = target_pos.fillna(0.0).values

        equity = []
        equity_dates = []
        trades: List[Dict[str, Any]] = []
        position = 0.0  # 股数
        cash_left = float(cash)
        entry_price = 0.0
        wins = 0
        closed = 0

        # 信号在 i 日收盘产生，在 i+1 日执行
        for i in range(len(df)):
            desired = float(signal[i - 1]) if i > 0 and exec_price == "next_open" else float(signal[i])
            if exec_price == "next_open":
                px = float(open_[i])
            else:
                px = float(close[i])

            if px <= 0 or np.isnan(px):
                px = float(close[i]) if close[i] > 0 else 1.0

            target_shares = 0.0
            if desired > 0.5:
                # 满仓（整手近似）
                target_shares = (cash_left + position * px) * 0.98 / px
                target_shares = np.floor(target_shares / 100) * 100

            delta = target_shares - position
            if abs(delta) >= 100:
                side = "买入" if delta > 0 else "卖出"
                cost = delta * px
                cash_left -= cost
                if delta < 0 and entry_price > 0:
                    pnl = (px - entry_price) * abs(delta)
                    closed += 1
                    if pnl > 0:
                        wins += 1
                else:
                    pnl = None
                    entry_price = px
                position = target_shares
                if position <= 0:
                    entry_price = 0.0
                trades.append(
                    {
                        "date": dates[i],
                        "side": side,
                        "price": round(px, 4),
                        "size": int(abs(delta)),
                        "pnl": None if pnl is None else round(float(pnl), 2),
                    }
                )

            nav = cash_left + position * float(close[i])
            equity.append(round(nav / cash, 6))
            equity_dates.append(dates[i])

        eq = np.array(equity, dtype=float)
        if len(eq) < 2:
            return {
                "total_return": 0.0,
                "max_drawdown": 0.0,
                "sharpe": 0.0,
                "win_rate": 0.0,
                "trade_count": 0,
                "equity": [],
                "equity_dates": [],
                "trades": [],
            }

        total_return = float(eq[-1] / eq[0] - 1)
        peak = np.maximum.accumulate(eq)
        dd = (eq - peak) / peak
        max_drawdown = float(dd.min())
        rets = np.diff(eq) / eq[:-1]
        rets = rets[np.isfinite(rets)]
        if len(rets) > 1 and rets.std() > 1e-12:
            sharpe = float(rets.mean() / rets.std() * np.sqrt(252))
        else:
            sharpe = 0.0
        win_rate = float(wins / closed) if closed else 0.0

        # 权益曲线降采样到约 120 点，便于前端展示
        equity_out, dates_out = self._downsample(equity, equity_dates, 120)

        return {
            "total_return": round(total_return, 4),
            "max_drawdown": round(max_drawdown, 4),
            "sharpe": round(sharpe, 2),
            "win_rate": round(win_rate, 4),
            "trade_count": len(trades),
            "equity": equity_out,
            "equity_dates": dates_out,
            "trades": trades[-30:],  # 最近 30 笔
        }

    @staticmethod
    def _downsample(equity: List[float], dates: List[str], max_n: int) -> Tuple[List[float], List[str]]:
        if len(equity) <= max_n:
            return equity, dates
        idx = np.linspace(0, len(equity) - 1, max_n).astype(int)
        return [equity[i] for i in idx], [dates[i] for i in idx]

    def _walk_forward(
        self,
        df: pd.DataFrame,
        strategy_id: str,
        params: Dict[str, Any],
        cash: float,
        exec_price: str,
        folds: int = 3,
    ) -> List[Dict[str, Any]]:
        n = len(df)
        if n < 90:
            return []
        chunk = n // (folds + 1)
        rows = []
        for f in range(folds):
            train_end = chunk * (f + 1)
            test_end = min(n, train_end + chunk)
            test_df = df.iloc[train_end:test_end].reset_index(drop=True)
            if len(test_df) < 20:
                continue
            # 用全样本信号再切片不严谨；这里仅在 test 段独立生成信号
            sig = self._generate_signals(strategy_id, test_df, params)
            sim = self._simulate(test_df, sig, cash=cash, exec_price=exec_price, params=params)
            # train 段粗略收益
            train_df = df.iloc[max(0, train_end - chunk) : train_end].reset_index(drop=True)
            train_ret = 0.0
            if len(train_df) >= 20:
                tsig = self._generate_signals(strategy_id, train_df, params)
                tsim = self._simulate(train_df, tsig, cash=cash, exec_price=exec_price, params=params)
                train_ret = tsim["total_return"]
            rows.append(
                {
                    "fold": f + 1,
                    "train_ret": train_ret,
                    "test_ret": sim["total_return"],
                    "test_dd": sim["max_drawdown"],
                }
            )
        return rows

    # ------------------------------------------------------------------ #
    # 反思
    # ------------------------------------------------------------------ #
    def reflect(
        self,
        backtest_result: Dict[str, Any],
        regime: str = "",
        max_drawdown_pct: float = 20.0,
        min_sharpe: float = 0.5,
        mode: str = "A",
        auto_save_memory: bool = True,
    ) -> Dict[str, Any]:
        if not backtest_result or not backtest_result.get("ok", True):
            # 兼容前端直接传回测结果（已含 metrics）
            if not backtest_result or backtest_result.get("total_return") is None:
                return {"ok": False, "error": "无回测结果"}

        r = backtest_result
        total_return = float(r.get("total_return") or 0)
        max_dd = float(r.get("max_drawdown") or 0)
        sharpe = float(r.get("sharpe") or 0)
        name = r.get("strategy_name") or r.get("strategy_id") or "策略"
        label = REGIME_LABELS.get(regime, regime or "未知")

        success = (
            total_return > 0
            and abs(max_dd) * 100 <= max_drawdown_pct
            and sharpe >= min_sharpe
        )

        if success:
            reflection = (
                f"策略「{name}」在 {label} 下达到风控阈值，可写入记忆供后续假设检索。"
            )
        else:
            reasons = []
            if total_return <= 0:
                reasons.append("收益未为正")
            if abs(max_dd) * 100 > max_drawdown_pct:
                reasons.append(f"回撤 {abs(max_dd)*100:.1f}% > {max_drawdown_pct}%")
            if sharpe < min_sharpe:
                reasons.append(f"夏普 {sharpe} < {min_sharpe}")
            reflection = (
                f"未达 harness 阈值（{'; '.join(reasons) or '综合指标不足'}）。"
                f"建议换参或换策略，并记录失败案例。"
            )

        # 建议下一步改参（第一个参数 +1，供 Agent 使用）
        next_params = dict(r.get("params") or {})
        strat = strategy_service.get_strategy(r.get("strategy_id") or "")
        param_tweak = None
        if not success and strat and strat.get("params"):
            p0 = strat["params"][0]
            cur = float(next_params.get(p0["name"], p0.get("default", 0)))
            step = float(p0.get("step") or 1)
            new_v = min(float(p0.get("max", cur + step)), cur + step)
            next_params[p0["name"]] = new_v
            param_tweak = {"name": p0["name"], "value": new_v}

        memory_item = {
            "id": str(int(datetime.now().timestamp() * 1000)),
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "strategy_name": name,
            "strategy_id": r.get("strategy_id"),
            "ts_code": r.get("ts_code"),
            "regime": regime,
            "total_return": total_return,
            "max_drawdown": max_dd,
            "sharpe": sharpe,
            "success": success,
            "reflection": reflection,
            "mode": mode,
        }

        save_memory = bool(auto_save_memory or mode == "A")

        return {
            "ok": True,
            "success": success,
            "reflection": reflection,
            "regime": regime,
            "regime_label": label,
            "save_memory": save_memory,
            "memory_item": memory_item,
            "param_tweak": param_tweak,
            "next_params": next_params if param_tweak else None,
        }


strategy_cycle_service = StrategyCycleService()

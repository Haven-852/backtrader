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

from server.a_share_fees import (
    DEFAULT_COMMISSION_MIN,
    DEFAULT_COMMISSION_RATE,
    DEFAULT_STAMP_RATE,
    affordable_buy_shares,
    calc_fees,
    fee_summary,
)
from server.dash_service import calc_ma, calc_ema, calc_rsi, calc_macd, calc_bollinger
from server.elder_ladder import plan_ladder_actions, round_lot
from server.strategy_service import strategy_service
from server.structure_pullback import lot_shares
from server.universe_backtest import (
    after_return,
    build_param_grid,
    daily_regime_map,
    decorate_row,
    name_map,
    pair_round_trips,
    pick_liquid_universe,
    read_bars_batch,
    sanitize_codes,
    split_holdout,
    summarize_rows,
    summarize_signal_trips,
)

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
        if strategy_id == "ma60_shrink":
            params["position_mode"] = "percent"
        df = strategy_service._read_bars(ts_code, start=start, end=end, limit=limit)
        min_bars = 80 if strategy_id == "ma60_shrink" else 30
        if lookback_guard:
            exec_price = "next_open"
        else:
            exec_price = "same_close"

        result = self._run_on_df(strategy_id, df, params, cash, exec_price)
        if not result:
            return {
                "ok": False,
                "error": f"标的 {ts_code} 可用日线不足（需≥{min_bars}），请检查代码或日期",
            }

        # 基准简单 buy&hold
        bench_ret = 0.0
        bdf = strategy_service._read_bars(benchmark, start=df["trade_date"].iloc[0], end=df["trade_date"].iloc[-1], limit=limit)
        if not bdf.empty and len(bdf) >= 2:
            bench_ret = float(bdf["close"].iloc[-1] / bdf["close"].iloc[0] - 1)

        alpha = float(result["total_return"] - bench_ret)

        walk_rows: List[Dict[str, Any]] = []
        if walk_forward:
            if strategy_id == "structure_pullback":
                walk_rows = self._walk_forward_structure(df, params, cash)
            elif strategy_id == "ma60_shrink":
                walk_rows = self._walk_forward_ma60(df, params, cash, exec_price)
            else:
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
            "position": {
                "position_pct": float(params.get("position_pct", 30) or 30),
                "max_lots": int(params.get("max_lots") or 0),
                "reserve_pct": float(params.get("reserve_pct", 2) or 2),
                "lot_size": 100,
                "label": "仓位",
                "status": (
                    f"{float(params.get('position_pct', 30) or 30):g}%"
                    + (
                        f" · 最多{int(params.get('max_lots') or 0)}手"
                        if int(params.get("max_lots") or 0) > 0
                        else ""
                    )
                    + (
                        " · 高龄阶梯"
                        if str(params.get("position_mode") or "elder_ladder") == "elder_ladder"
                        else ""
                    )
                ),
                "mode": str(params.get("position_mode") or "elder_ladder"),
                "detail": (
                    "信号开仓后按高龄秘籍加减仓；策略出场信号则全仓卖出。"
                    if str(params.get("position_mode") or "elder_ladder") == "elder_ladder"
                    else "按当时总资产比例开仓，A股整手100股；不足一手则跳过。"
                ),
            },
            "fees": fee_summary(params),
        }

        # K 线供前端标注买卖点（与权益日期对齐）
        bars: List[Dict[str, Any]] = []
        vol_col = "vol" if "vol" in df.columns else ("volume" if "volume" in df.columns else None)
        for _, row in df.iterrows():
            vol_v = None
            if vol_col is not None:
                raw_v = row[vol_col]
                if pd.notna(raw_v):
                    vol_v = float(raw_v)
            bars.append(
                {
                    "date": str(row["trade_date"])[:10],
                    "open": round(float(row["open"]), 4),
                    "high": round(float(row["high"]), 4),
                    "low": round(float(row["low"]), 4),
                    "close": round(float(row["close"]), 4),
                    "volume": vol_v,
                }
            )

        return {
            "ok": True,
            "source": "postgres",
            "engine": "vector_sim",
            "strategy_id": strategy_id,
            "strategy_name": strat["name"],
            "ts_code": ts_code,
            "benchmark": benchmark,
            "date_range": {
                "start": str(df["trade_date"].iloc[0])[:10],
                "end": str(df["trade_date"].iloc[-1])[:10],
            },
            "bar_count": int(len(df)),
            "cash": float(result.get("cash") or cash),
            "final_equity": result.get("final_equity"),
            "total_return": result["total_return"],
            "max_drawdown": result["max_drawdown"],
            "sharpe": result["sharpe"],
            "win_rate": result["win_rate"],
            "trade_count": result["trade_count"],
            "alpha": round(alpha, 4),
            "benchmark_return": round(bench_ret, 4),
            "equity": result["equity"],
            "equity_cash": result.get("equity_cash") or [
                round(float(cash) * float(x), 2) for x in (result.get("equity") or [])
            ],
            "equity_dates": result["equity_dates"],
            "bars": bars,
            "trades": result["trades"],
            "lookback_ok": lookback_guard,
            "exec_price": exec_price,
            "walk_forward": walk_forward,
            "walk_forward_rows": walk_rows,
            "options_applied": options_applied,
            "params": params,
            "fees": result.get("fees") or {},
            "note": (
                "趋势回踩：收盘信号、次日开盘买、T+1 才能卖；"
                "不含涨跌停无法成交/停牌。填充模型为初步近似。"
                if strategy_id == "structure_pullback"
                else (
                    "MA60均线分两套机制。价<MA60：折价短线，远超/放量可卖。"
                    "价>MA60且近10日有8日斜率为正：关闭远超均线卖出，趋势持有，"
                    "回踩贴近MA60可补；放量且最高价超MA5达10个点并收高位十字星则当天卖。"
                    "不含涨跌停/停牌。"
                    if strategy_id == "ma60_shrink"
                    else "图中本金曲线 = 现金 + 持仓市值（按当日收盘计价）；买卖点标注成交价与股数。"
                )
            )
            + (
                f" 仓位按总资产 {float(params.get('position_pct', 30) or 30):g}% 开仓；"
                "持仓后按高龄秘籍加减仓，策略出场则全清。"
                if str(params.get("position_mode") or "elder_ladder") == "elder_ladder"
                else f" 仓位按总资产 {float(params.get('position_pct', 30) or 30):g}% 开仓（整手）。"
            )
            + " 已扣佣金与印花税。",
        }

    @staticmethod
    def _prepare_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame()
        g = df.copy()
        if "volume" not in g.columns and "vol" in g.columns:
            g["volume"] = pd.to_numeric(g["vol"], errors="coerce")
        return g

    @staticmethod
    def _buyhold_return(df: pd.DataFrame) -> float:
        if df is None or len(df) < 2:
            return 0.0
        c0 = float(df["close"].iloc[0] or 0)
        c1 = float(df["close"].iloc[-1] or 0)
        if c0 <= 0:
            return 0.0
        return float(c1 / c0 - 1)

    def _run_on_df(
        self,
        strategy_id: str,
        df: pd.DataFrame,
        params: Dict[str, Any],
        cash: float,
        exec_price: str,
    ) -> Optional[Dict[str, Any]]:
        min_bars = 80 if strategy_id == "ma60_shrink" else 30
        g = self._prepare_ohlcv(df)
        if g.empty or len(g) < min_bars:
            return None
        if strategy_id == "structure_pullback":
            from server.structure_pullback import backtest as sp_backtest, trades_to_sim_result

            dates = [str(d)[:10] for d in g["trade_date"].tolist()]
            trades = sp_backtest(
                g,
                max_holding=int(params.get("max_holding", 5)),
                atr_multiple=float(params.get("atr_multiple", 2.5)),
                trigger_r=float(params.get("trigger_r", 2.0)),
                slippage_bps=float(params.get("slippage_bps", 5)),
            )
            result = trades_to_sim_result(trades, cash, dates, params=params)
        elif strategy_id == "ma60_shrink":
            from server.ma60_shrink_strategy import simulate as ma60_simulate

            result = ma60_simulate(g, cash=cash, params=params, exec_price=exec_price)
        else:
            signals = self._generate_signals(strategy_id, g, params)
            result = self._simulate(
                g,
                signals,
                cash=cash,
                exec_price=exec_price,
                params=params,
            )
        result["buyhold_return"] = round(self._buyhold_return(g), 4)
        return result

    def backtest_universe(
        self,
        strategy_id: str,
        params: Optional[Dict[str, Any]] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
        cash: float = 1_000_000,
        lookback_guard: bool = True,
        n: int = 100,
        board: str = "main",
        ts_codes: Optional[List[str]] = None,
        search: bool = False,
        holdout_frac: float = 0.5,
    ) -> Dict[str, Any]:
        import time

        t0 = time.perf_counter()
        strat = strategy_service.get_strategy(strategy_id)
        if not strat:
            return {"ok": False, "error": f"未知策略: {strategy_id}"}
        if not start or not end:
            return {"ok": False, "error": "请填写开始和结束日期"}
        if not strategy_service.engine:
            return {"ok": False, "error": "PostgreSQL 未连接"}

        params = self._merge_params(strat, params or {})
        if strategy_id == "ma60_shrink":
            params["position_mode"] = "percent"
        exec_price = "next_open" if lookback_guard else "same_close"
        min_bars = 80 if strategy_id == "ma60_shrink" else 30

        universe = sanitize_codes(ts_codes or [])
        meta: List[Dict[str, Any]] = []
        if universe:
            names = name_map(strategy_service.engine, universe)
            meta = [{"ts_code": c, "name": names.get(c, c), "industry": ""} for c in universe]
            source = "custom"
        else:
            meta = pick_liquid_universe(strategy_service.engine, start, end, n=n, board=board)
            universe = [m["ts_code"] for m in meta]
            source = "liquid_start"

        if not universe:
            return {
                "ok": False,
                "error": "股票池为空：库内该区间日线不足，或主板成交额选池没有结果",
            }

        bars_map = read_bars_batch(strategy_service.engine, universe, start, end)
        names = {m["ts_code"]: m.get("name") or m["ts_code"] for m in meta}

        idx_df = strategy_service._read_bars("000300.SH", start=start, end=end, limit=900)
        regime_map = daily_regime_map(idx_df) if not idx_df.empty else {}
        current_regime = "unknown"
        if regime_map:
            last_day = str(idx_df["trade_date"].iloc[-1])[:10]
            current_regime = regime_map.get(last_day, "unknown")

        collected_trips: List[Dict[str, Any]] = []

        def run_params(
            p: Dict[str, Any],
            codes: Optional[List[str]] = None,
            collect_trips: bool = False,
        ) -> List[Dict[str, Any]]:
            rows: List[Dict[str, Any]] = []
            for code in (codes or universe):
                df = bars_map.get(code)
                if df is None or len(df) < min_bars:
                    rows.append(
                        {
                            "ts_code": code,
                            "name": names.get(code, code),
                            "skipped": True,
                            "reason": f"日线不足{min_bars}",
                            "total_return": 0.0,
                            "max_drawdown": 0.0,
                            "trade_count": 0,
                        }
                    )
                    continue
                result = self._run_on_df(strategy_id, df, p, cash, exec_price)
                if not result:
                    rows.append(
                        {
                            "ts_code": code,
                            "name": names.get(code, code),
                            "skipped": True,
                            "reason": "模拟失败",
                            "total_return": 0.0,
                            "max_drawdown": 0.0,
                            "trade_count": 0,
                        }
                    )
                    continue
                if collect_trips:
                    for trip in pair_round_trips(result.get("trades") or []):
                        trip["ts_code"] = code
                        trip["regime"] = regime_map.get(trip.get("entry_date") or "", "unknown")
                        trip["after_sell_5d"] = after_return(df, trip.get("exit_date") or "", 5)
                        collected_trips.append(trip)
                rows.append(decorate_row(code, names.get(code, code), result))
            return rows

        current_rows = run_params(params, collect_trips=True)
        active = [r for r in current_rows if not r.get("skipped")]
        tune_rows, confirm_rows = split_holdout(active, holdout_frac)
        tune_codes = {r["ts_code"] for r in tune_rows}
        confirm_codes = {r["ts_code"] for r in confirm_rows}
        for r in current_rows:
            if r["ts_code"] in tune_codes:
                r["split"] = "寻参"
            elif r["ts_code"] in confirm_codes:
                r["split"] = "确认"

        current_summary = {
            "all": summarize_rows(active),
            "tune": summarize_rows(tune_rows),
            "confirm": summarize_rows(confirm_rows),
        }

        suggested = None
        search_tried = 0
        if search and len(active) >= 8:
            grid = build_param_grid(strategy_id, params)
            best_score = current_summary["tune"]["robustness_score"]
            best_params = params
            best_tune = current_summary["tune"]
            best_confirm = None
            tune_list = [c for c in universe if c in tune_codes]
            confirm_list = [c for c in universe if c in confirm_codes]
            for cand in grid:
                search_tried += 1
                cand_tune = [r for r in run_params(cand, tune_list) if not r.get("skipped")]
                sc = summarize_rows(cand_tune)
                if sc["robustness_score"] > best_score:
                    best_score = sc["robustness_score"]
                    best_params = cand
                    best_tune = sc
                    best_confirm = summarize_rows(
                        [r for r in run_params(cand, confirm_list) if not r.get("skipped")]
                    )
            if best_params is not params and best_confirm is not None:
                confirm_better = best_confirm["robustness_score"] >= current_summary["confirm"]["robustness_score"]
                suggested = {
                    "accepted": bool(confirm_better),
                    "params": {
                        k: best_params.get(k)
                        for k in (
                            "vol_expand", "b_ma60_high", "ma5_doji_excess", "slope_hits",
                            "position_pct", "fast", "slow", "min_dev", "crash_dev",
                            "ma60_stretch", "ma5_stretch", "doji_body_pct",
                        )
                        if k in best_params
                    },
                    "tune": best_tune,
                    "confirm": best_confirm,
                    "note": (
                        "确认半区也更好，可以考虑换成这组参数。"
                        if confirm_better
                        else "寻参半区更好，但确认半区没有变好，视为过拟合，不建议改参数。"
                    ),
                }

        ranked = sorted(active, key=lambda r: float(r.get("calmar") or 0.0), reverse=True)
        tune_trips = [t for t in collected_trips if t.get("ts_code") in tune_codes]
        signal_base = summarize_signal_trips(collected_trips, current_regime)
        advice_src = tune_trips if len(tune_trips) >= 8 else collected_trips
        signal_base["advice"] = summarize_signal_trips(advice_src, current_regime)["advice"]
        signal_base["advice_from"] = "寻参半区" if advice_src is tune_trips else "全部样本"
        signal_base["index"] = "000300.SH"
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        return {
            "ok": True,
            "strategy_id": strategy_id,
            "strategy_name": strat["name"],
            "universe_source": source,
            "universe_n": len(universe),
            "tested_n": len(active),
            "skipped_n": len(current_rows) - len(active),
            "board": board,
            "date_range": {"start": start, "end": end},
            "lookback_ok": lookback_guard,
            "exec_price": exec_price,
            "params": params,
            "summary": current_summary,
            "suggested": suggested,
            "search_tried": search_tried,
            "rows": ranked,
            "signal_stats": signal_base,
            "note": (
                "股票池按回测开始后前20个交易日成交额选，不是按后来谁涨得多。"
                "按代码切成寻参/确认两半；看中位收益、盈利占比和中位回撤，不要只看最赚钱的那一只。"
                "信号有效性按沪深300当日阶段归类（均线只用到当天），建议来自寻参半区；样本不足的格子只观察、不作为交易依据。"
            ),
            "elapsed_ms": elapsed_ms,
        }

    def _merge_params(self, strat: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for p in strat.get("params") or []:
            name = p["name"]
            out[name] = override.get(name, p.get("default"))
        for k, v in override.items():
            if k not in out:
                out[k] = v
        out.setdefault("commission_rate", DEFAULT_COMMISSION_RATE)
        out.setdefault("commission_min", DEFAULT_COMMISSION_MIN)
        out.setdefault("stamp_rate", DEFAULT_STAMP_RATE)
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
        equity_cash = []
        equity_dates = []
        trades: List[Dict[str, Any]] = []
        position = 0.0  # 股数
        cash_left = float(cash)
        entry_price = 0.0
        ref_px = 0.0
        initial_shares = 0.0
        fired_add: set = set()
        fired_sell: set = set()
        wins = 0
        closed = 0
        use_ladder = str(params.get("position_mode") or "elder_ladder") == "elder_ladder"
        commission_total = 0.0
        stamp_total = 0.0

        def _fill(side: str, qty: float, px: float, day: str, reason: str) -> None:
            nonlocal position, cash_left, entry_price, wins, closed, commission_total, stamp_total
            qty = float(qty)
            if qty < 1:
                return
            commission = 0.0
            stamp = 0.0
            if side == "买入":
                affordable = float(affordable_buy_shares(cash_left, px, params))
                qty = min(qty, affordable)
                qty = float(round_lot(qty))
                if qty < 100:
                    return
                amount = qty * px
                commission, stamp = calc_fees("买入", amount, params)
                if amount + commission > cash_left + 1e-6:
                    qty = float(affordable_buy_shares(cash_left, px, params))
                    if qty < 100:
                        return
                    amount = qty * px
                    commission, stamp = calc_fees("买入", amount, params)
                if position <= 0:
                    entry_price = px
                else:
                    entry_price = (entry_price * position + px * qty) / (position + qty)
                cash_left -= amount + commission
                position += qty
                pnl = None
            else:
                qty = min(qty, position)
                if use_ladder and reason != "策略信号清仓" and reason != "上涨100%清仓":
                    qty = float(round_lot(qty))
                if qty < 1:
                    return
                amount = qty * px
                commission, stamp = calc_fees("卖出", amount, params)
                cash_left += amount - commission - stamp
                pnl = ((px - entry_price) * qty if entry_price > 0 else 0.0) - commission - stamp
                closed += 1
                if pnl > 0:
                    wins += 1
                position -= qty
                if position < 1:
                    position = 0.0
                    entry_price = 0.0
            commission_total += commission
            stamp_total += stamp
            trades.append(
                {
                    "date": day,
                    "side": side,
                    "price": round(float(px), 4),
                    "size": int(abs(qty)),
                    "pnl": None if pnl is None else round(float(pnl), 2),
                    "reason": reason,
                    "commission": round(float(commission), 2),
                    "stamp_tax": round(float(stamp), 2),
                    "fee": round(float(commission + stamp), 2),
                    "cash_after": round(float(cash_left), 2),
                    "position_after": int(position),
                }
            )

        # 信号在 i 日收盘产生，在 i+1 日执行
        for i in range(len(df)):
            desired = float(signal[i - 1]) if i > 0 and exec_price == "next_open" else float(signal[i])
            if exec_price == "next_open":
                px = float(open_[i])
            else:
                px = float(close[i])

            if px <= 0 or np.isnan(px):
                px = float(close[i]) if close[i] > 0 else 1.0
            day = str(dates[i])[:10]

            if desired <= 0.5:
                if position >= 1:
                    _fill("卖出", position, px, day, "策略信号清仓")
                    initial_shares = 0.0
                    ref_px = 0.0
                    fired_add.clear()
                    fired_sell.clear()
            elif position < 100:
                equity_now = cash_left + position * px
                wanted = float(lot_shares(equity_now, px, params, cash_left=cash_left))
                if wanted >= 100:
                    _fill("买入", wanted, px, day, "策略开仓")
                    initial_shares = position
                    ref_px = px
                    fired_add.clear()
                    fired_sell.clear()
            elif use_ladder:
                actions, fired_add, fired_sell = plan_ladder_actions(
                    position=position,
                    initial_shares=initial_shares or position,
                    ref_px=ref_px or entry_price or px,
                    px=px,
                    fired_add=fired_add,
                    fired_sell=fired_sell,
                )
                for act in actions:
                    _fill(act["side"], act["qty"], px, day, act["reason"])
                    if position < 1:
                        initial_shares = 0.0
                        ref_px = 0.0
                        fired_add.clear()
                        fired_sell.clear()
                        break
            # percent 模式：已持仓且信号仍在，维持仓位不变

            nav = cash_left + position * float(close[i])
            equity.append(round(nav / cash, 6))
            equity_cash.append(round(nav, 2))
            equity_dates.append(str(dates[i])[:10])

        eq = np.array(equity, dtype=float)
        if len(eq) < 2:
            return {
                "total_return": 0.0,
                "max_drawdown": 0.0,
                "sharpe": 0.0,
                "win_rate": 0.0,
                "trade_count": 0,
                "cash": float(cash),
                "equity": [],
                "equity_cash": [],
                "equity_dates": [],
                "trades": [],
                "fees": {
                    **fee_summary(params),
                    "commission_total": 0.0,
                    "stamp_total": 0.0,
                    "fee_total": 0.0,
                },
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

        # 前端要画完整 K 线对齐曲线：最多保留 800 点（与 bars 上限一致）
        equity_out, dates_out = self._downsample(equity, equity_dates, 800)
        cash_out, _ = self._downsample(equity_cash, equity_dates, 800)

        return {
            "total_return": round(total_return, 4),
            "max_drawdown": round(max_drawdown, 4),
            "sharpe": round(sharpe, 2),
            "win_rate": round(win_rate, 4),
            "trade_count": len(trades),
            "cash": float(cash),
            "final_equity": round(float(equity_cash[-1]), 2) if equity_cash else float(cash),
            "equity": equity_out,
            "equity_cash": cash_out,
            "equity_dates": dates_out,
            "trades": trades[-200:],
            "fees": {
                **fee_summary(params),
                "commission_total": round(commission_total, 2),
                "stamp_total": round(stamp_total, 2),
                "fee_total": round(commission_total + stamp_total, 2),
            },
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
            train_fees: Dict[str, Any] = {}
            if len(train_df) >= 20:
                tsig = self._generate_signals(strategy_id, train_df, params)
                tsim = self._simulate(train_df, tsig, cash=cash, exec_price=exec_price, params=params)
                train_ret = tsim["total_return"]
                train_fees = tsim.get("fees") or {}
            test_fees = sim.get("fees") or {}
            rows.append(
                {
                    "fold": f + 1,
                    "train_ret": train_ret,
                    "test_ret": sim["total_return"],
                    "test_dd": sim["max_drawdown"],
                    "train_fee": train_fees.get("fee_total", 0.0),
                    "test_fee": test_fees.get("fee_total", 0.0),
                    "test_commission": test_fees.get("commission_total", 0.0),
                    "test_stamp": test_fees.get("stamp_total", 0.0),
                }
            )
        return rows

    def _walk_forward_structure(
        self,
        df: pd.DataFrame,
        params: Dict[str, Any],
        cash: float,
        folds: int = 3,
    ) -> List[Dict[str, Any]]:
        from server.structure_pullback import backtest as sp_backtest, trades_to_sim_result

        n = len(df)
        if n < 90:
            return []
        chunk = n // (folds + 1)
        rows = []
        for f in range(folds):
            train_end = chunk * (f + 1)
            test_end = min(n, train_end + chunk)
            test_df = df.iloc[train_end:test_end].reset_index(drop=True)
            if len(test_df) < 30:
                continue
            test_dates = [str(d)[:10] for d in test_df["trade_date"].tolist()]
            test_trades = sp_backtest(
                test_df,
                max_holding=int(params.get("max_holding", 5)),
                atr_multiple=float(params.get("atr_multiple", 2.5)),
                trigger_r=float(params.get("trigger_r", 2.0)),
                slippage_bps=float(params.get("slippage_bps", 5)),
            )
            sim = trades_to_sim_result(test_trades, cash, test_dates, params=params)
            train_df = df.iloc[max(0, train_end - chunk) : train_end].reset_index(drop=True)
            train_ret = 0.0
            train_fees: Dict[str, Any] = {}
            if len(train_df) >= 30:
                train_dates = [str(d)[:10] for d in train_df["trade_date"].tolist()]
                train_trades = sp_backtest(
                    train_df,
                    max_holding=int(params.get("max_holding", 5)),
                    atr_multiple=float(params.get("atr_multiple", 2.5)),
                    trigger_r=float(params.get("trigger_r", 2.0)),
                    slippage_bps=float(params.get("slippage_bps", 5)),
                )
                tsim = trades_to_sim_result(train_trades, cash, train_dates, params=params)
                train_ret = tsim["total_return"]
                train_fees = tsim.get("fees") or {}
            test_fees = sim.get("fees") or {}
            rows.append(
                {
                    "fold": f + 1,
                    "train_ret": train_ret,
                    "test_ret": sim["total_return"],
                    "test_dd": sim["max_drawdown"],
                    "train_fee": train_fees.get("fee_total", 0.0),
                    "test_fee": test_fees.get("fee_total", 0.0),
                    "test_commission": test_fees.get("commission_total", 0.0),
                    "test_stamp": test_fees.get("stamp_total", 0.0),
                }
            )
        return rows

    def _walk_forward_ma60(
        self,
        df: pd.DataFrame,
        params: Dict[str, Any],
        cash: float,
        exec_price: str,
        folds: int = 3,
    ) -> List[Dict[str, Any]]:
        from server.ma60_shrink_strategy import MIN_BARS, simulate as ma60_simulate

        n = len(df)
        if n < MIN_BARS * 2:
            return []
        chunk = n // (folds + 1)
        rows = []
        for f in range(folds):
            train_end = chunk * (f + 1)
            test_end = min(n, train_end + chunk)
            test_df = df.iloc[train_end:test_end].reset_index(drop=True)
            if len(test_df) < MIN_BARS:
                continue
            sim = ma60_simulate(test_df, cash=cash, params=params, exec_price=exec_price)
            train_df = df.iloc[max(0, train_end - chunk) : train_end].reset_index(drop=True)
            train_ret = 0.0
            train_fees: Dict[str, Any] = {}
            if len(train_df) >= MIN_BARS:
                tsim = ma60_simulate(train_df, cash=cash, params=params, exec_price=exec_price)
                train_ret = tsim["total_return"]
                train_fees = tsim.get("fees") or {}
            test_fees = sim.get("fees") or {}
            rows.append(
                {
                    "fold": f + 1,
                    "train_ret": train_ret,
                    "test_ret": sim["total_return"],
                    "test_dd": sim["max_drawdown"],
                    "train_fee": train_fees.get("fee_total", 0.0),
                    "test_fee": test_fees.get("fee_total", 0.0),
                    "test_commission": test_fees.get("commission_total", 0.0),
                    "test_stamp": test_fees.get("stamp_total", 0.0),
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

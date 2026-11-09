import unittest

import pandas as pd

from server.universe_backtest import (
    build_param_grid,
    calmar,
    decorate_row,
    daily_regime_map,
    normalize_signal,
    pair_round_trips,
    robustness_score,
    sanitize_codes,
    split_holdout,
    summarize_rows,
    summarize_signal_trips,
)


class UniverseBacktestUnitTests(unittest.TestCase):
    def test_sanitize_codes(self):
        codes = sanitize_codes(["000001.sz", "bad", "000001.SZ", "600519.SH", "123"])
        self.assertEqual(codes, ["000001.SZ", "600519.SH"])

    def test_split_holdout_stable_not_by_return(self):
        rows = [
            {"ts_code": "600000.SH", "total_return": 2.0},
            {"ts_code": "000001.SZ", "total_return": -0.5},
            {"ts_code": "000002.SZ", "total_return": 0.1},
            {"ts_code": "601398.SH", "total_return": 0.8},
        ]
        tune, confirm = split_holdout(rows, 0.5)
        self.assertEqual([r["ts_code"] for r in tune], ["000001.SZ", "000002.SZ"])
        self.assertEqual([r["ts_code"] for r in confirm], ["600000.SH", "601398.SH"])

    def test_summarize_prefers_breadth_not_single_winner(self):
        peaked = [
            {"total_return": 1.5, "max_drawdown": -0.6, "sharpe": 1.0, "buyhold_return": 1.2, "calmar": 2.5},
            {"total_return": -0.2, "max_drawdown": -0.3, "sharpe": -0.2, "buyhold_return": 0.1, "calmar": -0.6},
            {"total_return": -0.1, "max_drawdown": -0.25, "sharpe": 0.0, "buyhold_return": 0.0, "calmar": -0.4},
        ]
        steady = [
            {"total_return": 0.18, "max_drawdown": -0.08, "sharpe": 1.1, "buyhold_return": 0.05, "calmar": 2.2},
            {"total_return": 0.12, "max_drawdown": -0.07, "sharpe": 0.9, "buyhold_return": 0.04, "calmar": 1.7},
            {"total_return": 0.09, "max_drawdown": -0.06, "sharpe": 0.8, "buyhold_return": 0.03, "calmar": 1.5},
        ]
        s1 = summarize_rows(peaked)
        s2 = summarize_rows(steady)
        self.assertLess(s1["pct_positive"], 0.5)
        self.assertGreater(s2["pct_positive"], 0.9)
        self.assertGreater(s2["robustness_score"], s1["robustness_score"])

    def test_calmar_and_decorate(self):
        self.assertAlmostEqual(calmar(0.5, -0.25), 2.0)
        row = decorate_row(
            "000001.SZ",
            "平安银行",
            {"total_return": 0.2, "max_drawdown": -0.1, "sharpe": 1.2, "win_rate": 0.5, "trade_count": 4, "buyhold_return": 0.05},
        )
        self.assertEqual(row["name"], "平安银行")
        self.assertAlmostEqual(row["calmar"], 2.0)
        self.assertAlmostEqual(row["excess_return"], 0.15)

    def test_ma60_grid_caps_and_avoids_all_in(self):
        grid = build_param_grid("ma60_shrink", {"position_pct": 100, "vol_expand": 1.8})
        self.assertGreaterEqual(len(grid), 2)
        self.assertLessEqual(len(grid), 8)
        others = grid[1:]
        self.assertTrue(any(int(p.get("position_pct") or 0) <= 50 for p in others))

    def test_robustness_score_penalizes_drawdown(self):
        a = robustness_score({"median_return": 0.2, "pct_positive": 0.6, "median_max_drawdown": -0.1})
        b = robustness_score({"median_return": 0.2, "pct_positive": 0.6, "median_max_drawdown": -0.5})
        self.assertGreater(a, b)

    def test_normalize_signal_strips_notes(self):
        self.assertEqual(normalize_signal("MA60缩量折价买（早前萎靡偏离13.1%）", "买入"), "缩量折价买")
        self.assertEqual(normalize_signal("高位阴十字星放量卖出", "卖出"), "高位十字星卖")
        self.assertEqual(normalize_signal("MA60斜率转正当天买", "买入"), "斜率转正当天买")

    def test_pair_round_trips_uses_first_buy_and_flat_sell(self):
        trades = [
            {"side": "买入", "reason": "MA60折价15%放量买", "date": "2025-04-08", "price": 10, "size": 1000, "commission": 0, "position_after": 1000},
            {"side": "买入", "reason": "趋势回踩补仓 贴近MA60 0.8%", "date": "2025-07-03", "price": 11, "size": 100, "commission": 0, "position_after": 1100},
            {"side": "卖出", "reason": "高位阴十字星放量卖出", "date": "2026-02-10", "price": 20, "size": 1100, "pnl": 10000, "commission": 0, "position_after": 0},
        ]
        trips = pair_round_trips(trades)
        self.assertEqual(len(trips), 1)
        self.assertEqual(trips[0]["entry_signal"], "深折价放量买")
        self.assertEqual(trips[0]["exit_signal"], "高位十字星卖")
        self.assertGreater(trips[0]["ret"], 0)

    def test_daily_regime_map_is_causal_trend(self):
        days = pd.date_range("2024-01-02", periods=80, freq="B")
        close = [10 + i * 0.05 for i in range(80)]
        df = pd.DataFrame({"trade_date": days, "close": close})
        m = daily_regime_map(df)
        last = str(days[-1].date())
        self.assertEqual(m[last], "trend_up")
        self.assertEqual(m[str(days[10].date())], "unknown")

    def test_signal_advice_pauses_losing_cell(self):
        trips = []
        for i in range(10):
            trips.append({"entry_signal": "缩量折价买", "exit_signal": "MA20放量兑现", "regime": "trend_down", "ret": -0.08, "after_sell_5d": -0.02})
        for i in range(10):
            trips.append({"entry_signal": "斜率转正当天买", "exit_signal": "高位十字星卖", "regime": "trend_up", "ret": 0.12, "after_sell_5d": -0.04})
        down = summarize_signal_trips(trips, "trend_down", min_n=8)
        up = summarize_signal_trips(trips, "trend_up", min_n=8)
        down_buy = [a for a in down["advice"] if a["signal"] == "缩量折价买"][0]
        up_buy = [a for a in up["advice"] if a["signal"] == "斜率转正当天买"][0]
        self.assertEqual(down_buy["action"], "暂停")
        self.assertEqual(up_buy["action"], "采用")


if __name__ == "__main__":
    unittest.main()

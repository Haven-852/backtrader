"""A 股回测佣金、印花税。"""

from __future__ import annotations

import unittest

import pandas as pd

from server.a_share_fees import affordable_buy_shares, calc_fees, fee_summary
from server.strategy_cycle_service import StrategyCycleService
from server.structure_pullback import trades_to_sim_result


class CalcFeesTest(unittest.TestCase):
    def test_buy_hits_minimum(self):
        commission, stamp = calc_fees("买入", 1000)
        self.assertEqual(commission, 5.0)
        self.assertEqual(stamp, 0.0)

    def test_buy_uses_rate(self):
        commission, stamp = calc_fees("买入", 1_000_000)
        self.assertEqual(commission, 200.0)
        self.assertEqual(stamp, 0.0)

    def test_sell_adds_stamp(self):
        commission, stamp = calc_fees("卖出", 1_000_000)
        self.assertEqual(commission, 200.0)
        self.assertEqual(stamp, 500.0)

    def test_zero_rate_skips_minimum(self):
        commission, stamp = calc_fees(
            "买入", 1000, {"commission_rate": 0, "stamp_rate": 0}
        )
        self.assertEqual(commission, 0.0)
        self.assertEqual(stamp, 0.0)

    def test_wanfen_integer_rate(self):
        commission, stamp = calc_fees(
            "卖出", 100_000, {"commission_rate": 2, "stamp_rate": 5}
        )
        self.assertEqual(commission, 20.0)
        self.assertEqual(stamp, 50.0)

    def test_affordable_leaves_room_for_commission(self):
        qty = affordable_buy_shares(10_000, 10.0)
        amount = qty * 10.0
        commission, _ = calc_fees("买入", amount)
        self.assertGreaterEqual(qty, 100)
        self.assertLessEqual(amount + commission, 10_000 + 1e-6)


class SimulateFeesTest(unittest.TestCase):
    def _bars(self):
        days = pd.date_range("2024-01-02", periods=6, freq="B")
        return pd.DataFrame(
            {
                "trade_date": days,
                "open": 10.0,
                "high": 10.2,
                "low": 9.8,
                "close": 10.0,
                "vol": 1_000_000,
            }
        )

    def test_simulate_deducts_round_trip_fees(self):
        df = self._bars()
        signal = pd.Series([0.0, 1.0, 1.0, 1.0, 0.0, 0.0])
        params = {
            "position_pct": 30,
            "reserve_pct": 0,
            "max_lots": 0,
            "position_mode": "percent",
        }
        svc = StrategyCycleService()
        with_fees = svc._simulate(df, signal, cash=1_000_000, exec_price="next_open", params=params)
        no_fees = svc._simulate(
            df,
            signal,
            cash=1_000_000,
            exec_price="next_open",
            params={**params, "commission_rate": 0, "stamp_rate": 0, "commission_min": 0},
        )
        self.assertGreater(with_fees["fees"]["fee_total"], 0)
        self.assertEqual(no_fees["fees"]["fee_total"], 0)
        self.assertLess(with_fees["final_equity"], no_fees["final_equity"])
        self.assertAlmostEqual(
            no_fees["final_equity"] - with_fees["final_equity"],
            with_fees["fees"]["fee_total"],
            places=2,
        )

    def test_trades_to_sim_result_deducts_fees(self):
        trades = pd.DataFrame(
            [
                {
                    "entry_date": "2024-01-02",
                    "exit_date": "2024-01-05",
                    "entry_price": 10.0,
                    "exit_price": 10.0,
                    "exit_reason": "到期",
                }
            ]
        )
        dates = ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
        params = {"position_pct": 30, "reserve_pct": 0, "position_mode": "percent"}
        result = trades_to_sim_result(trades, 1_000_000, dates, params=params)
        self.assertEqual(result["trade_count"], 1)
        self.assertGreater(result["fees"]["fee_total"], 0)
        self.assertAlmostEqual(result["final_equity"], 1_000_000 - result["fees"]["fee_total"], places=2)
        sell = [t for t in result["trades"] if t["side"] == "卖出"][0]
        self.assertGreater(sell["stamp_tax"], 0)
        self.assertLess(sell["pnl"], 0)

    def test_fee_summary_status(self):
        summary = fee_summary({})
        self.assertIn("佣金万2", summary["status"])
        self.assertIn("印花税万5", summary["status"])


if __name__ == "__main__":
    unittest.main()

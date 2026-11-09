"""A 股整手仓位：按比例开仓，不足一手则 0。"""

from __future__ import annotations

import unittest

from server.structure_pullback import lot_shares


class LotSharesTest(unittest.TestCase):
    def test_default_thirty_percent(self):
        # 100 万 × 30% / 10 元 = 30000 股
        self.assertEqual(lot_shares(1_000_000, 10.0, {"position_pct": 30}), 30000)

    def test_rounds_down_to_lot(self):
        self.assertEqual(lot_shares(1_000_000, 13.0, {"position_pct": 30}), 23000)

    def test_skip_if_less_than_one_lot(self):
        self.assertEqual(lot_shares(1000, 20.0, {"position_pct": 30}), 0)

    def test_max_lots_cap(self):
        self.assertEqual(
            lot_shares(1_000_000, 10.0, {"position_pct": 100, "max_lots": 10}),
            1000,
        )

    def test_cash_left_limits_buy(self):
        self.assertEqual(
            lot_shares(1_000_000, 10.0, {"position_pct": 50, "reserve_pct": 0}, cash_left=5000),
            500,
        )


if __name__ == "__main__":
    unittest.main()

"""高龄交易员仓位阶梯。"""

from __future__ import annotations

import unittest

from server.elder_ladder import plan_ladder_actions, round_lot


class ElderLadderTest(unittest.TestCase):
    def test_round_lot(self):
        self.assertEqual(round_lot(99), 0)
        self.assertEqual(round_lot(250), 200)

    def test_no_action_near_entry(self):
        acts, add, sell = plan_ladder_actions(
            position=30000, initial_shares=30000, ref_px=10, px=9.6,
            fired_add=[], fired_sell=[],
        )
        self.assertEqual(acts, [])
        self.assertEqual(add, set())
        self.assertEqual(sell, set())

    def test_add_on_drawdown_gap(self):
        acts, add, _ = plan_ladder_actions(
            position=30000, initial_shares=30000, ref_px=10, px=7.4,
            fired_add=[], fired_sell=[],
        )
        reasons = [a["reason"] for a in acts]
        self.assertEqual(reasons, ["下跌15%增持10%", "下跌25%增持25%"])
        self.assertEqual(acts[0]["qty"], 3000)
        self.assertEqual(acts[1]["qty"], 7500)
        self.assertEqual(add, {-0.15, -0.25})

    def test_scale_out_gap_up(self):
        acts, _, sell = plan_ladder_actions(
            position=30000, initial_shares=30000, ref_px=10, px=14.0,
            fired_add=[], fired_sell=[],
        )
        reasons = [a["reason"] for a in acts]
        self.assertEqual(reasons, ["上涨25%卖出10%", "上涨35%卖出20%"])
        self.assertEqual(acts[0]["qty"], 3000)
        self.assertEqual(acts[1]["qty"], 6000)
        self.assertEqual(sell, {0.25, 0.35})

    def test_full_clear_at_double(self):
        acts, _, sell = plan_ladder_actions(
            position=12000, initial_shares=30000, ref_px=10, px=20.5,
            fired_add=[], fired_sell=[0.25, 0.35, 0.45, 0.60],
        )
        self.assertEqual(len(acts), 1)
        self.assertEqual(acts[0]["reason"], "上涨100%清仓")
        self.assertEqual(acts[0]["qty"], 12000)
        self.assertIn(1.0, sell)

    def test_levels_fire_once(self):
        first, add, sell = plan_ladder_actions(
            position=30000, initial_shares=30000, ref_px=10, px=8.4,
            fired_add=[], fired_sell=[],
        )
        self.assertEqual(len(first), 1)
        second, add2, _ = plan_ladder_actions(
            position=33000, initial_shares=30000, ref_px=10, px=8.3,
            fired_add=add, fired_sell=sell,
        )
        self.assertEqual(second, [])
        self.assertEqual(add2, {-0.15})


if __name__ == "__main__":
    unittest.main()

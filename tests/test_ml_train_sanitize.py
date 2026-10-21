"""训练表标签、一字/停牌标记、close_location 兜底。"""

import unittest
from datetime import date

import pandas as pd

from tools.ml_train_toolkit import (
    SESSION_HALT,
    SESSION_NORMAL,
    SESSION_ONE_WORD_DOWN,
    SESSION_ONE_WORD_FLAT,
    SESSION_ONE_WORD_UP,
    close_location_filled,
    limit_pct_for,
    next_day_return_labels,
    session_state,
)
from tools.ml_train_sanitize_toolkit import PROBLEMS, MlTrainSanitizeToolkit


class NextDayLabelTest(unittest.TestCase):
    def test_y_high_is_next_high_over_today_close(self):
        close = pd.Series([10.0, 11.0, 12.0])
        high = pd.Series([10.5, 12.0, 13.0])
        low = pd.Series([9.5, 10.5, 11.0])
        y_high, y_low = next_day_return_labels(close, high, low)
        self.assertAlmostEqual(float(y_high.iloc[0]), 12.0 / 10.0 - 1.0)
        self.assertAlmostEqual(float(y_low.iloc[0]), 10.5 / 10.0 - 1.0)
        self.assertAlmostEqual(float(y_high.iloc[1]), 13.0 / 11.0 - 1.0)
        self.assertAlmostEqual(float(y_low.iloc[1]), 11.0 / 11.0 - 1.0)
        self.assertTrue(pd.isna(y_high.iloc[2]))
        self.assertTrue(pd.isna(y_low.iloc[2]))

    def test_not_same_day_open_high(self):
        close = pd.Series([10.0, 10.0])
        high = pd.Series([11.0, 10.2])
        low = pd.Series([9.0, 9.8])
        y_high, _ = next_day_return_labels(close, high, low)
        same_day = (11.0 - 10.0) / 10.0
        self.assertNotAlmostEqual(float(y_high.iloc[0]), same_day)
        self.assertAlmostEqual(float(y_high.iloc[0]), 10.2 / 10.0 - 1.0)


class SessionFlagTest(unittest.TestCase):
    def test_limit_pct(self):
        self.assertAlmostEqual(limit_pct_for("主板"), 0.10)
        self.assertAlmostEqual(limit_pct_for("ST"), 0.05)
        self.assertAlmostEqual(limit_pct_for("科创板"), 0.20)
        self.assertAlmostEqual(limit_pct_for("北交所"), 0.30)
        self.assertAlmostEqual(limit_pct_for("创业板", date(2020, 8, 23)), 0.10)
        self.assertAlmostEqual(limit_pct_for("创业板", date(2020, 8, 24)), 0.20)

    def test_one_word_limit_up(self):
        flag = session_state(
            open_=11.0, high=11.0, low=11.0, close=11.0, prev_close=10.0,
            vol=1000, amount=11000, board_type="主板", trade_date=date(2024, 1, 2),
        )
        self.assertEqual(flag, SESSION_ONE_WORD_UP)

    def test_one_word_limit_down(self):
        flag = session_state(
            open_=9.0, high=9.0, low=9.0, close=9.0, prev_close=10.0,
            vol=1000, amount=9000, board_type="主板", trade_date=date(2024, 1, 2),
        )
        self.assertEqual(flag, SESSION_ONE_WORD_DOWN)

    def test_one_word_flat(self):
        flag = session_state(
            open_=10.0, high=10.0, low=10.0, close=10.0, prev_close=10.0,
            vol=100, amount=1000, board_type="主板",
        )
        self.assertEqual(flag, SESSION_ONE_WORD_FLAT)

    def test_halt_by_flag_or_zero_volume(self):
        self.assertEqual(
            session_state(
                open_=10.0, high=10.0, low=10.0, close=10.0, prev_close=10.0,
                halted=True,
            ),
            SESSION_HALT,
        )
        self.assertEqual(
            session_state(
                open_=10.0, high=10.0, low=10.0, close=10.0, prev_close=10.0,
                vol=0, amount=0,
            ),
            SESSION_HALT,
        )

    def test_normal_bar(self):
        flag = session_state(
            open_=10.0, high=10.5, low=9.8, close=10.2, prev_close=10.0,
            vol=1000, amount=10000, board_type="主板",
        )
        self.assertEqual(flag, SESSION_NORMAL)

    def test_st_five_percent_one_word(self):
        flag = session_state(
            open_=10.5, high=10.5, low=10.5, close=10.5, prev_close=10.0,
            vol=1000, amount=10000, board_type="ST",
        )
        self.assertEqual(flag, SESSION_ONE_WORD_UP)


class CloseLocationTest(unittest.TestCase):
    def test_one_word_is_half(self):
        loc = close_location_filled(
            pd.Series([8.84]), pd.Series([8.84]), pd.Series([8.84])
        )
        self.assertAlmostEqual(float(loc.iloc[0]), 0.5)

    def test_close_at_high(self):
        loc = close_location_filled(
            pd.Series([11.0]), pd.Series([11.0]), pd.Series([10.0])
        )
        self.assertAlmostEqual(float(loc.iloc[0]), 1.0)


class ComputeFeaturesLabelTest(unittest.TestCase):
    def test_compute_features_uses_next_day_labels(self):
        from tools.ml_train_toolkit import MlTrainToolkit

        tk = MlTrainToolkit()
        daily = pd.DataFrame({
            "trade_date": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)],
            "open": [10.0, 10.2, 10.4],
            "high": [10.5, 11.0, 10.8],
            "low": [9.8, 10.0, 10.1],
            "close": [10.1, 10.6, 10.5],
            "pre_close": [10.0, 10.1, 10.6],
            "vol": [1000, 1100, 1200],
            "amount": [10000, 11000, 12000],
            "pct_chg": [1.0, 4.95, -0.94],
        })
        feat = tk.compute_features_one(
            "000001.SZ",
            daily,
            meta={
                "board_type": "主板",
                "industry_code": "BK0475.DC",
                "industry_name": "银行",
                "industry_l1": "银行",
            },
        )
        self.assertAlmostEqual(float(feat["y_high"].iloc[0]), 11.0 / 10.1 - 1.0)
        self.assertAlmostEqual(float(feat["y_low"].iloc[0]), 10.0 / 10.1 - 1.0)
        self.assertTrue(pd.isna(feat["y_high"].iloc[-1]))
        self.assertEqual(feat["session_flag"].iloc[0], SESSION_NORMAL)
        self.assertEqual(int(feat["is_halted"].iloc[0]), 0)


class CatalogTest(unittest.TestCase):
    def test_catalog_covers_label_and_session(self):
        cat = MlTrainSanitizeToolkit().catalog()
        ids = {p["id"] for p in PROBLEMS}
        self.assertIn("P01_LABEL_WRONG", ids)
        self.assertIn("P05_NO_SESSION_FLAG", ids)
        self.assertIn("P06_HALT_GAPS", ids)
        self.assertEqual(cat["label_definition"]["y_high"], "次日最高价 / 当日收盘价 - 1")
        self.assertIn("fix_labels", cat["fix_order"])


if __name__ == "__main__":
    unittest.main()

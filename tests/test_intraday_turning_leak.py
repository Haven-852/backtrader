"""日内高低点：特征不能看见未来，标签必须看见未来，切分不能让标签跨界。"""

import unittest

import numpy as np
import pandas as pd

from server.intraday_turning_service import (
    add_labels,
    add_price_features,
    assign_splits,
    leak_probe,
    resample_5min,
)


def _morning(day: str, n: int = 120, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    start = pd.Timestamp(f"{day} 09:30", tz="Asia/Shanghai")
    times = pd.date_range(start, periods=n, freq="1min")
    price = 20 + np.cumsum(rng.normal(0, 0.02, n))
    price = np.maximum(price, 1)
    return pd.DataFrame({
        "ts_code": "000001.SZ",
        "bar_time": times,
        "open": price,
        "high": price + 0.05,
        "low": price - 0.05,
        "close": price,
        "vol": 1000,
        "amount": price * 1000,
    })


class IntradayTurningLeakTest(unittest.TestCase):
    def test_resample_stays_inside_session(self):
        raw = pd.concat([
            _morning("2026-09-17", seed=1),
            _morning("2026-09-18", seed=2),
        ], ignore_index=True)
        bars = resample_5min(raw)
        self.assertGreaterEqual(len(bars), 40)
        minutes = bars["bar_time"].dt.hour * 60 + bars["bar_time"].dt.minute
        self.assertTrue(((minutes >= 575) & (minutes <= 690)).all())

    def test_future_bar_does_not_change_features(self):
        raw = pd.concat([
            _morning("2026-09-17", seed=3),
            _morning("2026-09-18", seed=4),
            _morning("2026-09-19", seed=5),
        ], ignore_index=True)
        leak_probe(resample_5min(raw))

    def test_same_bar_both_barriers_is_ambiguous(self):
        times = pd.date_range("2026-09-17 09:35", periods=20, freq="5min", tz="Asia/Shanghai")
        close = np.full(20, 10.0)
        high = close + 0.02
        low = close - 0.02
        high[16] = 12
        low[16] = 8
        df = pd.DataFrame({
            "ts_code": "000001.SZ",
            "bar_time": times,
            "day": [t.date() for t in times],
            "open": close, "high": high, "low": low, "close": close,
            "vol": 1000, "amount": 10000,
        })
        feat = add_price_features(df)
        feat["atr"] = 1.0
        labeled = add_labels(feat, horizon=3, mult=0.5)
        self.assertEqual(labeled["label"].iloc[15], "AMBIGUOUS")

    def test_split_embargoes_label_that_crosses_boundary(self):
        times = pd.date_range("2026-09-17 09:35", periods=40, freq="5min", tz="Asia/Shanghai")
        df = pd.DataFrame({
            "ts_code": "000001.SZ",
            "bar_time": times,
            "label": ["UP_FIRST"] * 40,
            "label_end_time": list(times[3:]) + [pd.NaT, pd.NaT, pd.NaT],
        })
        out = assign_splits(df)
        bounds = out.attrs["split_bounds"]
        crossed = out[(out["bar_time"] < bounds["validation_start"]) & (out["label_end_time"] >= bounds["validation_start"])]
        self.assertTrue((crossed["time_split"] == "embargo").all())
        self.assertGreater(len(crossed), 0)


if __name__ == "__main__":
    unittest.main()

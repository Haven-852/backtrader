"""按方法选股：窗口、分类器。"""

from __future__ import annotations

import unittest
from datetime import date, timedelta

import pandas as pd

from server.screener_methods import (
    classify_crash_buy,
    classify_elder,
    classify_ma60_slope,
    classify_vol_halt,
    max_lookback_bars,
    resolve_methods,
)


def _session_days(n: int, start: date = date(2024, 1, 2)) -> list:
    days = []
    d = start
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def _bars(closes, vols=None) -> pd.DataFrame:
    days = _session_days(len(closes))
    rows = []
    for i, (d, c) in enumerate(zip(days, closes)):
        c = float(c)
        vol = float(vols[i]) if vols is not None else 10000.0
        rows.append(
            {
                "ts_code": "000001.SZ",
                "trade_date": d,
                "open": c * 0.999,
                "high": c * 1.01,
                "low": c * 0.99,
                "close": c,
                "vol": vol,
                "volume": vol,
            }
        )
    return pd.DataFrame(rows)


class ScreenerMethodsTests(unittest.TestCase):
    def test_resolve_methods_prefers_explicit_list(self):
        ids = resolve_methods(["ma60-slope", "b-ma5", "nope"], include_a=True, include_b=True)
        self.assertEqual(ids, ["ma60-slope", "b-ma5"])

    def test_resolve_legacy_abc(self):
        ids = resolve_methods(None, include_a=True, include_b=False, include_c=True)
        self.assertEqual(ids, ["base-watch", "c-struct"])

    def test_lookback_uses_longest_method(self):
        self.assertEqual(max_lookback_bars(["b-ma5"]), 40)
        self.assertEqual(max_lookback_bars(["b-ma5", "ma60-slope"]), 90)

    def test_vol_halt_flags_noisy_series(self):
        closes = [10.0]
        for i in range(30):
            closes.append(closes[-1] * (1.08 if i % 2 == 0 else 0.92))
        g = _bars(closes)
        asof = g["trade_date"].iloc[-1]
        hit = classify_vol_halt(g, asof)
        self.assertIsNotNone(hit)
        self.assertEqual(hit["method_id"], "vol-halt")
        self.assertFalse(hit["buyable"])

    def test_vol_halt_skips_quiet_series(self):
        closes = [10.0 + i * 0.01 for i in range(30)]
        g = _bars(closes)
        asof = g["trade_date"].iloc[-1]
        self.assertIsNone(classify_vol_halt(g, asof))

    def test_elder_add_band(self):
        closes = [10.0] * 10 + [10.0 * (0.97 ** i) for i in range(15)]
        g = _bars(closes)
        asof = g["trade_date"].iloc[-1]
        hit = classify_elder(g, asof)
        self.assertIsNotNone(hit)
        self.assertIn("增持", hit.get("elder_band") or hit["reason"])

    def test_crash_buy_needs_deep_discount_volume(self):
        from server.ma60_shrink_strategy import add_features

        days = _session_days(110)
        rows = []
        close = 15.0
        for i, d in enumerate(days):
            if i < 85:
                close *= 0.993
                vol = 8000.0 + i * 50.0
            elif i == 85:
                close *= 0.99
                vol = 80000.0
            else:
                close *= 1.0008
                vol = 9000.0
            rows.append(
                {
                    "ts_code": "000001.SZ",
                    "trade_date": d,
                    "open": close * 0.999,
                    "high": close * 1.004,
                    "low": close * 0.996,
                    "close": close,
                    "vol": vol,
                    "volume": vol,
                }
            )
        g = pd.DataFrame(rows)
        feat = add_features(g)
        crash_days = feat[feat["crash_buy"]]
        self.assertFalse(crash_days.empty)
        asof = pd.Timestamp(crash_days.iloc[0]["trade_date"]).date()
        sliced = g[pd.to_datetime(g["trade_date"]).dt.date <= asof].copy()
        hit = classify_crash_buy(sliced, asof)
        self.assertIsNotNone(hit)
        self.assertEqual(hit["method_id"], "crash-buy")
        self.assertTrue(hit["buyable"])

    def test_ma60_slope_turn(self):
        closes = [15.0 - i * 0.04 for i in range(50)] + [13.0 + i * 0.05 for i in range(45)]
        g = _bars(closes)
        feat_hits = []
        for i in range(70, len(g)):
            asof = g["trade_date"].iloc[i]
            hit = classify_ma60_slope(g.iloc[: i + 1], asof)
            if hit:
                feat_hits.append(hit)
        self.assertTrue(feat_hits, "上升段应出现至少一次 MA60 斜率转正")
        self.assertEqual(feat_hits[0]["method_id"], "ma60-slope")


if __name__ == "__main__":
    unittest.main()

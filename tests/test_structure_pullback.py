"""趋势回踩结构止损：特征、选股、成交规则。"""

from __future__ import annotations

import unittest
from datetime import date, timedelta

import pandas as pd

from server.structure_pullback import add_features, backtest, classify_last
from server.strategy_service import strategy_service


def _session_days(n: int, start: date = date(2024, 1, 2)) -> list:
    days = []
    d = start
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def _uptrend_pullback(n: int = 42) -> pd.DataFrame:
    """慢升 + 缩量回踩 MA10，后面几天反弹，便于进出场。"""
    days = _session_days(n)
    rows = []
    close = 10.0
    for i, d in enumerate(days):
        if i < 22:
            close *= 1.006
            vol = 12000.0
        elif i == 30:
            close *= 0.992
            vol = 5000.0
        elif i > 30:
            close *= 1.012
            vol = 11000.0
        else:
            close *= 1.0015
            vol = 10000.0
        o = close * 0.999
        h = close * 1.006
        low = close * 0.997
        if i == 30:
            low = close * 0.989
        rows.append(
            {
                "ts_code": "000001.SZ",
                "trade_date": d,
                "open": round(o, 4),
                "high": round(max(o, h, close), 4),
                "low": round(min(low, o, close), 4),
                "close": round(close, 4),
                "volume": vol,
            }
        )
    return pd.DataFrame(rows)


class StructurePullbackTests(unittest.TestCase):
    def test_catalog_has_structure_pullback(self):
        item = strategy_service.get_strategy("structure_pullback")
        self.assertIsNotNone(item)
        self.assertEqual(item["name"], "趋势回踩结构止损")
        self.assertIn("次日开盘", item["logic"])
        self.assertIn("怎么赚钱", item.get("profit") or "")

    def test_add_features_marks_candidate(self):
        feat = add_features(_uptrend_pullback())
        self.assertTrue(bool(feat["candidate"].any()))
        hit = feat[feat["candidate"]].iloc[0]
        self.assertGreater(float(hit["ma10"]), float(hit["ma20"]))
        self.assertGreater(float(hit["risk_fraction_signal"]), 0)
        self.assertLessEqual(float(hit["risk_fraction_signal"]), 0.04)
        self.assertLessEqual(float(hit["vol_pullback"]), 1.0)

    def test_classify_last_on_signal_day(self):
        raw = _uptrend_pullback()
        feat = add_features(raw)
        sig = feat[feat["candidate"]].iloc[0]
        asof = pd.Timestamp(sig["trade_date"]).date()
        cut = raw[raw["trade_date"] <= sig["trade_date"]].copy()
        hit = classify_last(cut, asof)
        self.assertIsNotNone(hit)
        self.assertEqual(hit["setup_type"], "C")
        self.assertEqual(hit["signal_stop"], hit["structure_low"])

    def test_classify_last_rejects_wrong_day(self):
        raw = _uptrend_pullback()
        feat = add_features(raw)
        sig = feat[feat["candidate"]].iloc[0]
        asof = pd.Timestamp(sig["trade_date"]).date()
        cut = raw[raw["trade_date"] <= sig["trade_date"]].copy()
        self.assertIsNone(classify_last(cut, asof + timedelta(days=1)))

    def test_backtest_enters_next_open_and_skips_gap(self):
        raw = _uptrend_pullback()
        trades = backtest(raw, max_holding=5, slippage_bps=5)
        self.assertFalse(trades.empty)
        t0 = trades.iloc[0]
        self.assertGreaterEqual(int(t0["holding_sessions"]), 2)
        self.assertIn(
            t0["exit_reason"],
            {
                "stop_or_trail",
                "gap_below_stop",
                "ma10_high_volume",
                "time_exit_close_proxy",
            },
        )

        feat = add_features(raw)
        sig_i = int(feat.index[feat["candidate"]][0])
        gapped = raw.copy()
        gapped.loc[sig_i + 1, "open"] = float(feat.loc[sig_i, "close"]) * 1.03
        gapped.loc[sig_i + 1, "high"] = float(gapped.loc[sig_i + 1, "open"]) * 1.01
        skipped = backtest(gapped, max_holding=5)
        if not skipped.empty:
            first_entry = pd.Timestamp(skipped.iloc[0]["entry_date"]).date()
            gap_day = pd.Timestamp(gapped.loc[sig_i + 1, "trade_date"]).date()
            self.assertNotEqual(first_entry, gap_day)


if __name__ == "__main__":
    unittest.main()

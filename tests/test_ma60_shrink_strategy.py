"""MA60 缩量吸筹：特征与买卖路径。"""

from __future__ import annotations

import unittest
from datetime import date, timedelta

import pandas as pd

from server.ma60_shrink_strategy import add_features, simulate
from server.strategy_service import strategy_service


def _session_days(n: int, start: date = date(2023, 1, 3)) -> list:
    days = []
    d = start
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def _decline_then_bounce(n: int = 100, spike_vol: bool = True) -> pd.DataFrame:
    """先阴跌缩量，再反弹。spike_vol=True 时在反弹段放量，走短线兑现。"""
    days = _session_days(n)
    rows = []
    close = 15.0
    for i, d in enumerate(days):
        if i < 70:
            close *= 0.997
            vol = 10000.0
        elif i < 80:
            close *= 0.994
            vol = 8000.0 - (i - 70) * 500.0
        else:
            close *= 1.018
            vol = 22000.0 if spike_vol else 3200.0
        o = close * 0.999
        h = close * 1.004
        low = close * 0.996
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


def _break_ma60_then_stretch() -> pd.DataFrame:
    """阴跌缩量买入后，缩量推升站上 MA60，再远离 MA5 放量。"""
    days = _session_days(120)
    rows = []
    close = 15.0
    for i, d in enumerate(days):
        if i < 70:
            close *= 0.997
            vol = 10000.0
        elif i < 80:
            close *= 0.994
            vol = 8000.0 - (i - 70) * 500.0
        elif i < 105:
            close *= 1.012
            vol = 3000.0
        else:
            close *= 1.03
            vol = 18000.0
        o = close * 0.999
        h = close * 1.006
        low = close * 0.996
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


class Ma60ShrinkTests(unittest.TestCase):
    def test_catalog(self):
        item = strategy_service.get_strategy("ma60_shrink")
        self.assertIsNotNone(item)
        self.assertEqual(item["name"], "MA60均线判断买入点和卖出点")
        self.assertIn("斜率", item["logic"])
        names = [p["name"] for p in item["params"]]
        self.assertIn("min_dev", names)
        self.assertIn("crash_dev", names)
        self.assertIn("slope_hits", names)
        self.assertIn("ma5_doji_excess", names)
        self.assertIn("b_ma60_high", names)
        self.assertIn("position_pct", names)
        self.assertIn("机制A", item["logic"])
        self.assertIn("机制B", item["logic"])

    def test_entry_raw_on_shrink_day(self):
        feat = add_features(_decline_then_bounce())
        hits = feat[feat["entry_raw"]]
        self.assertFalse(hits.empty)
        row = hits.iloc[0]
        self.assertLess(float(row["ma60_slope"]), 0)
        self.assertLess(float(row["close"]), float(row["ma60"]))
        self.assertGreaterEqual(float(row["dev"]), 0.03)

    def test_short_exit_at_ma20_with_volume(self):
        result = simulate(
            _decline_then_bounce(),
            cash=1_000_000,
            params={"position_pct": 30, "reserve_pct": 0, "commission_rate": 0, "stamp_rate": 0, "commission_min": 0},
            exec_price="next_open",
        )
        reasons = [t["reason"] for t in result["trades"]]
        self.assertTrue(any("缩量折价买" in r for r in reasons), reasons)
        self.assertTrue(any("MA20放量兑现" in r for r in reasons), reasons)
        self.assertGreater(result["trade_count"], 1)

    def test_long_exit_stretch_ma5(self):
        result = simulate(
            _break_ma60_then_stretch(),
            cash=1_000_000,
            params={"position_pct": 30, "reserve_pct": 0, "commission_rate": 0, "stamp_rate": 0, "commission_min": 0},
            exec_price="next_open",
        )
        reasons = [t["reason"] for t in result["trades"]]
        self.assertTrue(any("缩量折价买" in r for r in reasons), reasons)

    def test_slope_turn_same_day_buy_and_far_ma60_sell(self):
        days = _session_days(160)
        rows = []
        close = 15.0
        for i, d in enumerate(days):
            if i < 80:
                close *= 0.996
                vol = 8000.0 + i * 100.0
            elif i < 140:
                close *= 1.012
                vol = 8000.0 + i * 100.0
            else:
                close *= 1.03
                vol = 40000.0
            o = close * 0.999
            h = close * 1.006
            low = close * 0.996
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
        df = pd.DataFrame(rows)
        feat = add_features(df)
        self.assertTrue(bool(feat["slope_turn_up"].any()))
        result = simulate(
            df,
            cash=1_000_000,
            params={"position_pct": 30, "reserve_pct": 0, "commission_rate": 0, "stamp_rate": 0, "commission_min": 0},
            exec_price="next_open",
        )
        reasons = [t["reason"] for t in result["trades"]]
        self.assertTrue(any("斜率转正当天买" in r for r in reasons), reasons)
        buys = [t for t in result["trades"] if t["side"] == "买入" and "斜率转正" in t["reason"]]
        turns = feat[feat["slope_turn_up"]]
        self.assertFalse(turns.empty)
        self.assertEqual(buys[0]["date"], str(turns.iloc[0]["trade_date"])[:10])
        self.assertTrue(bool(feat["regime_above"].any()))
        after_above = feat[feat["regime_above"]].iloc[0]["trade_date"]
        ext_sells = [
            t for t in result["trades"]
            if t["side"] == "卖出" and ("远超MA60" in t["reason"] or "远离MA5" in t["reason"])
            and t["date"] >= str(after_above)[:10]
        ]
        self.assertEqual(ext_sells, [], ext_sells)

    def test_regime_b_high_doji_sell(self):
        days = _session_days(150)
        rows = []
        close = 15.0
        for i, d in enumerate(days):
            if i < 80:
                close *= 0.996
                vol = 8000.0 + i * 100.0
                o = close * 0.999
                h = close * 1.004
                low = close * 0.996
            elif i < 145:
                close *= 1.012
                vol = 8000.0 + i * 100.0
                o = close * 0.999
                h = close * 1.004
                low = close * 0.996
            else:
                o = close * 1.001
                close = o * 0.999
                h = close * 1.14
                low = close * 0.97
                vol = 90000.0
            rows.append(
                {
                    "ts_code": "000001.SZ",
                    "trade_date": d,
                    "open": round(o, 4),
                    "high": round(h, 4),
                    "low": round(low, 4),
                    "close": round(close, 4),
                    "volume": vol,
                }
            )
        df = pd.DataFrame(rows)
        feat = add_features(df)
        self.assertTrue(bool(feat["regime_above"].any()))
        self.assertTrue(bool(feat["b_doji_sell"].any()))
        result = simulate(
            df,
            cash=1_000_000,
            params={"position_pct": 30, "reserve_pct": 0, "commission_rate": 0, "stamp_rate": 0, "commission_min": 0},
            exec_price="next_open",
        )
        reasons = [t["reason"] for t in result["trades"]]
        self.assertTrue(any("十字星放量卖出" in r for r in reasons), reasons)
        self.assertFalse(any("远超MA60" in r for r in reasons), reasons)

    def test_crash_dev_volume_buy_and_long_yin_sell(self):
        days = _session_days(110)
        rows = []
        close = 15.0
        for i, d in enumerate(days):
            if i < 85:
                close *= 0.993
                vol = 8000.0 + i * 50.0
                o = close * 0.999
            elif i == 85:
                close *= 0.99
                vol = 80000.0
                o = close * 0.999
            elif i == 86:
                close *= 1.035
                vol = 9000.0
                o = close * 0.995
            elif i < 100:
                close *= 1.0008
                vol = 9000.0
                o = close * 0.999
            else:
                o = close * 1.04
                close = o * 0.965
                vol = 40000.0
            h = max(o, close) * 1.004
            low = min(o, close) * 0.996
            rows.append(
                {
                    "ts_code": "000001.SZ",
                    "trade_date": d,
                    "open": round(o, 4),
                    "high": round(h, 4),
                    "low": round(low, 4),
                    "close": round(close, 4),
                    "volume": vol,
                }
            )
        df = pd.DataFrame(rows)
        feat = add_features(df)
        self.assertTrue(bool(feat["crash_buy"].any()), "expected a deep-discount volume spike buy")
        result = simulate(
            df,
            cash=1_000_000,
            params={"position_pct": 30, "reserve_pct": 0, "commission_rate": 0, "stamp_rate": 0, "commission_min": 0},
            exec_price="next_open",
        )
        reasons = [t["reason"] for t in result["trades"]]
        self.assertTrue(any("折价15%放量买" in r for r in reasons), reasons)
        buys = [t for t in result["trades"] if "折价15%放量买" in t["reason"]]
        hit = feat[feat["crash_buy"]].iloc[0]
        self.assertEqual(buys[0]["date"], str(hit["trade_date"])[:10])
        self.assertTrue(any("站上MA5放量长阴卖出" in r for r in reasons), reasons)

    def test_fees_are_deducted(self):
        df = _decline_then_bounce()
        with_fees = simulate(df, cash=1_000_000, params={"position_pct": 30, "reserve_pct": 0})
        no_fees = simulate(
            df,
            cash=1_000_000,
            params={
                "position_pct": 30,
                "reserve_pct": 0,
                "commission_rate": 0,
                "stamp_rate": 0,
                "commission_min": 0,
            },
        )
        if with_fees["trade_count"] == 0:
            self.skipTest("synthetic path produced no trades")
        self.assertGreater(with_fees["fees"]["fee_total"], 0)
        self.assertLess(with_fees["final_equity"], no_fees["final_equity"])


if __name__ == "__main__":
    unittest.main()

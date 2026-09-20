"""Synthetic checks for A/B/C/D combination hit-test. Run: python -m agents.strategy.strategy_t0._selftest_hit_combos"""

from __future__ import annotations

from .combo import HIT_COMBOS, combo_flags, merge_plain_clock, parse_hit_combos
from .hit_test import evaluate_day


def _row(clock: str, close: float, **flags) -> dict:
    high = flags.pop("high", close)
    low = flags.pop("low", close)
    base = {
        "clock": clock,
        "bar_time": f"2026-09-15 {clock[:2]}:{clock[2:]}:00",
        "open": close,
        "high": high,
        "low": low,
        "close": close,
        "vol": 1000,
        "sell_a": False,
        "buy_a": False,
        "sell_b": False,
        "buy_b": False,
        "sell_d": False,
        "buy_d": False,
        "reason_a": "A",
        "reason_a_buy": "A",
        "reason_b": "B",
        "reason_b_buy": "B",
        "reason_d": "D",
        "reason_d_buy": "D",
        "x": 1.0,
        "vol_state": "valley",
        "reason_c": "",
    }
    base.update(flags)
    return base


def test_parse_and_flags() -> None:
    assert parse_hit_combos("all") == list(HIT_COMBOS.keys())
    assert parse_hit_combos("a,vote2") == ["a", "vote2"]
    row = {
        "buy_a": True, "sell_a": False,
        "buy_b": False, "sell_b": True,
        "buy_b_c": True, "sell_b_c": True,
        "buy_d": False, "sell_d": True,
        "reason_a_buy": "A", "reason_a": "A",
        "reason_b": "B", "reason_b_buy": "B",
        "reason_b_c": "B+C", "reason_b_c_buy": "B+C",
        "reason_d": "D", "reason_d_buy": "D",
    }
    buy, sell, _, _ = combo_flags(row, "a")
    assert buy and not sell
    buy, sell, _, _ = combo_flags(row, "a_or_bc")
    assert buy and sell
    buy, sell, _, _ = combo_flags(row, "a_and_bc")
    assert buy and not sell
    buy, sell, _, _ = combo_flags(row, "vote2")
    assert buy and sell  # buy 2/3, sell 2/3


def test_evaluate_combos() -> None:
    # low at 0931 close=10, high at 1400 close=12
    plain = [
        _row("0931", 10.0, low=10.0, high=10.2, buy_a=True, buy_b=False),
        _row("1000", 10.5, buy_a=False, sell_b=False),
        _row("1400", 12.0, high=12.0, low=11.8, sell_a=True, sell_b=True),
        _row("1455", 11.7, sell_a=False),
    ]
    clocked = [
        _row("0931", 10.0, low=10.0, high=10.2, buy_b=True, buy_d=True, vol_state="peak", x=2.0),
        _row("1000", 10.5, buy_b=False, buy_d=False),
        _row("1400", 12.0, high=12.0, low=11.8, sell_b=True, sell_d=True, vol_state="peak", x=2.5),
        _row("1455", 11.7, sell_b=False, sell_d=False),
    ]
    scored = evaluate_day(plain, rows_clock=clocked, hit_tol_pct=1.0)
    assert scored["ok"]
    by = {c["combo"]: c for c in scored["combos"]}
    assert by["a"]["hit_low"] and by["a"]["hit_high"]
    assert by["a"]["mark_high"] and by["a"]["mark_low"] and by["a"]["mark_both"]
    assert by["b_c"]["hit_low"] and by["b_c"]["hit_high"]
    assert by["d_c"]["hit_low"] and by["d_c"]["hit_high"]
    assert by["vote2"]["hit_both"]
    assert by["a_and_bc"]["hit_low"]  # 0931 A and B+C both buy
    assert "a" in scored["best_hit_both"]
    assert scored["combo_ranking"][0]["hit_both"] is True
    merged = merge_plain_clock(plain, clocked)
    assert merged[0]["buy_b_c"] is True
    assert merged[2]["sell_b_c"] is True


def test_or_catches_what_single_misses() -> None:
    plain = [
        _row("0931", 10.0, low=10.0, buy_a=False, buy_b=False),
        _row("1000", 10.2, buy_a=True),  # A buy not at low
        _row("1400", 12.0, high=12.0, sell_a=False, sell_b=False),
        _row("1410", 11.99, sell_a=False, sell_b=True),  # B sell near high
    ]
    clocked = [
        _row("0931", 10.0, low=10.0, buy_b=True, buy_d=False),  # B+C buy at low
        _row("1000", 10.2, buy_b=False, buy_d=False),
        _row("1400", 12.0, high=12.0, sell_b=False, sell_d=True),  # D sell at high
        _row("1410", 11.99, sell_b=False, sell_d=False),
    ]
    scored = evaluate_day(plain, rows_clock=clocked, hit_tol_pct=1.0)
    by = {c["combo"]: c for c in scored["combos"]}
    assert not by["a"]["hit_low"]
    assert by["b_c"]["hit_low"]
    assert by["a_or_bc"]["hit_low"]
    assert by["d_c"]["hit_high"]
    assert by["bc_or_d"]["hit_high"]
    assert by["a_and_bc"]["hit_low"] is False


def test_regime() -> None:
    from .regime import classify_regime

    def bars(closes):
        return [{"trade_date": f"2026-08-{i+1:02d}", "open": c, "high": c * 1.01,
                 "low": c * 0.99, "close": c} for i, c in enumerate(closes)]

    up = classify_regime(bars([10 + i * 0.2 for i in range(30)]))
    down = classify_regime(bars([16 - i * 0.15 for i in range(30)]))
    rng = classify_regime(bars([10 + (0.15 if i % 4 < 2 else -0.15) for i in range(30)]))
    assert up["regime"] == "up", up
    assert down["regime"] == "down", down
    assert rng["regime"] == "range", rng


if __name__ == "__main__":
    test_parse_and_flags()
    test_evaluate_combos()
    test_or_catches_what_single_misses()
    test_regime()
    print("ok", len(HIT_COMBOS), "combos")

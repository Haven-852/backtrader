"""用收盘后真实高低，给 A/B/C/D 的因果信号打分。

特征只用到信号当时的价格/成交量；day_high / day_low 只作标签。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from .combo import HIT_COMBOS, combo_flags, merge_plain_clock


def _extreme(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    hi_row = max(rows, key=lambda r: float(r.get("high") if r.get("high") is not None else r["close"]))
    lo_row = min(rows, key=lambda r: float(r.get("low") if r.get("low") is not None else r["close"]))
    day_high = float(hi_row.get("high") if hi_row.get("high") is not None else hi_row["close"])
    day_low = float(lo_row.get("low") if lo_row.get("low") is not None else lo_row["close"])
    return {
        "day_high": day_high,
        "day_high_time": hi_row.get("bar_time"),
        "day_high_clock": hi_row.get("clock"),
        "day_low": day_low,
        "day_low_time": lo_row.get("bar_time"),
        "day_low_clock": lo_row.get("clock"),
        "open": rows[0].get("open") or rows[0]["close"],
        "close": rows[-1]["close"],
        "n": len(rows),
        "high_row": hi_row,
        "low_row": lo_row,
    }


def _gap_pct(price: Optional[float], target: float) -> Optional[float]:
    if price is None or not target:
        return None
    return round((price - target) / target * 100.0, 4)


def _range_pos(price: Optional[float], lo: float, hi: float) -> Optional[float]:
    if price is None:
        return None
    span = hi - lo
    if span <= 1e-9:
        return 0.5
    return round((price - lo) / span, 4)


def _pick_signal(rows: List[Dict[str, Any]], flag: str, target: float, want: str) -> Dict[str, Any]:
    return _pick_pred(rows, lambda r: bool(r.get(flag)), target, flag)


def _pick_pred(
    rows: List[Dict[str, Any]],
    pred: Callable[[Dict[str, Any]], bool],
    target: float,
    reason_key: str = "",
) -> Dict[str, Any]:
    hits = [r for r in rows if pred(r)]
    if not hits:
        return {"has_signal": False, "first": None, "nearest": None}

    reason_map = {
        "sell_a": "reason_a", "sell_b": "reason_b", "sell_d": "reason_d",
        "buy_a": "reason_a_buy", "buy_b": "reason_b_buy", "buy_d": "reason_d_buy",
        "combo_sell": "combo_sell_reason",
        "combo_buy": "combo_buy_reason",
    }

    def view(r: Dict[str, Any]) -> Dict[str, Any]:
        px = float(r["close"])
        return {
            "time": r.get("bar_time"),
            "clock": r.get("clock"),
            "price": px,
            "gap_pct": _gap_pct(px, target),
            "vol": r.get("vol"),
            "reason": r.get(reason_map.get(reason_key, reason_key)) or r.get("combo_reason") or "",
        }

    first = view(hits[0])
    nearest_row = min(hits, key=lambda r: abs(float(r["close"]) - target))
    nearest = view(nearest_row)
    return {"has_signal": True, "first": first, "nearest": nearest, "n_signals": len(hits)}


def _hit(gap_pct: Optional[float], tol: float) -> Optional[bool]:
    if gap_pct is None:
        return None
    return abs(gap_pct) <= tol


def score_c_at_extreme(row: Dict[str, Any]) -> Dict[str, Any]:
    x = row.get("x")
    return {
        "time": row.get("bar_time"),
        "clock": row.get("clock"),
        "close": row.get("close"),
        "vol": row.get("vol"),
        "x": x,
        "vol_state": row.get("vol_state"),
        "on_volume_event": row.get("vol_state") in ("peak", "ridge"),
        "relatively_heavy": bool(x is not None and x >= 1.2),
        "reason": row.get("reason_c"),
    }


def _pack_side(picked: Dict[str, Any], lo: float, hi: float, tol: float) -> Dict[str, Any]:
    first_px = (picked.get("first") or {}).get("price")
    near_px = (picked.get("nearest") or {}).get("price")
    return {
        **picked,
        "first_hit": _hit((picked.get("first") or {}).get("gap_pct"), tol),
        "nearest_hit": _hit((picked.get("nearest") or {}).get("gap_pct"), tol),
        "first_range_pos": _range_pos(first_px, lo, hi),
        "nearest_range_pos": _range_pos(near_px, lo, hi),
    }


def evaluate_combos(
    merged: List[Dict[str, Any]],
    hi: float,
    lo: float,
    hit_tol_pct: float,
    combo_ids: Optional[List[str]] = None,
    high_clock: Optional[str] = None,
    low_clock: Optional[str] = None,
) -> List[Dict[str, Any]]:
    ids = combo_ids or list(HIT_COMBOS.keys())
    out: List[Dict[str, Any]] = []
    for cid in ids:
        tagged: List[Dict[str, Any]] = []
        for r in merged:
            buy, sell, br, sr = combo_flags(r, cid)
            tagged.append({
                **r,
                "combo_buy": buy,
                "combo_sell": sell,
                "combo_buy_reason": br,
                "combo_sell_reason": sr,
            })
        sell = _pack_side(_pick_pred(tagged, lambda x: bool(x.get("combo_sell")), hi, "combo_sell"), lo, hi, hit_tol_pct)
        buy = _pack_side(_pick_pred(tagged, lambda x: bool(x.get("combo_buy")), lo, "combo_buy"), lo, hi, hit_tol_pct)
        hi_bar = _row_by_clock(tagged, high_clock)
        lo_bar = _row_by_clock(tagged, low_clock)
        rec = {
            "combo": cid,
            "title": HIT_COMBOS.get(cid, cid),
            "sell": sell,
            "buy": buy,
            "hit_high": bool(sell.get("nearest_hit")),
            "hit_low": bool(buy.get("nearest_hit")),
            "first_hit_high": bool(sell.get("first_hit")),
            "first_hit_low": bool(buy.get("first_hit")),
            "mark_high": bool(hi_bar and hi_bar.get("combo_sell")),
            "mark_low": bool(lo_bar and lo_bar.get("combo_buy")),
        }
        rec["hit_both"] = rec["hit_high"] and rec["hit_low"]
        rec["first_hit_both"] = rec["first_hit_high"] and rec["first_hit_low"]
        rec["mark_both"] = rec["mark_high"] and rec["mark_low"]
        out.append(rec)
    return out


def _nearest_gap_abs(side: Dict[str, Any]) -> float:
    g = (side.get("nearest") or {}).get("gap_pct")
    return 99.0 if g is None else abs(float(g))


def rank_combos(combo_scores: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ranked = sorted(
        combo_scores,
        key=lambda c: (
            -int(c.get("hit_both") or 0),
            -int(c.get("hit_high") or 0) - int(c.get("hit_low") or 0),
            _nearest_gap_abs(c.get("sell") or {}) + _nearest_gap_abs(c.get("buy") or {}),
        ),
    )
    return [
        {
            "combo": c["combo"],
            "title": c["title"],
            "hit_high": c["hit_high"],
            "hit_low": c["hit_low"],
            "hit_both": c["hit_both"],
            "sell_nearest_gap_pct": (c["sell"].get("nearest") or {}).get("gap_pct"),
            "buy_nearest_gap_pct": (c["buy"].get("nearest") or {}).get("gap_pct"),
            "gap_sum_abs": round(
                _nearest_gap_abs(c.get("sell") or {}) + _nearest_gap_abs(c.get("buy") or {}),
                4,
            ),
        }
        for c in ranked
    ]


def evaluate_day(
    rows: List[Dict[str, Any]],
    rows_clock: Optional[List[Dict[str, Any]]] = None,
    hit_tol_pct: float = 1.0,
    combo_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    if not rows:
        return {"ok": False, "error": "无连续竞价分钟"}
    ext = _extreme(rows)
    hi, lo = ext["day_high"], ext["day_low"]
    span = hi - lo
    clock_rows = rows_clock or rows

    methods = [
        ("A", "sell_a", "buy_a", rows, "回撤+剩余时间"),
        ("B", "sell_b", "buy_b", rows, "冲击衰竭（不加C）"),
        ("B+C", "sell_b", "buy_b", clock_rows, "冲击衰竭+同时点放量"),
        ("D", "sell_d", "buy_d", clock_rows, "峰岭谷（含同时点尺子）"),
    ]

    method_scores = []
    for name, sell_flag, buy_flag, src, title in methods:
        sell = _pack_side(_pick_signal(src, sell_flag, hi, "high"), lo, hi, hit_tol_pct)
        buy = _pack_side(_pick_signal(src, buy_flag, lo, "low"), lo, hi, hit_tol_pct)
        rec = {
            "method": name,
            "title": title,
            "sell": sell,
            "buy": buy,
        }
        rec["hit_high"] = bool(rec["sell"]["nearest_hit"])
        rec["hit_low"] = bool(rec["buy"]["nearest_hit"])
        rec["hit_both"] = rec["hit_high"] and rec["hit_low"]
        method_scores.append(rec)

    merged = merge_plain_clock(rows, clock_rows)
    combo_scores = evaluate_combos(
        merged, hi, lo, hit_tol_pct,
        combo_ids=combo_ids,
        high_clock=ext["day_high_clock"],
        low_clock=ext["day_low_clock"],
    )
    ranking = rank_combos(combo_scores)
    combo_summary = [
        {
            "combo": c["combo"],
            "title": c["title"],
            "hit_high": c["hit_high"],
            "hit_low": c["hit_low"],
            "hit_both": c["hit_both"],
            "first_hit_high": c.get("first_hit_high"),
            "first_hit_low": c.get("first_hit_low"),
            "first_hit_both": c.get("first_hit_both"),
            "mark_high": c.get("mark_high"),
            "mark_low": c.get("mark_low"),
            "mark_both": c.get("mark_both"),
            "sell_first_gap_pct": (c["sell"].get("first") or {}).get("gap_pct"),
            "buy_first_gap_pct": (c["buy"].get("first") or {}).get("gap_pct"),
            "sell_nearest_gap_pct": (c["sell"].get("nearest") or {}).get("gap_pct"),
            "buy_nearest_gap_pct": (c["buy"].get("nearest") or {}).get("gap_pct"),
        }
        for c in combo_scores
    ]
    best_both = [r["combo"] for r in ranking if r.get("hit_both")]
    best_high = [r["combo"] for r in ranking if r.get("hit_high")]
    best_low = [r["combo"] for r in ranking if r.get("hit_low")]

    c_high_row = _row_by_clock(clock_rows, ext["day_high_clock"]) or ext["high_row"]
    c_low_row = _row_by_clock(clock_rows, ext["day_low_clock"]) or ext["low_row"]
    c_high = score_c_at_extreme(c_high_row)
    c_low = score_c_at_extreme(c_low_row)

    labels = {
        "day_high": hi,
        "day_high_time": ext["day_high_time"],
        "day_high_clock": ext["day_high_clock"],
        "day_low": lo,
        "day_low_time": ext["day_low_time"],
        "day_low_clock": ext["day_low_clock"],
        "open": ext["open"],
        "close": ext["close"],
        "range_pct": round(span / lo * 100.0, 4) if lo else None,
        "n_bars": ext["n"],
        "note": "以上高低只作评分标签，未参与 A/B/C/D 的盘中特征。",
    }

    return {
        "ok": True,
        "hit_tol_pct": hit_tol_pct,
        "labels": labels,
        "combo_ranking": ranking,
        "best_hit_both": best_both,
        "best_hit_high": best_high,
        "best_hit_low": best_low,
        "best_mark_both": [c["combo"] for c in combo_scores if c.get("mark_both")],
        "best_first_both": [c["combo"] for c in combo_scores if c.get("first_hit_both")],
        "combo_summary": combo_summary,
        "combos": combo_scores,
        "methods": method_scores,
        "method_c": {
            "title": "U型量时钟（不直接给出买卖点，只作为 B/D 滤镜）",
            "at_actual_high": c_high,
            "at_actual_low": c_low,
            "high_on_volume_event": c_high.get("on_volume_event"),
            "low_on_volume_event": c_low.get("on_volume_event"),
        },
        "howto_read": (
            "主结果看 combo_ranking。写死到分时图标记请看 mark_high/mark_low："
            "该分钟收盘时组合是否发出买卖点，且该分钟正好是当日真实高低（判定不用未来高低）。"
            "first=当天第一次信号，更接近实盘成交；nearest=所有信号里最接近极值，只作诊断。"
            f"hit=相对真实极值偏差绝对值 ≤ {hit_tol_pct}%。"
            "或=任一方法触发；且=同一分钟同时触发；vote2=A、B+C、D 至少两法同意。"
            "C 不单独下单。methods 是单法对照。"
        ),
    }


def _row_by_clock(rows: List[Dict[str, Any]], clock: Optional[str]) -> Optional[Dict[str, Any]]:
    if not clock:
        return None
    for r in rows:
        if r.get("clock") == clock:
            return r
    return None

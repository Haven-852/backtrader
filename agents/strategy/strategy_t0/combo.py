"""同一成交纪律下的组合：只换信号。

仓位默认视为已持有（T+1 卖出已有仓）。14:55 强制清完。
信号在该分钟收盘判定，按该分钟收盘价成交（因果）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .session import force_flat_clock

EARLY_FRAC = 0.5

COMBOS = {
    "a": "只开 A（回撤+剩余时间）",
    "b": "只开 B（冲击衰竭，不加 C）",
    "b_c": "B + C（涌入须相对同时点放量）",
    "d_c": "D + C（峰岭谷，同时点喷发）",
    "a_floor_b": "A 底线强制卖 + B 提前卖",
    "a_floor_d": "A 底线强制卖 + D 提前卖",
}

HIT_COMBOS = {
    "a": "只开 A",
    "b": "只开 B（不加 C）",
    "b_c": "B + C",
    "d_c": "D + C",
    "a_or_b": "A 或 B（不加 C）",
    "a_or_bc": "A 或 (B+C)",
    "a_or_d": "A 或 D",
    "bc_or_d": "(B+C) 或 D",
    "a_and_b": "A 且 B（不加 C）",
    "a_and_bc": "A 且 (B+C)",
    "a_and_d": "A 且 D",
    "bc_and_d": "(B+C) 且 D",
    "vote2": "A / B+C / D 至少两法同意",
    "a_floor_b": "A 底线 + B 提前（不加 C）",
    "a_floor_bc": "A 底线 + (B+C) 提前",
    "a_floor_d": "A 底线 + D 提前",
}


def merge_plain_clock(
    plain: List[Dict[str, Any]],
    clocked: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """A 用未加 C 的 B；B+C / D 用来自时钟过滤的标注。"""
    cmap = {r.get("clock"): r for r in (clocked or [])}
    out: List[Dict[str, Any]] = []
    for r in plain:
        c = cmap.get(r.get("clock")) or {}
        out.append({
            **r,
            "sell_b_c": c.get("sell_b"),
            "buy_b_c": c.get("buy_b"),
            "sell_d": c.get("sell_d", r.get("sell_d")),
            "buy_d": c.get("buy_d", r.get("buy_d")),
            "x": c.get("x", r.get("x")),
            "vol_state": c.get("vol_state", r.get("vol_state")),
            "reason_b_c": c.get("reason_b"),
            "reason_b_c_buy": c.get("reason_b_buy"),
            "reason_d": c.get("reason_d", r.get("reason_d")),
            "reason_d_buy": c.get("reason_d_buy", r.get("reason_d_buy")),
        })
    return out


def combo_flags(row: Dict[str, Any], combo: str) -> Tuple[bool, bool, str, str]:
    """返回 (buy, sell, buy_reason, sell_reason)。C 只作为 B/D 的滤镜，不单独发信号。"""
    sa, ba = bool(row.get("sell_a")), bool(row.get("buy_a"))
    sb, bb = bool(row.get("sell_b")), bool(row.get("buy_b"))
    sbc, bbc = bool(row.get("sell_b_c")), bool(row.get("buy_b_c"))
    sd, bd = bool(row.get("sell_d")), bool(row.get("buy_d"))
    ra, ra_b = row.get("reason_a") or "A", row.get("reason_a_buy") or "A"
    rb, rb_b = row.get("reason_b") or "B", row.get("reason_b_buy") or "B"
    rbc, rbc_b = row.get("reason_b_c") or "B+C", row.get("reason_b_c_buy") or "B+C"
    rd, rd_b = row.get("reason_d") or "D", row.get("reason_d_buy") or "D"

    def _or(parts):
        buy = any(p[0] for p in parts)
        sell = any(p[1] for p in parts)
        br = " / ".join(p[2] for p in parts if p[0]) or ""
        sr = " / ".join(p[3] for p in parts if p[1]) or ""
        return buy, sell, br, sr

    def _and(parts):
        buy = all(p[0] for p in parts)
        sell = all(p[1] for p in parts)
        br = "且".join(p[2] for p in parts) if buy else ""
        sr = "且".join(p[3] for p in parts) if sell else ""
        return buy, sell, br, sr

    if combo == "a":
        return ba, sa, ra_b, ra
    if combo == "b":
        return bb, sb, rb_b, rb
    if combo == "b_c":
        return bbc, sbc, rbc_b, rbc
    if combo == "d_c":
        return bd, sd, rd_b, rd
    if combo == "a_or_b":
        return _or([(ba, sa, ra_b, ra), (bb, sb, rb_b, rb)])
    if combo == "a_or_bc":
        return _or([(ba, sa, ra_b, ra), (bbc, sbc, rbc_b, rbc)])
    if combo == "a_or_d":
        return _or([(ba, sa, ra_b, ra), (bd, sd, rd_b, rd)])
    if combo == "bc_or_d":
        return _or([(bbc, sbc, rbc_b, rbc), (bd, sd, rd_b, rd)])
    if combo == "a_and_b":
        return _and([(ba, sa, ra_b, ra), (bb, sb, rb_b, rb)])
    if combo == "a_and_bc":
        return _and([(ba, sa, ra_b, ra), (bbc, sbc, rbc_b, rbc)])
    if combo == "a_and_d":
        return _and([(ba, sa, ra_b, ra), (bd, sd, rd_b, rd)])
    if combo == "bc_and_d":
        return _and([(bbc, sbc, rbc_b, rbc), (bd, sd, rd_b, rd)])
    if combo == "vote2":
        sell_n = int(sa) + int(sbc) + int(sd)
        buy_n = int(ba) + int(bbc) + int(bd)
        br = f"买同意{buy_n}/3" if buy_n >= 2 else ""
        sr = f"卖同意{sell_n}/3" if sell_n >= 2 else ""
        return buy_n >= 2, sell_n >= 2, br, sr
    if combo == "a_floor_b":
        return _or([(ba, sa, ra_b, ra), (bb, sb, rb_b, rb)])
    if combo == "a_floor_bc":
        return _or([(ba, sa, ra_b, ra), (bbc, sbc, rbc_b, rbc)])
    if combo == "a_floor_d":
        return _or([(ba, sa, ra_b, ra), (bd, sd, rd_b, rd)])
    return False, False, "", ""


def parse_hit_combos(raw: Any) -> List[str]:
    if raw is None or str(raw).strip() == "":
        return list(HIT_COMBOS.keys())
    s = str(raw).strip().lower()
    if s in ("all", "compare", "*"):
        return list(HIT_COMBOS.keys())
    parts = [p.strip() for p in s.replace("，", ",").split(",") if p.strip()]
    bad = [p for p in parts if p not in HIT_COMBOS]
    if bad:
        raise ValueError(f"未知 combo: {bad}，可选 {list(HIT_COMBOS)}")
    return parts


def _signal(row: Dict[str, Any], combo: str) -> Tuple[str, str]:
    """返回 (kind, reason)。kind: early | floor | force | hold"""
    if force_flat_clock(row.get("clock")):
        return "force", "14:55 后强制清仓"
    if combo == "a":
        if row.get("sell_a"):
            return "floor", row.get("reason_a") or "方法A"
        return "hold", ""
    if combo == "b":
        if row.get("sell_b"):
            return "early", row.get("reason_b") or "方法B"
        return "hold", ""
    if combo == "b_c":
        if row.get("sell_b"):
            return "early", row.get("reason_b") or "方法B+C"
        return "hold", ""
    if combo == "d_c":
        if row.get("sell_d"):
            return "early", row.get("reason_d") or "方法D+C"
        return "hold", ""
    if combo == "a_floor_b":
        if row.get("sell_b"):
            return "early", row.get("reason_b") or "B提前卖"
        if row.get("sell_a"):
            return "floor", row.get("reason_a") or "A底线"
        return "hold", ""
    if combo == "a_floor_d":
        if row.get("sell_d"):
            return "early", row.get("reason_d") or "D提前卖"
        if row.get("sell_a"):
            return "floor", row.get("reason_a") or "A底线"
        return "hold", ""
    return "hold", ""


def execute_combo(rows: List[Dict[str, Any]], combo: str) -> Dict[str, Any]:
    if combo not in COMBOS:
        return {"ok": False, "error": f"未知 combo: {combo}，可选 {list(COMBOS)}"}
    if not rows:
        return {"ok": False, "error": "无连续竞价分钟"}

    remaining = 1.0
    fills: List[Dict[str, Any]] = []
    early_used = False

    for row in rows:
        if remaining <= 1e-9:
            break
        kind, reason = _signal(row, combo)
        if kind == "hold":
            continue
        if kind == "early" and combo in ("a_floor_b", "a_floor_d"):
            if early_used:
                continue
            qty = min(remaining, EARLY_FRAC)
            early_used = True
        else:
            qty = remaining
        px = float(row["close"])
        fills.append({
            "time": row.get("bar_time"),
            "clock": row.get("clock"),
            "price": px,
            "qty": round(qty, 4),
            "kind": kind,
            "reason": reason,
        })
        remaining = round(remaining - qty, 6)

    last = rows[-1]
    day_high = float(last.get("session_high") or last["close"])
    day_low = float(last.get("session_low") or last["close"])
    complete = force_flat_clock(last.get("clock")) or last.get("clock") == "1500"
    status = "flat" if remaining <= 1e-9 else ("holding" if not complete else "flat_eod")

    avg_sell = None
    if fills:
        avg_sell = sum(f["price"] * f["qty"] for f in fills) / sum(f["qty"] for f in fills)

    capture = None
    range_pos = None
    early = None
    late = None
    if avg_sell is not None and day_high > 0:
        capture = avg_sell / day_high
        span = day_high - day_low
        range_pos = ((avg_sell - day_low) / span) if span > 1e-9 else 0.5
        if complete:
            early = bool(day_high > avg_sell * 1.01)
            late = bool(range_pos < 0.30)

    return {
        "ok": True,
        "combo": combo,
        "combo_name": COMBOS[combo],
        "status": status,
        "remaining": remaining,
        "fills": fills,
        "avg_sell": None if avg_sell is None else round(avg_sell, 4),
        "day_high": round(day_high, 4),
        "day_low": round(day_low, 4),
        "last_close": last.get("close"),
        "last_time": last.get("bar_time"),
        "complete": complete,
        "metrics": {
            "capture": None if capture is None else round(capture, 4),
            "range_pos": None if range_pos is None else round(range_pos, 4),
            "early": early,
            "late": late,
            "n_fills": len(fills),
        },
        "note": (
            "capture / early / late 仅在当日分时走完后有终值；"
            "盘中 complete=false 时 capture 相对的是截至目前的 session_high，不是收盘后最高。"
            if not complete else
            "评分用收盘后高低，决策未使用这些字段。"
        ),
    }


def summarize_runs(runs: List[Dict[str, Any]]) -> Dict[str, Any]:
    done = [r for r in runs if r.get("ok") and r.get("complete") and r.get("avg_sell") is not None]
    if not done:
        return {"n": len(runs), "n_complete": 0}
    caps = [r["metrics"]["capture"] for r in done if r["metrics"].get("capture") is not None]
    early_n = sum(1 for r in done if r["metrics"].get("early") is True)
    late_n = sum(1 for r in done if r["metrics"].get("late") is True)
    return {
        "n": len(runs),
        "n_complete": len(done),
        "capture_mean": round(sum(caps) / len(caps), 4) if caps else None,
        "early_rate": round(early_n / len(done), 4),
        "late_rate": round(late_n / len(done), 4),
    }

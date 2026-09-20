"""日内做 T 策略工具入口。供 function calling / /skills 调用。"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, List, Optional

from .combo import COMBOS, HIT_COMBOS, execute_combo, parse_hit_combos, summarize_runs
from .market_scan import run_market_scan
from .data import (
    load_daily_lookback,
    load_day_bars,
    load_lookback_session_vols,
    load_pre_close,
    parse_ts_codes,
    trading_days_between,
)
from .hit_test import evaluate_day
from .methods import annotate_session, build_clock_stats, last_and_events
from .regime import classify_regime, hist_sigma_1m_from_daily
from .session import now_sh, parse_as_of, parse_date

logger = logging.getLogger("agent.strategy.t0.tools")

METHOD_META = {
    "a": {
        "id": "method-a",
        "title": "方法A：回撤+剩余时间",
        "need_clock": False,
        "use_clock_for_b": False,
    },
    "b": {
        "id": "method-b",
        "title": "方法B：冲击衰竭",
        "need_clock": False,
        "use_clock_for_b": False,
    },
    "c": {
        "id": "method-c",
        "title": "方法C：U型量时钟",
        "need_clock": True,
        "use_clock_for_b": False,
    },
    "d": {
        "id": "method-d",
        "title": "方法D：峰岭谷",
        "need_clock": True,
        "use_clock_for_b": False,
    },
}


def _lookback_days(payload: Dict[str, Any]) -> int:
    try:
        n = int(payload.get("lookback_days") or 20)
    except (TypeError, ValueError):
        n = 20
    return max(5, min(n, 60))


def _max_events(payload: Dict[str, Any]) -> int:
    try:
        n = int(payload.get("max_events") or 12)
    except (TypeError, ValueError):
        n = 12
    return max(3, min(n, 40))


def _resolve_dates(payload: Dict[str, Any]) -> Dict[str, Any]:
    as_of = parse_as_of(payload.get("as_of"))
    today = now_sh().date()
    trade_date = parse_date(payload.get("trade_date"))
    start = parse_date(payload.get("start_date"))
    end = parse_date(payload.get("end_date"))
    if trade_date and not start and not end:
        start = end = trade_date
    if as_of and not start and not end and not trade_date:
        start = end = as_of.date()
    if not start and not end:
        start = end = today
    if start and not end:
        end = start
    if end and not start:
        start = end
    days = trading_days_between(start, end)
    if not days:
        days = [start]
    return {"start": start, "end": end, "days": days, "as_of": as_of}


def _annotate_day(
    ts_code: str,
    trade_date: date,
    as_of,
    lookback_days: int,
    use_clock_for_b: bool,
) -> Dict[str, Any]:
    bars = load_day_bars(ts_code, trade_date, as_of=as_of)
    session_n = sum(1 for b in bars if b.get("session"))
    if session_n == 0:
        return {
            "ok": False,
            "ts_code": ts_code,
            "trade_date": trade_date.isoformat(),
            "error": "无连续竞价分时，请确认 stock_intraday_mins 已采集",
        }
    lookback = load_lookback_session_vols(ts_code, trade_date, lookback_days)
    stats = build_clock_stats(lookback)
    pre = load_pre_close(ts_code, trade_date)
    daily = load_daily_lookback(ts_code, trade_date, n=30)
    sigma = hist_sigma_1m_from_daily(daily)
    regime = classify_regime(daily)
    rows = annotate_session(
        bars,
        clock_stats=stats,
        pre_close=pre,
        ts_code=ts_code,
        use_clock_for_b=use_clock_for_b,
        hist_sigma_1m=sigma,
    )
    return {
        "ok": True,
        "ts_code": ts_code,
        "trade_date": trade_date.isoformat(),
        "pre_close": pre,
        "n_session": len(rows),
        "lookback_days_used": len(next(iter(lookback.values()), [])) if lookback else 0,
        "clock_points": len(stats),
        "hist_sigma_1m": round(sigma, 6),
        "regime": regime,
        "rows": rows,
    }


def run_method(method: str, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    payload = payload or {}
    method = (method or "").lower().strip()
    if method not in METHOD_META:
        return {"ok": False, "error": f"未知方法 {method}，应为 a/b/c/d"}
    codes = parse_ts_codes(payload.get("ts_codes") or payload.get("ts_code"))
    if not codes:
        return {"ok": False, "error": "缺少 ts_codes，例如 000001.SZ,600519.SH"}
    meta = METHOD_META[method]
    dates = _resolve_dates(payload)
    lookback_days = _lookback_days(payload)
    include_bars = bool(payload.get("include_bars"))
    max_events = _max_events(payload)
    use_clock_for_b = bool(payload.get("use_clock")) if method == "b" else meta["use_clock_for_b"]
    if method == "b" and payload.get("use_clock") is None:
        use_clock_for_b = False

    results = []
    for code in codes:
        for d in dates["days"]:
            packed = _annotate_day(code, d, dates["as_of"], lookback_days, use_clock_for_b)
            if not packed.get("ok"):
                results.append(packed)
                continue
            last, events = last_and_events(packed["rows"], method, max_events=max_events)
            item = {
                "ok": True,
                "ts_code": code,
                "trade_date": packed["trade_date"],
                "method": method,
                "method_title": meta["title"],
                "pre_close": packed["pre_close"],
                "n_session": packed["n_session"],
                "lookback_days_used": packed["lookback_days_used"],
                "clock_points": packed["clock_points"],
                "use_clock": use_clock_for_b if method == "b" else (method in ("c", "d")),
                "last": last,
                "events": events,
            }
            if method in ("a", "b", "d"):
                combo_key = {"a": "a", "b": "b_c" if use_clock_for_b else "b", "d": "d_c"}[method]
                item["if_sold_by_this_method"] = execute_combo(packed["rows"], combo_key)
            if include_bars:
                item["bars"] = packed["rows"]
            results.append(item)

    return {
        "ok": True,
        "skill": "日内做T策略",
        "method": method,
        "method_title": meta["title"],
        "as_of": dates["as_of"].isoformat(sep=" ", timespec="seconds") if dates["as_of"] else None,
        "n": len(results),
        "results": results,
        "hint": _method_hint(method),
    }


def _compact_bar(b: Dict[str, Any]) -> List[Any]:
    clock = b.get("clock") or ""
    try:
        hhmm = int(clock)
    except (TypeError, ValueError):
        hhmm = clock
    return [
        hhmm,
        b.get("open"),
        b.get("high"),
        b.get("low"),
        b.get("close"),
        int(b.get("vol") or 0),
        b.get("avg_price"),
        1 if b.get("auction") else 0,
    ]


def run_load_day(payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """从 stock_intraday_mins 取出当天全部 1 分钟价格和成交量。"""
    payload = payload or {}
    codes = parse_ts_codes(payload.get("ts_codes") or payload.get("ts_code"))
    if not codes:
        return {"ok": False, "error": "缺少 ts_codes，例如 600118.SH"}
    dates = _resolve_dates(payload)
    include_bars = payload.get("include_bars")
    if include_bars is None:
        include_bars = True

    results = []
    for code in codes:
        for d in dates["days"]:
            bars = load_day_bars(code, d, as_of=dates["as_of"])
            session = [b for b in bars if b.get("session")]
            auction = [b for b in bars if b.get("auction")]
            if not session:
                results.append({
                    "ok": False,
                    "ts_code": code,
                    "trade_date": d.isoformat(),
                    "error": "无连续竞价分时，请确认 stock_intraday_mins 已采集",
                })
                continue
            highs = [float(b["high"] if b.get("high") is not None else b["close"]) for b in session]
            lows = [float(b["low"] if b.get("low") is not None else b["close"]) for b in session]
            hi_i = max(range(len(highs)), key=lambda i: highs[i])
            lo_i = min(range(len(lows)), key=lambda i: lows[i])
            vol_sum = int(sum(int(b.get("vol") or 0) for b in session))
            item = {
                "ok": True,
                "ts_code": code,
                "trade_date": d.isoformat(),
                "source": "stock_intraday_mins",
                "n_session": len(session),
                "n_auction": len(auction),
                "pre_close": load_pre_close(code, d),
                "summary": {
                    "open": session[0].get("open") or session[0]["close"],
                    "high": highs[hi_i],
                    "high_time": session[hi_i].get("bar_time"),
                    "low": lows[lo_i],
                    "low_time": session[lo_i].get("bar_time"),
                    "close": session[-1]["close"],
                    "volume": vol_sum,
                    "last_time": session[-1].get("bar_time"),
                    "complete": session[-1].get("clock") == "1500",
                },
                "bar_columns": ["hhmm", "open", "high", "low", "close", "vol", "avg_price", "auction"],
            }
            if include_bars:
                item["bars"] = [_compact_bar(b) for b in bars]
            results.append(item)

    return {
        "ok": True,
        "skill": "日内做T策略",
        "step": "load_day",
        "n": len(results),
        "results": results,
        "next": (
            "已从数据库取出当天分时。下一步请调用 t0_intraday__hit_test，"
            "用同一 ts_codes 和 trade_date 跑 A/B/C/D 及其组合，对照真实高低打分。"
            "结论优先看 combo_ranking / combo_summary，不要再用 sql-toolkit 自己算高低。"
        ),
    }


def run_hit_test(payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """用当天价格和成交量跑 A/B/C/D 及组合，对照真实最高最低打分。"""
    payload = payload or {}
    codes = parse_ts_codes(payload.get("ts_codes") or payload.get("ts_code"))
    if not codes:
        return {"ok": False, "error": "缺少 ts_codes"}
    dates = _resolve_dates(payload)
    lookback_days = _lookback_days(payload)
    try:
        tol = float(payload.get("hit_tol_pct") or 1.0)
    except (TypeError, ValueError):
        tol = 1.0
    tol = max(0.1, min(tol, 5.0))
    try:
        combo_ids = parse_hit_combos(payload.get("combos") or payload.get("combo") or "all")
    except ValueError as e:
        return {"ok": False, "error": str(e), "available": list(HIT_COMBOS)}

    out = []
    for code in codes:
        for d in dates["days"]:
            packed = _annotate_day(code, d, dates["as_of"], lookback_days, use_clock_for_b=False)
            packed_c = _annotate_day(code, d, dates["as_of"], lookback_days, use_clock_for_b=True)
            if not packed.get("ok"):
                out.append(packed)
                continue
            scored = evaluate_day(
                packed["rows"],
                rows_clock=packed_c.get("rows") if packed_c.get("ok") else packed["rows"],
                hit_tol_pct=tol,
                combo_ids=combo_ids,
            )
            last = packed["rows"][-1]
            complete = last.get("clock") == "1500"
            out.append({
                "ok": True,
                "ts_code": code,
                "trade_date": packed["trade_date"],
                "complete": complete,
                "pre_close": packed["pre_close"],
                "n_session": packed["n_session"],
                "lookback_days_used": packed["lookback_days_used"],
                "regime": packed.get("regime"),
                **scored,
                "warning": None if complete else (
                    "分时未到 15:00，labels 只是截至目前的高低，不是收盘后全日极值。"
                ),
            })

    return {
        "ok": True,
        "skill": "日内做T策略",
        "step": "hit_test",
        "n": len(out),
        "combo_ids": combo_ids,
        "results": out,
        "pipeline": (
            "load_day(数据库全部分时) → 标注 A/B 与 B+C/D → "
            "按 HIT_COMBOS 组合成买卖点 → 用真实高低打分"
        ),
        "read_first": "combo_ranking（越好越靠前）、best_hit_both、combo_summary",
    }


def _method_hint(method: str) -> str:
    return {
        "a": "只看现价相对 session_high 的回撤和剩余分钟。创出新高时不应卖。",
        "b": "默认不加 C。若要 B+C，设 use_clock=true，或改调 combo-run 的 combo=b_c。",
        "c": "不产生卖点，只给同时点放量倍数 x。对比实验请与 B 或 D 一起用 combo-run。",
        "d": "峰岭谷本身用同时点喷发（即 C 的尺子）。涨停附近不会提前卖。",
    }[method]


def run_combo(payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    payload = payload or {}
    codes = parse_ts_codes(payload.get("ts_codes") or payload.get("ts_code"))
    if not codes:
        return {"ok": False, "error": "缺少 ts_codes"}
    combo_raw = str(payload.get("combo") or "all").strip().lower()
    if combo_raw in ("all", "compare", "compare_all", "*"):
        combos = list(COMBOS.keys())
    elif combo_raw in COMBOS:
        combos = [combo_raw]
    else:
        return {"ok": False, "error": f"未知 combo={combo_raw}，可选 {list(COMBOS)} 或 all"}

    dates = _resolve_dates(payload)
    lookback_days = _lookback_days(payload)
    include_fills = payload.get("include_fills")
    if include_fills is None:
        include_fills = combo_raw not in ("all", "compare", "compare_all", "*")
    include_fills = bool(include_fills)

    by_combo: Dict[str, List[Dict[str, Any]]] = {c: [] for c in combos}
    missing: List[Dict[str, Any]] = []

    for code in codes:
        for d in dates["days"]:
            packed_plain = _annotate_day(code, d, dates["as_of"], lookback_days, use_clock_for_b=False)
            packed_clock = _annotate_day(code, d, dates["as_of"], lookback_days, use_clock_for_b=True)
            src = {
                "a": packed_plain,
                "b": packed_plain,
                "b_c": packed_clock,
                "d_c": packed_clock,
                "a_floor_b": packed_clock if bool(payload.get("floor_use_clock")) else packed_plain,
                "a_floor_d": packed_clock,
            }

            for c in combos:
                packed = src.get(c) or packed_plain
                if not packed.get("ok"):
                    missing.append({"ts_code": code, "trade_date": d.isoformat(), "error": packed.get("error")})
                    continue
                exe = execute_combo(packed["rows"], c)
                rec = {
                    "ts_code": code,
                    "trade_date": packed["trade_date"],
                    **{k: exe[k] for k in exe if k != "fills" or include_fills},
                }
                by_combo[c].append(rec)

    comparison = []
    for c in combos:
        summary = summarize_runs(by_combo[c])
        comparison.append({
            "combo": c,
            "combo_name": COMBOS[c],
            "summary": summary,
            "runs": by_combo[c],
        })

    return {
        "ok": True,
        "skill": "日内做T策略",
        "discipline": {
            "fill": "信号分钟收盘价",
            "force_flat": "14:55",
            "a_floor_early_frac": 0.5,
            "same_codes": codes,
            "same_dates": [d.isoformat() for d in dates["days"]],
        },
        "as_of": dates["as_of"].isoformat(sep=" ", timespec="seconds") if dates["as_of"] else None,
        "missing": missing,
        "comparison": comparison,
        "howto_read": (
            "同一批票、同一段日期、同一条成交纪律，只换信号。"
            "capture=卖出价/当日最高（收盘后才是终值）；"
            "early=卖出后当天最高仍高出1%；late=卖出价落在当日波幅最低30%。"
            "做T更应看过早率低，而不是偶尔摸到顶。"
        ),
    }


async def execute(tool_id: str, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    return execute_sync(tool_id, payload)


def execute_sync(tool_id: str, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    payload = payload or {}
    tid = (tool_id or "").strip().lower().replace("_", "-")
    mapping = {
        "method-a": "a",
        "method-b": "b",
        "method-c": "c",
        "method-d": "d",
        "a": "a",
        "b": "b",
        "c": "c",
        "d": "d",
    }
    if tid in mapping:
        return run_method(mapping[tid], payload)
    if tid in ("combo-run", "combo", "compare"):
        return run_combo(payload)
    if tid in ("load-day", "load_day", "load"):
        return run_load_day(payload)
    if tid in ("hit-test", "hit_test", "hit"):
        return run_hit_test(payload)
    if tid in ("market-scan", "market_scan", "market"):
        return run_market_scan(payload)
    return {"ok": False, "error": f"未知工具: {tool_id}"}

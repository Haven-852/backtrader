"""全市场因果命中测试：先按近 30 日日线分态势，再只凭当日已出现的分钟做 A/B/C/D 组合。"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
from .combo import HIT_COMBOS, parse_hit_combos
from .data import (
    list_minute_codes,
    load_daily_lookback_bulk,
    load_listed_codes,
    load_lookback_vols_bulk,
    load_mins_by_date,
    load_stock_names,
)
from .hit_test import evaluate_day
from .methods import annotate_session, build_clock_stats
from .regime import REGIME_TITLE, classify_regime, hist_sigma_1m_from_daily
from .session import parse_date

logger = logging.getLogger("agent.strategy.t0.market_scan")

OUTPUT_DIR = Path(__file__).resolve().parent / "output"

CORE_COMBOS = [
    "a", "b", "b_c", "d_c",
    "a_or_b", "a_or_bc", "a_or_d", "bc_or_d",
    "a_and_b", "vote2",
    "a_floor_b", "a_floor_bc", "a_floor_d",
]


def _fetch_one_mins(fetcher, code: str, trade_date: date):
    import urllib.parse
    import urllib.error
    secid = fetcher._em_secid(code)
    params = {
        "secid": secid,
        "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
        "iscr": "0",
        "ndays": "1",
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
    }
    url = "https://push2.eastmoney.com/api/qt/stock/trends2/get?" + urllib.parse.urlencode(params)
    last_err = "empty"
    for attempt in range(2):
        try:
            payload = fetcher._http_json(url, headers={"Referer": "https://quote.eastmoney.com/"}, timeout=8)
            data = payload.get("data") or {}
            trends = data.get("trends") or []
            rows = []
            for item in trends:
                parts = str(item).split(",")
                if len(parts) < 8:
                    continue
                rows.append({
                    "ts_code": code,
                    "trade_time": parts[0],
                    "open": parts[1],
                    "close": parts[2],
                    "high": parts[3],
                    "low": parts[4],
                    "vol": parts[5],
                    "amount": parts[6],
                    "avg_price": parts[7],
                })
            if rows:
                df = fetcher._normalize_mins_df(code, pd.DataFrame(rows), trade_date)
                if df is not None and not df.empty:
                    return df, "ok"
            last_err = "empty"
        except (urllib.error.URLError, TimeoutError, OSError, Exception) as e:
            last_err = str(e)
        if attempt == 0:
            time.sleep(0.25)
    return None, last_err


def collect_day_mins(codes: List[str], trade_date: date, workers: int = 6) -> Dict[str, Any]:
    """用东财拉当日分时入库。已有足够分钟的代码会跳过。"""
    from data_layer.collectors.datahubco.intraday import IntradayFetcher

    logging.getLogger("collector.datahubco.intraday").setLevel(logging.ERROR)
    logging.getLogger("collector.datahubco").setLevel(logging.ERROR)

    fetcher = IntradayFetcher()
    have = set(list_minute_codes(trade_date, min_bars=180))
    todo = [c for c in codes if c not in have]
    stats = {"already": len(have), "todo": len(todo), "ok": 0, "fail": 0, "rows": 0}

    def one(code: str) -> tuple:
        df, st = _fetch_one_mins(fetcher, code, trade_date)
        if df is None or df.empty:
            return code, 0, st
        n = fetcher.upsert_mins(df)
        return code, n, "ok"

    if not todo:
        return stats

    workers = max(1, min(int(workers or 6), 12))
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(one, c): c for c in todo}
        for fut in as_completed(futs):
            code, n, st = fut.result()
            done += 1
            if st == "ok" and n > 0:
                stats["ok"] += 1
                stats["rows"] += n
            else:
                stats["fail"] += 1
            if done % 50 == 0 or done == len(todo):
                logger.info("分时采集 %d/%d ok=%d fail=%d", done, len(todo), stats["ok"], stats["fail"])
                print(f"mins {done}/{len(todo)} ok={stats['ok']} fail={stats['fail']}", flush=True)
            if done % 200 == 0:
                time.sleep(1.0)
    stats["have_after"] = len(list_minute_codes(trade_date, min_bars=180))
    return stats


def _open_days_before(before: date, n: int) -> List[date]:
    from sqlalchemy import text as sa_text
    from .data import _engine

    start = before - timedelta(days=max(n * 3, 60))
    eng = _engine()
    df = pd.read_sql(
        sa_text(
            """
            SELECT cal_date FROM ref_trade_cal
            WHERE exchange = 'SSE' AND COALESCE(is_open, 1) = 1
              AND cal_date >= :a AND cal_date < :b
            ORDER BY cal_date
            """
        ),
        eng,
        params={"a": start, "b": before},
    )
    days = []
    if df is not None and not df.empty:
        days = [parse_date(x) for x in df["cal_date"].tolist()]
        days = [x for x in days if x]
    if len(days) >= n:
        return days[-n:]
    out: List[date] = []
    x = before - timedelta(days=1)
    while len(out) < n and (before - x).days < 90:
        if x.weekday() < 5:
            out.append(x)
        x -= timedelta(days=1)
    return list(reversed(out))


def fill_daily_window(before: date, n: int = 30, min_rows: int = 800) -> Dict[str, Any]:
    """按交易日补全近 n 日全市场日线，供态势分类。不含测试日。"""
    from data_layer.collectors.datahubco.client import DatahubcoClient
    from data_layer.collectors.datahubco.selective import BAR_COLS, selective_fetcher
    from sqlalchemy import text as sa_text
    from .data import _engine

    days = _open_days_before(before, n)
    eng = _engine()
    client = DatahubcoClient()
    stats = {"days": [d.isoformat() for d in days], "pulled": 0, "skipped": 0, "rows": 0, "fail": []}
    consecutive_413 = 0
    for d in days:
        cnt = int(pd.read_sql(
            sa_text("SELECT COUNT(*) AS n FROM stock_daily WHERE trade_date = :d"),
            eng, params={"d": d},
        ).iloc[0]["n"] or 0)
        if cnt >= min_rows:
            stats["skipped"] += 1
            continue
        ymd = d.strftime("%Y%m%d")
        try:
            raw = client.query("daily", trade_date=ymd)
        except Exception as e:
            stats["fail"].append({"date": d.isoformat(), "error": str(e)})
            print(f"daily {ymd} fail {e}", flush=True)
            if "413" in str(e):
                consecutive_413 += 1
                if consecutive_413 >= 3:
                    stats["stopped"] = "daily 接口 413，停止补拉"
                    break
            continue
        consecutive_413 = 0
        if raw is None or raw.empty:
            stats["fail"].append({"date": d.isoformat(), "error": "empty"})
            continue
        df = raw.copy()
        if "trade_date" in df.columns:
            df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce").dt.date
        for c in BAR_COLS:
            if c not in df.columns:
                df[c] = None
        n_up = selective_fetcher.upsert_bars("daily", df[BAR_COLS])
        stats["pulled"] += 1
        stats["rows"] += int(n_up or 0)
        print(f"daily {ymd} upsert {n_up} (had {cnt})", flush=True)
        time.sleep(0.12)
    return stats


def _compact_combo(c: Dict[str, Any]) -> Dict[str, Any]:
    sell_first = (c.get("sell") or {}).get("first") or {}
    buy_first = (c.get("buy") or {}).get("first") or {}
    return {
        "combo": c["combo"],
        "mark_high": bool(c.get("mark_high")),
        "mark_low": bool(c.get("mark_low")),
        "mark_both": bool(c.get("mark_both")),
        "first_hit_high": bool(c.get("first_hit_high")),
        "first_hit_low": bool(c.get("first_hit_low")),
        "first_hit_both": bool(c.get("first_hit_both")),
        "nearest_hit_high": bool(c.get("hit_high")),
        "nearest_hit_low": bool(c.get("hit_low")),
        "sell_first_gap_pct": sell_first.get("gap_pct"),
        "buy_first_gap_pct": buy_first.get("gap_pct"),
    }


def _blank_agg() -> Dict[str, Any]:
    return {
        "n": 0,
        "mark_high": 0,
        "mark_low": 0,
        "mark_both": 0,
        "first_hit_high": 0,
        "first_hit_low": 0,
        "first_hit_both": 0,
        "nearest_hit_high": 0,
        "nearest_hit_low": 0,
        "nearest_hit_both": 0,
        "first_gap_high_abs": [],
        "first_gap_low_abs": [],
    }


def _rate(num: int, den: int) -> Optional[float]:
    if not den:
        return None
    return round(num / den, 4)


def _mean_abs(xs: List[float]) -> Optional[float]:
    vals = [abs(x) for x in xs if x is not None]
    if not vals:
        return None
    return round(sum(vals) / len(vals), 4)


def _recommend(rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not rows:
        return None
    ranked = sorted(
        rows,
        key=lambda r: (
            -(r.get("mark_both_rate") or 0),
            -((r.get("mark_high_rate") or 0) + (r.get("mark_low_rate") or 0)),
            -(r.get("first_hit_both_rate") or 0),
            (r.get("first_gap_high_mae") or 9) + (r.get("first_gap_low_mae") or 9),
        ),
    )
    best = ranked[0]
    return {
        "combo": best["combo"],
        "title": HIT_COMBOS.get(best["combo"], best["combo"]),
        "mark_both_rate": best.get("mark_both_rate"),
        "mark_high_rate": best.get("mark_high_rate"),
        "mark_low_rate": best.get("mark_low_rate"),
        "first_hit_both_rate": best.get("first_hit_both_rate"),
        "why": (
            "同一态势内，优先看真实高低那一分钟有没有发出因果信号（mark），"
            "再看第一次信号的命中率。"
        ),
    }


def scan_market(
    trade_date: date,
    lookback_days: int = 30,
    hit_tol_pct: float = 1.0,
    combo_ids: Optional[List[str]] = None,
    collect: bool = False,
    fill_daily: bool = False,
    max_codes: Optional[int] = None,
    workers: int = 10,
) -> Dict[str, Any]:
    combo_ids = combo_ids or CORE_COMBOS
    listed = load_listed_codes()
    collect_stats = None
    if collect:
        target = listed[: int(max_codes)] if max_codes else listed
        collect_stats = collect_day_mins(target, trade_date, workers=workers)
    daily_fill = fill_daily_window(trade_date, n=lookback_days) if fill_daily else None

    codes = list_minute_codes(trade_date, min_bars=180)
    if max_codes:
        codes = codes[: int(max_codes)]
    if not codes:
        return {
            "ok": False,
            "error": f"{trade_date} 无足够分时。可设 collect=true 从东财拉当日分钟。",
            "collect": collect_stats,
            "daily_fill": daily_fill,
        }

    names = load_stock_names(codes)
    daily_map = load_daily_lookback_bulk(trade_date, n=lookback_days)
    vol_map = load_lookback_vols_bulk(trade_date, lookback_days=min(lookback_days, 20))
    mins_map = load_mins_by_date(trade_date)
    stocks: List[Dict[str, Any]] = []
    skipped = 0
    agg: Dict[str, Dict[str, Dict[str, Any]]] = defaultdict(lambda: defaultdict(_blank_agg))
    overall = defaultdict(_blank_agg)

    for i, code in enumerate(codes, 1):
        bars = mins_map.get(code) or []
        session = [b for b in bars if b.get("session")]
        if len(session) < 180:
            skipped += 1
            continue
        daily = daily_map.get(code) or []
        regime = classify_regime(daily)
        sigma = hist_sigma_1m_from_daily(daily)
        pre = daily[-1].get("close") if daily else None
        stats = build_clock_stats(vol_map.get(code) or {})
        plain = annotate_session(
            bars, clock_stats=stats, pre_close=pre,
            ts_code=code, use_clock_for_b=False, hist_sigma_1m=sigma,
        )
        clocked = annotate_session(
            bars, clock_stats=stats, pre_close=pre,
            ts_code=code, use_clock_for_b=True, hist_sigma_1m=sigma,
        )
        scored = evaluate_day(plain, rows_clock=clocked, hit_tol_pct=hit_tol_pct, combo_ids=combo_ids)
        if not scored.get("ok"):
            skipped += 1
            continue
        rg = regime.get("regime") or "unknown"
        compact = [_compact_combo(c) for c in scored.get("combos") or []]
        item = {
            "ts_code": code,
            "name": names.get(code, ""),
            "regime": rg,
            "regime_title": regime.get("title"),
            "regime_reason": regime.get("reason"),
            "regime_ret_20": regime.get("ret_20"),
            "regime_asof": regime.get("asof_trade_date"),
            "lookback_n": regime.get("n_days"),
            "clock_lookback_n": len(next(iter((vol_map.get(code) or {}).values()), [])),
            "labels": scored.get("labels"),
            "combos": compact,
        }
        stocks.append(item)
        for c in scored.get("combos") or []:
            cid = c["combo"]
            for bucket in (agg[rg][cid], overall[cid]):
                bucket["n"] += 1
                for k in (
                    "mark_high", "mark_low", "mark_both",
                    "first_hit_high", "first_hit_low", "first_hit_both",
                ):
                    if c.get(k):
                        bucket[k] += 1
                if c.get("hit_high"):
                    bucket["nearest_hit_high"] += 1
                if c.get("hit_low"):
                    bucket["nearest_hit_low"] += 1
                if c.get("hit_both"):
                    bucket["nearest_hit_both"] += 1
                sg = (c.get("sell") or {}).get("first") or {}
                bg = (c.get("buy") or {}).get("first") or {}
                if sg.get("gap_pct") is not None:
                    bucket["first_gap_high_abs"].append(abs(float(sg["gap_pct"])))
                if bg.get("gap_pct") is not None:
                    bucket["first_gap_low_abs"].append(abs(float(bg["gap_pct"])))
        if i % 20 == 0 or i == len(codes):
            print(f"扫描 {i}/{len(codes)} 有效 {len(stocks)}", flush=True)

    def pack_table(src: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
        rows = []
        for cid, b in src.items():
            n = b["n"]
            rows.append({
                "combo": cid,
                "title": HIT_COMBOS.get(cid, cid),
                "n": n,
                "mark_high_rate": _rate(b["mark_high"], n),
                "mark_low_rate": _rate(b["mark_low"], n),
                "mark_both_rate": _rate(b["mark_both"], n),
                "first_hit_high_rate": _rate(b["first_hit_high"], n),
                "first_hit_low_rate": _rate(b["first_hit_low"], n),
                "first_hit_both_rate": _rate(b["first_hit_both"], n),
                "nearest_hit_high_rate": _rate(b["nearest_hit_high"], n),
                "nearest_hit_low_rate": _rate(b["nearest_hit_low"], n),
                "nearest_hit_both_rate": _rate(b["nearest_hit_both"], n),
                "first_gap_high_mae": _mean_abs(b["first_gap_high_abs"]),
                "first_gap_low_mae": _mean_abs(b["first_gap_low_abs"]),
            })
        rows.sort(key=lambda r: (-(r.get("mark_both_rate") or 0), -(r.get("mark_high_rate") or 0)))
        return rows

    regime_tables = {}
    recommended = {}
    regime_n = defaultdict(int)
    for s in stocks:
        regime_n[s["regime"]] += 1
    for rg, table in agg.items():
        packed = pack_table(table)
        regime_tables[rg] = {
            "title": REGIME_TITLE.get(rg, rg),
            "n_stocks": regime_n.get(rg, 0),
            "combos": packed,
        }
        recommended[rg] = _recommend(packed)

    overall_table = pack_table(overall)
    report = {
        "ok": True,
        "trade_date": trade_date.isoformat(),
        "n_listed": len(listed),
        "n_with_mins": len(codes),
        "n_scored": len(stocks),
        "n_skipped": skipped,
        "lookback_days": lookback_days,
        "hit_tol_pct": hit_tol_pct,
        "causal": {
            "signals": "每分钟只用截至该分钟的价量；session_high/low 是截至目前的高低。",
            "regime": "态势只用测试日之前最多 30 根日线。",
            "sigma": "方法A阈值用历史日波动种子 + 当日已走过分钟的滚动波动，不用当天未走完的分钟。",
            "labels": "收盘后真实高低只用来打分，不进特征。",
            "mark": "真实高低那一分钟当时有没有发出买卖点（适合以后画在分时图上）。",
            "first": "当天第一次信号，接近实盘第一笔成交。",
            "nearest": "事后在所有信号里挑最接近极值的一次，不能当实盘规则。",
        },
        "collect": collect_stats,
        "daily_fill": daily_fill,
        "regime_counts": dict(regime_n),
        "recommended_by_regime": recommended,
        "overall": overall_table,
        "by_regime": regime_tables,
        "stocks": stocks,
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"market_scan_{trade_date.strftime('%Y%m%d')}.json"
    slim = {k: v for k, v in report.items() if k != "stocks"}
    slim["stocks"] = stocks
    out_path.write_text(json.dumps(slim, ensure_ascii=False, default=str), encoding="utf-8")
    report["output"] = str(out_path)
    return report


def run_market_scan(payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    payload = payload or {}
    d = parse_date(payload.get("trade_date"))
    if d is None:
        from .session import now_sh
        d = now_sh().date()
    try:
        lookback = int(payload.get("lookback_days") or 30)
    except (TypeError, ValueError):
        lookback = 30
    lookback = max(10, min(lookback, 60))
    try:
        tol = float(payload.get("hit_tol_pct") or 1.0)
    except (TypeError, ValueError):
        tol = 1.0
    combos = None
    raw = payload.get("combos") or payload.get("combo")
    if raw:
        try:
            combos = parse_hit_combos(raw)
        except ValueError as e:
            return {"ok": False, "error": str(e), "available": list(HIT_COMBOS)}
    max_codes = payload.get("max_codes")
    try:
        max_codes = int(max_codes) if max_codes not in (None, "", "all") else None
    except (TypeError, ValueError):
        max_codes = None
    collect = bool(payload.get("collect"))
    fill_daily = bool(payload.get("fill_daily"))
    include_stocks = bool(payload.get("include_stocks"))
    report = scan_market(
        d,
        lookback_days=lookback,
        hit_tol_pct=max(0.1, min(tol, 5.0)),
        combo_ids=combos,
        collect=collect,
        fill_daily=fill_daily,
        max_codes=max_codes,
    )
    if not include_stocks and report.get("ok"):
        report = dict(report)
        n = len(report.get("stocks") or [])
        report["stocks"] = f"共 {n} 只，明细见 output 文件；需要明细时设 include_stocks=true"
    return report


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    p = argparse.ArgumentParser(description="全市场 T0 组合命中扫描")
    p.add_argument("--trade-date", default="2026-09-17")
    p.add_argument("--collect", action="store_true")
    p.add_argument("--max-codes", type=int, default=0)
    p.add_argument("--lookback-days", type=int, default=30)
    p.add_argument("--fill-daily", action="store_true")
    args = p.parse_args()
    d = parse_date(args.trade_date)
    r = scan_market(
        d,
        lookback_days=args.lookback_days,
        collect=args.collect,
        fill_daily=args.fill_daily,
        max_codes=args.max_codes or None,
    )
    print("ok", r.get("ok"), "scored", r.get("n_scored"), "output", r.get("output"))
    print("regime", r.get("regime_counts"))
    print("recommend", json.dumps(r.get("recommended_by_regime"), ensure_ascii=False, indent=2))
    print("overall top:")
    for row in (r.get("overall") or [])[:8]:
        print(row)

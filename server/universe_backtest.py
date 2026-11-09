"""跨股稳健回测：按区间初期流动性选池，避免用事后涨幅挑赢家。"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from sqlalchemy import bindparam
from sqlalchemy.sql import text as sa_text

_CODE_RE = re.compile(r"^\d{6}\.(SZ|SH|BJ)$", re.I)


def sanitize_codes(raw: Optional[Sequence[str]]) -> List[str]:
    out: List[str] = []
    seen = set()
    for item in raw or []:
        code = str(item or "").strip().upper()
        if not _CODE_RE.match(code) or code in seen:
            continue
        seen.add(code)
        out.append(code)
    return out


def split_holdout(rows: Sequence[Dict[str, Any]], frac: float = 0.5) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """按 ts_code 稳定切半：前半寻参、后半确认，不按收益排序。"""
    ordered = sorted(rows, key=lambda r: str(r.get("ts_code") or ""))
    if len(ordered) < 4:
        return list(ordered), list(ordered)
    n_tune = max(1, int(round(len(ordered) * (1.0 - float(frac)))))
    n_tune = min(n_tune, len(ordered) - 1)
    return list(ordered[:n_tune]), list(ordered[n_tune:])


def _median(xs: List[float], default: float = 0.0) -> float:
    arr = [float(x) for x in xs if x is not None and np.isfinite(x)]
    if not arr:
        return default
    return float(np.median(arr))


def _mean(xs: List[float], default: float = 0.0) -> float:
    arr = [float(x) for x in xs if x is not None and np.isfinite(x)]
    if not arr:
        return default
    return float(np.mean(arr))


def calmar(total_return: float, max_drawdown: float) -> float:
    dd = abs(float(max_drawdown or 0.0))
    if dd < 1e-6:
        return float(total_return or 0.0) * 10.0
    return float(total_return or 0.0) / dd


def robustness_score(summary: Dict[str, Any]) -> float:
    """中位收益 × 盈利占比 / (0.15 + |中位回撤|)。不奖励单票暴利。"""
    med_ret = float(summary.get("median_return") or 0.0)
    pct_pos = float(summary.get("pct_positive") or 0.0)
    med_dd = abs(float(summary.get("median_max_drawdown") or 0.0))
    return round(med_ret * (0.5 + pct_pos) / (0.15 + med_dd), 4)


def summarize_rows(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    active = [r for r in rows if r.get("skipped") is not True]
    n = len(active)
    if n == 0:
        return {
            "n": 0,
            "pct_positive": 0.0,
            "pct_dd_under_25": 0.0,
            "median_return": 0.0,
            "mean_return": 0.0,
            "median_max_drawdown": 0.0,
            "mean_max_drawdown": 0.0,
            "median_sharpe": 0.0,
            "median_calmar": 0.0,
            "median_buyhold": 0.0,
            "median_excess": 0.0,
            "robustness_score": 0.0,
        }
    rets = [float(r.get("total_return") or 0.0) for r in active]
    dds = [float(r.get("max_drawdown") or 0.0) for r in active]
    sharpes = [float(r.get("sharpe") or 0.0) for r in active]
    calmars = [float(r.get("calmar") or 0.0) for r in active]
    bhs = [float(r.get("buyhold_return") or 0.0) for r in active]
    excess = [float(r.get("total_return") or 0.0) - float(r.get("buyhold_return") or 0.0) for r in active]
    summary = {
        "n": n,
        "pct_positive": round(sum(1 for x in rets if x > 0) / n, 4),
        "pct_dd_under_25": round(sum(1 for x in dds if abs(x) <= 0.25) / n, 4),
        "median_return": round(_median(rets), 4),
        "mean_return": round(_mean(rets), 4),
        "median_max_drawdown": round(_median(dds), 4),
        "mean_max_drawdown": round(_mean(dds), 4),
        "median_sharpe": round(_median(sharpes), 4),
        "median_calmar": round(_median(calmars), 4),
        "median_buyhold": round(_median(bhs), 4),
        "median_excess": round(_median(excess), 4),
    }
    summary["robustness_score"] = robustness_score(summary)
    return summary


def board_sql_clause(board: str, alias: str = "r") -> str:
    col = f"{alias}.ts_code"
    b = (board or "main").strip().lower()
    if b == "all":
        return "TRUE"
    if b == "chinext":
        return f"({col} LIKE '300%' OR {col} LIKE '301%')"
    if b == "star":
        return f"{col} LIKE '688%'"
    return (
        f"({col} LIKE '00%' OR {col} LIKE '60%') "
        f"AND {col} NOT LIKE '300%' AND {col} NOT LIKE '301%' "
        f"AND {col} NOT LIKE '688%'"
    )


def pick_liquid_universe(
    engine,
    start: str,
    end: str,
    n: int = 100,
    board: str = "main",
) -> List[Dict[str, Any]]:
    """用回测窗口前 20 个交易日的成交额均值选池，避免用后来大涨的票。"""
    n = max(10, min(int(n or 100), 150))
    board_sql = board_sql_clause(board, "r")
    sql = sa_text(
        f"""
        WITH days AS (
            SELECT cal_date AS trade_date
            FROM ref_trade_cal
            WHERE exchange = 'SSE'
              AND is_open = 1
              AND cal_date >= CAST(:start AS date)
              AND cal_date <= CAST(:end AS date)
            ORDER BY cal_date
            LIMIT 20
        ),
        ranked AS (
            SELECT
                d.ts_code,
                AVG(d.amount) AS avg_amount,
                COUNT(*) AS n_days
            FROM stock_daily d
            INNER JOIN days x ON x.trade_date = d.trade_date
            WHERE d.amount IS NOT NULL AND d.amount > 0
            GROUP BY d.ts_code
            HAVING COUNT(*) >= 12
        )
        SELECT
            r.ts_code,
            COALESCE(b.name, u.name, r.ts_code) AS name,
            COALESCE(b.industry, u.industry, '') AS industry,
            r.avg_amount,
            r.n_days
        FROM ranked r
        LEFT JOIN ref_stock_basic b ON b.ts_code = r.ts_code
        LEFT JOIN ref_market_universe u ON u.ts_code = r.ts_code
        WHERE COALESCE(b.list_status, u.list_status, 'L') = 'L'
          AND COALESCE(b.name, u.name, '') NOT ILIKE '%ST%'
          AND {board_sql}
        ORDER BY r.avg_amount DESC NULLS LAST
        LIMIT :lim
        """
    )
    try:
        df = pd.read_sql(sql, engine, params={"start": start, "end": end, "lim": n})
    except Exception:
        df = pd.DataFrame()
    if df.empty:
        sql_fb = sa_text(
            f"""
            WITH days AS (
                SELECT trade_date
                FROM (
                    SELECT DISTINCT trade_date
                    FROM stock_daily
                    WHERE trade_date >= CAST(:start AS date)
                      AND trade_date <= CAST(:end AS date)
                ) t
                ORDER BY trade_date
                LIMIT 20
            ),
            ranked AS (
                SELECT d.ts_code, AVG(d.amount) AS avg_amount, COUNT(*) AS n_days
                FROM stock_daily d
                INNER JOIN days x ON x.trade_date = d.trade_date
                WHERE d.amount IS NOT NULL AND d.amount > 0
                GROUP BY d.ts_code
                HAVING COUNT(*) >= 12
            )
            SELECT r.ts_code, r.ts_code AS name, '' AS industry, r.avg_amount, r.n_days
            FROM ranked r
            WHERE {board_sql}
            ORDER BY r.avg_amount DESC NULLS LAST
            LIMIT :lim
            """
        )
        try:
            df = pd.read_sql(sql_fb, engine, params={"start": start, "end": end, "lim": n})
        except Exception:
            return []
    if df.empty:
        return []
    rows = []
    for _, r in df.iterrows():
        rows.append(
            {
                "ts_code": str(r["ts_code"]).upper(),
                "name": str(r["name"] or r["ts_code"]),
                "industry": str(r["industry"] or ""),
                "avg_amount": float(r["avg_amount"] or 0.0),
            }
        )
    return rows


def read_bars_batch(
    engine,
    codes: Sequence[str],
    start: Optional[str],
    end: Optional[str],
) -> Dict[str, pd.DataFrame]:
    codes = sanitize_codes(codes)
    if not codes:
        return {}
    sql = (
        sa_text(
            """
            SELECT ts_code, trade_date, open, high, low, close, vol, amount
            FROM stock_daily
            WHERE ts_code IN :codes
            """
            + (" AND trade_date >= :start" if start else "")
            + (" AND trade_date <= :end" if end else "")
            + " ORDER BY ts_code, trade_date"
        )
        .bindparams(bindparam("codes", expanding=True))
    )
    params: Dict[str, Any] = {"codes": codes}
    if start:
        params["start"] = start
    if end:
        params["end"] = end
    df = pd.read_sql(sql, engine, params=params)
    if df.empty:
        return {}
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d")
    for col in ("open", "high", "low", "close", "vol", "amount"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["volume"] = df["vol"]
    out: Dict[str, pd.DataFrame] = {}
    for code, g in df.groupby("ts_code", sort=False):
        out[str(code).upper()] = g.reset_index(drop=True)
    return out


def name_map(engine, codes: Sequence[str]) -> Dict[str, str]:
    codes = sanitize_codes(codes)
    if not codes:
        return {}
    sql = sa_text(
        """
        SELECT ts_code, name FROM ref_stock_basic WHERE ts_code IN :codes
        """
    ).bindparams(bindparam("codes", expanding=True))
    try:
        df = pd.read_sql(sql, engine, params={"codes": codes})
    except Exception:
        return {}
    return {str(r["ts_code"]).upper(): str(r["name"] or "") for _, r in df.iterrows()}


def build_param_grid(strategy_id: str, base: Dict[str, Any]) -> List[Dict[str, Any]]:
    """小网格。仓位不用 100%，避免把单票暴利当成通用解。"""
    grid: List[Dict[str, Any]] = [dict(base)]
    if strategy_id == "ma60_shrink":
        variants = [
            {"vol_expand": 1.5, "b_ma60_high": 25, "ma5_doji_excess": 5, "slope_hits": 8, "position_pct": 30},
            {"vol_expand": 1.8, "b_ma60_high": 35, "ma5_doji_excess": 4, "slope_hits": 8, "position_pct": 30},
            {"vol_expand": 1.5, "b_ma60_high": 35, "ma5_doji_excess": 8, "slope_hits": 8, "position_pct": 30},
            {"vol_expand": 1.8, "b_ma60_high": 20, "ma5_doji_excess": 4, "slope_hits": 6, "position_pct": 30},
            {"vol_expand": 1.5, "b_ma60_high": 25, "ma5_doji_excess": 5, "slope_hits": 8, "position_pct": 50},
            {"vol_expand": 2.0, "b_ma60_high": 40, "ma5_doji_excess": 6, "slope_hits": 8, "position_pct": 30},
        ]
        for v in variants:
            item = dict(base)
            item.update(v)
            grid.append(item)
    elif strategy_id == "dual_ma":
        for fast, slow, pct in ((5, 20, 30), (10, 30, 30), (5, 10, 30), (5, 20, 50)):
            item = dict(base)
            item.update({"fast": fast, "slow": slow, "position_pct": pct})
            grid.append(item)
    # 去重
    uniq: List[Dict[str, Any]] = []
    seen = set()
    for item in grid:
        key = tuple(sorted((k, str(item.get(k))) for k in sorted(item.keys()) if k in (
            "vol_expand", "b_ma60_high", "ma5_doji_excess", "slope_hits", "position_pct",
            "fast", "slow", "min_dev", "crash_dev",
        )))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(item)
    return uniq[:8]


def decorate_row(
    ts_code: str,
    name: str,
    result: Dict[str, Any],
    split: str = "",
) -> Dict[str, Any]:
    ret = float(result.get("total_return") or 0.0)
    dd = float(result.get("max_drawdown") or 0.0)
    bh = float(result.get("buyhold_return") or 0.0)
    return {
        "ts_code": ts_code,
        "name": name,
        "total_return": round(ret, 4),
        "max_drawdown": round(dd, 4),
        "sharpe": round(float(result.get("sharpe") or 0.0), 2),
        "win_rate": round(float(result.get("win_rate") or 0.0), 4),
        "trade_count": int(result.get("trade_count") or 0),
        "final_equity": result.get("final_equity"),
        "buyhold_return": round(bh, 4),
        "excess_return": round(ret - bh, 4),
        "calmar": round(calmar(ret, dd), 4),
        "split": split,
        "skipped": False,
    }


REGIME_LABELS = {
    "trend_up": "上升趋势",
    "trend_down": "下降趋势",
    "range": "震荡市",
    "high_vol": "高波动",
    "unknown": "未知",
}

_SIGNAL_RULES = (
    ("折价15%放量买", "深折价放量买", "买入"),
    ("斜率转正", "斜率转正当天买", "买入"),
    ("趋势回踩开仓", "趋势回踩开仓", "买入"),
    ("趋势回踩补仓", "趋势回踩补仓", "买入"),
    ("缩量折价买", "缩量折价买", "买入"),
    ("十字星", "高位十字星卖", "卖出"),
    ("MA20放量兑现", "MA20放量兑现", "卖出"),
    ("远超MA60", "远超MA60放量卖", "卖出"),
    ("远离MA5", "远离MA5放量清仓", "卖出"),
    ("跌破MA60", "跌破MA60离场", "卖出"),
    ("站上MA5放量长阴", "站上MA5放量长阴卖", "卖出"),
)

MIN_SIGNAL_N = 8


def normalize_signal(reason: Any, side: str = "") -> str:
    text = str(reason or "")
    for key, label, _kind in _SIGNAL_RULES:
        if key in text:
            return label
    if "买入" in str(side):
        return "其他买入"
    if "卖出" in str(side):
        return "其他卖出"
    return text[:16] or "未知信号"


def daily_regime_map(df: pd.DataFrame) -> Dict[str, str]:
    """只用当日及以前的均线/波动，不按全程分位打标签（避免用未来定义牛熊）。"""
    if df is None or df.empty or "close" not in df.columns:
        return {}
    g = df.sort_values("trade_date").reset_index(drop=True)
    close = pd.to_numeric(g["close"], errors="coerce")
    dates = g["trade_date"].astype(str).str[:10]
    ma20 = close.rolling(20, min_periods=20).mean()
    ma60 = close.rolling(60, min_periods=60).mean()
    vol = close.pct_change().rolling(20, min_periods=20).std()
    vol_q = vol.rolling(120, min_periods=40).quantile(0.8)
    out: Dict[str, str] = {}
    for i in range(len(g)):
        day = dates.iloc[i]
        if pd.isna(ma60.iloc[i]) or pd.isna(ma20.iloc[i]):
            out[day] = "unknown"
            continue
        c = float(close.iloc[i])
        m20 = float(ma20.iloc[i])
        m60 = float(ma60.iloc[i])
        if pd.notna(vol.iloc[i]) and pd.notna(vol_q.iloc[i]) and float(vol_q.iloc[i]) > 0 and float(vol.iloc[i]) > float(vol_q.iloc[i]):
            out[day] = "high_vol"
        elif c > m20 > m60:
            out[day] = "trend_up"
        elif c < m20 < m60:
            out[day] = "trend_down"
        else:
            out[day] = "range"
    return out


def after_return(df: pd.DataFrame, date: str, days: int = 5) -> Optional[float]:
    if df is None or df.empty:
        return None
    ds = df["trade_date"].astype(str).str[:10].reset_index(drop=True)
    close = pd.to_numeric(df["close"], errors="coerce").reset_index(drop=True)
    hit = ds[ds == str(date)[:10]].index
    if len(hit) == 0:
        return None
    i = int(hit[0])
    j = i + int(days)
    if j >= len(close):
        return None
    c0 = float(close.iloc[i] or 0)
    c1 = float(close.iloc[j] or 0)
    if c0 <= 0:
        return None
    return float(c1 / c0 - 1)


def pair_round_trips(trades: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    trips: List[Dict[str, Any]] = []
    entry_sig = None
    entry_date = None
    exit_sig = None
    exit_date = None
    cost = 0.0
    pnl = 0.0
    for t in trades or []:
        side = str(t.get("side") or "")
        reason = t.get("reason")
        if side == "买入":
            if entry_sig is None:
                entry_sig = normalize_signal(reason, "买入")
                entry_date = str(t.get("date") or "")[:10]
            cost += float(t.get("price") or 0) * float(t.get("size") or 0) + float(t.get("commission") or 0)
        elif side == "卖出":
            exit_sig = normalize_signal(reason, "卖出")
            exit_date = str(t.get("date") or "")[:10]
            if t.get("pnl") is not None:
                pnl += float(t.get("pnl") or 0)
            if int(t.get("position_after") or 0) == 0 and entry_sig:
                trips.append(
                    {
                        "entry_signal": entry_sig,
                        "exit_signal": exit_sig,
                        "entry_date": entry_date,
                        "exit_date": exit_date,
                        "ret": (pnl / cost) if cost > 0 else 0.0,
                        "pnl": pnl,
                        "cost": cost,
                    }
                )
                entry_sig = None
                entry_date = None
                exit_sig = None
                exit_date = None
                cost = 0.0
                pnl = 0.0
    return trips


def _cell_stats(trips: Sequence[Dict[str, Any]], ret_key: str = "ret") -> Dict[str, Any]:
    n = len(trips)
    rets = [float(t.get(ret_key) or 0.0) for t in trips]
    after = [float(t["after_sell_5d"]) for t in trips if t.get("after_sell_5d") is not None]
    return {
        "n": n,
        "win_rate": round(sum(1 for x in rets if x > 0) / n, 4) if n else 0.0,
        "median_ret": round(_median(rets), 4) if n else 0.0,
        "mean_ret": round(_mean(rets), 4) if n else 0.0,
        "median_after_5d": round(_median(after), 4) if after else None,
    }


def summarize_signal_trips(
    trips: Sequence[Dict[str, Any]],
    current_regime: str = "unknown",
    min_n: int = MIN_SIGNAL_N,
) -> Dict[str, Any]:
    """按 买入信号×阶段、卖出信号×阶段 汇总；建议只看当前阶段且样本足够的格子。"""
    by_entry: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    by_exit: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for t in trips:
        regime = str(t.get("regime") or "unknown")
        by_entry.setdefault((str(t.get("entry_signal") or "未知"), regime), []).append(t)
        by_exit.setdefault((str(t.get("exit_signal") or "未知"), regime), []).append(t)

    def rows_from(groups: Dict[Tuple[str, str], List[Dict[str, Any]]], kind: str) -> List[Dict[str, Any]]:
        rows = []
        for (signal, regime), items in groups.items():
            st = _cell_stats(items)
            st.update(
                {
                    "kind": kind,
                    "signal": signal,
                    "regime": regime,
                    "regime_label": REGIME_LABELS.get(regime, regime),
                    "current": regime == current_regime,
                }
            )
            rows.append(st)
        rows.sort(key=lambda r: (0 if r["current"] else 1, -r["n"], str(r["signal"])))
        return rows

    entry_rows = rows_from(by_entry, "买入")
    exit_rows = rows_from(by_exit, "卖出")

    advice = []
    for row in entry_rows:
        if row["regime"] != current_regime:
            continue
        if row["n"] < min_n:
            action, note = "观察", f"样本不足（{row['n']} < {min_n}）"
        elif row["median_ret"] > 0 and row["win_rate"] >= 0.45:
            action, note = "采用", "该阶段买入后中位收益为正"
        elif row["median_ret"] < 0:
            action, note = "暂停", "该阶段买入后中位收益为负"
        else:
            action, note = "观察", "胜率或收益不明显"
        advice.append(
            {
                "kind": "买入",
                "signal": row["signal"],
                "regime": current_regime,
                "regime_label": REGIME_LABELS.get(current_regime, current_regime),
                "action": action,
                "note": note,
                "n": row["n"],
                "median_ret": row["median_ret"],
                "win_rate": row["win_rate"],
            }
        )
    for row in exit_rows:
        if row["regime"] != current_regime:
            continue
        after = row.get("median_after_5d")
        if row["n"] < min_n:
            action, note = "观察", f"样本不足（{row['n']} < {min_n}）"
        elif after is not None and after > 0.03:
            action, note = "慎用", "卖出后5日价格仍常继续涨，容易卖早"
        elif row["median_ret"] > 0:
            action, note = "采用", "搭配该卖点的完整交易中位收益为正"
        elif row["median_ret"] < 0:
            action, note = "暂停", "搭配该卖点的完整交易中位收益为负"
        else:
            action, note = "观察", "卖点区分度不高"
        advice.append(
            {
                "kind": "卖出",
                "signal": row["signal"],
                "regime": current_regime,
                "regime_label": REGIME_LABELS.get(current_regime, current_regime),
                "action": action,
                "note": note,
                "n": row["n"],
                "median_ret": row["median_ret"],
                "win_rate": row["win_rate"],
                "median_after_5d": after,
            }
        )
    order = {"采用": 0, "慎用": 1, "暂停": 2, "观察": 3}
    advice.sort(key=lambda a: (order.get(a["action"], 9), a["kind"], a["signal"]))
    return {
        "current_regime": current_regime,
        "current_regime_label": REGIME_LABELS.get(current_regime, current_regime),
        "min_n": min_n,
        "trip_count": len(trips),
        "entry_rows": entry_rows,
        "exit_rows": exit_rows,
        "advice": advice,
    }

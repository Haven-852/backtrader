"""机会库：东财二级行业，最近 5 个交易日。

成交额、主力资金只和该板块自己的近期水平比。
上涨家数、领涨股用成分股当日涨跌统计；主力净额优先用板块资金，没有时用成分股净额合计。
退潮不入库。没有这些数、或没有事件来源时，对应类型不标注，也不编造。
"""

from __future__ import annotations

import json
import logging
import statistics
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import text

from data_layer.db_manager import storage_manager

logger = logging.getLogger(__name__)

SOURCE = "dc_l2_scan"
LEVEL = "东财二级行业"
TYPE_PRIORITY = (
    "持续性",
    "趋势强化",
    "突破",
    "龙头带动",
    "板块共振",
    "资金流入",
    "弱转强",
    "超跌",
    "放量",
    "强度排名",
)

_ALTERS = (
    "ALTER TABLE opportunity ADD COLUMN IF NOT EXISTS sector_code VARCHAR(16)",
    "ALTER TABLE opportunity ADD COLUMN IF NOT EXISTS sector_name VARCHAR(64)",
    "ALTER TABLE opportunity ADD COLUMN IF NOT EXISTS sector_level VARCHAR(32)",
    "ALTER TABLE opportunity ADD COLUMN IF NOT EXISTS phase VARCHAR(20)",
    "ALTER TABLE opportunity ADD COLUMN IF NOT EXISTS matched_types JSONB",
    "ALTER TABLE opportunity ADD COLUMN IF NOT EXISTS amount_ratio NUMERIC(10,4)",
    "ALTER TABLE opportunity ADD COLUMN IF NOT EXISTS main_net NUMERIC(24,4)",
)


def _f(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _i(v: Any) -> Optional[int]:
    n = _f(v)
    if n is None:
        return None
    return int(n)


def _med(vals: Sequence[float]) -> Optional[float]:
    if not vals:
        return None
    return float(statistics.median(vals))


def _clip(n: float, lo: float = 0, hi: float = 100) -> float:
    return max(lo, min(hi, n))


def _yuan(n: float) -> str:
    if abs(n) >= 1e8:
        return f"{n / 1e8:.2f}亿元"
    if abs(n) >= 1e4:
        return f"{n / 1e4:.0f}万元"
    return f"{n:.0f}元"


def _as_date(v: Any) -> date:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()


def judge_sector(
    code: str,
    name: str,
    bars: List[Dict[str, Any]],
    signal_dates: Sequence[date],
    medians: Dict[date, float],
    ranks: Dict[Tuple[date, str], Dict[str, bool]],
) -> Optional[Dict[str, Any]]:
    """返回 None 表示不收录（含退潮）。"""
    signal_set = set(signal_dates)
    ordered = sorted(bars, key=lambda b: b["date"])
    window = [b for b in ordered if b["date"] in signal_set and b.get("pct") is not None]
    if not window:
        return None
    last = window[-1]
    prior = [b for b in ordered if b["date"] < window[0]["date"] and b.get("pct") is not None]
    prior_pcts = [b["pct"] for b in prior[-8:]]
    prior_cum = sum(prior_pcts) if prior_pcts else 0.0

    def excess(bar: Dict[str, Any]) -> Optional[float]:
        med = medians.get(bar["date"])
        if bar.get("pct") is None or med is None:
            return None
        return bar["pct"] - med

    last_ex = excess(last)
    if last_ex is None:
        return None

    peak = max(window, key=lambda b: b["pct"])
    ebb = False
    if peak["date"] != last["date"] and peak["pct"] >= 2 and last["pct"] < 0:
        if last.get("amount") and peak.get("amount"):
            ebb = last["amount"] < peak["amount"] * 0.85
        else:
            ebb = last_ex < -0.3
    if ebb:
        return {"ebb": True, "code": code, "name": name}

    base_amts = [b["amount"] for b in ordered if b["date"] < last["date"] and b.get("amount") and b["amount"] > 0]
    amount_ratio = None
    if len(base_amts) >= 3 and last.get("amount"):
        base = _med(base_amts)
        if base:
            amount_ratio = last["amount"] / base

    base_nets = [b["net"] for b in ordered if b["date"] < last["date"] and b.get("net") is not None]
    net_base = _med(base_nets) if len(base_nets) >= 3 else None

    up = last.get("up")
    down = last.get("down")
    up_ratio = None
    if up is not None and down is not None and (up + down) > 0:
        up_ratio = up / (up + down)

    types: List[str] = []
    if prior_cum >= 5 and len(window) >= 2:
        cooled = any((excess(b) or 0) < 0.3 for b in window[:-1])
        if cooled and last["pct"] > 0 and last_ex >= 0.5:
            types.append("趋势强化")
    if up_ratio is not None and up_ratio >= 0.65 and (up or 0) >= 8:
        types.append("板块共振")
    if last.get("net") is not None and last["net"] > 0:
        prev_nets = [b["net"] for b in window[:-1] if b.get("net") is not None]
        if prev_nets and prev_nets[-1] > 0 and (net_base is None or last["net"] >= net_base):
            types.append("资金流入")
    rank = ranks.get((last["date"], code), {})
    if rank.get("pct_top") or rank.get("amt_top") or rank.get("mf_top"):
        types.append("强度排名")
    leading_pct = last.get("leading_pct")
    if last.get("leading") and leading_pct is not None:
        if (leading_pct >= 5 or leading_pct >= last["pct"] + 3) and (up or 0) >= 3:
            types.append("龙头带动")
    prior_ex = [excess(b) for b in (prior[-5:] if prior else window[:-1])]
    prior_ex = [x for x in prior_ex if x is not None]
    if prior_ex and sum(prior_ex) / len(prior_ex) < 0 and last_ex >= 1.5:
        types.append("弱转强")
    if amount_ratio is not None and amount_ratio >= 1.5 and last["pct"] > 0:
        types.append("放量")
    if prior_cum <= -6 and last["pct"] > 0 and (amount_ratio is None or amount_ratio >= 0.9):
        types.append("超跌")
    if amount_ratio is not None and amount_ratio >= 1.8 and last["pct"] >= 2 and last_ex >= 1:
        if up_ratio is None or up_ratio >= 0.55:
            types.append("突破")
    pos_days = [b for b in window if b["pct"] > 0]
    ex_days = [b for b in window if (excess(b) or -999) > 0]
    earlier_pos = [b for b in window[:-1] if b["pct"] > 0]
    if len(pos_days) >= 3 and len(ex_days) >= 3 and len(earlier_pos) >= 1:
        types.append("持续性")

    early_hot = sum(1 for b in window[:-1] if (excess(b) or -999) >= 1)
    if prior_cum <= -4 and last["pct"] > 0 and last_ex >= 0.8:
        phase = "修复"
    elif prior_cum < 4 and last_ex >= 1 and last["pct"] >= 1.2 and early_hot <= 1:
        phase = "启动"
    elif "持续性" in types or "趋势强化" in types:
        phase = "持续强势"
    elif rank.get("pct_top") and last_ex >= 0.8 and last["pct"] > 0:
        phase = "持续强势"
    else:
        phase = None

    if phase not in ("启动", "修复", "持续强势"):
        return None
    if phase == "持续强势" and "强度排名" not in types and last_ex < 1.5:
        return None
    if not types and phase == "持续强势":
        return None
    if not types and phase in ("启动", "修复") and rank.get("pct_top"):
        types.append("强度排名")

    primary = next((item for item in TYPE_PRIORITY if item in types), phase)
    strength = 50 + last_ex * 10
    if amount_ratio is not None:
        strength += min(amount_ratio, 3) * 6
    if up_ratio is not None:
        strength += (up_ratio - 0.5) * 30
    strength = round(_clip(strength), 2)

    technical_keys = [k for k in ("趋势强化", "放量", "突破", "持续性", "超跌") if k in types]
    technical = round(_clip(45 + 12 * len(technical_keys)), 2) if technical_keys else None
    sentiment = round(up_ratio * 100, 2) if up_ratio is not None else None
    parts = [n for n in (strength, technical, sentiment) if n is not None]
    score = round(sum(parts) / len(parts), 2) if parts else None
    confidence = 0.45
    if amount_ratio is not None:
        confidence += 0.15
    if up_ratio is not None:
        confidence += 0.1
    if last.get("net") is not None:
        confidence += 0.1
    if last.get("leading"):
        confidence += 0.05
    confidence = round(min(confidence, 0.9), 4)

    path = [
        {
            "trade_date": b["date"].isoformat(),
            "pct": round(b["pct"], 2),
            "excess": None if excess(b) is None else round(excess(b), 2),
        }
        for b in window
    ]
    bits = [phase]
    bits.append(f"今日 {last['pct']:+.2f}%，相对二级行业中位数超额 {last_ex:+.2f}%")
    bits.append(f"窗口前累计 {prior_cum:+.2f}%")
    if amount_ratio is not None:
        bits.append(f"成交额是该板块自身近期中位数的 {amount_ratio:.2f} 倍")
    else:
        bits.append("自身成交额样本不足，没有用其他板块的成交额代替")
    if last.get("net") is not None:
        flow_bit = f"主力净额 {_yuan(last['net'])}"
        if last.get("flow_from_members"):
            flow_bit += "（成分股净额合计，万元已换算成元）"
        bits.append(flow_bit)
    else:
        bits.append("这一天没有主力净额，不标资金流入")
    if up is None:
        bits.append("这一天没有上涨家数和领涨股，不标板块共振和龙头带动")
    else:
        breadth_bit = f"上涨 {up} 家，下跌 {down or 0} 家"
        if last.get("breadth_from_members"):
            breadth_bit += "（由成分股当日涨跌统计）"
        bits.append(breadth_bit)
    if last.get("leading") and leading_pct is not None:
        bits.append(f"领涨 {last['leading']} {leading_pct:+.2f}%")
    bits.append("命中：" + "、".join(types) if types else "只标出时机，没有同时命中分项类型")
    return {
        "ebb": False,
        "code": code,
        "name": name,
        "trade_date": last["date"],
        "phase": phase,
        "opportunity_type": primary,
        "matched_types": types,
        "trigger_reason": "。".join(bits) + "。",
        "sector_strength": strength,
        "technical_score": technical,
        "fundamental_score": None,
        "sentiment_score": sentiment,
        "opportunity_score": score,
        "confidence": confidence,
        "amount_ratio": None if amount_ratio is None else round(amount_ratio, 4),
        "main_net": last.get("net"),
        "evidence": {
            "level": LEVEL,
            "phase": phase,
            "matched_types": types,
            "prior_cum_pct": round(prior_cum, 2),
            "pct": round(last["pct"], 2),
            "excess_pct": round(last_ex, 2),
            "amount": last.get("amount"),
            "amount_ratio": None if amount_ratio is None else round(amount_ratio, 4),
            "amount_baseline_days": len(base_amts),
            "main_net": last.get("net"),
            "main_net_baseline": None if net_base is None else round(net_base, 2),
            "up_num": up,
            "down_num": down,
            "up_ratio": None if up_ratio is None else round(up_ratio, 4),
            "leading_name": last.get("leading"),
            "leading_pct": leading_pct,
            "breadth_from_members": bool(last.get("breadth_from_members")),
            "flow_from_members": bool(last.get("flow_from_members")),
            "path": path,
            "rules": {
                "volume": "成交额 / 该板块更早交易日成交额中位数，至少 3 天",
                "flow": "主力净额和该板块自己的近期净额比，没有净额就不标资金流入",
                "ebb": "5 日高点不在最后一天，最后一天转跌，且成交额低于高点当天的 85%",
                "event": "没有事件来源，不标事件催化",
            },
        },
    }


def _stock_row(code: str, name: str, ordered: List[Dict[str, Any]], last: date) -> Dict[str, Any]:
    last_bar = next(bar for bar in ordered if bar["date"] == last)
    prior = [bar for bar in ordered if bar["date"] < last]
    return {
        "ts_code": code,
        "name": name,
        "last_pct": round(last_bar["pct"], 2),
        "cum_pct": round(sum(bar["pct"] for bar in ordered), 2),
        "prior_pct": round(sum(bar["pct"] for bar in prior), 2),
    }


def _is_pullback(ordered: List[Dict[str, Any]], last: date) -> bool:
    """窗口内先涨起来，最后一天下跌，收盘仍高于起点，且回吐不超过这段涨幅的一半。"""
    last_bar = next(bar for bar in ordered if bar["date"] == last)
    prior = [bar for bar in ordered if bar["date"] < last]
    if not prior or last_bar["pct"] >= 0 or last_bar["pct"] <= -7:
        return False
    if sum(bar["pct"] for bar in prior) < 4:
        return False
    closes = [bar["close"] for bar in ordered if bar.get("close")]
    if len(closes) < 2:
        return sum(bar["pct"] for bar in ordered) > 0
    first = ordered[0]["close"]
    end = last_bar["close"]
    peak = max(ordered, key=lambda bar: bar["close"] or -1)
    if not first or not end or not peak.get("close") or end <= first or peak["close"] <= first:
        return False
    if peak["date"] == ordered[0]["date"]:
        return False
    giveback = (peak["close"] - end) / (peak["close"] - first)
    return giveback <= 0.5


def _extract_pick_rows(text_out: str) -> List[Dict[str, Any]]:
    """JSON 被截断时，仍收下已经写完整的推荐。"""
    import re

    rows = []
    pattern = re.compile(
        r'"ts_code"\s*:\s*"([^"]+)"\s*,\s*"name"\s*:\s*"([^"]*)"\s*,\s*"role"\s*:\s*"([^"]*)"\s*,\s*"reason"\s*:\s*"([^"]*)"',
    )
    for ts_code, name, role, reason in pattern.findall(text_out or ""):
        rows.append({
            "ts_code": ts_code,
            "name": name or ts_code,
            "role": role or "成分",
            "reason": reason[:200],
        })
    return rows


def _parse_picks(text_out: str) -> List[Dict[str, Any]]:
    raw = text_out or ""
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return []
    try:
        payload = json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return []
    sectors = payload.get("sectors") if isinstance(payload, dict) else None
    return sectors if isinstance(sectors, list) else []


def _watch_members(book: Dict[str, Any], window: Sequence[date], last: date) -> Dict[str, Any]:
    roster = []
    for code, item in book.items():
        if str(code).startswith(("200", "900")):
            continue
        name = item.get("name") or code
        if "ST" in str(name).upper():
            continue
        ordered = sorted(
            [bar for bar in item.get("bars") or [] if bar["date"] in set(window) and bar.get("pct") is not None],
            key=lambda bar: bar["date"],
        )
        if not any(bar["date"] == last for bar in ordered):
            continue
        row = _stock_row(code, name, ordered, last)
        row["pullback"] = _is_pullback(ordered, last)
        roster.append(row)
    if not roster:
        return {
            "member_count": 0,
            "quoted": 0,
            "leader": None,
            "peers": [],
            "pullbacks": [],
            "roster": [],
            "summary": "这个板块在窗口内没有可用的成分股日线，没有编造个股表现。",
        }
    leader = max(roster, key=lambda row: row["last_pct"])
    peers = [row for row in roster if row["ts_code"] != leader["ts_code"] and row["last_pct"] > 0]
    peers.sort(key=lambda row: row["last_pct"], reverse=True)
    pullbacks = [row for row in roster if row["pullback"] and row["ts_code"] != leader["ts_code"]]
    pullbacks.sort(key=lambda row: row["prior_pct"], reverse=True)
    peers = peers[:4]
    pullbacks = pullbacks[:4]
    bits = [f"领涨 {leader['name']}（当日 {leader['last_pct']:+.2f}%）"]
    if peers:
        bits.append("跟涨 " + "、".join(row["name"] for row in peers))
    if pullbacks:
        bits.append("涨后回调 " + "、".join(
            f"{row['name']}（窗口内先涨 {row['prior_pct']:+.2f}%，当日 {row['last_pct']:+.2f}%）"
            for row in pullbacks
        ))
    else:
        bits.append("没有符合「先涨起来、最后短暂回调、仍高于窗口起点」的其他成分股")
    return {
        "member_count": len(book),
        "quoted": len(roster),
        "leader": {k: leader[k] for k in ("ts_code", "name", "last_pct", "cum_pct")},
        "peers": [{k: row[k] for k in ("ts_code", "name", "last_pct", "cum_pct")} for row in peers],
        "pullbacks": [{k: row[k] for k in ("ts_code", "name", "last_pct", "cum_pct", "prior_pct")} for row in pullbacks],
        "roster": [{k: row[k] for k in ("ts_code", "name", "last_pct", "cum_pct")} for row in roster],
        "summary": "成分股里值得看：" + "；".join(bits) + "。",
    }


class OpportunityScanService:
    def __init__(self) -> None:
        self._engine = None
        self._ready = False

    @property
    def engine(self):
        if self._engine is None:
            self._engine = storage_manager.get_postgres_engine()
        if self._engine is None:
            raise RuntimeError("PostgreSQL 未连接")
        return self._engine

    def ensure(self) -> None:
        if self._ready:
            return
        with self.engine.begin() as conn:
            for ddl in _ALTERS:
                conn.execute(text(ddl))
        self._ready = True

    def _open_days(self, conn, end: date, n: int) -> List[date]:
        rows = conn.execute(
            text(
                """
                SELECT cal_date FROM ref_trade_cal
                WHERE exchange = 'SSE' AND is_open = 1 AND cal_date <= :d
                ORDER BY cal_date DESC
                LIMIT :n
                """
            ),
            {"d": end, "n": n},
        ).fetchall()
        return sorted(_as_date(r[0]) for r in rows)

    def _load_bars(self, conn, start: date) -> Dict[str, Dict[str, Any]]:
        rows = conn.execute(
            text(
                """
                SELECT i.ts_code, i.trade_date, i.name, i.pct_change, i.up_num, i.down_num,
                       i.leading_name, i.leading_pct, d.amount, d.close, m.net_amount
                FROM sector_dc_index_daily i
                LEFT JOIN sector_dc_daily d
                  ON d.ts_code = i.ts_code AND d.trade_date = i.trade_date
                LEFT JOIN sector_moneyflow_daily m
                  ON m.ts_code = i.ts_code AND m.trade_date = i.trade_date
                 AND m.content_type = '行业'
                WHERE i.industry_level = :lv AND i.idx_type = '行业板块'
                  AND i.trade_date >= :start
                """
            ),
            {"lv": LEVEL, "start": start},
        ).fetchall()
        books: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            code = str(r[0])
            book = books.setdefault(code, {"name": r[2] or code, "bars": []})
            if r[2]:
                book["name"] = r[2]
            book["bars"].append({
                "date": _as_date(r[1]),
                "pct": _f(r[3]),
                "up": _i(r[4]),
                "down": _i(r[5]),
                "leading": r[6] or None,
                "leading_pct": _f(r[7]),
                "amount": _f(r[8]),
                "close": _f(r[9]),
                "net": _f(r[10]),
            })
        return books

    def _overlay_members(self, conn, books: Dict[str, Dict[str, Any]], signal_dates: Sequence[date]) -> Dict[str, Dict[str, Any]]:
        """用成分股当日涨跌补上涨家数和领涨；板块资金为空时用成分股净额合计。"""
        codes = list(books.keys())
        if not codes or not signal_dates:
            return {}
        rows = conn.execute(
            text(
                """
                WITH mem AS (
                    SELECT DISTINCT ON (m.ts_code, m.con_code)
                           m.ts_code AS sector, m.con_code,
                           COALESCE(b.name, m.name) AS name
                    FROM sector_dc_member m
                    LEFT JOIN ref_stock_basic b ON b.ts_code = m.con_code
                    WHERE m.ts_code = ANY(:codes)
                    ORDER BY m.ts_code, m.con_code, m.trade_date DESC
                )
                SELECT mem.sector, mem.con_code, mem.name, d.trade_date, d.close, d.pct_chg, f.net_mf_amount
                FROM mem
                JOIN stock_daily d
                  ON d.ts_code = mem.con_code AND d.trade_date = ANY(:days)
                LEFT JOIN stock_moneyflow_daily f
                  ON f.ts_code = mem.con_code AND f.trade_date = d.trade_date
                """
            ),
            {"codes": codes, "days": list(signal_dates)},
        ).fetchall()
        quotes: Dict[str, Dict[str, Any]] = {}
        buckets: Dict[Tuple[str, date], List[Dict[str, Any]]] = {}
        for sector, code, name, trade_date, close, pct, net in rows:
            pct_f = _f(pct)
            if pct_f is None:
                continue
            day = _as_date(trade_date)
            series = quotes.setdefault(str(sector), {}).setdefault(str(code), {"name": name or code, "bars": []})
            series["bars"].append({"date": day, "close": _f(close), "pct": pct_f})
            buckets.setdefault((str(sector), day), []).append({
                "code": str(code),
                "name": name or code,
                "pct": pct_f,
                "net": _f(net),
            })
        for (sector, day), items in buckets.items():
            book = books.get(sector)
            if not book:
                continue
            bar = next((item for item in book["bars"] if item["date"] == day), None)
            if bar is None:
                continue
            if bar.get("up") is None:
                bar["up"] = sum(1 for item in items if item["pct"] > 0)
                bar["down"] = sum(1 for item in items if item["pct"] < 0)
                bar["breadth_from_members"] = True
            if not bar.get("leading"):
                top = max(items, key=lambda item: item["pct"])
                bar["leading"] = top["name"]
                bar["leading_pct"] = top["pct"]
                bar["leading_code"] = top["code"]
                bar["breadth_from_members"] = True
            if bar.get("net") is None:
                nets = [item["net"] for item in items if item["net"] is not None]
                if nets:
                    bar["net"] = sum(nets) * 10000
                    bar["flow_from_members"] = True
        return quotes

    def _regime(self, conn, trade_date: date) -> Optional[str]:
        try:
            row = conn.execute(
                text(
                    """
                    SELECT regime_label FROM market_regime
                    WHERE trade_date = :d
                    ORDER BY created_at DESC
                    LIMIT 1
                    """
                ),
                {"d": trade_date},
            ).fetchone()
        except Exception:
            return None
        return str(row[0]) if row else None

    def missing_dates(self) -> Dict[str, Any]:
        """最近 5 个交易日里，东财二级行业没有行情的日期。"""
        self.ensure()
        with self.engine.connect() as conn:
            latest = conn.execute(
                text(
                    """
                    SELECT MAX(trade_date) FROM sector_dc_index_daily
                    WHERE industry_level = :lv AND idx_type = '行业板块'
                    """
                ),
                {"lv": LEVEL},
            ).scalar()
            if not latest:
                return {"ok": False, "error": "还没有东财二级行业行情", "missing": [], "signal_dates": []}
            latest_d = _as_date(latest)
            signal_dates = self._open_days(conn, latest_d, 5)
            covered_rows = conn.execute(
                text(
                    """
                    SELECT trade_date FROM sector_dc_index_daily
                    WHERE industry_level = :lv AND idx_type = '行业板块'
                      AND trade_date = ANY(:days)
                    GROUP BY trade_date
                    HAVING COUNT(*) >= 30
                    """
                ),
                {"lv": LEVEL, "days": signal_dates},
            ).fetchall()
        covered = {_as_date(r[0]) for r in covered_rows}
        missing = [d.isoformat() for d in signal_dates if d not in covered]
        return {
            "ok": True,
            "signal_dates": [d.isoformat() for d in signal_dates],
            "missing": missing,
        }

    async def fill_missing_via_agent(self) -> Dict[str, Any]:
        """把缺日告诉大模型，由它调用补数工具写入数据库。"""
        found = self.missing_dates()
        if not found.get("ok"):
            return found
        missing = found.get("missing") or []
        if not missing:
            return {"ok": True, "filled": False, "missing": [], "note": "最近 5 个交易日的东财二级行业行情是齐的。"}
        from server.data_gap_agent import fill_by_agent

        task = (
            "机会库扫描东财二级行业时，最近 5 个交易日是 "
            + "、".join(found["signal_dates"])
            + "。数据库缺少这些交易日的板块行情："
            + "、".join(missing)
            + "。请对每个缺少的日期调用一次 datahubco_data__fill_sector_gap，"
            "参数 trade_date 用该日期，level 用东财二级行业。"
            "不要改用 get-bars 逐只股票查询，也不要编造涨跌幅或成交额。"
            "工具返回后，说明每个日期写入了多少条、来源是什么；如果工具失败，原样说明失败原因。"
        )

        def verify() -> Dict[str, Any]:
            again = self.missing_dates()
            gap = again.get("missing") or []
            return {
                "complete": not gap and again.get("ok", False),
                "missing": gap,
                "detail": "板块行情已齐" if not gap else "仍缺 " + "、".join(gap),
            }

        return await fill_by_agent(task, verify)

    def scan(self) -> Dict[str, Any]:
        self.ensure()
        with self.engine.connect() as conn:
            latest = conn.execute(
                text(
                    """
                    SELECT MAX(trade_date) FROM sector_dc_index_daily
                    WHERE industry_level = :lv AND idx_type = '行业板块'
                    """
                ),
                {"lv": LEVEL},
            ).scalar()
            if not latest:
                return {"ok": False, "error": "还没有东财二级行业行情"}
            latest = _as_date(latest)
            signal_dates = self._open_days(conn, latest, 5)
            books = self._load_bars(conn, latest - timedelta(days=40))
            quotes = self._overlay_members(conn, books, signal_dates)
            regime = self._regime(conn, latest)

        covered = set()
        for book in books.values():
            for bar in book["bars"]:
                if bar.get("pct") is not None:
                    covered.add(bar["date"])
        missing = [d for d in signal_dates if d not in covered]
        present = [d for d in signal_dates if d in covered]
        if len(present) < 2:
            return {"ok": False, "error": "最近 5 个交易日里，东财二级行业可用天数不足"}

        by_day: Dict[date, List[Tuple[str, Dict[str, Any]]]] = {}
        for code, book in books.items():
            for bar in book["bars"]:
                if bar.get("pct") is None:
                    continue
                by_day.setdefault(bar["date"], []).append((code, bar))
        medians = {d: _med([bar["pct"] for _, bar in rows]) or 0.0 for d, rows in by_day.items()}
        ranks: Dict[Tuple[date, str], Dict[str, bool]] = {}
        for d, rows in by_day.items():
            n = len(rows)
            if not n:
                continue
            top_n = max(1, int(n * 0.15 + 0.999))
            for key, getter in (("pct_top", "pct"), ("amt_top", "amount"), ("mf_top", "net")):
                usable = [(code, bar.get(getter)) for code, bar in rows if bar.get(getter) is not None]
                usable.sort(key=lambda item: item[1], reverse=True)
                for code, _val in usable[:top_n]:
                    ranks.setdefault((d, code), {})[key] = True

        found = []
        ebb_n = 0
        for code, book in books.items():
            judged = judge_sector(code, book["name"], book["bars"], present, medians, ranks)
            if not judged:
                continue
            if judged.get("ebb"):
                ebb_n += 1
                continue
            found.append(judged)
        for item in found:
            watch = _watch_members(
                quotes.get(item["code"]) or {},
                present,
                item["trade_date"],
            )
            item["evidence"]["stocks"] = watch
            if watch.get("summary"):
                item["trigger_reason"] = item["trigger_reason"] + watch["summary"]
        found.sort(key=lambda item: (-(item.get("opportunity_score") or 0), item["name"]))

        breadth_days = 0
        flow_days = 0
        for d in present:
            rows = by_day.get(d) or []
            if any(bar.get("up") is not None for _, bar in rows):
                breadth_days += 1
            if any(bar.get("net") is not None for _, bar in rows):
                flow_days += 1
        notes = [
            "只扫描东财二级行业。成交额和主力资金都跟板块自己的近期水平比。",
            "退潮板块没有写入机会库。",
            "事件催化没有公告或新闻来源，本次不标注。",
        ]
        if missing:
            notes.append("这 5 个交易日里，库内缺少 " + "、".join(d.isoformat() for d in missing) + " 的二级行业行情，判断用已有交易日。")
        if breadth_days == 0:
            notes.append("这些交易日没有上涨家数和领涨股，板块共振、龙头带动本次不标注。")
        if flow_days == 0:
            notes.append("这些交易日没有主力净额，资金流入本次不标注。")

        with self.engine.begin() as conn:
            if present:
                conn.execute(
                    text(
                        """
                        DELETE FROM opportunity
                        WHERE source = :src AND trade_date BETWEEN :a AND :b
                        """
                    ),
                    {"src": SOURCE, "a": present[0], "b": present[-1]},
                )
            for item in found:
                conn.execute(
                    text(
                        """
                        INSERT INTO opportunity (
                            trade_date, opportunity_type, trigger_reason, market_regime,
                            sector_strength, technical_score, fundamental_score, sentiment_score,
                            opportunity_score, confidence, status, evidence, source,
                            sector_code, sector_name, sector_level, phase, matched_types,
                            amount_ratio, main_net
                        ) VALUES (
                            :d, :typ, :reason, :regime,
                            :strength, :technical, :fundamental, :sentiment,
                            :score, :confidence, 'NEW', CAST(:evidence AS jsonb), :src,
                            :code, :name, :level, :phase, CAST(:types AS jsonb),
                            :amount_ratio, :main_net
                        )
                        """
                    ),
                    {
                        "d": item["trade_date"],
                        "typ": item["opportunity_type"],
                        "reason": item["trigger_reason"],
                        "regime": regime,
                        "strength": item["sector_strength"],
                        "technical": item["technical_score"],
                        "fundamental": item["fundamental_score"],
                        "sentiment": item["sentiment_score"],
                        "score": item["opportunity_score"],
                        "confidence": item["confidence"],
                        "evidence": json.dumps(item["evidence"], ensure_ascii=False),
                        "src": SOURCE,
                        "code": item["code"],
                        "name": item["name"][:64],
                        "level": LEVEL,
                        "phase": item["phase"],
                        "types": json.dumps(item["matched_types"], ensure_ascii=False),
                        "amount_ratio": item["amount_ratio"],
                        "main_net": item["main_net"],
                    },
                )
        return {
            "ok": True,
            "level": LEVEL,
            "signal_dates": [d.isoformat() for d in signal_dates],
            "used_dates": [d.isoformat() for d in present],
            "missing_dates": [d.isoformat() for d in missing],
            "skipped_ebb": ebb_n,
            "saved": len(found),
            "note": "".join(notes),
            "items": self._public_items(found),
        }

    def _public_items(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out = []
        for item in rows:
            out.append({
                "sector_code": item["code"],
                "sector_name": item["name"],
                "name": item["name"],
                "trade_date": item["trade_date"].isoformat(),
                "phase": item["phase"],
                "opportunity_type": item["opportunity_type"],
                "matched_types": item["matched_types"],
                "trigger_reason": item["trigger_reason"],
                "sector_strength": item["sector_strength"],
                "technical_score": item["technical_score"],
                "fundamental_score": item["fundamental_score"],
                "sentiment_score": item["sentiment_score"],
                "opportunity_score": item["opportunity_score"],
                "confidence": item["confidence"],
                "amount_ratio": item["amount_ratio"],
                "main_net": item["main_net"],
                "evidence": item["evidence"],
                "status": "NEW",
                "source": SOURCE,
            })
        return out

    def missing_flow_dates(self) -> Dict[str, Any]:
        info = self.missing_dates()
        if not info.get("ok"):
            return info
        days = [datetime.strptime(day, "%Y-%m-%d").date() for day in info["signal_dates"]]
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT trade_date FROM stock_moneyflow_daily
                    WHERE trade_date = ANY(:days)
                    GROUP BY trade_date
                    HAVING COUNT(*) >= 3000
                    """
                ),
                {"days": days},
            ).fetchall()
        have = {_as_date(row[0]) for row in rows}
        missing = [day.isoformat() for day in days if day not in have]
        return {"ok": True, "missing": missing, "signal_dates": info["signal_dates"]}

    async def fill_missing_moneyflow(self) -> Dict[str, Any]:
        """缺整日个股资金流向时，让大模型调用 get_fund_data 写入。"""
        found = self.missing_flow_dates()
        if not found.get("ok"):
            return found
        missing = found.get("missing") or []
        if not missing:
            return {"ok": True, "filled": False, "missing": [], "note": "最近 5 个交易日的个股资金流向是齐的。"}
        from server.data_gap_agent import fill_by_agent

        task = (
            "机会库扫描需要全市场个股资金流向。数据库缺少这些交易日："
            + "、".join(missing)
            + "。请对每个日期调用一次 datahubco_data__get_fund_data，"
            "dataset 用 moneyflow，trade_date 用 8 位数字，source 用 datahubco，write_back 用 true。"
            "不要传 ts_code，也不要编造主力净额。工具失败就原样说明原因。"
        )

        def verify() -> Dict[str, Any]:
            again = self.missing_flow_dates()
            gap = again.get("missing") or []
            return {
                "complete": not gap and again.get("ok", False),
                "missing": gap,
                "detail": "个股资金流向已齐" if not gap else "仍缺 " + "、".join(gap),
            }

        agent = await fill_by_agent(task, verify)
        still = list(agent.get("still_missing") or [])
        if still:
            from tools.datahubco_toolkit import datahubco_toolkit

            direct = []
            for day in still:
                packed = datahubco_toolkit.get_fund_data(
                    dataset="moneyflow",
                    trade_date=day.replace("-", ""),
                    source="datahubco",
                    write_back=True,
                    limit=5,
                )
                direct.append({
                    "trade_date": day,
                    "ok": bool(packed.get("ok")),
                    "upserted": packed.get("upserted"),
                    "error": packed.get("error"),
                })
            agent["direct"] = direct
            agent["still_missing"] = self.missing_flow_dates().get("missing") or []
            agent["complete"] = not agent["still_missing"]
        return agent

    async def review_picks(self, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        """让大模型只在已算出的成分股里挑几只，不补充名单外的股票。"""
        if not items:
            return {"ok": True, "note": "没有板块需要挑选"}
        blocks = []
        allowed: Dict[str, set] = {}
        for item in items:
            stocks = (item.get("evidence") or {}).get("stocks") or {}
            code = item.get("sector_code") or item.get("code")
            allowed[code] = set()
            lines = [
                f"板块 {item.get('sector_name') or item.get('name')} {code} "
                f"时机 {item.get('phase')} 类型 {'、'.join(item.get('matched_types') or [])}"
            ]
            if stocks.get("leader"):
                row = stocks["leader"]
                allowed[code].add(row["ts_code"])
                lines.append(
                    f"领涨 {row['name']} {row['ts_code']} 当日 {row['last_pct']:+.2f}% 窗口累计 {row['cum_pct']:+.2f}%"
                )
            for role, key in (("跟涨", "peers"), ("涨后回调", "pullbacks")):
                for row in stocks.get(key) or []:
                    allowed[code].add(row["ts_code"])
                    extra = f" 回调前 {row.get('prior_pct', 0):+.2f}%" if role == "涨后回调" else ""
                    lines.append(
                        f"{role} {row['name']} {row['ts_code']} 当日 {row['last_pct']:+.2f}% 窗口累计 {row['cum_pct']:+.2f}%{extra}"
                    )
            blocks.append("\n".join(lines))
        from server.chat_service import ChatRequest, chat_service

        by_code: Dict[str, List[Dict[str, Any]]] = {}
        notes = []

        async def _ask_one_open(code: str, block: str) -> List[Dict[str, Any]]:
            resp = await chat_service.chat(ChatRequest(
                query=(
                    "只根据下面这一只板块的成分股涨跌，最多推荐 3 只，不要加入名单以外的股票。"
                    "有领涨就看领涨；有涨后回调也要看，那是已经涨起来后又短暂下跌的。只输出 JSON："
                    "{\"sectors\":[{\"code\":\"" + str(code) + "\",\"picks\":[{\"ts_code\":\"\",\"name\":\"\",\"role\":\"领涨|跟涨|涨后回调\",\"reason\":\"一句话\"}]}]}\n\n"
                    + block
                ),
                model="deepseek-flash",
                provider="deepseek",
                temperature=0.1,
                max_tokens=2500,
                system_prompt="只输出 JSON。理由只能引用给定的涨跌幅，每条不超过 30 字。",
            ))
            raw = resp.content or ""
            if not raw.strip():
                return []
            found = []
            for sector in _parse_picks(raw):
                for pick in sector.get("picks") or []:
                    if isinstance(pick, dict):
                        found.append(pick)
            if not found:
                found = _extract_pick_rows(raw)
            keep = []
            for pick in found:
                if len(keep) >= 3:
                    break
                ts_code = str(pick.get("ts_code") or "")
                if ts_code not in allowed.get(code, ()):
                    continue
                keep.append({
                    "ts_code": ts_code,
                    "name": pick.get("name") or ts_code,
                    "role": pick.get("role") or "成分",
                    "reason": str(pick.get("reason") or "")[:200],
                })
            return keep

        for item, block in zip(items, blocks):
            code = item.get("sector_code") or item.get("code")
            picks = await _ask_one_open(code, block)
            if picks:
                by_code[code] = picks
            else:
                notes.append(str(code))
        with self.engine.begin() as conn:
            for item in items:
                code = item.get("sector_code") or item.get("code")
                evidence = dict(item.get("evidence") or {})
                evidence["review"] = {
                    "picks": by_code.get(code) or [],
                    "text": "" if by_code.get(code) else "模型这次没有从给定名单里选出股票",
                }
                item["evidence"] = evidence
                conn.execute(
                    text(
                        """
                        UPDATE opportunity
                        SET evidence = CAST(:evidence AS jsonb)
                        WHERE source = :src AND sector_code = :code AND trade_date = :d
                        """
                    ),
                    {
                        "evidence": json.dumps(evidence, ensure_ascii=False),
                        "src": SOURCE,
                        "code": code,
                        "d": item.get("trade_date"),
                    },
                )
        return {
            "ok": True,
            "sectors": len(items),
            "picked": sum(len(v) for v in by_code.values()),
            "note": "" if not notes else f"{len(notes)} 个板块这次没有选出股票",
        }

    def list_items(self) -> Dict[str, Any]:
        self.ensure()
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT o.id, o.trade_date, o.opportunity_type, o.trigger_reason, o.market_regime,
                           o.sector_strength, o.technical_score, o.fundamental_score, o.sentiment_score,
                           o.opportunity_score, o.confidence, o.status, o.evidence, o.source,
                           o.sector_code, o.sector_name, o.sector_level, o.phase, o.matched_types,
                           o.amount_ratio, o.main_net, s.symbol, s.name AS stock_name
                    FROM opportunity o
                    LEFT JOIN stocks s ON s.id = o.stock_id
                    ORDER BY o.trade_date DESC NULLS LAST, o.opportunity_score DESC NULLS LAST
                    LIMIT 200
                    """
                )
            ).mappings().fetchall()
        items = []
        for row in rows:
            item = dict(row)
            for key, value in list(item.items()):
                if hasattr(value, "isoformat"):
                    item[key] = value.isoformat()
                elif hasattr(value, "as_tuple"):
                    item[key] = float(value)
                elif hasattr(value, "hex") and hasattr(value, "int"):
                    item[key] = str(value)
            if isinstance(item.get("evidence"), str):
                try:
                    item["evidence"] = json.loads(item["evidence"])
                except json.JSONDecodeError:
                    pass
            if isinstance(item.get("matched_types"), str):
                try:
                    item["matched_types"] = json.loads(item["matched_types"])
                except json.JSONDecodeError:
                    pass
            item["name"] = item.get("sector_name") or item.get("stock_name") or item.get("symbol") or "机会"
            items.append(item)
        return {"ok": True, "items": items}


opportunity_scan_service = OpportunityScanService()

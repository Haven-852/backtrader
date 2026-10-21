"""板块成分股 + 最近若干个交易日股价。

库里有就直接读。成分或日线缺失时，交给 data_gap_agent，由大模型调用
get_sector_members / get_bars 写入后再读。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from data_layer.db_manager import storage_manager

LEVEL_RANK = {"东财二级行业": 0, "东财三级行业": 1, "东财一级行业": 2}


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def _f(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class SectorBriefService:
    def __init__(self) -> None:
        self._engine = None

    @property
    def engine(self):
        if self._engine is None:
            self._engine = storage_manager.get_postgres_engine()
        if self._engine is None:
            raise RuntimeError("PostgreSQL 未连接")
        return self._engine

    def find_boards(self, name: str) -> List[Dict[str, Any]]:
        key = (name or "").strip()
        if not key:
            return []
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT DISTINCT ON (ts_code) ts_code, name, industry_level
                    FROM sector_dc_index_daily
                    WHERE idx_type = '行业板块'
                      AND (name = :name OR name ILIKE :like)
                    ORDER BY ts_code, trade_date DESC
                    """
                ),
                {"name": key, "like": f"%{key}%"},
            ).fetchall()
        boards = [
            {"ts_code": r[0], "name": r[1], "level": r[2]}
            for r in rows
        ]
        def _rank(item: Dict[str, Any]) -> tuple:
            nm = item["name"] or ""
            exact = 0 if nm == key or nm.startswith(key) else 1
            negated = 1 if nm.startswith("非") else 0
            return (exact, negated, LEVEL_RANK.get(item["level"], 9), len(nm))

        boards.sort(key=_rank)
        return boards

    def _open_days(self, end: date, n: int) -> List[date]:
        with self.engine.connect() as conn:
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

    def _member_snapshot(self, ts_code: str) -> Dict[str, Any]:
        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT trade_date, COUNT(*) FROM sector_dc_member
                    WHERE ts_code = :c
                    GROUP BY trade_date
                    ORDER BY trade_date DESC
                    LIMIT 1
                    """
                ),
                {"c": ts_code},
            ).fetchone()
        if not row:
            return {"trade_date": None, "count": 0, "codes": []}
        member_date = _as_date(row[0])
        with self.engine.connect() as conn:
            codes = conn.execute(
                text(
                    """
                    SELECT con_code, COALESCE(b.name, m.name) AS name
                    FROM sector_dc_member m
                    LEFT JOIN ref_stock_basic b ON b.ts_code = m.con_code
                    WHERE m.ts_code = :c AND m.trade_date = :d
                    ORDER BY con_code
                    """
                ),
                {"c": ts_code, "d": member_date},
            ).fetchall()
        return {
            "trade_date": member_date,
            "count": len(codes),
            "codes": [{"ts_code": r[0], "name": r[1] or r[0]} for r in codes],
        }

    def _price_gap(self, codes: List[str], days: List[date]) -> List[str]:
        if not codes or not days:
            return list(codes)
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT ts_code, trade_date FROM stock_daily
                    WHERE ts_code = ANY(:codes) AND trade_date = ANY(:days)
                    """
                ),
                {"codes": codes, "days": days},
            ).fetchall()
        have = {(r[0], _as_date(r[1])) for r in rows}
        missing = []
        for code in codes:
            lack = [d.isoformat() for d in days if (code, d) not in have]
            if lack:
                missing.append(f"{code} 缺少 {'、'.join(lack)}")
        return missing

    def inspect(self, ts_code: str, days: int = 5) -> Dict[str, Any]:
        snap = self._member_snapshot(ts_code)
        with self.engine.connect() as conn:
            latest = conn.execute(text("SELECT MAX(trade_date) FROM stock_daily")).scalar()
        end = _as_date(latest) if latest else date.today()
        window = self._open_days(end, days)
        ashare = [c["ts_code"] for c in snap["codes"] if not str(c["ts_code"]).startswith(("200", "900"))]
        bshare = [c for c in snap["codes"] if str(c["ts_code"]).startswith(("200", "900"))]
        price_missing = self._price_gap(ashare, window) if snap["count"] else []
        member_missing = [] if snap["count"] else [ts_code]
        return {
            "complete": not member_missing and not price_missing,
            "missing": member_missing + price_missing,
            "skipped_b_shares": bshare,
            "detail": "成分和近端股价都在库里。" if not member_missing and not price_missing else "还有缺口。",
            "member_date": snap["trade_date"].isoformat() if snap["trade_date"] else None,
            "member_count": snap["count"],
            "codes": snap["codes"],
            "trade_dates": [d.isoformat() for d in window],
            "price_missing": price_missing,
        }

    def _table(self, ts_code: str, state: Dict[str, Any]) -> Dict[str, Any]:
        codes = [c["ts_code"] for c in state["codes"]]
        days = [datetime.strptime(d, "%Y-%m-%d").date() for d in state["trade_dates"]]
        prices: Dict[tuple, Dict[str, Any]] = {}
        if codes and days:
            with self.engine.connect() as conn:
                rows = conn.execute(
                    text(
                        """
                        SELECT ts_code, trade_date, close, pct_chg
                        FROM stock_daily
                        WHERE ts_code = ANY(:codes) AND trade_date = ANY(:days)
                        """
                    ),
                    {"codes": codes, "days": days},
                ).fetchall()
            for row in rows:
                prices[(row[0], _as_date(row[1]).isoformat())] = {
                    "close": _f(row[2]),
                    "pct_chg": _f(row[3]),
                }
        members = []
        for item in state["codes"]:
            closes = {}
            pcts = {}
            for day in state["trade_dates"]:
                cell = prices.get((item["ts_code"], day))
                closes[day] = None if not cell else cell["close"]
                pcts[day] = None if not cell else cell["pct_chg"]
            members.append({
                "ts_code": item["ts_code"],
                "name": item["name"],
                "b_share": item["ts_code"].startswith(("200", "900")),
                "closes": closes,
                "pct_chg": pcts,
            })
        last = state["trade_dates"][-1] if state["trade_dates"] else None
        members.sort(key=lambda row: ((row["pct_chg"] or {}).get(last) is None, -((row["pct_chg"] or {}).get(last) or 0)))
        return {"members": members, "trade_dates": state["trade_dates"], "member_date": state["member_date"]}

    async def load(self, name: str, days: int = 5) -> Dict[str, Any]:
        boards = self.find_boards(name)
        if not boards:
            return {"ok": False, "error": f"库里没有名称包含「{name}」的东财行业板块"}
        board = boards[0]
        code = board["ts_code"]
        days = max(1, min(int(days or 5), 20))
        agents = []

        state = self.inspect(code, days)
        if not state["member_count"]:
            from server.data_gap_agent import fill_by_agent

            task = (
                f"数据库没有板块 {code}（{board['name']}，{board['level']}）的成分股。"
                f"请调用一次 datahubco_data__get_sector_members，ts_code={code}，write_back=true。"
                "不要编造成分股名单。如果远端接口失败，原样说明错误。"
            )
            agents.append(await fill_by_agent(
                task,
                lambda: self._member_check(code),
            ))
            state = self.inspect(code, days)

        if state["price_missing"]:
            from server.data_gap_agent import fill_by_agent

            listed = "\n".join(state["price_missing"][:40])
            window = "、".join(state["trade_dates"])
            task = (
                f"板块 {code} {board['name']} 的成分股已经在库里。"
                f"最近交易日是 {window}。"
                "下面这些股票的日线不完整：\n"
                f"{listed}\n"
                "请对每只缺少的股票调用 datahubco_data__get_bars，"
                "dataset=stock_daily，start_date 用窗口第一天，end_date 用窗口最后一天，"
                "source=auto，write_back=true。不要编造收盘价。"
            )
            agents.append(await fill_by_agent(
                task,
                lambda: self._price_check(code, days),
            ))
            state = self.inspect(code, days)

        table = self._table(code, state)
        return {
            "ok": True,
            "sector": board,
            "candidates": boards,
            "member_date": table["member_date"],
            "trade_dates": table["trade_dates"],
            "count": len(table["members"]),
            "members": table["members"],
            "still_missing": state["missing"],
            "skipped_b_shares": state.get("skipped_b_shares") or [],
            "agents": agents,
        }

    def _member_check(self, ts_code: str) -> Dict[str, Any]:
        snap = self._member_snapshot(ts_code)
        missing = [] if snap["count"] else [ts_code]
        return {
            "complete": not missing,
            "missing": missing,
            "detail": f"成分 {snap['count']} 只" if snap["count"] else "还没有成分股",
        }

    def _price_check(self, ts_code: str, days: int) -> Dict[str, Any]:
        state = self.inspect(ts_code, days)
        return {
            "complete": not state["price_missing"],
            "missing": state["price_missing"],
            "detail": "股价已齐" if not state["price_missing"] else "日线仍有缺口",
        }


sector_brief_service = SectorBriefService()

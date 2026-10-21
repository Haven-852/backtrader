"""市场记忆：一个交易日 = 事实 + 状态判断 + 结构 + 事件 + 总结 + 待验证假设。"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import text as sa_text

from data_layer.db_manager import storage_manager

_SH = ZoneInfo("Asia/Shanghai")

_REASON = {
    "STRATEGY": "策略",
    "TIMING": "追高",
    "EXECUTION": "执行",
    "RISK": "止损过晚",
    "MARKET_REGIME": "市场判断错误",
    "LUCK": "运气",
    "OTHER": "其他",
}


def _today() -> date:
    return datetime.now(_SH).date()


def _json(v: Any) -> str:
    return json.dumps(v if v is not None else [], ensure_ascii=False)


def _row(row) -> Dict[str, Any]:
    if row is None:
        return {}
    d = dict(row._mapping)
    for k, v in list(d.items()):
        if hasattr(v, "isoformat"):
            d[k] = v.isoformat()
        elif hasattr(v, "hex") and hasattr(v, "int"):
            d[k] = str(v)
        elif hasattr(v, "as_tuple"):
            d[k] = float(v)
    return d


def _num(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _lines(v: Any) -> List[str]:
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    if isinstance(v, str):
        return [s.strip() for s in v.splitlines() if s.strip()]
    return []


class MarketMemoryService:
    def __init__(self):
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
        from data_layer.schema import POSTGRES_DDL_LIST, POSTGRES_INDEX_DDL_LIST

        wanted = ("market_memory", "market_event", "market_hypothesis", "ADD COLUMN IF NOT EXISTS", "CREATE TABLE IF NOT EXISTS market_regime")
        with self.engine.begin() as conn:
            for ddl in POSTGRES_DDL_LIST:
                if any(k in ddl for k in wanted):
                    conn.execute(sa_text(ddl))
            for ddl in POSTGRES_INDEX_DDL_LIST:
                if "market_memory" in ddl or "market_regime" in ddl or "market_event" in ddl or "market_hypothesis" in ddl:
                    conn.execute(sa_text(ddl))
        self._ready = True

    def list_days(self, span: str = "all") -> Dict[str, Any]:
        self.ensure()
        start = self._span_start(span)
        sql = """
            SELECT m.id, m.trade_date, m.ai_summary, m.narrative, m.structure,
                   s.sentiment_score, s.turnover_trillion, s.change_pct,
                   s.advancers, s.decliners, s.limit_up_count, s.limit_down_count,
                   r.regime_label, r.confidence
            FROM market_memory m
            LEFT JOIN market_snapshot s ON s.id = m.snapshot_id
            LEFT JOIN LATERAL (
                SELECT regime_label, confidence
                FROM market_regime
                WHERE memory_id = m.id
                ORDER BY created_at DESC
                LIMIT 1
            ) r ON TRUE
            WHERE (:start IS NULL OR m.trade_date >= :start)
              AND (:only_today IS FALSE OR m.trade_date = :today)
            ORDER BY m.trade_date DESC
            LIMIT 120
        """
        today = _today()
        with self.engine.connect() as conn:
            rows = conn.execute(
                sa_text(sql),
                {
                    "start": start,
                    "only_today": span == "today",
                    "today": today,
                },
            ).fetchall()
            stats = self._hypothesis_stats(conn)
            gauge = self._gauge(conn)
            reminders = self._reminders(conn)
        days = []
        for row in rows:
            item = _row(row)
            structure = item.get("structure") or {}
            sectors = structure.get("sectors") or []
            item["lead_sector"] = sectors[0]["name"] if sectors else ""
            item["lead_strength"] = sectors[0].get("strength") if sectors else None
            days.append(item)
        return {
            "ok": True,
            "today": today.isoformat(),
            "days": days,
            "hypothesis_stats": stats,
            "gauge": gauge,
            "reminders": reminders,
        }

    def _reminders(self, conn) -> List[Dict[str, Any]]:
        rows = conn.execute(
            sa_text(
                """
                SELECT hypothesis_code, statement, verify_on, trade_date, status
                FROM market_hypothesis
                WHERE status = 'PENDING'
                  AND verify_on IS NOT NULL
                  AND verify_on <= :today
                ORDER BY verify_on, hypothesis_code
                """
            ),
            {"today": _today()},
        ).fetchall()
        return [_row(r) for r in rows]

    def _span_start(self, span: str) -> Optional[date]:
        today = _today()
        if span == "week":
            return today - timedelta(days=today.weekday())
        if span == "month":
            return today.replace(day=1)
        return None

    def get_day(self, trade_date: str) -> Dict[str, Any]:
        self.ensure()
        with self.engine.connect() as conn:
            mem = conn.execute(
                sa_text("SELECT * FROM market_memory WHERE trade_date = :d"),
                {"d": trade_date},
            ).fetchone()
            if not mem:
                return {"ok": False, "error": "这一天还没有市场记忆"}
            memory = _row(mem)
            snap = None
            if memory.get("snapshot_id"):
                snap = conn.execute(
                    sa_text("SELECT * FROM market_snapshot WHERE id = :id"),
                    {"id": memory["snapshot_id"]},
                ).fetchone()
            regime = conn.execute(
                sa_text(
                    """
                    SELECT * FROM market_regime
                    WHERE memory_id = :id
                    ORDER BY created_at DESC LIMIT 1
                    """
                ),
                {"id": memory["id"]},
            ).fetchone()
            events = conn.execute(
                sa_text(
                    """
                    SELECT * FROM market_event
                    WHERE memory_id = :id
                    ORDER BY event_time NULLS LAST, created_at
                    """
                ),
                {"id": memory["id"]},
            ).fetchall()
            hypos = conn.execute(
                sa_text(
                    """
                    SELECT * FROM market_hypothesis
                    WHERE memory_id = :id
                    ORDER BY hypothesis_code, created_at
                    """
                ),
                {"id": memory["id"]},
            ).fetchall()
            packed = self._pack(memory, _row(snap) if snap else {}, _row(regime) if regime else {}, [_row(e) for e in events], [_row(h) for h in hypos])
            packed["similar"] = self._similar(conn, packed)
            packed["personal"] = self._personal(conn, packed.get("regime") or {})
            packed["hypothesis_stats"] = self._hypothesis_stats(conn)
        return {"ok": True, "day": packed}

    def _pack(self, memory, snap, regime, events, hypos) -> Dict[str, Any]:
        facts = {
            "temperature": snap.get("sentiment_score"),
            "turnover_trillion": snap.get("turnover_trillion"),
            "turnover_change_pct": snap.get("turnover_change_pct"),
            "advancers": snap.get("advancers"),
            "decliners": snap.get("decliners"),
            "limit_up": snap.get("limit_up_count"),
            "limit_down": snap.get("limit_down_count"),
            "limit_break": snap.get("limit_break_count"),
            "streak": snap.get("max_limit_up_streak"),
            "up_down_ratio": snap.get("up_down_ratio"),
            "volume_ratio": snap.get("volume_ratio"),
            "turnover_rate": snap.get("turnover_rate"),
            "amplitude": snap.get("amplitude"),
            "volatility": snap.get("volatility"),
            "change_pct": snap.get("change_pct"),
            "index_name": snap.get("index_name"),
            "indices": snap.get("index_board") or [],
            "funds": snap.get("flow_board") or {},
            "narrative_fact": snap.get("raw_data", {}) if isinstance(snap.get("raw_data"), dict) else {},
            "fact_note": (snap.get("raw_data") or {}).get("fact_note") if isinstance(snap.get("raw_data"), dict) else "",
        }
        return {
            "id": memory.get("id"),
            "trade_date": str(memory.get("trade_date") or "")[:10],
            "facts": facts,
            "regime": {
                "label": regime.get("regime_label") or "",
                "confidence": regime.get("confidence"),
                "evidence": regime.get("evidence") or [],
            },
            "structure": memory.get("structure") or {},
            "events": [
                {"id": e.get("id"), "time": e.get("event_time") or "", "title": e.get("title") or "", "detail": e.get("detail") or ""}
                for e in events
            ],
            "summary": {
                "text": memory.get("ai_summary") or "",
                "evidence": memory.get("summary_evidence") or [],
                "confidence": memory.get("summary_confidence"),
            },
            "narrative": memory.get("narrative") or "",
            "hypotheses": hypos,
        }

    def save_day(self, body: Dict[str, Any]) -> Dict[str, Any]:
        self.ensure()
        trade_date = str(body.get("trade_date") or _today().isoformat())[:10]
        facts = body.get("facts") or {}
        regime = body.get("regime") or {}
        structure = body.get("structure") or {}
        events = body.get("events") or []
        summary = body.get("summary") or {}
        hypos = body.get("hypotheses") or []
        adv = _num(facts.get("advancers"))
        dec = _num(facts.get("decliners"))
        ratio = _num(facts.get("up_down_ratio"))
        if ratio is None and adv and dec:
            ratio = round(adv / dec, 4) if dec else None
        label = (regime.get("label") or "").strip() or "未命名状态"
        conf = _num(regime.get("confidence"))
        if conf is not None and conf > 1:
            conf = round(conf / 100, 4)
        sconf = _num(summary.get("confidence"))
        if sconf is not None and sconf > 1:
            sconf = round(sconf / 100, 4)
        with self.engine.begin() as conn:
            existing = conn.execute(
                sa_text("SELECT id, snapshot_id FROM market_memory WHERE trade_date = :d"),
                {"d": trade_date},
            ).fetchone()
            snap_id = existing.snapshot_id if existing else None
            snap_params = {
                "d": trade_date,
                "name": facts.get("index_name") or "沪深",
                "chg": _num(facts.get("change_pct")),
                "temp": _num(facts.get("temperature")),
                "tt": _num(facts.get("turnover_trillion")),
                "tch": _num(facts.get("turnover_change_pct")),
                "up": int(adv) if adv is not None else None,
                "down": int(dec) if dec is not None else None,
                "lu": int(_num(facts.get("limit_up"))) if _num(facts.get("limit_up")) is not None else None,
                "ld": int(_num(facts.get("limit_down"))) if _num(facts.get("limit_down")) is not None else None,
                "lb": int(_num(facts.get("limit_break"))) if _num(facts.get("limit_break")) is not None else None,
                "streak": int(_num(facts.get("streak"))) if _num(facts.get("streak")) is not None else None,
                "ratio": ratio,
                "vr": _num(facts.get("volume_ratio")),
                "tr": _num(facts.get("turnover_rate")),
                "amp": _num(facts.get("amplitude")),
                "vol": _num(facts.get("volatility")),
                "indices": _json(facts.get("indices") or []),
                "funds": _json(facts.get("funds") or {}),
                "raw": _json({"fact_note": facts.get("fact_note") or ""}),
            }
            if snap_id:
                conn.execute(
                    sa_text(
                        """
                        UPDATE market_snapshot SET
                            index_name = :name, change_pct = :chg, sentiment_score = :temp,
                            turnover_trillion = :tt, turnover_change_pct = :tch,
                            advancers = :up, decliners = :down,
                            limit_up_count = :lu, limit_down_count = :ld, limit_break_count = :lb,
                            max_limit_up_streak = :streak, up_down_ratio = :ratio,
                            volume_ratio = :vr, turnover_rate = :tr, amplitude = :amp, volatility = :vol,
                            index_board = CAST(:indices AS jsonb), flow_board = CAST(:funds AS jsonb),
                            raw_data = CAST(:raw AS jsonb), source = 'kb'
                        WHERE id = :id
                        """
                    ),
                    {**snap_params, "id": snap_id},
                )
            else:
                snap_id = conn.execute(
                    sa_text(
                        """
                        INSERT INTO market_snapshot (
                            trade_date, index_name, change_pct, sentiment_score, turnover_trillion,
                            turnover_change_pct, advancers, decliners, limit_up_count, limit_down_count,
                            limit_break_count, max_limit_up_streak, up_down_ratio, volume_ratio,
                            turnover_rate, amplitude, volatility, index_board, flow_board, raw_data, source
                        ) VALUES (
                            :d, :name, :chg, :temp, :tt, :tch, :up, :down, :lu, :ld, :lb, :streak,
                            :ratio, :vr, :tr, :amp, :vol, CAST(:indices AS jsonb), CAST(:funds AS jsonb),
                            CAST(:raw AS jsonb), 'kb'
                        ) RETURNING id
                        """
                    ),
                    snap_params,
                ).scalar()
            if existing:
                memory_id = str(existing.id)
                conn.execute(
                    sa_text(
                        """
                        UPDATE market_memory SET
                            snapshot_id = :sid, ai_summary = :summary,
                            summary_evidence = CAST(:ev AS jsonb), summary_confidence = :sc,
                            narrative = :narrative, structure = CAST(:st AS jsonb), updated_at = NOW()
                        WHERE id = :id
                        """
                    ),
                    {
                        "sid": snap_id,
                        "summary": summary.get("text") or "",
                        "ev": _json(_lines(summary.get("evidence"))),
                        "sc": sconf,
                        "narrative": body.get("narrative") or "",
                        "st": _json(structure),
                        "id": memory_id,
                    },
                )
                conn.execute(sa_text("DELETE FROM market_regime WHERE memory_id = :id"), {"id": memory_id})
                conn.execute(sa_text("DELETE FROM market_event WHERE memory_id = :id"), {"id": memory_id})
                conn.execute(sa_text("DELETE FROM market_hypothesis WHERE memory_id = :id AND status = 'PENDING'"), {"id": memory_id})
            else:
                memory_id = str(conn.execute(
                    sa_text(
                        """
                        INSERT INTO market_memory (
                            trade_date, snapshot_id, ai_summary, summary_evidence, summary_confidence, narrative, structure
                        ) VALUES (
                            :d, :sid, :summary, CAST(:ev AS jsonb), :sc, :narrative, CAST(:st AS jsonb)
                        ) RETURNING id
                        """
                    ),
                    {
                        "d": trade_date,
                        "sid": snap_id,
                        "summary": summary.get("text") or "",
                        "ev": _json(_lines(summary.get("evidence"))),
                        "sc": sconf,
                        "narrative": body.get("narrative") or "",
                        "st": _json(structure),
                    },
                ).scalar())
            conn.execute(
                sa_text(
                    """
                    INSERT INTO market_regime (memory_id, trade_date, regime_label, confidence, evidence)
                    VALUES (:mid, :d, :label, :conf, CAST(:ev AS jsonb))
                    """
                ),
                {
                    "mid": memory_id,
                    "d": trade_date,
                    "label": label,
                    "conf": conf,
                    "ev": _json(_lines(regime.get("evidence"))),
                },
            )
            for ev in events:
                title = (ev.get("title") or "").strip()
                if not title:
                    continue
                conn.execute(
                    sa_text(
                        """
                        INSERT INTO market_event (memory_id, trade_date, event_time, title, detail, severity)
                        VALUES (:mid, :d, :tm, :title, :detail, :sev)
                        """
                    ),
                    {
                        "mid": memory_id,
                        "d": trade_date,
                        "tm": (ev.get("time") or "")[:8],
                        "title": title,
                        "detail": ev.get("detail") or "",
                        "sev": ev.get("severity") or "info",
                    },
                )
            kept = conn.execute(
                sa_text("SELECT COUNT(*) FROM market_hypothesis WHERE memory_id = :id"),
                {"id": memory_id},
            ).scalar() or 0
            for i, h in enumerate(hypos, start=int(kept) + 1):
                statement = (h.get("statement") or "").strip()
                if not statement:
                    continue
                code = h.get("hypothesis_code") or f"M-{trade_date.replace('-', '')}-{i:02d}"
                conn.execute(
                    sa_text(
                        """
                        INSERT INTO market_hypothesis (
                            memory_id, trade_date, hypothesis_code, statement, basis, conditions, verify_on, status
                        ) VALUES (
                            :mid, :d, :code, :statement, CAST(:basis AS jsonb), CAST(:cond AS jsonb), :verify, 'PENDING'
                        )
                        """
                    ),
                    {
                        "mid": memory_id,
                        "d": trade_date,
                        "code": code[:40],
                        "statement": statement,
                        "basis": _json(_lines(h.get("basis"))),
                        "cond": _json(_lines(h.get("conditions"))),
                        "verify": h.get("verify_on") or None,
                    },
                )
        return self.get_day(trade_date)

    def validate(self, hypothesis_id: str, status: str, note: str = "") -> Dict[str, Any]:
        self.ensure()
        status = (status or "").upper()
        if status not in ("CORRECT", "PARTIAL", "WRONG"):
            return {"ok": False, "error": "验证结果只能是正确、部分正确或错误"}
        with self.engine.begin() as conn:
            row = conn.execute(
                sa_text(
                    """
                    UPDATE market_hypothesis
                    SET status = :status,
                        validation_result = CAST(:res AS jsonb)
                    WHERE id = CAST(:id AS uuid)
                    RETURNING trade_date
                    """
                ),
                {
                    "status": status,
                    "res": _json({"verdict": status, "note": note, "checked_on": _today().isoformat()}),
                    "id": hypothesis_id,
                },
            ).fetchone()
        if not row:
            return {"ok": False, "error": "判断不存在"}
        return self.get_day(str(row.trade_date)[:10])

    def _hypothesis_stats(self, conn) -> Dict[str, int]:
        row = conn.execute(
            sa_text(
                """
                SELECT
                    COUNT(*) FILTER (WHERE status <> 'PENDING') AS checked,
                    COUNT(*) FILTER (WHERE status = 'CORRECT') AS correct,
                    COUNT(*) FILTER (WHERE status = 'PARTIAL') AS partial,
                    COUNT(*) FILTER (WHERE status = 'WRONG') AS wrong,
                    COUNT(*) FILTER (WHERE status = 'PENDING') AS pending
                FROM market_hypothesis
                """
            )
        ).fetchone()
        d = _row(row)
        return {k: int(d.get(k) or 0) for k in ("checked", "correct", "partial", "wrong", "pending")}

    def _gauge(self, conn) -> Dict[str, Any]:
        rows = conn.execute(
            sa_text(
                """
                SELECT m.trade_date, s.change_pct, s.sentiment_score, s.turnover_trillion, s.volatility, r.regime_label
                FROM market_memory m
                LEFT JOIN market_snapshot s ON s.id = m.snapshot_id
                LEFT JOIN LATERAL (
                    SELECT regime_label FROM market_regime WHERE memory_id = m.id ORDER BY created_at DESC LIMIT 1
                ) r ON TRUE
                ORDER BY m.trade_date DESC
                LIMIT 30
                """
            )
        ).fetchall()
        if not rows:
            return {}
        changes = [float(r.change_pct) for r in rows if r.change_pct is not None]
        temps = [float(r.sentiment_score) for r in rows if r.sentiment_score is not None]
        turns = [float(r.turnover_trillion) for r in rows if r.turnover_trillion is not None]
        vols = [float(r.volatility) for r in rows if r.volatility is not None]
        up_days = sum(1 for c in changes if c > 0)
        label = next((r.regime_label for r in rows if r.regime_label), "")
        ordered = list(reversed(rows))
        later_up = later_down = later_mixed = same = 0
        for i, r in enumerate(ordered):
            if not label or r.regime_label != label or r.trade_date == rows[0].trade_date:
                continue
            same += 1
            nxt = next((x for x in ordered[i + 1:] if x.change_pct is not None), None)
            if not nxt:
                continue
            chg = float(nxt.change_pct)
            if chg > 0.2:
                later_up += 1
            elif chg < -0.2:
                later_down += 1
            else:
                later_mixed += 1
        return {
            "days": len(rows),
            "trend": round(up_days / len(changes) * 100) if changes else None,
            "sentiment": round(sum(temps) / len(temps)) if temps else None,
            "turnover": round(min(100, (sum(turns) / len(turns)) / 2 * 100)) if turns else None,
            "volatility": round(min(100, sum(vols) / len(vols))) if vols else None,
            "current": label,
            "similar_count": same,
            "later_up": later_up,
            "later_down": later_down,
            "later_mixed": later_mixed,
        }

    def _similar(self, conn, day: Dict[str, Any]) -> List[Dict[str, Any]]:
        label = (day.get("regime") or {}).get("label") or ""
        sectors = [s.get("name") for s in (day.get("structure") or {}).get("sectors") or [] if s.get("name")]
        rows = conn.execute(
            sa_text(
                """
                SELECT m.trade_date, m.structure, s.change_pct, r.regime_label
                FROM market_memory m
                LEFT JOIN market_snapshot s ON s.id = m.snapshot_id
                LEFT JOIN LATERAL (
                    SELECT regime_label FROM market_regime WHERE memory_id = m.id ORDER BY created_at DESC LIMIT 1
                ) r ON TRUE
                WHERE m.trade_date <> :d
                ORDER BY m.trade_date DESC
                LIMIT 80
                """
            ),
            {"d": day.get("trade_date")},
        ).fetchall()
        found = []
        for r in rows:
            item = _row(r)
            score = 0.0
            if label and item.get("regime_label") == label:
                score += 0.55
            names = [s.get("name") for s in (item.get("structure") or {}).get("sectors") or [] if isinstance(s, dict)]
            overlap = len(set(sectors) & set(names))
            if sectors:
                score += 0.35 * overlap / len(sectors)
            if score < 0.45:
                continue
            later = conn.execute(
                sa_text(
                    """
                    SELECT s.change_pct, m.structure
                    FROM market_memory m
                    LEFT JOIN market_snapshot s ON s.id = m.snapshot_id
                    WHERE m.trade_date > :d AND m.trade_date <= CAST(:d AS date) + 5
                    ORDER BY m.trade_date
                    LIMIT 1
                    """
                ),
                {"d": item["trade_date"]},
            ).fetchone()
            after = _row(later) if later else {}
            sector_after = None
            if sectors and after.get("structure"):
                for s in after["structure"].get("sectors") or []:
                    if s.get("name") == sectors[0]:
                        sector_after = s.get("change_pct")
            found.append({
                "trade_date": str(item.get("trade_date") or "")[:10],
                "score": round(min(score, 0.99), 2),
                "regime_label": item.get("regime_label") or "",
                "index_after": after.get("change_pct"),
                "sector_after": sector_after,
                "sector_name": sectors[0] if sectors else "",
            })
        found.sort(key=lambda x: x["score"], reverse=True)
        return found[:5]

    def _personal(self, conn, regime: Dict[str, Any]) -> Dict[str, Any]:
        label = (regime.get("label") or "").strip()
        if not label:
            return {"count": 0, "wins": 0, "losses": 0, "reasons": [], "titles": []}
        rows = conn.execute(
            sa_text(
                """
                SELECT t.thesis_title, o.outcome_type, a.primary_category
                FROM trade_thesis t
                JOIN trade_outcome o ON o.thesis_id = t.id
                LEFT JOIN LATERAL (
                    SELECT primary_category FROM attribution
                    WHERE outcome_id = o.id
                    ORDER BY created_at DESC LIMIT 1
                ) a ON TRUE
                WHERE COALESCE(t.market_context, '') ILIKE :pat
                   OR COALESCE(t.thesis_text, '') ILIKE :pat
                """
            ),
            {"pat": f"%{label}%"},
        ).fetchall()
        reasons: Dict[str, int] = {}
        wins = losses = 0
        titles = []
        for r in rows:
            if r.outcome_type == "WIN":
                wins += 1
            elif r.outcome_type == "LOSS":
                losses += 1
            if r.primary_category:
                key = _REASON.get(r.primary_category, r.primary_category)
                reasons[key] = reasons.get(key, 0) + 1
            if r.thesis_title:
                titles.append(r.thesis_title)
        ranked = sorted(reasons.items(), key=lambda x: x[1], reverse=True)
        return {
            "count": len(rows),
            "wins": wins,
            "losses": losses,
            "reasons": [{"label": k, "n": n} for k, n in ranked],
            "titles": titles[:8],
        }


market_memory_service = MarketMemoryService()

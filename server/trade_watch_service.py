"""
Trade Watch Service - 选股层 watchlist + 最小盘中巡检事件

选股层（低频）：
  - screener A/B 命中
  - dash_watchlist 自选
  → 写入 trade_watchlist（默认 3～15 只）

巡检层（规则，不调 LLM）：
  - 对 watchlist 读日线/分钟线
  - 触发 gap / pct_from_open / volume_spike / near_limit 等事件
  - 写入 trade_scan_event（带冷却，避免刷屏）
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Set

from sqlalchemy import text as sa_text

from data_layer.db_manager import storage_manager

logger = logging.getLogger(__name__)

WATCHLIST_DDL = [
    """
    CREATE TABLE IF NOT EXISTS trade_watchlist (
        id              SERIAL PRIMARY KEY,
        ts_code         VARCHAR(16)   NOT NULL,
        name            VARCHAR(64),
        source          VARCHAR(32)   NOT NULL,
        reason          TEXT,
        score           NUMERIC(8,2),
        asof            DATE          NOT NULL,
        active          BOOLEAN       DEFAULT TRUE,
        meta            JSONB,
        created_at      TIMESTAMPTZ   DEFAULT NOW(),
        UNIQUE (ts_code, asof, source)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS trade_scan_event (
        id              SERIAL PRIMARY KEY,
        ts_code         VARCHAR(16)   NOT NULL,
        asof            DATE          NOT NULL,
        event_type      VARCHAR(32)   NOT NULL,
        severity        VARCHAR(8)    NOT NULL DEFAULT 'soft',
        price           NUMERIC(16,4),
        detail          JSONB,
        created_at      TIMESTAMPTZ   DEFAULT NOW()
    )
    """,
]

WATCHLIST_INDEX = [
    "CREATE INDEX IF NOT EXISTS idx_trade_watchlist_asof ON trade_watchlist (asof DESC, active)",
    "CREATE INDEX IF NOT EXISTS idx_trade_watchlist_code ON trade_watchlist (ts_code, asof DESC)",
    "CREATE INDEX IF NOT EXISTS idx_trade_scan_event_asof ON trade_scan_event (asof DESC, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_trade_scan_event_code ON trade_scan_event (ts_code, asof, event_type)",
]


def _to_date(v: Any) -> Optional[date]:
    if v is None:
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    return date.fromisoformat(str(v).strip()[:10])


def _f(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _row(row) -> Dict[str, Any]:
    if row is None:
        return {}
    d = dict(row._mapping)
    for k, v in list(d.items()):
        if isinstance(v, datetime):
            d[k] = v.isoformat()
        elif isinstance(v, date):
            d[k] = v.isoformat()
    return d


class TradeWatchService:
    def __init__(self):
        self._engine = None
        self._ready = False

    @property
    def engine(self):
        if self._engine is None:
            self._engine = storage_manager.get_postgres_engine()
        return self._engine

    def _ensure(self) -> None:
        if self._ready:
            return
        if not self.engine:
            raise RuntimeError("PostgreSQL 未连接")
        with self.engine.begin() as conn:
            for ddl in WATCHLIST_DDL:
                conn.execute(sa_text(ddl))
            for idx in WATCHLIST_INDEX:
                try:
                    conn.execute(sa_text(idx))
                except Exception as e:
                    logger.warning("watchlist 索引: %s", e)
        self._ready = True

    # ------------------------------------------------------------------ #
    # 选股层：构建 watchlist
    # ------------------------------------------------------------------ #
    def build_watchlist(
        self,
        asof: Optional[str] = None,
        *,
        include_screener: bool = True,
        include_dash: bool = True,
        setup_types: Optional[List[str]] = None,
        max_total: int = 15,
        max_per_source: int = 10,
        min_score_a: float = 55.0,
        min_score_b: float = 60.0,
        replace: bool = True,
    ) -> Dict[str, Any]:
        """
        从 screener 命中 + dash 自选 生成当日盯盘名单。
        默认总数上限 15，优先 B（升势回踩）再 A（观察池）再自选。
        """
        self._ensure()
        asof_d = _to_date(asof) or self._latest_trade_date() or date.today()
        setup_types = [s.upper() for s in (setup_types or ["A", "B"])]
        candidates: List[Dict[str, Any]] = []

        with self.engine.begin() as conn:
            if replace:
                conn.execute(
                    sa_text("UPDATE trade_watchlist SET active = FALSE WHERE asof = :d"),
                    {"d": asof_d},
                )

            if include_screener:
                candidates.extend(
                    self._load_screener_hits(
                        conn,
                        asof_d,
                        setup_types=setup_types,
                        min_score_a=min_score_a,
                        min_score_b=min_score_b,
                        limit=max_per_source,
                    )
                )

            if include_dash:
                candidates.extend(self._load_dash_watchlist(conn, asof_d, limit=max_per_source))

            # 去重：同一 ts_code 保留更高优先级（B > A > dash > manual）
            prio = {"screener_b": 3, "screener_a": 2, "dash": 1, "manual": 0}
            best: Dict[str, Dict[str, Any]] = {}
            for c in candidates:
                code = c["ts_code"]
                if code not in best or prio.get(c["source"], 0) > prio.get(best[code]["source"], 0):
                    best[code] = c
                elif (
                    code in best
                    and prio.get(c["source"], 0) == prio.get(best[code]["source"], 0)
                    and _f(c.get("score")) > _f(best[code].get("score"))
                ):
                    best[code] = c

            ranked = sorted(
                best.values(),
                key=lambda x: (prio.get(x["source"], 0), _f(x.get("score"))),
                reverse=True,
            )[: max(1, int(max_total))]

            saved = []
            for item in ranked:
                row = conn.execute(
                    sa_text(
                        """
                        INSERT INTO trade_watchlist
                            (ts_code, name, source, reason, score, asof, active, meta)
                        VALUES
                            (:code, :name, :src, :reason, :score, :asof, TRUE, CAST(:meta AS jsonb))
                        ON CONFLICT (ts_code, asof, source) DO UPDATE SET
                            name = EXCLUDED.name,
                            reason = EXCLUDED.reason,
                            score = EXCLUDED.score,
                            active = TRUE,
                            meta = EXCLUDED.meta
                        RETURNING *
                        """
                    ),
                    {
                        "code": item["ts_code"],
                        "name": item.get("name"),
                        "src": item["source"],
                        "reason": item.get("reason"),
                        "score": item.get("score"),
                        "asof": asof_d,
                        "meta": json.dumps(item.get("meta") or {}, ensure_ascii=False, default=str),
                    },
                ).fetchone()
                saved.append(_row(row))

        return {
            "ok": True,
            "asof": asof_d.isoformat(),
            "count": len(saved),
            "watchlist": saved,
            "sources": {
                "screener": include_screener,
                "dash": include_dash,
            },
            "hint": (
                "优先 B 升势回踩，其次 A 观察池，再次自选；"
                "建议总数 3～15。后续可用命中→收益反馈改进阈值。"
            ),
        }

    def _latest_trade_date(self) -> Optional[date]:
        try:
            with self.engine.connect() as conn:
                row = conn.execute(
                    sa_text("SELECT MAX(trade_date) AS d FROM stock_daily")
                ).fetchone()
                return _to_date(row.d) if row else None
        except Exception:
            return None

    def _load_screener_hits(
        self,
        conn,
        asof_d: date,
        *,
        setup_types: List[str],
        min_score_a: float,
        min_score_b: float,
        limit: int,
    ) -> List[Dict[str, Any]]:
        # 取最近一次 <= asof 的 run
        run = conn.execute(
            sa_text(
                """
                SELECT id, asof FROM strategy_screener_run
                WHERE asof <= :d
                ORDER BY asof DESC, id DESC
                LIMIT 1
                """
            ),
            {"d": asof_d},
        ).fetchone()
        if not run:
            return []

        rows = conn.execute(
            sa_text(
                """
                SELECT ts_code, name, setup_type, score, label, reason, trade_date, pct_chg
                FROM strategy_screener_hit
                WHERE run_id = :rid
                ORDER BY score DESC NULLS LAST
                """
            ),
            {"rid": run.id},
        ).fetchall()

        out: List[Dict[str, Any]] = []
        for r in rows:
            st = (r.setup_type or "").upper()
            if st not in setup_types:
                continue
            score = _f(r.score)
            if st == "A" and score < min_score_a:
                continue
            if st == "B" and score < min_score_b:
                continue
            src = "screener_b" if st == "B" else "screener_a"
            out.append(
                {
                    "ts_code": r.ts_code,
                    "name": r.name,
                    "source": src,
                    "score": score,
                    "reason": r.reason or r.label or f"选股{st}",
                    "meta": {
                        "setup_type": st,
                        "run_id": run.id,
                        "run_asof": str(run.asof),
                        "pct_chg": _f(r.pct_chg),
                    },
                }
            )
            if len([x for x in out if x["source"] == src]) >= limit:
                # 每种 setup 最多 limit；继续扫另一种
                if all(
                    len([x for x in out if x["source"] == f"screener_{t.lower()}"]) >= limit
                    for t in setup_types
                    if t in ("A", "B")
                ):
                    break
        return out

    def _load_dash_watchlist(self, conn, asof_d: date, limit: int) -> List[Dict[str, Any]]:
        try:
            rows = conn.execute(
                sa_text(
                    """
                    SELECT ts_code, name, industry, kind
                    FROM dash_watchlist
                    ORDER BY created_at DESC NULLS LAST
                    LIMIT :lim
                    """
                ),
                {"lim": limit},
            ).fetchall()
        except Exception as e:
            logger.warning("读取 dash_watchlist 失败: %s", e)
            return []

        return [
            {
                "ts_code": r.ts_code,
                "name": r.name,
                "source": "dash",
                "score": 50.0,
                "reason": f"自选股 · {r.industry or r.kind or ''}".strip(" ·"),
                "meta": {"kind": r.kind, "asof": asof_d.isoformat()},
            }
            for r in rows
        ]

    def list_watchlist(
        self,
        asof: Optional[str] = None,
        active_only: bool = True,
    ) -> Dict[str, Any]:
        self._ensure()
        asof_d = _to_date(asof)
        sql = "SELECT * FROM trade_watchlist WHERE 1=1"
        params: Dict[str, Any] = {}
        if asof_d:
            sql += " AND asof = :d"
            params["d"] = asof_d
        if active_only:
            sql += " AND active = TRUE"
        sql += " ORDER BY score DESC NULLS LAST, id ASC"
        with self.engine.connect() as conn:
            if not asof_d:
                latest = conn.execute(
                    sa_text("SELECT MAX(asof) AS d FROM trade_watchlist WHERE active = TRUE")
                ).fetchone()
                if latest and latest.d:
                    rows = conn.execute(
                        sa_text(
                            """
                            SELECT * FROM trade_watchlist
                            WHERE asof = :d AND active = TRUE
                            ORDER BY score DESC NULLS LAST, id ASC
                            """
                        ),
                        {"d": latest.d},
                    ).fetchall()
                    return {
                        "ok": True,
                        "asof": _to_date(latest.d).isoformat(),
                        "count": len(rows),
                        "watchlist": [_row(r) for r in rows],
                    }
            rows = conn.execute(sa_text(sql), params).fetchall()
        return {
            "ok": True,
            "asof": asof_d.isoformat() if asof_d else None,
            "count": len(rows),
            "watchlist": [_row(r) for r in rows],
        }

    def add_manual(
        self,
        ts_code: str,
        *,
        asof: Optional[str] = None,
        name: Optional[str] = None,
        reason: Optional[str] = None,
        score: float = 55.0,
    ) -> Dict[str, Any]:
        self._ensure()
        asof_d = _to_date(asof) or self._latest_trade_date() or date.today()
        code = ts_code.strip().upper()
        with self.engine.begin() as conn:
            row = conn.execute(
                sa_text(
                    """
                    INSERT INTO trade_watchlist
                        (ts_code, name, source, reason, score, asof, active, meta)
                    VALUES
                        (:code, :name, 'manual', :reason, :score, :asof, TRUE, '{}'::jsonb)
                    ON CONFLICT (ts_code, asof, source) DO UPDATE SET
                        name = COALESCE(EXCLUDED.name, trade_watchlist.name),
                        reason = COALESCE(EXCLUDED.reason, trade_watchlist.reason),
                        score = EXCLUDED.score,
                        active = TRUE
                    RETURNING *
                    """
                ),
                {
                    "code": code,
                    "name": name,
                    "reason": reason or "手动加入盯盘",
                    "score": score,
                    "asof": asof_d,
                },
            ).fetchone()
        return {"ok": True, "item": _row(row)}

    # ------------------------------------------------------------------ #
    # 巡检：发事件
    # ------------------------------------------------------------------ #
    def run_scan(
        self,
        asof: Optional[str] = None,
        *,
        cooldown_minutes: int = 30,
        pct_soft: float = 2.0,
        pct_hard: float = 5.0,
        volume_ratio: float = 2.0,
        use_mins: bool = True,
    ) -> Dict[str, Any]:
        """对当前 active watchlist 做一次规则巡检，产出事件（带冷却）。"""
        self._ensure()
        wl = self.list_watchlist(asof=asof, active_only=True)
        if not wl.get("watchlist"):
            return {"ok": True, "events": [], "count": 0, "message": "无 active watchlist，请先 build"}

        asof_d = _to_date(wl.get("asof")) or _to_date(asof) or date.today()
        events_out: List[Dict[str, Any]] = []

        with self.engine.begin() as conn:
            for item in wl["watchlist"]:
                code = item["ts_code"]
                generated = self._scan_one(
                    conn,
                    code,
                    asof_d,
                    cooldown_minutes=cooldown_minutes,
                    pct_soft=pct_soft,
                    pct_hard=pct_hard,
                    volume_ratio=volume_ratio,
                    use_mins=use_mins,
                    watch_meta=item,
                )
                events_out.extend(generated)

        return {
            "ok": True,
            "asof": asof_d.isoformat(),
            "watchlist_count": wl["count"],
            "count": len(events_out),
            "events": events_out,
        }

    def _recent_event_types(
        self, conn, ts_code: str, asof_d: date, cooldown_minutes: int
    ) -> Set[str]:
        since = datetime.utcnow() - timedelta(minutes=max(1, cooldown_minutes))
        rows = conn.execute(
            sa_text(
                """
                SELECT DISTINCT event_type FROM trade_scan_event
                WHERE ts_code = :code AND asof = :d AND created_at >= :since
                """
            ),
            {"code": ts_code, "d": asof_d, "since": since},
        ).fetchall()
        return {r.event_type for r in rows}

    def _scan_one(
        self,
        conn,
        ts_code: str,
        asof_d: date,
        *,
        cooldown_minutes: int,
        pct_soft: float,
        pct_hard: float,
        volume_ratio: float,
        use_mins: bool,
        watch_meta: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        cooled = self._recent_event_types(conn, ts_code, asof_d, cooldown_minutes)
        events: List[Dict[str, Any]] = []

        bar = conn.execute(
            sa_text(
                """
                SELECT trade_date, open, high, low, close, vol, pre_close, pct_chg
                FROM stock_daily
                WHERE ts_code = :code AND trade_date <= :d
                ORDER BY trade_date DESC LIMIT 1
                """
            ),
            {"code": ts_code, "d": asof_d},
        ).fetchone()
        if not bar:
            return events

        open_ = _f(bar.open)
        close = _f(bar.close)
        high = _f(bar.high)
        low = _f(bar.low)
        pre = _f(bar.pre_close) or open_
        vol = _f(bar.vol)
        pct = _f(bar.pct_chg)
        if open_ > 0:
            pct_from_open = (close / open_ - 1.0) * 100.0
        else:
            pct_from_open = pct

        def emit(etype: str, severity: str, detail: Dict[str, Any]):
            if etype in cooled:
                return
            row = conn.execute(
                sa_text(
                    """
                    INSERT INTO trade_scan_event
                        (ts_code, asof, event_type, severity, price, detail)
                    VALUES
                        (:code, :asof, :etype, :sev, :px, CAST(:detail AS jsonb))
                    RETURNING *
                    """
                ),
                {
                    "code": ts_code,
                    "asof": asof_d,
                    "etype": etype,
                    "sev": severity,
                    "px": close,
                    "detail": json.dumps(
                        {
                            **detail,
                            "watch_source": watch_meta.get("source"),
                            "watch_reason": watch_meta.get("reason"),
                            "watch_score": watch_meta.get("score"),
                        },
                        ensure_ascii=False,
                        default=str,
                    ),
                },
            ).fetchone()
            events.append(_row(row))
            cooled.add(etype)

        # 相对开盘涨跌
        abs_po = abs(pct_from_open)
        if abs_po >= pct_hard:
            emit(
                "pct_from_open",
                "hard",
                {
                    "pct_from_open": round(pct_from_open, 3),
                    "threshold": pct_hard,
                    "direction": "up" if pct_from_open > 0 else "down",
                },
            )
        elif abs_po >= pct_soft:
            emit(
                "pct_from_open",
                "soft",
                {
                    "pct_from_open": round(pct_from_open, 3),
                    "threshold": pct_soft,
                    "direction": "up" if pct_from_open > 0 else "down",
                },
            )

        # 接近涨停/跌停（简化：±9.5%）
        if pre > 0:
            chg = (close / pre - 1.0) * 100.0
            if chg >= 9.5:
                emit("near_limit_up", "hard", {"pct_chg": round(chg, 3)})
            elif chg <= -9.5:
                emit("near_limit_down", "hard", {"pct_chg": round(chg, 3)})

        # 放量：相对近 5 日均量
        avg_vol_row = conn.execute(
            sa_text(
                """
                SELECT AVG(vol) AS av FROM (
                    SELECT vol FROM stock_daily
                    WHERE ts_code = :code AND trade_date < :d
                    ORDER BY trade_date DESC LIMIT 5
                ) t
                """
            ),
            {"code": ts_code, "d": _to_date(bar.trade_date) or asof_d},
        ).fetchone()
        avg_vol = _f(avg_vol_row.av) if avg_vol_row else 0.0
        if avg_vol > 0 and vol >= avg_vol * volume_ratio:
            emit(
                "volume_spike",
                "soft" if vol < avg_vol * (volume_ratio + 1) else "hard",
                {
                    "vol": vol,
                    "avg_vol_5d": round(avg_vol, 2),
                    "ratio": round(vol / avg_vol, 2),
                },
            )

        # 可选：分钟线近端波动
        if use_mins:
            try:
                mins = conn.execute(
                    sa_text(
                        """
                        SELECT bar_time, open, high, low, close, vol
                        FROM stock_intraday_mins
                        WHERE ts_code = :code AND trade_date = :d
                        ORDER BY bar_time DESC
                        LIMIT 30
                        """
                    ),
                    {"code": ts_code, "d": _to_date(bar.trade_date) or asof_d},
                ).fetchall()
                if len(mins) >= 6:
                    last = mins[0]
                    prev5 = mins[5]
                    c0, c5 = _f(last.close), _f(prev5.close)
                    if c5 > 0:
                        m5 = (c0 / c5 - 1.0) * 100.0
                        if abs(m5) >= 1.5:
                            emit(
                                "move_5m",
                                "soft" if abs(m5) < 3 else "hard",
                                {
                                    "pct_5m": round(m5, 3),
                                    "from": str(prev5.bar_time),
                                    "to": str(last.bar_time),
                                },
                            )
            except Exception as e:
                logger.debug("分钟巡检跳过 %s: %s", ts_code, e)

        # 振幅提示
        if open_ > 0 and high > 0 and low > 0:
            amp = (high - low) / open_ * 100.0
            if amp >= 6.0:
                emit(
                    "high_amplitude",
                    "soft",
                    {"amplitude_pct": round(amp, 3), "high": high, "low": low},
                )

        return events

    def list_events(
        self,
        asof: Optional[str] = None,
        *,
        ts_code: Optional[str] = None,
        severity: Optional[str] = None,
        limit: int = 100,
    ) -> Dict[str, Any]:
        self._ensure()
        asof_d = _to_date(asof)
        sql = "SELECT * FROM trade_scan_event WHERE 1=1"
        params: Dict[str, Any] = {"lim": limit}
        if asof_d:
            sql += " AND asof = :d"
            params["d"] = asof_d
        if ts_code:
            sql += " AND ts_code = :code"
            params["code"] = ts_code.strip().upper()
        if severity:
            sql += " AND severity = :sev"
            params["sev"] = severity
        sql += " ORDER BY created_at DESC LIMIT :lim"
        with self.engine.connect() as conn:
            rows = conn.execute(sa_text(sql), params).fetchall()
        return {
            "ok": True,
            "count": len(rows),
            "events": [_row(r) for r in rows],
        }


trade_watch_service = TradeWatchService()

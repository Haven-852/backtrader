"""
策略流水线：观察池(A) → 自选(上限50) → 起飞推荐池 → 动能衰竭出池

阶段1：策略 A 分数≥70 写入观察池/自选，维持最多 50 只
阶段2：对池内个股判起飞（放量×1.3、站上MA5/MA10、破颈线≥1%、板块共振、龙虎榜）
阶段3：推荐池内判动能衰竭则移出，进入下一轮循环
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd
from sqlalchemy import text as sa_text

from data_layer.collectors.datahubco.selective import selective_fetcher
from data_layer.db_manager import storage_manager

logger = logging.getLogger(__name__)

# ── 固定阈值 ──
OBSERVE_MIN_SCORE = 70.0
OBSERVE_MAX_N = 50
TAKEOFF_VOL_MULT = 1.30
TAKEOFF_NECK_BREAK = 0.01
EXIT_VOL_WARN_MULT = 1.50          # 出货警示量 = base_vol × 1.5
EXHAUST_MA_DIST = 0.12             # 偏离 MA5 过远
EXHAUST_UPPER_SHADOW = 0.45        # 上影占振幅
EXHAUST_VOL_CLIMAX = 2.0           # 天量相对 20 日均量
EXHAUST_EXIT_DAYS = 2              # 连续两日超警示量

DDL = [
    """
    CREATE TABLE IF NOT EXISTS strategy_observe_pool (
        ts_code        VARCHAR(16) PRIMARY KEY,
        name           VARCHAR(64),
        asof           DATE NOT NULL,
        score          NUMERIC(8,2),
        close          NUMERIC(12,4),
        ma5            NUMERIC(12,4),
        watch_low      NUMERIC(12,4),
        neckline       NUMERIC(12,4),
        base_vol       NUMERIC(20,4),
        sector_l2      VARCHAR(16),
        sector_l2_name VARCHAR(64),
        sector_l3      VARCHAR(16),
        sector_l3_name VARCHAR(64),
        reason         TEXT,
        active         BOOLEAN DEFAULT TRUE,
        source         VARCHAR(32) DEFAULT 'screener_a',
        meta           JSONB,
        entered_at     TIMESTAMPTZ DEFAULT NOW(),
        updated_at     TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS strategy_recommend_pool (
        ts_code           VARCHAR(16) PRIMARY KEY,
        name              VARCHAR(64),
        takeoff_asof      DATE NOT NULL,
        score             NUMERIC(8,2),
        close             NUMERIC(12,4),
        ma5               NUMERIC(12,4),
        ma10              NUMERIC(12,4),
        watch_low         NUMERIC(12,4),
        neckline          NUMERIC(12,4),
        base_vol          NUMERIC(20,4),
        exit_vol_warn     NUMERIC(20,4),
        takeoff_vol       NUMERIC(20,4),
        vol_ratio         NUMERIC(12,4),
        sector_l2         VARCHAR(16),
        sector_l2_name    VARCHAR(64),
        sector_l3         VARCHAR(16),
        sector_l3_name    VARCHAR(64),
        sector_resonance  BOOLEAN DEFAULT FALSE,
        top_list_hit      BOOLEAN DEFAULT FALSE,
        takeoff_reason    TEXT,
        status            VARCHAR(16) DEFAULT 'active',
        exhaust_reason    TEXT,
        exhaust_asof      DATE,
        meta              JSONB,
        entered_at        TIMESTAMPTZ DEFAULT NOW(),
        updated_at        TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_observe_pool_asof ON strategy_observe_pool (asof DESC, active, score DESC)",
    "CREATE INDEX IF NOT EXISTS idx_recommend_pool_status ON strategy_recommend_pool (status, takeoff_asof DESC)",
    """
    ALTER TABLE dash_watchlist ADD COLUMN IF NOT EXISTS source VARCHAR(32) DEFAULT 'user'
    """,
]


def _f(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return default
        return float(v)
    except Exception:
        return default


def _to_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except Exception:
        return None


def _parse_reason_levels(reason: str) -> Dict[str, float]:
    """从观察池 reason 解析 低点/颈线/基准量。"""
    out: Dict[str, float] = {}
    if not reason:
        return out
    m = re.search(r"低点([0-9.]+)", reason)
    if m:
        out["watch_low"] = float(m.group(1))
    m = re.search(r"颈线([0-9.]+)", reason)
    if m:
        out["neckline"] = float(m.group(1))
    m = re.search(r"基准量([0-9.]+)", reason)
    if m:
        out["base_vol"] = float(m.group(1))
    return out


class RecommendPoolService:
    def __init__(self):
        self._engine = None
        self._ready = False

    @property
    def engine(self):
        if self._engine is None:
            self._engine = storage_manager.get_postgres_engine()
        return self._engine

    def _ensure(self) -> None:
        if self._ready or not self.engine:
            return
        with self.engine.begin() as conn:
            for ddl in DDL:
                try:
                    conn.execute(sa_text(ddl))
                except Exception as e:
                    logger.debug("recommend ddl: %s", e)
        self._ready = True

    def _asof(self, asof: Optional[str] = None) -> date:
        d = _to_date(asof)
        if d:
            return d
        selective_fetcher.ensure_trade_cal()
        return selective_fetcher.session_context()["asof_date"]

    # ─── 板块 / 龙虎榜 ───────────────────────────────

    def _find_sectors(self, ts_code: str, asof: date) -> Dict[str, Any]:
        if not self.engine:
            return {}
        sql = sa_text(
            """
            SELECT m.ts_code AS sector_code,
                   COALESCE(i.name, m.name) AS sector_name,
                   i.industry_level
            FROM sector_dc_member m
            LEFT JOIN sector_dc_index_daily i
              ON i.ts_code = m.ts_code AND i.trade_date = m.trade_date
            WHERE m.con_code = :c
              AND m.trade_date >= :d - INTERVAL '40 days'
              AND i.industry_level IN ('东财二级行业', '东财三级行业')
            ORDER BY m.trade_date DESC
            LIMIT 20
            """
        )
        try:
            rows = self.engine.connect().execute(sql, {"c": ts_code, "d": asof}).fetchall()
        except Exception as e:
            logger.debug("find sectors %s: %s", ts_code, e)
            return {}
        out: Dict[str, Any] = {}
        for r in rows:
            lv = r[2] or ""
            if lv == "东财二级行业" and "sector_l2" not in out:
                out["sector_l2"] = r[0]
                out["sector_l2_name"] = r[1]
            elif lv == "东财三级行业" and "sector_l3" not in out:
                out["sector_l3"] = r[0]
                out["sector_l3_name"] = r[1]
        return out

    def _sector_resonance(self, sector_codes: Sequence[str], asof: date) -> Tuple[bool, Dict[str, Any]]:
        """板块当日资金净流入或涨幅为正视为共振。"""
        codes = [c for c in sector_codes if c]
        if not codes or not self.engine:
            return False, {}
        try:
            from sqlalchemy import bindparam

            stmt = sa_text(
                """
                SELECT ts_code, name, net_amount, pct_change, content_type
                FROM sector_moneyflow_daily
                WHERE trade_date = :d AND ts_code IN :codes
                """
            ).bindparams(bindparam("codes", expanding=True))
            df = pd.read_sql(stmt, self.engine, params={"d": asof, "codes": list(codes)})
        except Exception:
            frames = []
            for c in codes:
                try:
                    part = pd.read_sql(
                        sa_text(
                            "SELECT ts_code, name, net_amount, pct_change, content_type "
                            "FROM sector_moneyflow_daily WHERE trade_date=:d AND ts_code=:c"
                        ),
                        self.engine,
                        params={"d": asof, "c": c},
                    )
                    if not part.empty:
                        frames.append(part)
                except Exception:
                    pass
            df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

        if df is None or df.empty:
            # 退回板块指数涨跌
            try:
                from sqlalchemy import bindparam

                stmt = sa_text(
                    "SELECT ts_code, name, pct_change, NULL::float AS net_amount "
                    "FROM sector_dc_index_daily WHERE trade_date=:d AND ts_code IN :codes"
                ).bindparams(bindparam("codes", expanding=True))
                df = pd.read_sql(stmt, self.engine, params={"d": asof, "codes": list(codes)})
            except Exception:
                df = pd.DataFrame()
        if df is None or df.empty:
            return False, {}
        detail = {}
        ok = False
        for _, r in df.iterrows():
            net = _f(r.get("net_amount"), 0)
            pct = _f(r.get("pct_change"), 0)
            detail[str(r["ts_code"])] = {"net_amount": net, "pct_change": pct, "name": r.get("name")}
            if net > 0 or pct > 0:
                ok = True
        return ok, detail

    def _top_list_hit(self, ts_code: str, asof: date) -> bool:
        if not self.engine:
            return False
        try:
            row = self.engine.connect().execute(
                sa_text(
                    "SELECT 1 FROM stock_top_list_daily "
                    "WHERE ts_code=:c AND trade_date=:d LIMIT 1"
                ),
                {"c": ts_code, "d": asof},
            ).fetchone()
            if row:
                return True
        except Exception:
            pass
        # 远端补一次
        try:
            from data_layer.collectors.datahubco.client import DatahubcoClient

            client = DatahubcoClient()
            df = client.query("top_list", trade_date=asof.strftime("%Y%m%d"))
            if df is None or df.empty:
                return False
            df.columns = [str(c).lower() for c in df.columns]
            return ts_code.upper() in set(df["ts_code"].astype(str).str.upper())
        except Exception as e:
            logger.debug("top_list %s: %s", ts_code, e)
            return False

    def _load_bars(self, ts_code: str, asof: date, lookback: int = 60) -> pd.DataFrame:
        if not self.engine:
            return pd.DataFrame()
        start = asof - timedelta(days=lookback * 3)
        df = pd.read_sql(
            sa_text(
                "SELECT trade_date, open, high, low, close, vol "
                "FROM stock_daily WHERE ts_code=:c AND trade_date BETWEEN :s AND :e "
                "ORDER BY trade_date"
            ),
            self.engine,
            params={"c": ts_code, "s": start, "e": asof},
        )
        if df.empty:
            return df
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
        for col in ("open", "high", "low", "close", "vol"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df

    # ─── 阶段1：A≥70 → 观察池 + 自选（维持50）────────

    def sync_observe_from_screener(
        self,
        asof: Optional[str] = None,
        *,
        min_score: float = OBSERVE_MIN_SCORE,
        max_n: int = OBSERVE_MAX_N,
        fill: bool = True,
        sync_dash: bool = True,
    ) -> Dict[str, Any]:
        self._ensure()
        from server.screener_service import screener_service

        asof_d = self._asof(asof)
        logger.info(
            "[pipeline/observe] start asof=%s min_score=%.1f max_n=%s fill=%s",
            asof_d, min_score, max_n, fill,
        )
        screen = screener_service.screen_setups(
            asof=asof_d.isoformat(),
            include_a=True,
            include_b=False,
            limit=max(200, max_n * 4),
            fill=fill,
        )
        if not screen.get("ok"):
            logger.warning("[pipeline/observe] screener failed: %s", screen.get("error"))
            return {"ok": False, "error": screen.get("error") or "选股失败", "asof": asof_d.isoformat()}

        raw_a = list(screen.get("hits_a") or [])
        scores = sorted((_f(h.get("score")) for h in raw_a), reverse=True)
        hits = [h for h in raw_a if _f(h.get("score")) >= float(min_score)]
        hits.sort(key=lambda x: _f(x.get("score")), reverse=True)
        hits = hits[: int(max_n)]
        logger.info(
            "[pipeline/observe] screener_hit_a=%s universe=%s score_max=%.2f score_p50=%.2f "
            "ge_min=%s keep=%s top5=%s",
            screen.get("hit_count_a"),
            screen.get("universe"),
            scores[0] if scores else 0.0,
            scores[len(scores) // 2] if scores else 0.0,
            len([s for s in scores if s >= float(min_score)]),
            len(hits),
            [(h.get("ts_code"), round(_f(h.get("score")), 2)) for h in hits[:5]]
            or [(h.get("ts_code"), round(_f(h.get("score")), 2)) for h in raw_a[:5]],
        )
        if not hits and raw_a:
            logger.warning(
                "[pipeline/observe] 全部未过门槛：min_score=%.1f 但最高分仅 %.2f（共 %s 只 A）",
                min_score, scores[0] if scores else 0.0, len(raw_a),
            )

        saved = []
        with self.engine.begin() as conn:
            conn.execute(sa_text("UPDATE strategy_observe_pool SET active = FALSE, updated_at = NOW()"))
            for h in hits:
                code = str(h["ts_code"]).upper()
                parsed = _parse_reason_levels(str(h.get("reason") or ""))
                sectors = self._find_sectors(code, asof_d)
                watch_low = parsed.get("watch_low") or _f(h.get("close")) * 0.95
                neckline = parsed.get("neckline") or _f(h.get("close")) * 1.02
                base_vol = parsed.get("base_vol") or 0.0
                # 若缺 base_vol，用日线估
                if base_vol <= 0:
                    bars = self._load_bars(code, asof_d, 40)
                    if not bars.empty and len(bars) >= 5:
                        base_vol = float(bars["vol"].tail(20).mean())
                meta = {
                    "pct_chg": h.get("pct_chg"),
                    "below_pct": h.get("below_pct"),
                    "screener_reason": h.get("reason"),
                }
                conn.execute(
                    sa_text(
                        """
                        INSERT INTO strategy_observe_pool (
                            ts_code, name, asof, score, close, ma5,
                            watch_low, neckline, base_vol,
                            sector_l2, sector_l2_name, sector_l3, sector_l3_name,
                            reason, active, source, meta, entered_at, updated_at
                        ) VALUES (
                            :c, :n, :d, :sc, :cl, :ma,
                            :wl, :nk, :bv,
                            :l2, :l2n, :l3, :l3n,
                            :rs, TRUE, 'screener_a', CAST(:meta AS jsonb), NOW(), NOW()
                        )
                        ON CONFLICT (ts_code) DO UPDATE SET
                            name=EXCLUDED.name, asof=EXCLUDED.asof, score=EXCLUDED.score,
                            close=EXCLUDED.close, ma5=EXCLUDED.ma5,
                            watch_low=EXCLUDED.watch_low, neckline=EXCLUDED.neckline,
                            base_vol=EXCLUDED.base_vol,
                            sector_l2=EXCLUDED.sector_l2, sector_l2_name=EXCLUDED.sector_l2_name,
                            sector_l3=EXCLUDED.sector_l3, sector_l3_name=EXCLUDED.sector_l3_name,
                            reason=EXCLUDED.reason, active=TRUE, meta=EXCLUDED.meta,
                            updated_at=NOW()
                        """
                    ),
                    {
                        "c": code,
                        "n": h.get("name") or code,
                        "d": asof_d,
                        "sc": _f(h.get("score")),
                        "cl": _f(h.get("close")),
                        "ma": _f(h.get("ma5")),
                        "wl": watch_low,
                        "nk": neckline,
                        "bv": base_vol,
                        "l2": sectors.get("sector_l2"),
                        "l2n": sectors.get("sector_l2_name"),
                        "l3": sectors.get("sector_l3"),
                        "l3n": sectors.get("sector_l3_name"),
                        "rs": h.get("reason") or "观察池",
                        "meta": json.dumps(meta, ensure_ascii=False),
                    },
                )
                saved.append({"ts_code": code, "name": h.get("name"), "score": _f(h.get("score"))})

        dash_sync = {"added": 0, "removed": 0}
        if sync_dash:
            dash_sync = self._sync_dash_watchlist_from_observe(saved)

        note = None
        if not saved and raw_a:
            note = (
                f"A类命中{len(raw_a)}只，但分数均<{min_score:g} "
                f"(最高{scores[0]:.2f})，观察池为空"
            )
        logger.info(
            "[pipeline/observe] done count=%s dash=%s note=%s",
            len(saved), dash_sync, note,
        )
        return {
            "ok": True,
            "step": "observe",
            "asof": asof_d.isoformat(),
            "min_score": float(min_score),
            "count": len(saved),
            "items": saved,
            "dash_sync": dash_sync,
            "screener_hit_a": screen.get("hit_count_a"),
            "score_max": round(scores[0], 2) if scores else None,
            "score_top5": [round(s, 2) for s in scores[:5]],
            "below_min_score": max(0, len(raw_a) - len([s for s in scores if s >= float(min_score)])),
            "note": note,
        }

    def _sync_dash_watchlist_from_observe(self, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        """把观察池同步到 dash_watchlist（source=observe_a），维持不超过 50；不删用户自选。"""
        self._ensure()
        codes = {str(x["ts_code"]).upper() for x in items}
        added = removed = 0
        with self.engine.begin() as conn:
            # 移除过期的 observe_a
            old = conn.execute(
                sa_text("SELECT ts_code FROM dash_watchlist WHERE source = 'observe_a'")
            ).fetchall()
            for r in old:
                if str(r[0]).upper() not in codes:
                    conn.execute(
                        sa_text("DELETE FROM dash_watchlist WHERE ts_code=:c AND source='observe_a'"),
                        {"c": r[0]},
                    )
                    removed += 1
            for it in items:
                code = str(it["ts_code"]).upper()
                conn.execute(
                    sa_text(
                        """
                        INSERT INTO dash_watchlist (ts_code, name, short_name, kind, industry, continuous, source, created_at)
                        VALUES (:c, :n, :n, 'stock', '观察池A', TRUE, 'observe_a', NOW())
                        ON CONFLICT (ts_code) DO UPDATE SET
                            name = EXCLUDED.name,
                            short_name = EXCLUDED.short_name,
                            industry = CASE
                                WHEN dash_watchlist.source = 'user' THEN dash_watchlist.industry
                                ELSE EXCLUDED.industry END,
                            source = CASE
                                WHEN dash_watchlist.source = 'user' THEN 'user'
                                ELSE 'observe_a' END,
                            continuous = TRUE
                        """
                    ),
                    {"c": code, "n": it.get("name") or code},
                )
                added += 1
        return {"added": added, "removed": removed}

    def list_observe_pool(self, active_only: bool = True) -> Dict[str, Any]:
        self._ensure()
        sql = "SELECT * FROM strategy_observe_pool"
        if active_only:
            sql += " WHERE active = TRUE"
        sql += " ORDER BY score DESC NULLS LAST"
        rows = self.engine.connect().execute(sa_text(sql)).mappings().fetchall()
        items = [dict(r) for r in rows]
        for it in items:
            for k, v in list(it.items()):
                if isinstance(v, (date, datetime)):
                    it[k] = v.isoformat()
                elif hasattr(v, "isoformat"):
                    it[k] = str(v)
        return {"ok": True, "count": len(items), "items": items}

    # ─── 阶段2：起飞 → 推荐池 ─────────────────────────

    def scan_takeoff(
        self,
        asof: Optional[str] = None,
        *,
        require_sector: bool = False,
        require_top_list: bool = False,
        require_flow_signal: bool = True,
    ) -> Dict[str, Any]:
        """
        对观察池 active 标的判起飞：
        放量≥20日均量×1.3，收盘>MA5且>MA10，破颈线≥1%；
        默认还要求「板块共振 或 龙虎榜/游资异动」至少一项。
        """
        self._ensure()
        asof_d = self._asof(asof)
        pool = self.list_observe_pool(active_only=True).get("items") or []
        promoted = []
        rejected = []
        logger.info(
            "[pipeline/takeoff] start asof=%s observe=%s require_sector=%s require_top=%s require_flow=%s",
            asof_d, len(pool), require_sector, require_top_list, require_flow_signal,
        )

        for item in pool:
            code = str(item["ts_code"]).upper()
            bars = self._load_bars(code, asof_d, 60)
            if bars.empty or len(bars) < 15:
                rejected.append({"ts_code": code, "reason": "日线不足"})
                continue
            last = bars.iloc[-1]
            if last["trade_date"] != asof_d:
                rejected.append({"ts_code": code, "reason": f"无{asof_d}日线"})
                continue

            close = _f(last["close"])
            open_ = _f(last["open"])
            high = _f(last["high"])
            low = _f(last["low"])
            vol = _f(last["vol"])
            ma5 = _f(bars["close"].tail(5).mean())
            ma10 = _f(bars["close"].tail(10).mean())
            base_vol = _f(item.get("base_vol"))
            if base_vol <= 0:
                base_vol = _f(bars["vol"].tail(20).mean())
            neckline = _f(item.get("neckline"))
            if neckline <= 0:
                neckline = _f(bars["high"].iloc[-16:-1].max()) if len(bars) > 16 else close

            vol_ratio = vol / base_vol if base_vol > 0 else 0.0
            neck_break = (close / neckline - 1.0) if neckline > 0 else -1.0

            checks = {
                "vol_ok": vol_ratio >= TAKEOFF_VOL_MULT,
                "ma5_ok": close > ma5,
                "ma10_ok": close > ma10,
                "neck_ok": neck_break >= TAKEOFF_NECK_BREAK,
                "yang_ok": close >= open_,
            }
            if not all(checks.values()):
                rejected.append({"ts_code": code, "reason": "未满足起飞条件", "checks": checks})
                continue

            sectors = {
                "sector_l2": item.get("sector_l2"),
                "sector_l2_name": item.get("sector_l2_name"),
                "sector_l3": item.get("sector_l3"),
                "sector_l3_name": item.get("sector_l3_name"),
            }
            if not sectors.get("sector_l2"):
                sectors.update(self._find_sectors(code, asof_d))

            sec_codes = [sectors.get("sector_l2"), sectors.get("sector_l3")]
            resonance, sec_detail = self._sector_resonance(sec_codes, asof_d)
            top_hit = self._top_list_hit(code, asof_d)

            if require_sector and not resonance:
                rejected.append({"ts_code": code, "reason": "板块未共振", "checks": checks})
                continue
            if require_top_list and not top_hit:
                rejected.append({"ts_code": code, "reason": "无龙虎榜", "checks": checks})
                continue
            if require_flow_signal and not (resonance or top_hit):
                rejected.append(
                    {
                        "ts_code": code,
                        "reason": "无板块共振且无游资/龙虎榜异动",
                        "checks": {**checks, "resonance": resonance, "top_list": top_hit},
                    }
                )
                continue

            # 打分
            score = 40.0
            score += min(25.0, (vol_ratio - 1.3) / 0.7 * 25)
            score += min(20.0, neck_break / 0.05 * 20)
            if resonance:
                score += 15
            if top_hit:
                score += 10
            score = round(min(score, 100.0), 2)

            exit_warn = base_vol * EXIT_VOL_WARN_MULT
            reason = (
                f"起飞·量比{vol_ratio:.2f}·破颈{(neck_break*100):.1f}%·"
                f"站上MA5/MA10·共振{'是' if resonance else '否'}·龙虎榜{'是' if top_hit else '否'}"
            )
            with self.engine.begin() as conn:
                conn.execute(
                    sa_text(
                        """
                        INSERT INTO strategy_recommend_pool (
                            ts_code, name, takeoff_asof, score, close, ma5, ma10,
                            watch_low, neckline, base_vol, exit_vol_warn, takeoff_vol, vol_ratio,
                            sector_l2, sector_l2_name, sector_l3, sector_l3_name,
                            sector_resonance, top_list_hit, takeoff_reason,
                            status, meta, entered_at, updated_at
                        ) VALUES (
                            :c, :n, :d, :sc, :cl, :m5, :m10,
                            :wl, :nk, :bv, :ew, :tv, :vr,
                            :l2, :l2n, :l3, :l3n,
                            :res, :top, :rs,
                            'active', CAST(:meta AS jsonb), NOW(), NOW()
                        )
                        ON CONFLICT (ts_code) DO UPDATE SET
                            name=EXCLUDED.name, takeoff_asof=EXCLUDED.takeoff_asof,
                            score=EXCLUDED.score, close=EXCLUDED.close, ma5=EXCLUDED.ma5, ma10=EXCLUDED.ma10,
                            watch_low=EXCLUDED.watch_low, neckline=EXCLUDED.neckline,
                            base_vol=EXCLUDED.base_vol, exit_vol_warn=EXCLUDED.exit_vol_warn,
                            takeoff_vol=EXCLUDED.takeoff_vol, vol_ratio=EXCLUDED.vol_ratio,
                            sector_l2=EXCLUDED.sector_l2, sector_l2_name=EXCLUDED.sector_l2_name,
                            sector_l3=EXCLUDED.sector_l3, sector_l3_name=EXCLUDED.sector_l3_name,
                            sector_resonance=EXCLUDED.sector_resonance, top_list_hit=EXCLUDED.top_list_hit,
                            takeoff_reason=EXCLUDED.takeoff_reason, status='active',
                            exhaust_reason=NULL, exhaust_asof=NULL,
                            meta=EXCLUDED.meta, updated_at=NOW()
                        """
                    ),
                    {
                        "c": code,
                        "n": item.get("name") or code,
                        "d": asof_d,
                        "sc": score,
                        "cl": close,
                        "m5": ma5,
                        "m10": ma10,
                        "wl": _f(item.get("watch_low")),
                        "nk": neckline,
                        "bv": base_vol,
                        "ew": exit_warn,
                        "tv": vol,
                        "vr": vol_ratio,
                        "l2": sectors.get("sector_l2"),
                        "l2n": sectors.get("sector_l2_name"),
                        "l3": sectors.get("sector_l3"),
                        "l3n": sectors.get("sector_l3_name"),
                        "res": resonance,
                        "top": top_hit,
                        "rs": reason,
                        "meta": json.dumps(
                            {"checks": checks, "sector_flow": sec_detail, "high": high, "low": low},
                            ensure_ascii=False,
                            default=str,
                        ),
                    },
                )
            promoted.append(
                {
                    "ts_code": code,
                    "name": item.get("name"),
                    "score": score,
                    "vol_ratio": round(vol_ratio, 3),
                    "neck_break_pct": round(neck_break * 100, 2),
                    "sector_resonance": resonance,
                    "top_list_hit": top_hit,
                    "reason": reason,
                    "sector_l2_name": sectors.get("sector_l2_name"),
                    "sector_l3_name": sectors.get("sector_l3_name"),
                }
            )

        # 拒绝原因汇总
        reason_counter: Dict[str, int] = {}
        for r in rejected:
            key = str(r.get("reason") or "unknown")
            reason_counter[key] = reason_counter.get(key, 0) + 1
        logger.info(
            "[pipeline/takeoff] done promoted=%s rejected=%s reasons=%s sample=%s",
            len(promoted),
            len(rejected),
            reason_counter,
            [(p.get("ts_code"), p.get("score")) for p in promoted[:5]],
        )

        return {
            "ok": True,
            "step": "takeoff",
            "asof": asof_d.isoformat(),
            "observe_count": len(pool),
            "promoted_count": len(promoted),
            "promoted": promoted,
            "rejected_count": len(rejected),
            "rejected_reasons": reason_counter,
            "rejected_sample": rejected[:20],
        }

    def list_recommend_pool(self, status: str = "active") -> Dict[str, Any]:
        self._ensure()
        sql = "SELECT * FROM strategy_recommend_pool"
        params: Dict[str, Any] = {}
        if status and status != "all":
            sql += " WHERE status = :st"
            params["st"] = status
        sql += " ORDER BY score DESC NULLS LAST, takeoff_asof DESC"
        rows = self.engine.connect().execute(sa_text(sql), params).mappings().fetchall()
        items = []
        for r in rows:
            it = dict(r)
            for k, v in list(it.items()):
                if isinstance(v, (date, datetime)):
                    it[k] = v.isoformat()
            items.append(it)
        return {"ok": True, "count": len(items), "items": items, "status": status}

    # ─── 阶段3：动能衰竭出池 ─────────────────────────

    def scan_exhaustion(self, asof: Optional[str] = None) -> Dict[str, Any]:
        self._ensure()
        asof_d = self._asof(asof)
        pool = self.list_recommend_pool(status="active").get("items") or []
        exhausted = []
        kept = []
        logger.info("[pipeline/exhaust] start asof=%s active_recommend=%s", asof_d, len(pool))

        for item in pool:
            code = str(item["ts_code"]).upper()
            bars = self._load_bars(code, asof_d, 60)
            if bars.empty or len(bars) < 10:
                kept.append(code)
                continue
            if bars.iloc[-1]["trade_date"] != asof_d:
                kept.append(code)
                continue

            reason = self._detect_exhaustion(bars, item)
            if reason:
                with self.engine.begin() as conn:
                    conn.execute(
                        sa_text(
                            """
                            UPDATE strategy_recommend_pool
                            SET status='exhausted', exhaust_reason=:rs, exhaust_asof=:d, updated_at=NOW()
                            WHERE ts_code=:c
                            """
                        ),
                        {"c": code, "rs": reason, "d": asof_d},
                    )
                exhausted.append({"ts_code": code, "name": item.get("name"), "reason": reason})
                logger.info("[pipeline/exhaust] out %s %s | %s", code, item.get("name"), reason)
            else:
                kept.append(code)

        logger.info(
            "[pipeline/exhaust] done exhausted=%s kept=%s sample=%s",
            len(exhausted),
            len(kept),
            [(e.get("ts_code"), e.get("reason")) for e in exhausted[:5]],
        )
        return {
            "ok": True,
            "step": "exhaustion",
            "asof": asof_d.isoformat(),
            "active_before": len(pool),
            "exhausted_count": len(exhausted),
            "exhausted": exhausted,
            "kept_count": len(kept),
        }

    def _detect_exhaustion(self, bars: pd.DataFrame, item: Dict[str, Any]) -> Optional[str]:
        c = bars["close"].astype(float)
        o = bars["open"].astype(float)
        h = bars["high"].astype(float)
        low = bars["low"].astype(float)
        v = bars["vol"].astype(float)
        ma5 = c.rolling(5).mean()
        ma10 = c.rolling(10).mean()
        vma20 = v.rolling(20, min_periods=10).mean()

        last_i = len(c) - 1
        close = float(c.iloc[last_i])
        open_ = float(o.iloc[last_i])
        high = float(h.iloc[last_i])
        low_p = float(low.iloc[last_i])
        vol = float(v.iloc[last_i])
        ma5v = float(ma5.iloc[last_i]) if pd.notna(ma5.iloc[last_i]) else close
        ma10v = float(ma10.iloc[last_i]) if pd.notna(ma10.iloc[last_i]) else close
        vma = float(vma20.iloc[last_i]) if pd.notna(vma20.iloc[last_i]) else vol
        rng = max(high - low_p, abs(close) * 0.01)
        upper_sh = (high - max(open_, close)) / rng
        dist_ma5 = (close - ma5v) / ma5v if ma5v > 0 else 0.0

        reasons = []

        # 1) 抛物线偏离 + 长上影/滞涨阳
        stagnant_yang = close >= open_ and (close / open_ - 1.0) < 0.015 and upper_sh >= EXHAUST_UPPER_SHADOW
        if dist_ma5 >= EXHAUST_MA_DIST and (upper_sh >= EXHAUST_UPPER_SHADOW or stagnant_yang):
            reasons.append(f"偏离MA5达{dist_ma5*100:.1f}%且上影/滞涨")

        # 2) 放量滞涨：天量无新高或阴吞没
        recent_high = float(h.iloc[-10:-1].max()) if last_i >= 10 else float(h.iloc[:-1].max())
        vol_climax = vma > 0 and vol >= EXHAUST_VOL_CLIMAX * vma
        if vol_climax and close < recent_high * 0.995:
            reasons.append("天量滞涨未创新高")
        if last_i >= 1:
            prev_o, prev_c = float(o.iloc[last_i - 1]), float(c.iloc[last_i - 1])
            engulf = close < open_ and open_ >= prev_c and close <= prev_o and prev_c > prev_o
            if vol_climax and engulf:
                reasons.append("天量阴线吞没")

        # 3) 跌破短均且反抽失败（昨跌破，今反抽不过）
        if last_i >= 1:
            prev_c = float(c.iloc[last_i - 1])
            prev_ma5 = float(ma5.iloc[last_i - 1]) if pd.notna(ma5.iloc[last_i - 1]) else prev_c
            broke = prev_c < prev_ma5 and close < ma5v
            fail_retest = close < ma5v and high >= ma5v * 0.998 and close < open_
            if broke and (close < ma5v * 0.99 or fail_retest):
                reasons.append("有效跌破MA5且反抽失败")

        # 4) 均线黏合走平
        if last_i >= 5 and ma5v > 0 and ma10v > 0:
            spread = abs(ma5v - ma10v) / ma5v
            slope5 = float(ma5.iloc[last_i] / ma5.iloc[last_i - 5] - 1.0) if pd.notna(ma5.iloc[last_i - 5]) else 0
            if spread < 0.008 and abs(slope5) < 0.01 and dist_ma5 < 0.03:
                reasons.append("均线黏合走平")

        # 5) 连续两日量能超过出货警示量
        exit_warn = _f(item.get("exit_vol_warn"))
        if exit_warn <= 0:
            exit_warn = _f(item.get("base_vol")) * EXIT_VOL_WARN_MULT
        if exit_warn > 0 and last_i >= 1:
            v0 = float(v.iloc[last_i])
            v1 = float(v.iloc[last_i - 1])
            if v0 >= exit_warn and v1 >= exit_warn:
                reasons.append(f"连续2日量能≥出货警示({exit_warn:.0f})")

        if not reasons:
            return None
        return "；".join(reasons[:3])

    # ─── 一键全流程 ───────────────────────────────────

    def run_cycle(
        self,
        asof: Optional[str] = None,
        *,
        min_score: float = OBSERVE_MIN_SCORE,
        max_n: int = OBSERVE_MAX_N,
        fill: bool = True,
        require_sector: bool = False,
        require_top_list: bool = False,
        require_flow_signal: bool = True,
    ) -> Dict[str, Any]:
        """观察池同步 → 起飞扫描 → 衰竭出池。"""
        asof_d = self._asof(asof)
        t0 = datetime.now()
        logger.info(
            "[pipeline/cycle] ===== START asof=%s(req=%s) min_score=%.1f max_n=%s "
            "fill=%s flow=%s sector=%s top=%s =====",
            asof_d, asof, min_score, max_n, fill,
            require_flow_signal, require_sector, require_top_list,
        )
        observe = self.sync_observe_from_screener(
            asof_d.isoformat(), min_score=min_score, max_n=max_n, fill=fill, sync_dash=True
        )
        takeoff = self.scan_takeoff(
            asof_d.isoformat(),
            require_sector=require_sector,
            require_top_list=require_top_list,
            require_flow_signal=require_flow_signal,
        )
        exhaust = self.scan_exhaustion(asof_d.isoformat())
        recommend = self.list_recommend_pool(status="active")
        elapsed = (datetime.now() - t0).total_seconds()
        summary = {
            "observe": observe.get("count"),
            "screener_hit_a": observe.get("screener_hit_a"),
            "score_max": observe.get("score_max"),
            "takeoff": takeoff.get("promoted_count"),
            "exhaust": exhaust.get("exhausted_count"),
            "recommend_active": recommend.get("count", 0),
            "note": observe.get("note"),
            "elapsed_sec": round(elapsed, 2),
        }
        logger.info("[pipeline/cycle] ===== DONE %s =====", summary)
        return {
            "ok": bool(observe.get("ok") and takeoff.get("ok") and exhaust.get("ok")),
            "asof": asof_d.isoformat(),
            "observe": observe,
            "takeoff": takeoff,
            "exhaustion": exhaust,
            "recommend_active": recommend.get("count", 0),
            "summary": summary,
        }


recommend_pool_service = RecommendPoolService()

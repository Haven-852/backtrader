"""实验室：验证一个假设，不收藏策略，也不自动变成规则。

改条件会升版本，并重新锁定测试集。
测试集不参与调参。样本不够时标成证据不足，不凑成已验证。
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import text

from data_layer.db_manager import storage_manager

GATES = {
    "min_sample": 100,
    "min_expectancy": 0.0,
    "min_profit_factor": 1.0,
    "max_train_validation_gap": 1.0,
}

SOURCES = (
    ("MY_OBSERVATION", "我的观察"),
    ("EXTERNAL", "外部知识"),
    ("RULE", "交易规则"),
    ("LESSON", "交易经验"),
    ("TRADE_REVIEW", "交易复盘"),
)
STATUSES = (
    ("IDEA", "想法"),
    ("BACKTESTING", "回测中"),
    ("VALIDATING", "验证中"),
    ("PAPER_TRADING", "模拟"),
    ("LIVE_TEST", "实盘测试"),
    ("VALIDATED", "已验证"),
    ("REJECTED", "已拒绝"),
    ("NEED_MORE_DATA", "证据不足"),
    ("FIT", "时间与板块上比较符合"),
    ("ARCHIVED", "已归档"),
)
FAILURES = {
    "NOT_ENOUGH_SAMPLES": "样本不足",
    "NEGATIVE_EXPECTANCY": "期望超额不为正",
    "OUT_OF_SAMPLE_FAILURE": "样本外失效",
    "OVERFITTING": "训练集明显高于验证集",
    "NO_EDGE": "没有稳定超额",
    "NOT_BROAD": "没有在时间和板块上同时成立",
}

_ALTERS = (
    "ALTER TABLE experiment ADD COLUMN IF NOT EXISTS experiment_code VARCHAR(32)",
    "ALTER TABLE experiment ADD COLUMN IF NOT EXISTS source_experience_id UUID",
    "ALTER TABLE experiment ADD COLUMN IF NOT EXISTS universe VARCHAR(64)",
    "ALTER TABLE experiment ADD COLUMN IF NOT EXISTS benchmark VARCHAR(32)",
    "ALTER TABLE experiment ADD COLUMN IF NOT EXISTS version VARCHAR(16) NOT NULL DEFAULT '1.0'",
    "ALTER TABLE experiment ADD COLUMN IF NOT EXISTS conclusion TEXT",
    "ALTER TABLE experiment ADD COLUMN IF NOT EXISTS failure_reason VARCHAR(40)",
    "ALTER TABLE experiment ADD COLUMN IF NOT EXISTS test_unlocked BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE experiment ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    """
    CREATE TABLE IF NOT EXISTS experiment_run (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        experiment_id UUID NOT NULL REFERENCES experiment(id) ON DELETE CASCADE,
        version VARCHAR(16) NOT NULL,
        phase VARCHAR(20) NOT NULL,
        run_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        parameters JSONB,
        data_snapshot JSONB,
        result JSONB,
        warnings JSONB
    )
    """,
)


def _board_hit(name: Any, wanted: Any) -> bool:
    left = str(name or "").strip()
    right = str(wanted or "").strip()
    if not left or not right or left.startswith("非"):
        return False
    return left == right or left.startswith(right)


def _as_date(value: Any) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def _f(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _loads(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _brief(row: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for key, value in row.items():
        if hasattr(value, "isoformat"):
            out[key] = value.isoformat()
        elif hasattr(value, "hex") and hasattr(value, "int"):
            out[key] = str(value)
        else:
            out[key] = _num(value)
    return out


def _num(value: Any) -> Any:
    if hasattr(value, "as_tuple"):
        return float(value)
    return value


def _bump(version: str) -> str:
    major, _, minor = (version or "1.0").partition(".")
    try:
        return f"{int(major or 1)}.{int(minor or 0) + 1}"
    except ValueError:
        return "1.1"


def _metrics(excesses: Sequence[float]) -> Dict[str, Any]:
    rows = [float(item) for item in excesses]
    if not rows:
        return {
            "sample_size": 0, "win_rate": None, "avg_win": None, "avg_loss": None,
            "expectancy": None, "profit_factor": None, "max_drawdown": None,
        }
    wins = [item for item in rows if item > 0]
    losses = [item for item in rows if item < 0]
    gain = sum(wins)
    loss = sum(-item for item in losses)
    peak = 0.0
    cum = 0.0
    drawdown = 0.0
    for item in rows:
        cum += item
        peak = max(peak, cum)
        drawdown = max(drawdown, peak - cum)
    return {
        "sample_size": len(rows),
        "win_rate": round(len(wins) / len(rows), 4),
        "avg_win": None if not wins else round(sum(wins) / len(wins), 4),
        "avg_loss": None if not losses else round(sum(losses) / len(losses), 4),
        "expectancy": round(sum(rows) / len(rows), 4),
        "profit_factor": None if loss <= 0 else round(gain / loss, 4),
        "max_drawdown": round(drawdown, 4),
    }


class ExperimentService:
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

    def meta(self) -> Dict[str, Any]:
        self.ensure()
        with self.engine.connect() as conn:
            lessons = conn.execute(text("SELECT id, title FROM lesson ORDER BY updated_at DESC LIMIT 80")).mappings().fetchall()
            knowledge = conn.execute(
                text(
                    """
                    SELECT id, title, summary, knowledge_type, evidence_level
                    FROM external_knowledge ORDER BY created_at DESC LIMIT 40
                    """
                )
            ).mappings().fetchall()
            rules = conn.execute(
                text(
                    """
                    SELECT id, rule_code, name, description, status
                    FROM rule ORDER BY updated_at DESC NULLS LAST LIMIT 40
                    """
                )
            ).mappings().fetchall()
        return {
            "sources": [{"id": key, "label": label} for key, label in SOURCES],
            "statuses": [{"id": key, "label": label} for key, label in STATUSES],
            "regimes": ["牛", "熊", "震荡", "修复"],
            "holdings": [1, 3, 5, 10],
            "gates": GATES,
            "lessons": [{"id": str(row["id"]), "title": row["title"]} for row in lessons],
            "knowledge": [_brief(dict(row)) for row in knowledge],
            "rules": [_brief(dict(row)) for row in rules],
            "note": "可以从交易规则或外部知识点回测。模型只决定测哪个板块、哪种环境和持有几天，数字来自行情。测试集仍然锁定。",
        }

    def list_items(self) -> Dict[str, Any]:
        self.ensure()
        with self.engine.connect() as conn:
            rows = conn.execute(
                text("SELECT * FROM experiment ORDER BY created_at DESC LIMIT 100")
            ).mappings().fetchall()
        return {"ok": True, "items": [self._pack(dict(row)) for row in rows]}

    def create(self, body: Dict[str, Any]) -> Dict[str, Any]:
        self.ensure()
        parsed = self._parse(body, version="1.0")
        if not parsed["ok"]:
            return parsed
        code = self._next_code()
        spec = parsed["spec"]
        with self.engine.begin() as conn:
            row = conn.execute(
                text(
                    """
                    INSERT INTO experiment (
                        experiment_code, strategy_name, hypothesis, hypothesis_source,
                        source_knowledge_id, source_experience_id, market, universe, timeframe,
                        benchmark, train_start, train_end, validation_start, validation_end,
                        test_start, test_end, parameters, status, version, test_unlocked
                    ) VALUES (
                        :code, :name, :hyp, :source,
                        CAST(:kid AS uuid), CAST(:eid AS uuid), :market, :universe, 'DAILY',
                        :benchmark, :tr0, :tr1, :va0, :va1, :te0, :te1,
                        CAST(:params AS jsonb), 'IDEA', '1.0', FALSE
                    ) RETURNING id
                    """
                ),
                self._bind(spec, code),
            ).fetchone()
        return self.get(str(row[0]))

    async def backtest_from(self, kind: str, source_id: str) -> Dict[str, Any]:
        """读规则或外部知识，由模型选定回测方式，再用行情计算训练集和验证集。"""
        self.ensure()
        kind = (kind or "").lower()
        if kind not in ("rule", "external"):
            return {"ok": False, "error": "只能从交易规则或外部知识回测"}
        source = self._load_source(kind, source_id)
        if not source.get("ok"):
            return source
        if kind == "rule":
            created = self._create_market_rule(source)
            if not created.get("ok"):
                return created
            item = created["item"]
            await self.fill_market(item["id"])
            return self.run_market(item["id"])
        design = await self._design_backtest(source)
        if not design.get("ok"):
            return design
        created = self.create({
            "strategy_name": design["strategy_name"],
            "hypothesis": design["hypothesis"],
            "hypothesis_source": "RULE" if kind == "rule" else "EXTERNAL",
            "source_knowledge_id": source_id if kind == "external" else None,
            "rule_id": source_id if kind == "rule" else None,
            "universe_name": design["universe_name"],
            "regimes": design["regimes"],
            "require_benchmark_down": design["require_benchmark_down"],
            "holding_days": design["holding_days"],
            "min_up_ratio": design.get("min_up_ratio"),
            "design_reason": design["reason"],
        })
        if not created.get("ok"):
            return created
        item = created["item"]
        await self.fill_inputs(item["id"])
        trained = self.run(item["id"], "train")
        if not trained.get("ok"):
            return trained
        checked = self.run(item["id"], "validation")
        packed = checked if checked.get("ok") else trained
        packed["design_reason"] = design["reason"]
        return packed

    def update(self, experiment_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        self.ensure()
        current = self.get(experiment_id)
        if not current.get("ok"):
            return current
        item = current["item"]
        if item.get("status") in ("VALIDATED", "ARCHIVED"):
            return {"ok": False, "error": "已验证或已归档的实验不能直接改条件。请另建一个实验。"}
        version = _bump(item.get("version") or "1.0")
        parsed = self._parse(body, version=version)
        if not parsed["ok"]:
            return parsed
        spec = parsed["spec"]
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE experiment SET
                        strategy_name = :name, hypothesis = :hyp, hypothesis_source = :source,
                        source_knowledge_id = CAST(:kid AS uuid), source_experience_id = CAST(:eid AS uuid),
                        market = :market, universe = :universe, benchmark = :benchmark,
                        train_start = :tr0, train_end = :tr1, validation_start = :va0, validation_end = :va1,
                        test_start = :te0, test_end = :te1, parameters = CAST(:params AS jsonb),
                        version = :version, status = 'IDEA', test_unlocked = FALSE,
                        result = NULL, overfit_check = NULL, conclusion = NULL, failure_reason = NULL,
                        sample_size = NULL, win_rate = NULL, expectancy = NULL, profit_factor = NULL,
                        max_drawdown = NULL, updated_at = NOW()
                    WHERE id = CAST(:id AS uuid)
                    """
                ),
                {**self._bind(spec, item.get("experiment_code")), "version": version, "id": experiment_id},
            )
        return self.get(experiment_id)

    async def fill_inputs(self, experiment_id: str) -> Dict[str, Any]:
        """缺行情时让模型调用补数工具写入数据库。回测只读库。"""
        current = self.get(experiment_id)
        if not current.get("ok"):
            return current
        item = current["item"]
        params = item.get("parameters") or {}
        code = params.get("universe_code") or item.get("universe")
        start = str(item.get("train_start") or "2020-01-01")[:10]
        end = str(item.get("test_end") or item.get("train_end") or "2026-09-30")[:10]
        start_ymd = start.replace("-", "")
        end_ymd = end.replace("-", "")
        need_regime = bool(params.get("regimes"))
        need_breadth = params.get("min_up_ratio") is not None

        def verify() -> Dict[str, Any]:
            return self._input_coverage(code, start, end, need_regime, need_breadth)

        from server.data_gap_agent import fill_by_agent

        task = (
            f"回测「{params.get('universe_name') or code}」缺少历史数据。"
            "只调用一次 datahubco_data__fill_sector_history，"
            f"参数 ts_code={code}，start_date={start_ymd}，end_date={end_ymd}。"
            "不要改参数，不要调用别的工具，不要编造涨跌幅或上涨家数。"
        )
        filled = await fill_by_agent(task, verify, max_tokens=2500)
        if not verify().get("complete"):
            from tools.datahubco_toolkit import DatahubcoToolkit

            direct = DatahubcoToolkit().fill_sector_history(str(code), start_ymd, end_ymd)
            filled["direct"] = {
                "ok": direct.get("ok"),
                "error": direct.get("error"),
                "index_rows": direct.get("index_rows"),
                "breadth_rows": direct.get("breadth_rows"),
                "regime_rows": direct.get("regime_rows"),
            }
            filled["complete"] = bool(verify().get("complete"))
            if direct.get("note"):
                filled["assistant"] = direct["note"]
        coverage = verify()
        note = filled.get("assistant") or filled.get("note") or coverage.get("detail") or ""
        if filled.get("direct") and not filled["direct"].get("ok"):
            note = filled["direct"].get("error") or note
        params = dict(params)
        params["data_fill"] = {
            "complete": bool(coverage.get("complete")),
            "sector_days": coverage.get("sector_days"),
            "breadth_missing": coverage.get("breadth_missing"),
            "regime_days": coverage.get("regime_days"),
            "tools": filled.get("tools") or [],
            "note": str(note)[:500],
        }
        with self.engine.begin() as conn:
            conn.execute(
                text("UPDATE experiment SET parameters = CAST(:params AS jsonb), updated_at = NOW() WHERE id = CAST(:id AS uuid)"),
                {"id": experiment_id, "params": json.dumps(params, ensure_ascii=False)},
            )
        return {"ok": True, "item_id": experiment_id, **params["data_fill"]}

    def _input_coverage(self, code: str, start: str, end: str, need_regime: bool, need_breadth: bool) -> Dict[str, Any]:
        start_d = _as_date(start)
        end_d = _as_date(end)
        with self.engine.connect() as conn:
            sector_days = conn.execute(
                text(
                    """
                    SELECT COUNT(*) FROM sector_dc_index_daily
                    WHERE ts_code = :code AND pct_change IS NOT NULL
                      AND trade_date BETWEEN :a AND :b
                    """
                ),
                {"code": code, "a": start_d, "b": end_d},
            ).scalar()
            breadth_missing = conn.execute(
                text(
                    """
                    SELECT COUNT(*) FROM sector_dc_index_daily
                    WHERE ts_code = :code AND pct_change IS NOT NULL
                      AND trade_date BETWEEN :a AND :b
                      AND (up_num IS NULL OR down_num IS NULL)
                    """
                ),
                {"code": code, "a": start_d, "b": end_d},
            ).scalar()
            regime_days = conn.execute(
                text("SELECT COUNT(DISTINCT trade_date) FROM market_regime WHERE trade_date BETWEEN :a AND :b"),
                {"a": start_d, "b": end_d},
            ).scalar()
        sector_days = int(sector_days or 0)
        breadth_missing = int(breadth_missing or 0)
        regime_days = int(regime_days or 0)
        complete = sector_days >= 400
        if need_breadth:
            complete = complete and breadth_missing <= max(10, int(sector_days * 0.05))
        if need_regime:
            complete = complete and regime_days >= 200
        missing = []
        if sector_days < 400:
            missing.append(f"{code} 行情 {sector_days} 天")
        if need_breadth and breadth_missing > max(10, int(sector_days * 0.05)):
            missing.append(f"上涨家数缺 {breadth_missing} 天")
        if need_regime and regime_days < 200:
            missing.append(f"市场环境 {regime_days} 天")
        return {
            "complete": complete,
            "missing": missing,
            "detail": "；".join(missing) if missing else "回测要用的行情已经在库里",
            "sector_days": sector_days,
            "breadth_missing": breadth_missing,
            "regime_days": regime_days,
        }

    def _create_market_rule(self, source: Dict[str, Any]) -> Dict[str, Any]:
        """交易规则按整段时间和全部二级行业验证，不拆训练、验证、测试。"""
        self.ensure()
        raw = source.get("raw") or {}
        conditions = _loads(raw.get("conditions")) or []
        regimes = []
        min_up = None
        sector_name = ""
        for row in conditions if isinstance(conditions, list) else []:
            if not isinstance(row, dict):
                continue
            basis = str(row.get("basis") or "")
            if row.get("observable") == "market_regime" or basis == "market_regime":
                raw_value = row.get("value") or []
                if isinstance(raw_value, str):
                    raw_value = [part.strip() for part in raw_value.replace("，", ",").split(",") if part.strip()]
                regimes = [item for item in raw_value if item in ("牛", "熊", "震荡", "修复")]
            if row.get("observable") == "sector_name" or basis == "sector_name":
                sector_name = str(row.get("value") or "").strip()
            if row.get("observable") == "sector_up_ratio":
                min_up = _f(row.get("value"))
                if min_up is not None and min_up > 1:
                    min_up = min_up / 100
        if not regimes and min_up is None and not sector_name:
            return {"ok": False, "error": "这条规则没有能在行情上重放的条件"}
        name = str(raw.get("name") or source.get("title") or "交易规则")[:255]
        named = bool(sector_name)
        bits = []
        if regimes:
            bits.append("市场环境属于" + "、".join(regimes))
        if sector_name:
            bits.append(f"板块是{sector_name}")
        if min_up is not None:
            bits.append(f"上涨家数占比不低于 {round(min_up * 100)}%")
        if named:
            hypothesis = "当" + "，并且".join(bits) + "时，看该板块未来 5 日相对沪深300的超额，是否在整段时间上都成立。"
            design_reason = "不拆训练、验证和测试。指定板块用指数涨跌计算超额，不要求上涨家数。"
        else:
            hypothesis = "当" + "，并且".join(bits) + "时，看该板块未来 5 日相对沪深300的超额，是否在各个年份、各个东财二级行业上都成立。"
            design_reason = "不拆训练、验证和测试。用规则里的条件，在整段行情和全部东财二级行业上直接核对。"
        start = date(2020, 1, 1)
        end = date(2026, 9, 30)
        params = {
            "version": "1.0",
            "mode": "named_sector" if named else "full_market",
            "universe_code": sector_name or "ALL_L2",
            "universe_name": sector_name or "全部东财二级行业",
            "sector_name": sector_name or None,
            "benchmark": "000300.SH",
            "holding_days": [5],
            "primary_holding": 5,
            "regimes": regimes,
            "require_benchmark_down": False,
            "min_up_ratio": min_up,
            "rule_id": source.get("id"),
            "design_reason": design_reason,
        }
        code = self._next_code()
        with self.engine.begin() as conn:
            row = conn.execute(
                text(
                    """
                    INSERT INTO experiment (
                        experiment_code, strategy_name, hypothesis, hypothesis_source,
                        market, universe, timeframe, benchmark,
                        train_start, train_end, validation_start, validation_end, test_start, test_end,
                        parameters, status, version, test_unlocked
                    ) VALUES (
                        :code, :name, :hyp, 'RULE',
                        'CN_A_SHARE', :universe, 'DAILY', '000300.SH',
                        :a, :b, :a, :b, :a, :b,
                        CAST(:params AS jsonb), 'IDEA', '1.0', TRUE
                    ) RETURNING id
                    """
                ),
                {
                    "code": code,
                    "name": name,
                    "hyp": hypothesis,
                    "a": start,
                    "b": end,
                    "universe": (sector_name or "ALL_L2")[:40],
                    "params": json.dumps(params, ensure_ascii=False),
                },
            ).fetchone()
        return self.get(str(row[0]))

    async def fill_market(self, experiment_id: str) -> Dict[str, Any]:
        current = self.get(experiment_id)
        if not current.get("ok"):
            return current
        item = current["item"]
        if (item.get("parameters") or {}).get("mode") == "named_sector":
            return await self._fill_named(experiment_id, item)
        start = "2020-01-01"
        end = "2026-09-30"

        def verify() -> Dict[str, Any]:
            return self._market_coverage(start, end)

        from server.data_gap_agent import fill_by_agent

        filled = await fill_by_agent(
            "要在全部东财二级行业上验证规则，多数板块缺少 2020-01-01 至 2026-09-30 的行情。"
            "只调用一次 datahubco_data__fill_market_history，参数 start_date=20200101，end_date=20260930。"
            "不要逐个板块调用，不要编造涨跌幅或上涨家数。",
            verify,
            max_tokens=2500,
        )
        if not verify().get("complete"):
            from tools.datahubco_toolkit import DatahubcoToolkit

            direct = DatahubcoToolkit().fill_market_history("20200101", "20260930")
            filled["direct"] = {"ok": direct.get("ok"), "error": direct.get("error")}
            if direct.get("note"):
                filled["assistant"] = direct["note"]
        coverage = verify()
        item = self.get(experiment_id)["item"]
        params = dict(item.get("parameters") or {})
        params["data_fill"] = {
            "complete": bool(coverage.get("complete")),
            "ready_boards": coverage.get("ready_boards"),
            "boards": coverage.get("boards"),
            "regime_days": coverage.get("regime_days"),
            "tools": filled.get("tools") or [],
            "note": str(filled.get("assistant") or filled.get("note") or coverage.get("detail") or "")[:500],
        }
        with self.engine.begin() as conn:
            conn.execute(
                text("UPDATE experiment SET parameters = CAST(:params AS jsonb), updated_at = NOW() WHERE id = CAST(:id AS uuid)"),
                {"id": experiment_id, "params": json.dumps(params, ensure_ascii=False)},
            )
        return {"ok": True, **params["data_fill"]}

    async def _fill_named(self, experiment_id: str, item: Dict[str, Any]) -> Dict[str, Any]:
        """指定板块只核对这一块的指数和沪深300，不拉取全部二级行业。"""
        params = dict(item.get("parameters") or {})
        wanted = str(params.get("sector_name") or "").strip()
        start_d = date(2020, 1, 1)
        end_d = date(2026, 9, 30)
        note = "指定板块用库里的指数涨跌，不补上涨家数。"
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT ts_code, MAX(name) AS name, COUNT(*) FILTER (WHERE pct_change IS NOT NULL) AS days
                    FROM sector_dc_index_daily
                    WHERE industry_level = '东财二级行业' AND trade_date BETWEEN :a AND :b
                    GROUP BY ts_code
                    """
                ),
                {"a": start_d, "b": end_d},
            ).fetchall()
            regime_days = conn.execute(
                text("SELECT COUNT(DISTINCT trade_date) FROM market_regime WHERE trade_date BETWEEN :a AND :b"),
                {"a": start_d, "b": end_d},
            ).scalar()
        matched = [row for row in rows if _board_hit(row[1], wanted)]
        thin = [row for row in matched if int(row[2] or 0) < 200]
        if wanted and (not matched or thin):
            from tools.datahubco_toolkit import DatahubcoToolkit

            toolkit = DatahubcoToolkit()
            targets = thin or matched
            if not targets:
                with self.engine.connect() as conn:
                    names = conn.execute(
                        text(
                            """
                            SELECT DISTINCT ts_code, name FROM sector_dc_index_daily
                            WHERE industry_level = '东财二级行业' AND idx_type = '行业板块'
                            """
                        )
                    ).fetchall()
                targets = [row for row in names if _board_hit(row[1], wanted)]
            for row in targets[:3]:
                filled = toolkit.fill_sector_history(str(row[0]), "20200101", "20260930")
                note = str(filled.get("note") or filled.get("error") or note)[:500]
        ready = max((int(row[2] or 0) for row in matched), default=0)
        params["data_fill"] = {
            "complete": bool(matched) and ready >= 200 and int(regime_days or 0) >= 200,
            "ready_boards": 1 if ready >= 200 else 0,
            "boards": len(matched),
            "regime_days": int(regime_days or 0),
            "note": note,
        }
        with self.engine.begin() as conn:
            conn.execute(
                text("UPDATE experiment SET parameters = CAST(:params AS jsonb), updated_at = NOW() WHERE id = CAST(:id AS uuid)"),
                {"id": experiment_id, "params": json.dumps(params, ensure_ascii=False)},
            )
        return {"ok": True, **params["data_fill"]}

    def _market_coverage(self, start: str, end: str) -> Dict[str, Any]:
        start_d = _as_date(start)
        end_d = _as_date(end)
        with self.engine.connect() as conn:
            ready = conn.execute(
                text(
                    """
                    SELECT COUNT(*) FROM (
                        SELECT ts_code FROM sector_dc_index_daily
                        WHERE industry_level = '东财二级行业' AND pct_change IS NOT NULL
                          AND trade_date BETWEEN :a AND :b
                        GROUP BY ts_code HAVING COUNT(*) >= 200
                    ) s
                    """
                ),
                {"a": start_d, "b": end_d},
            ).scalar()
            total = conn.execute(
                text(
                    """
                    SELECT COUNT(DISTINCT ts_code) FROM sector_dc_index_daily
                    WHERE industry_level = '东财二级行业' AND idx_type = '行业板块'
                    """
                )
            ).scalar()
            regime_days = conn.execute(
                text("SELECT COUNT(DISTINCT trade_date) FROM market_regime WHERE trade_date BETWEEN :a AND :b"),
                {"a": start_d, "b": end_d},
            ).scalar()
        ready_n = int(ready or 0)
        total_n = int(total or 0)
        regime_n = int(regime_days or 0)
        complete = ready_n >= 80 and regime_n >= 200
        missing = []
        if ready_n < 80:
            missing.append(f"有整段行情的二级行业 {ready_n} 个")
        if regime_n < 200:
            missing.append(f"市场环境 {regime_n} 天")
        return {
            "complete": complete,
            "missing": missing,
            "detail": "；".join(missing) if missing else "全市场行情已经在库里",
            "ready_boards": ready_n,
            "boards": total_n,
            "regime_days": regime_n,
        }

    def run_market(self, experiment_id: str) -> Dict[str, Any]:
        self.ensure()
        current = self.get(experiment_id)
        if not current.get("ok"):
            return current
        item = current["item"]
        packed = self._evaluate_market(item)
        result = {
            "full": packed["metrics"],
            "by_year": packed["by_year"],
            "by_regime": packed["by_regime"],
            "consistency": packed["consistency"],
            "warnings": packed["warnings"],
            "coverage": packed["coverage"],
        }
        status, failure, conclusion = self._judge_market(packed)
        headline = packed["metrics"]
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE experiment SET
                        result = CAST(:result AS jsonb),
                        status = :status, conclusion = :conclusion, failure_reason = :failure,
                        sample_size = :n, win_rate = :win, expectancy = :exp,
                        profit_factor = :pf, max_drawdown = :dd,
                        test_unlocked = TRUE, updated_at = NOW()
                    WHERE id = CAST(:id AS uuid)
                    """
                ),
                {
                    "id": experiment_id,
                    "result": json.dumps(result, ensure_ascii=False, default=str),
                    "status": status,
                    "conclusion": conclusion,
                    "failure": failure,
                    "n": headline.get("sample_size"),
                    "win": headline.get("win_rate"),
                    "exp": headline.get("expectancy"),
                    "pf": headline.get("profit_factor"),
                    "dd": headline.get("max_drawdown"),
                },
            )
        out = self.get(experiment_id)
        out["message"] = conclusion
        return out

    def _evaluate_market(self, item: Dict[str, Any]) -> Dict[str, Any]:
        params = item.get("parameters") or {}
        holding = int(params.get("primary_holding") or 5)
        regimes = set(params.get("regimes") or [])
        min_up = _f(params.get("min_up_ratio"))
        start = _as_date(item.get("train_start"))
        end = _as_date(item.get("train_end"))
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT ts_code, name, trade_date, pct_change, up_num, down_num
                    FROM sector_dc_index_daily
                    WHERE industry_level = '东财二级行业' AND pct_change IS NOT NULL
                    ORDER BY ts_code, trade_date
                    """
                )
            ).fetchall()
            bench_rows = conn.execute(
                text("SELECT trade_date, pct_chg FROM index_daily WHERE ts_code = '000300.SH' ORDER BY trade_date")
            ).fetchall()
            regime_rows = conn.execute(
                text("SELECT DISTINCT ON (trade_date) trade_date, regime_label FROM market_regime ORDER BY trade_date, created_at")
            ).fetchall()
        bench = {_as_date(row[0]): _f(row[1]) for row in bench_rows if _f(row[1]) is not None}
        regime_map = {_as_date(row[0]): str(row[1]) for row in regime_rows if _as_date(row[0])}
        series: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            day = _as_date(row[2])
            pct = _f(row[3])
            if day is None or pct is None:
                continue
            bucket = series.setdefault(str(row[0]), {"name": row[1] or row[0], "pct": {}, "breadth": {}})
            bucket["pct"][day] = pct
            up = _f(row[4])
            down = _f(row[5])
            if up is not None and down is not None and up + down > 0:
                bucket["breadth"][day] = up / (up + down)
        wanted = str(params.get("sector_name") or "").strip()
        if wanted:
            series = {code: bucket for code, bucket in series.items() if _board_hit(bucket.get("name"), wanted)}
        excesses: List[float] = []
        by_year: Dict[str, List[float]] = {}
        by_regime: Dict[str, List[float]] = {}
        by_sector: Dict[str, List[float]] = {}
        for bucket in series.values():
            days = sorted(set(bucket["pct"]) & set(bench))
            for index, day in enumerate(days):
                if (start and day < start) or (end and day > end):
                    continue
                if regimes and regime_map.get(day) not in regimes:
                    continue
                if min_up is not None:
                    ratio = bucket["breadth"].get(day)
                    if ratio is None or ratio < min_up:
                        continue
                future = days[index + 1:index + 1 + holding]
                if len(future) < holding or any(item not in bucket["pct"] or item not in bench for item in future):
                    continue
                value = sum(bucket["pct"][item] - bench[item] for item in future)
                excesses.append(value)
                by_year.setdefault(str(day.year), []).append(value)
                by_regime.setdefault(regime_map.get(day) or "未记录", []).append(value)
                by_sector.setdefault(str(bucket["name"]), []).append(value)
        year_metrics = {key: _metrics(values) for key, values in sorted(by_year.items())}
        sector_metrics = {key: _metrics(values) for key, values in by_sector.items()}
        year_ready = [block for block in year_metrics.values() if int(block.get("sample_size") or 0) >= 30]
        sector_ready = [block for block in sector_metrics.values() if int(block.get("sample_size") or 0) >= 20]
        year_pos = sum(1 for block in year_ready if (_f(block.get("expectancy")) or 0) > 0)
        sector_pos = sum(1 for block in sector_ready if (_f(block.get("expectancy")) or 0) > 0)
        total_n = len(excesses)
        biggest_year = max((int(block.get("sample_size") or 0) for block in year_metrics.values()), default=0)
        ranked = sorted(sector_metrics.items(), key=lambda item: _f(item[1].get("expectancy")) or 0)
        shown = {}
        for name, block in list(ranked[:8]) + list(ranked[-8:]):
            if int(block.get("sample_size") or 0) >= 20:
                shown[name] = block
        consistency = {
            "sample_size": total_n,
            "years": len(year_ready),
            "years_positive": year_pos,
            "sectors": len(sector_ready),
            "sectors_positive": sector_pos,
            "sectors_triggered": len(by_sector),
            "largest_year_share": round(biggest_year / total_n, 4) if total_n else None,
        }
        warnings = []
        if wanted and not series:
            warnings.append(f"没有对上名称「{wanted}」的东财二级行业。")
        elif len(series) < 30 and not wanted:
            warnings.append(f"只有 {len(series)} 个二级行业有涨跌幅，还不能代表全市场。")
        broad = sum(1 for bucket in series.values() if len(bucket["breadth"]) >= 200)
        if min_up is not None and broad < 10:
            warnings.append(f"只有 {broad} 个板块有较长的上涨家数。其余板块在早些年份无法按这条规则触发。")
        consistency["breadth_boards"] = broad
        consistency["named_sector"] = wanted or None
        return {
            "metrics": _metrics(excesses),
            "by_year": year_metrics,
            "by_regime": {key: _metrics(values) for key, values in by_regime.items()},
            "by_sector": shown,
            "consistency": consistency,
            "warnings": warnings,
            "named_sector": wanted or None,
            "coverage": {"boards": len(series), "regime_days": len(regime_map), "benchmark": "000300.SH"},
        }

    def _judge_market(self, packed: Dict[str, Any]) -> tuple:
        metrics = packed["metrics"]
        fit = packed["consistency"]
        sample = int(metrics.get("sample_size") or 0)
        expectancy = _f(metrics.get("expectancy"))
        named = bool(packed.get("named_sector"))
        if sample < 100:
            scope = "这一板块" if named else "整段行情"
            return (
                "NEED_MORE_DATA",
                "NOT_ENOUGH_SAMPLES",
                f"{scope}里只有 {sample} 个触发，少于 100。还不能判断这条规律是否成立。",
            )
        sectors = int(fit.get("sectors") or 0)
        if not named and sectors < 10:
            return (
                "NEED_MORE_DATA",
                "NOT_ENOUGH_SAMPLES",
                "有涨跌的板块已经铺开，但上涨家数的长历史只够少数板块使用。"
                f"样本不少于 20 的板块只有 {sectors} 个，触发又有 {round((fit.get('largest_year_share') or 0) * 100)}% 集中在同一年。"
                "所以现在还不能判断这条规律在整个市场、整个时间段上是否成立。",
            )
        reasons = []
        if expectancy is None or expectancy <= 0:
            reasons.append("全样本平均超额不为正")
        years = int(fit.get("years") or 0)
        if years < 3 or int(fit.get("years_positive") or 0) < years * 0.6:
            reasons.append(f"样本不少于 30 的年份有 {years} 个，其中平均超额为正的有 {fit.get('years_positive') or 0} 个")
        sectors = int(fit.get("sectors") or 0)
        if not named and (sectors < 10 or int(fit.get("sectors_positive") or 0) < sectors * 0.6):
            reasons.append(f"样本不少于 20 的板块有 {sectors} 个，其中平均超额为正的有 {fit.get('sectors_positive') or 0} 个")
        share = _f(fit.get("largest_year_share"))
        if share is not None and share > 0.5:
            reasons.append("超过一半的触发集中在同一年")
        if named:
            if reasons:
                return ("REJECTED", "NOT_BROAD", "在指定板块的整段时间上没有成立：" + "；".join(reasons) + "。")
            return (
                "FIT",
                None,
                "指定板块在整段时间上的平均超额为正，而且多数年份为正，没有集中在单独一年。这只核对指数超额，不会写成生效规则。",
            )
        if reasons:
            return ("REJECTED", "NOT_BROAD", "这条规律没有同时在时间和板块上成立：" + "；".join(reasons) + "。")
        return (
            "FIT",
            None,
            "整段样本的平均超额为正，而且在多数年份、多数板块上都为正，没有集中在单独一年。这条规律在当前数据上比较符合。仍只是核对，不会写成生效规则。",
        )

    def run(self, experiment_id: str, phase: str) -> Dict[str, Any]:
        self.ensure()
        phase = (phase or "").lower()
        if phase not in ("train", "validation", "test"):
            return {"ok": False, "error": "只能运行训练集、验证集或测试集"}
        current = self.get(experiment_id)
        if not current.get("ok"):
            return current
        item = current["item"]
        if (item.get("parameters") or {}).get("mode") in ("full_market", "named_sector"):
            return self.run_market(experiment_id)
        params = item.get("parameters") or {}
        if phase == "validation" and not (item.get("result") or {}).get("train"):
            return {"ok": False, "error": "先跑训练集。验证集不用来寻找参数。"}
        if phase == "test":
            if not item.get("test_unlocked") and not (item.get("result") or {}).get("validation"):
                return {"ok": False, "error": "测试集还锁着。先完成当前版本的验证，再一次性打开。"}
            if (item.get("result") or {}).get("test"):
                return {"ok": True, "item": item, "message": "这个版本的测试集已经看过一次，没有重新计算。"}
        window = {
            "train": (item.get("train_start"), item.get("train_end")),
            "validation": (item.get("validation_start"), item.get("validation_end")),
            "test": (item.get("test_start"), item.get("test_end")),
        }[phase]
        packed = self._evaluate(item, _as_date(window[0]), _as_date(window[1]))
        result = dict(item.get("result") or {})
        result[phase] = packed["metrics"]
        result["by_year"] = packed["by_year"]
        result["by_regime"] = packed["by_regime"]
        result["coverage"] = packed["coverage"]
        result["warnings"] = packed["warnings"]
        if phase == "train":
            result.pop("validation", None)
            result.pop("test", None)
        if phase == "validation":
            result.pop("test", None)
        overfit = self._overfit(result.get("train"), result.get("validation"), result.get("test"), item.get("version"))
        status, failure, conclusion = self._judge(result, phase == "test" or bool(result.get("test")))
        headline = result.get("test") or result.get("validation") or result.get("train") or {}
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE experiment SET
                        result = CAST(:result AS jsonb),
                        overfit_check = CAST(:overfit AS jsonb),
                        status = :status, conclusion = :conclusion, failure_reason = :failure,
                        sample_size = :n, win_rate = :win, expectancy = :exp,
                        profit_factor = :pf, max_drawdown = :dd,
                        test_unlocked = :unlocked, updated_at = NOW()
                    WHERE id = CAST(:id AS uuid)
                    """
                ),
                {
                    "id": experiment_id,
                    "result": json.dumps(result, ensure_ascii=False),
                    "overfit": json.dumps(overfit, ensure_ascii=False),
                    "status": status,
                    "conclusion": conclusion,
                    "failure": failure,
                    "n": headline.get("sample_size"),
                    "win": headline.get("win_rate"),
                    "exp": headline.get("expectancy"),
                    "pf": headline.get("profit_factor"),
                    "dd": headline.get("max_drawdown"),
                    "unlocked": phase == "test",
                },
            )
            conn.execute(
                text(
                    """
                    INSERT INTO experiment_run (
                        experiment_id, version, phase, parameters, data_snapshot, result, warnings
                    ) VALUES (
                        CAST(:id AS uuid), :version, :phase, CAST(:params AS jsonb),
                        CAST(:snap AS jsonb), CAST(:result AS jsonb), CAST(:warnings AS jsonb)
                    )
                    """
                ),
                {
                    "id": experiment_id,
                    "version": item.get("version") or "1.0",
                    "phase": phase.upper(),
                    "params": json.dumps(params, ensure_ascii=False),
                    "snap": json.dumps(packed["snapshot"], ensure_ascii=False),
                    "result": json.dumps(packed["metrics"], ensure_ascii=False),
                    "warnings": json.dumps(packed["warnings"], ensure_ascii=False),
                },
            )
        return self.get(experiment_id)

    def to_candidate_rule(self, experiment_id: str) -> Dict[str, Any]:
        self.ensure()
        current = self.get(experiment_id)
        if not current.get("ok"):
            return current
        item = current["item"]
        if item.get("status") != "VALIDATED":
            return {"ok": False, "error": "只有已验证的实验可以生成候选规则。证据不足或已拒绝的实验先留在实验室。"}
        params = item.get("parameters") or {}
        regimes = params.get("regimes") or []
        holding = int(params.get("primary_holding") or 5)
        conditions = []
        if regimes:
            conditions.append({"observable": "market_regime", "op": "in", "value": regimes})
        conditions.append({"observable": "sector_excess_nd", "op": ">", "value": 0, "n": holding, "benchmark": item.get("benchmark") or "000300.SH"})
        from server.rule_service import rule_service

        created = rule_service.create({
            "name": f"{item.get('strategy_name') or '实验'} 候选",
            "category": "sector",
            "rule_type": "FILTER",
            "scope": "SECTOR",
            "lesson_ids": [item["source_experience_id"]] if item.get("source_experience_id") else [],
            "conditions": conditions,
            "action": {"type": "mark_priority", "note": f"来自 {item.get('experiment_code')} {item.get('version')}，先观察 {params.get('universe_name') or item.get('universe') or '该板块'}，不是买入。"},
            "exceptions": "实验没有覆盖的市场环境，或测试集衰减之后，暂停这条规则。",
        })
        if not created.get("ok"):
            return created
        rule = created["item"]
        if rule.get("status") == "TESTING":
            with self.engine.begin() as conn:
                conn.execute(
                    text("UPDATE rule SET status = 'DRAFT', updated_at = NOW() WHERE id = CAST(:id AS uuid)"),
                    {"id": rule["id"]},
                )
            rule = rule_service.get(rule["id"])["item"]
        return {"ok": True, "rule": rule, "message": "已生成草稿规则。它还没有生效，需要在交易规则里人工确认。"}

    def get(self, experiment_id: str) -> Dict[str, Any]:
        self.ensure()
        with self.engine.connect() as conn:
            row = conn.execute(
                text("SELECT * FROM experiment WHERE id = CAST(:id AS uuid)"),
                {"id": experiment_id},
            ).mappings().fetchone()
            if not row:
                return {"ok": False, "error": "这个实验不存在"}
            runs = conn.execute(
                text(
                    """
                    SELECT version, phase, run_at, warnings
                    FROM experiment_run
                    WHERE experiment_id = CAST(:id AS uuid)
                    ORDER BY run_at DESC
                    LIMIT 12
                    """
                ),
                {"id": experiment_id},
            ).mappings().fetchall()
        item = self._pack(dict(row))
        item["runs"] = []
        for run in runs:
            packed = {}
            for key, value in dict(run).items():
                packed[key] = value.isoformat() if hasattr(value, "isoformat") else _loads(value)
            item["runs"].append(packed)
        return {"ok": True, "item": item}

    def _load_source(self, kind: str, source_id: str) -> Dict[str, Any]:
        with self.engine.connect() as conn:
            if kind == "rule":
                row = conn.execute(
                    text("SELECT id, rule_code, name, description, conditions, action, exceptions, status FROM rule WHERE id = CAST(:id AS uuid)"),
                    {"id": source_id},
                ).mappings().fetchone()
                if not row:
                    return {"ok": False, "error": "这条交易规则不存在"}
                data = dict(row)
                text_body = "\n".join(
                    part for part in (
                        f"规则 {data.get('rule_code') or ''} {data.get('name') or ''}",
                        str(data.get("description") or ""),
                        json.dumps(_loads(data.get("conditions")), ensure_ascii=False),
                        json.dumps(_loads(data.get("action")), ensure_ascii=False),
                        str(_loads(data.get("exceptions")) or ""),
                    ) if part
                )
                title = data.get("name") or "交易规则"
            else:
                row = conn.execute(
                    text(
                        """
                        SELECT id, title, summary, claims, hypotheses, market_scope, applicability, content
                        FROM external_knowledge WHERE id = CAST(:id AS uuid)
                        """
                    ),
                    {"id": source_id},
                ).mappings().fetchone()
                if not row:
                    return {"ok": False, "error": "这条外部知识不存在"}
                data = dict(row)
                text_body = "\n".join(
                    part for part in (
                        str(data.get("title") or ""),
                        str(data.get("summary") or ""),
                        json.dumps(_loads(data.get("claims")), ensure_ascii=False)[:2000],
                        json.dumps(_loads(data.get("hypotheses")), ensure_ascii=False)[:1000],
                        str(data.get("content") or "")[:3000],
                    ) if part
                )
                title = data.get("title") or "外部知识"
            names = conn.execute(
                text(
                    """
                    SELECT DISTINCT name FROM sector_dc_index_daily
                    WHERE industry_level = '东财二级行业' AND idx_type = '行业板块' AND name IS NOT NULL
                    ORDER BY name
                    """
                )
            ).fetchall()
        return {
            "ok": True,
            "kind": kind,
            "id": str(data.get("id") or ""),
            "title": title,
            "text": text_body[:6000],
            "raw": data,
            "boards": [row[0] for row in names if row[0]],
        }

    async def _design_backtest(self, source: Dict[str, Any]) -> Dict[str, Any]:
        from server.chat_service import ChatRequest, chat_service

        resp = await chat_service.chat(ChatRequest(
            query=(
                "材料如下。请决定如何回测，不要写收益数字。\n"
                f"可选板块：{'、'.join(source['boards'][:160])}\n\n"
                + source["text"]
            ),
            model="deepseek-flash",
            provider="deepseek",
            temperature=0.1,
            max_tokens=2500,
            system_prompt=(
                "只输出一个 JSON，不要解释，不要写收益率。"
                "板块必须来自可选列表。环境只能是牛、熊、震荡、修复。持有只能是 1、3、5、10。"
                "材料要求上涨家数占比时，把 80% 写成 min_up_ratio 0.8；没有这项就写 null。"
                "对不上板块时输出 {\"ok\": false, \"error\": \"原因\"}。"
                "否则输出 {\"ok\": true, \"strategy_name\": \"\", \"hypothesis\": \"\", \"universe_name\": \"\", "
                "\"regimes\": [], \"require_benchmark_down\": false, \"holding_days\": [5], "
                "\"min_up_ratio\": null, \"reason\": \"\"}"
            ),
        ))
        raw = resp.content or ""
        start = raw.find("{")
        end = raw.rfind("}")
        try:
            payload = json.loads(raw[start:end + 1]) if start >= 0 and end > start else {}
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict) or not payload.get("ok"):
            return {"ok": False, "error": str(payload.get("error") or "模型没有给出可执行的回测设计")}
        name = str(payload.get("universe_name") or "").strip()
        if not name or not self._find_board(name):
            return {"ok": False, "error": f"模型选的板块「{name or '空'}」不在东财二级行业里，这次没有回测"}
        regimes = [item for item in (payload.get("regimes") or []) if item in ("牛", "熊", "震荡", "修复")]
        holdings = []
        for item in payload.get("holding_days") or [5]:
            try:
                day = int(item)
            except (TypeError, ValueError):
                continue
            if day in (1, 3, 5, 10) and day not in holdings:
                holdings.append(day)
        hypothesis = str(payload.get("hypothesis") or "").strip()
        strategy_name = str(payload.get("strategy_name") or source["title"]).strip()
        if not hypothesis or not strategy_name:
            return {"ok": False, "error": "模型没有写清假设，这次没有回测"}
        ratio = _f(payload.get("min_up_ratio"))
        if ratio is not None and ratio > 1:
            ratio = ratio / 100
        if ratio is not None and not 0 < ratio <= 1:
            ratio = None
        return {
            "ok": True,
            "strategy_name": strategy_name[:255],
            "hypothesis": hypothesis,
            "universe_name": name,
            "regimes": regimes,
            "require_benchmark_down": bool(payload.get("require_benchmark_down")),
            "holding_days": holdings or [5],
            "min_up_ratio": ratio,
            "reason": str(payload.get("reason") or "")[:500],
        }

    def _parse(self, body: Dict[str, Any], version: str) -> Dict[str, Any]:
        name = str(body.get("strategy_name") or body.get("name") or "").strip()
        hypothesis = str(body.get("hypothesis") or "").strip()
        if not name or not hypothesis:
            return {"ok": False, "error": "先写下实验名称和假设"}
        source = str(body.get("hypothesis_source") or "MY_OBSERVATION").upper()
        if source not in {item[0] for item in SOURCES}:
            return {"ok": False, "error": "来源类型无效"}
        universe_name = str(body.get("universe_name") or body.get("universe") or "").strip()
        board = self._find_board(universe_name)
        if not board:
            return {"ok": False, "error": f"库里没有找到「{universe_name}」对应的东财二级行业"}
        holdings = body.get("holding_days") or [5]
        clean_holdings = []
        for item in holdings:
            try:
                day = int(item)
            except (TypeError, ValueError):
                continue
            if day in (1, 3, 5, 10) and day not in clean_holdings:
                clean_holdings.append(day)
        if not clean_holdings:
            clean_holdings = [5]
        regimes = [str(item) for item in (body.get("regimes") or []) if str(item).strip()]
        spec = {
            "name": name[:255],
            "hypothesis": hypothesis,
            "source": source,
            "knowledge_id": str(body.get("source_knowledge_id") or "").strip() or None,
            "experience_id": str(body.get("source_experience_id") or "").strip() or None,
            "market": "CN_A_SHARE",
            "universe": board["ts_code"],
            "benchmark": "000300.SH",
            "train": (_as_date(body.get("train_start") or "2020-01-01"), _as_date(body.get("train_end") or "2024-12-31")),
            "validation": (_as_date(body.get("validation_start") or "2025-01-01"), _as_date(body.get("validation_end") or "2025-12-31")),
            "test": (_as_date(body.get("test_start") or "2026-01-01"), _as_date(body.get("test_end") or "2026-09-30")),
            "parameters": {
                "version": version,
                "universe_code": board["ts_code"],
                "universe_name": board["name"],
                "benchmark": "000300.SH",
                "holding_days": clean_holdings,
                "primary_holding": clean_holdings[0],
                "regimes": regimes,
                "require_benchmark_down": bool(body.get("require_benchmark_down")),
                "min_up_ratio": _f(body.get("min_up_ratio")),
                "design_reason": str(body.get("design_reason") or "")[:500],
                "rule_id": str(body.get("rule_id") or "") or None,
            },
        }
        return {"ok": True, "spec": spec}

    def _bind(self, spec: Dict[str, Any], code: Optional[str]) -> Dict[str, Any]:
        return {
            "code": code,
            "name": spec["name"],
            "hyp": spec["hypothesis"],
            "source": spec["source"],
            "kid": spec["knowledge_id"],
            "eid": spec["experience_id"],
            "market": spec["market"],
            "universe": spec["universe"],
            "benchmark": spec["benchmark"],
            "tr0": spec["train"][0],
            "tr1": spec["train"][1],
            "va0": spec["validation"][0],
            "va1": spec["validation"][1],
            "te0": spec["test"][0],
            "te1": spec["test"][1],
            "params": json.dumps(spec["parameters"], ensure_ascii=False),
        }

    def _find_board(self, name: str) -> Optional[Dict[str, str]]:
        key = (name or "").strip()
        if not key:
            return None
        code = key.upper()
        with self.engine.connect() as conn:
            if code.startswith("BK"):
                if not code.endswith(".DC"):
                    code = f"{code}.DC"
                row = conn.execute(
                    text(
                        """
                        SELECT ts_code, name FROM sector_dc_index_daily
                        WHERE ts_code = :code AND industry_level = '东财二级行业'
                        ORDER BY trade_date DESC LIMIT 1
                        """
                    ),
                    {"code": code},
                ).fetchone()
                if row:
                    return {"ts_code": row[0], "name": row[1]}
            rows = conn.execute(
                text(
                    """
                    SELECT DISTINCT ON (ts_code) ts_code, name
                    FROM sector_dc_index_daily
                    WHERE industry_level = '东财二级行业' AND idx_type = '行业板块'
                      AND (name = :name OR name ILIKE :like)
                    ORDER BY ts_code, trade_date DESC
                    """
                ),
                {"name": key, "like": f"%{key}%"},
            ).fetchall()
        boards = [{"ts_code": row[0], "name": row[1]} for row in rows]
        boards.sort(key=lambda item: (
            0 if item["name"] == key or str(item["name"]).startswith(key) else 1,
            1 if str(item["name"]).startswith("非") else 0,
            len(item["name"] or ""),
        ))
        return boards[0] if boards else None

    def _evaluate(self, item: Dict[str, Any], start: Optional[date], end: Optional[date]) -> Dict[str, Any]:
        params = item.get("parameters") or {}
        code = params.get("universe_code") or item.get("universe")
        holding = int(params.get("primary_holding") or 5)
        regimes = set(params.get("regimes") or [])
        need_down = bool(params.get("require_benchmark_down"))
        min_up = _f(params.get("min_up_ratio"))
        with self.engine.connect() as conn:
            sector_rows = conn.execute(
                text(
                    """
                    SELECT trade_date, pct_change, up_num, down_num FROM sector_dc_index_daily
                    WHERE ts_code = :code AND industry_level = '东财二级行业'
                    ORDER BY trade_date
                    """
                ),
                {"code": code},
            ).fetchall()
            bench_rows = conn.execute(
                text("SELECT trade_date, pct_chg FROM index_daily WHERE ts_code = '000300.SH' ORDER BY trade_date")
            ).fetchall()
            regime_rows = conn.execute(
                text("SELECT trade_date, regime_label FROM market_regime ORDER BY trade_date, created_at")
            ).fetchall()
        sector = {}
        breadth = {}
        missing_breadth = 0
        for row in sector_rows:
            day = _as_date(row[0])
            pct = _f(row[1])
            if day is None or pct is None:
                continue
            sector[day] = pct
            up = _f(row[2])
            down = _f(row[3])
            if up is None or down is None or up + down <= 0:
                missing_breadth += 1
                continue
            breadth[day] = up / (up + down)
        bench = {_as_date(row[0]): _f(row[1]) for row in bench_rows if _f(row[1]) is not None}
        regime_map = {_as_date(row[0]): str(row[1]) for row in regime_rows}
        days = sorted(set(sector) & set(bench))
        excesses = []
        tagged = []
        for index, day in enumerate(days):
            if start and day < start or end and day > end:
                continue
            if regimes and regime_map.get(day) not in regimes:
                continue
            if need_down and not (bench[day] < 0):
                continue
            if min_up is not None:
                ratio = breadth.get(day)
                if ratio is None or ratio < min_up:
                    continue
            future = days[index + 1:index + 1 + holding]
            if len(future) < holding:
                continue
            if any(item not in sector or item not in bench for item in future):
                continue
            value = sum(sector[item] - bench[item] for item in future)
            excesses.append(value)
            tagged.append((day, value, regime_map.get(day) or "未记录"))
        by_year: Dict[str, List[float]] = {}
        by_regime: Dict[str, List[float]] = {}
        for day, value, label in tagged:
            by_year.setdefault(str(day.year), []).append(value)
            by_regime.setdefault(label, []).append(value)
        warnings = []
        if regimes and len(regime_map) < 5:
            warnings.append(f"市场环境只有 {len(regime_map)} 天，多数日期不会触发。")
        if len(sector) < 30:
            warnings.append(f"{params.get('universe_name') or code} 在库里只有 {len(sector)} 个交易日，训练和验证窗口可能没有样本。")
        if not regimes and not need_down and min_up is None:
            warnings.append("没有填写市场条件，比较的是所有有数据的日期，不是某种特定环境。")
        if min_up is not None and missing_breadth:
            warnings.append(f"有 {missing_breadth} 天没有上涨家数，这些天没有当成触发。")
        return {
            "metrics": _metrics(excesses),
            "by_year": {key: _metrics(values) for key, values in sorted(by_year.items())},
            "by_regime": {key: _metrics(values) for key, values in by_regime.items()},
            "coverage": {"sector_days": len(sector), "regime_days": len(regime_map), "benchmark": "000300.SH"},
            "warnings": warnings,
            "snapshot": {
                "sector_last_date": max(sector).isoformat() if sector else None,
                "benchmark_last_date": max(bench).isoformat() if bench else None,
                "sector_days": len(sector),
                "regime_days": len(regime_map),
                "adjustment": "库内日线涨跌幅",
                "universe": code,
            },
        }

    def _overfit(self, train: Optional[Dict[str, Any]], validation: Optional[Dict[str, Any]], test: Optional[Dict[str, Any]], version: str) -> Dict[str, Any]:
        train_score = _f((train or {}).get("expectancy"))
        validation_score = _f((validation or {}).get("expectancy"))
        test_score = _f((test or {}).get("expectancy"))
        gap_tv = None if train_score is None or validation_score is None else round(train_score - validation_score, 4)
        gap_vt = None if validation_score is None or test_score is None else round(validation_score - test_score, 4)
        warnings = []
        risk = "UNKNOWN"
        if gap_tv is not None and gap_tv > GATES["max_train_validation_gap"]:
            risk = "HIGH"
            warnings.append("训练集表现明显高于验证集")
        elif gap_tv is not None and gap_tv > 0:
            risk = "MEDIUM"
            warnings.append("验证集弱于训练集")
        elif gap_tv is not None:
            risk = "LOW"
        try:
            variations = int(str(version or "1.0").split(".")[1]) + 1
        except (ValueError, IndexError):
            variations = 1
        return {
            "parameter_variations": variations,
            "best_train_score": train_score,
            "validation_score": validation_score,
            "test_score": test_score,
            "train_validation_gap": gap_tv,
            "validation_test_gap": gap_vt,
            "stability": "UNKNOWN" if gap_tv is None else ("GOOD" if risk == "LOW" else "WEAK"),
            "overfit_risk": risk,
            "warnings": warnings,
        }

    def _judge(self, result: Dict[str, Any], test_opened: bool) -> tuple:
        train = result.get("train") or {}
        validation = result.get("validation")
        test = result.get("test")
        train_n = int(train.get("sample_size") or 0)
        if train_n < GATES["min_sample"]:
            return (
                "NEED_MORE_DATA",
                "NOT_ENOUGH_SAMPLES",
                f"训练集只有 {train_n} 个已走出持有期的触发，少于 {GATES['min_sample']}。当前证据不足，不形成规则。",
            )
        if validation is None:
            return ("BACKTESTING", None, "训练集已跑完。请用验证集检查，不要拿测试集调参。")
        val_n = int(validation.get("sample_size") or 0)
        val_exp = _f(validation.get("expectancy"))
        if val_n < GATES["min_sample"]:
            return ("NEED_MORE_DATA", "NOT_ENOUGH_SAMPLES", f"验证集只有 {val_n} 个样本。证据还不够。")
        if val_exp is None or val_exp <= GATES["min_expectancy"]:
            return ("REJECTED", "OUT_OF_SAMPLE_FAILURE", "验证集平均超额不为正。这个假设先拒绝，避免在同一段历史上继续改到好看。")
        if not test_opened or test is None:
            return ("VALIDATING", None, "验证集仍为正。测试集保持锁定，确认不再改参数后才能打开一次。")
        test_exp = _f(test.get("expectancy"))
        test_n = int(test.get("sample_size") or 0)
        if test_n < GATES["min_sample"]:
            return ("NEED_MORE_DATA", "NOT_ENOUGH_SAMPLES", f"测试集只有 {test_n} 个样本。先不要生成规则。")
        if test_exp is None or test_exp <= 0:
            return ("REJECTED", "OUT_OF_SAMPLE_FAILURE", "测试集平均超额不为正。训练集和验证集不能代替这最后一次检验。")
        overfit = self._overfit(train, validation, test, "1.0")
        if overfit["overfit_risk"] == "HIGH":
            return ("REJECTED", "OVERFITTING", "训练集明显高于验证集，先不要把这个结果写成规则。")
        return ("VALIDATED", None, "训练、验证和测试的平均超额都为正，且样本达到配置门槛。可以生成草稿规则，不会直接生效。模拟和实盘还没有做。")

    def _next_code(self) -> str:
        with self.engine.connect() as conn:
            rows = conn.execute(text("SELECT experiment_code FROM experiment WHERE experiment_code LIKE 'EXP-%'")).fetchall()
        best = 0
        for row in rows:
            tail = str(row[0] or "").replace("EXP-", "")
            if tail.isdigit():
                best = max(best, int(tail))
        return f"EXP-{best + 1:03d}"

    def _pack(self, row: Dict[str, Any]) -> Dict[str, Any]:
        item: Dict[str, Any] = {}
        json_keys = {"parameters", "entry_rules", "exit_rules", "risk_rules", "result", "overfit_check"}
        for key, value in row.items():
            if hasattr(value, "isoformat"):
                item[key] = value.isoformat()
            elif hasattr(value, "hex") and hasattr(value, "int"):
                item[key] = str(value)
            else:
                item[key] = _num(_loads(value) if key in json_keys else value)
        item["status_label"] = dict(STATUSES).get(item.get("status") or "", item.get("status"))
        item["source_label"] = dict(SOURCES).get(item.get("hypothesis_source") or "", item.get("hypothesis_source"))
        item["failure_label"] = FAILURES.get(item.get("failure_reason") or "", "")
        params = item.get("parameters") or {}
        item["universe_name"] = params.get("universe_name") or item.get("universe")
        item["test_locked"] = not bool(item.get("test_unlocked")) and not (item.get("result") or {}).get("test")
        return item


experiment_service = ExperimentService()

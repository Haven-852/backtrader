"""交易规则：条件、动作、例外，以及能不能被历史重放。

经验写「发现了什么」。规则写「条件成立时做什么」。
结构检查通过只进入测试中。样本外结果达到标准之后，才允许标成生效中。
统计数字只来自重放，不拿一笔交易的盈亏去填。
"""

from __future__ import annotations

import json
import statistics
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import text

from data_layer.db_manager import storage_manager

LEVEL = "东财二级行业"
FORWARD_DAYS = 5
OUT_SAMPLE_MIN = 20
SPLIT = 0.7

CATEGORIES = (
    ("market", "市场规则"),
    ("sector", "板块规则"),
    ("stock", "选股规则"),
    ("entry", "买入规则"),
    ("exit", "卖出规则"),
    ("position", "仓位规则"),
    ("risk", "风险规则"),
    ("execution", "执行规则"),
)
CATEGORY_IDS = {item[0] for item in CATEGORIES}

STATUSES = (
    ("DRAFT", "草稿"),
    ("TESTING", "测试中"),
    ("PRODUCTION", "生效中"),
    ("PAUSED", "已暂停"),
)

RULE_TYPES = (
    ("FILTER", "筛选"),
    ("TRIGGER", "触发"),
    ("SIZE", "仓位"),
    ("VETO", "否决"),
)
SCOPES = (
    ("MARKET", "市场"),
    ("SECTOR", "板块"),
    ("STOCK", "个股"),
    ("PORTFOLIO", "组合"),
)

# replay=True 的条件，决策当时能从库里重算，不用未来收益。
OBSERVABLES = {
    "market_regime": {
        "label": "市场环境",
        "ops": ["in"],
        "replay": True,
        "choices": ["牛", "熊", "震荡", "修复"],
        "hint": "用市场记忆里的标签。牛是上升，震荡就是震荡，不会自动当成震荡偏强。",
    },
    "sector_excess_nd": {
        "label": "板块近N日相对沪深300超额（百分点）",
        "ops": [">", ">=", "<", "<="],
        "replay": True,
        "params": ["n", "benchmark"],
        "hint": "含当天在内的 N 日涨跌幅之和，减去同期沪深300。N 默认 3。",
    },
    "sector_up_ratio": {
        "label": "板块上涨家数占比",
        "ops": [">", ">="],
        "replay": True,
        "hint": "上涨家数 /（上涨+下跌）。填 60 表示 60%。",
    },
    "sector_amount_vs_ma": {
        "label": "板块成交额相对自身前N日均额",
        "ops": [">", ">="],
        "replay": True,
        "params": ["n"],
        "hint": "当天成交额 / 该板块更早 N 日成交额均值。1.2 表示高于均值 20%。N 默认 20。",
    },
    "sector_name": {
        "label": "指定板块",
        "ops": ["is"],
        "replay": True,
        "basis": "sector_name",
        "hint": "填写板块名称，如白酒。对上东财二级行业，不要求上涨家数。名称以「非」开头的不匹配。",
    },
}

ACTIONS = {
    "mark_priority": {"label": "将该板块标为高优先级", "expect": "positive"},
    "prefer_leaders": {"label": "优先在该板块里找强势个股", "expect": "positive"},
    "allow_entry": {"label": "允许按计划买入", "expect": "positive"},
    "forbid_entry": {"label": "禁止开仓", "expect": "negative"},
    "reduce_position": {"label": "降低仓位", "expect": "negative"},
    "exit": {"label": "卖出", "expect": "negative"},
}

_ALTERS = (
    "ALTER TABLE rule ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE rule ADD COLUMN IF NOT EXISTS rule_type VARCHAR(30)",
    "ALTER TABLE rule ADD COLUMN IF NOT EXISTS scope VARCHAR(30)",
    "ALTER TABLE rule ADD COLUMN IF NOT EXISTS check_verdict VARCHAR(30)",
    "ALTER TABLE rule ADD COLUMN IF NOT EXISTS check_items JSONB",
    "ALTER TABLE rule ADD COLUMN IF NOT EXISTS forward_result JSONB",
    "ALTER TABLE rule ADD COLUMN IF NOT EXISTS recent_result JSONB",
    "ALTER TABLE rule ADD COLUMN IF NOT EXISTS source_lesson_ids JSONB",
    "ALTER TABLE rule ADD COLUMN IF NOT EXISTS statement TEXT",
    "ALTER TABLE rule ADD COLUMN IF NOT EXISTS document JSONB",
    """
    CREATE TABLE IF NOT EXISTS rule_observable (
        id VARCHAR(40) PRIMARY KEY,
        label VARCHAR(120) NOT NULL,
        ops JSONB NOT NULL,
        hint TEXT,
        basis VARCHAR(40) NOT NULL DEFAULT 'note',
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
)

HARD_FAILS = {"explicit_conditions", "observable", "explicit_action", "no_leak"}

REGIME_ALIASES = (
    ("震荡偏强", None),
    ("牛市", "牛"),
    ("上升市", "牛"),
    ("多头", "牛"),
    ("熊市", "熊"),
    ("弱势", "熊"),
    ("下跌市", "熊"),
    ("空头", "熊"),
    ("震荡市", "震荡"),
    ("横盘", "震荡"),
    ("牛", "牛"),
    ("熊", "熊"),
    ("震荡", "震荡"),
    ("修复", "修复"),
)

_COMPOSE = """你把一句交易规则整理成 JSON。不要编造原文没有的数字、收益率或上涨家数。
超额、收益、涨幅是以后要验证的结果，写入 outcome，不要写成条件。
市场状态只能从这四个里选：牛、熊、震荡、修复。原文没说的不要补。
板块只写原文里的名称，不要编造代码。
动作 type 只能是 mark_priority、prefer_leaders、allow_entry、forbid_entry、reduce_position、exit。
category 只能是 market、sector、stock、entry、exit、position、risk、execution。
rule_type 只能是 FILTER、TRIGGER、SIZE、VETO。
scope 只能是 MARKET、SECTOR、STOCK、PORTFOLIO。
除非原文写了例外，exceptions 留空，不要自己编一条。

只输出 JSON：
{
  "name": "",
  "category": "sector",
  "rule_type": "FILTER",
  "scope": "SECTOR",
  "action": {"type": "prefer_leaders", "note": ""},
  "exceptions": "",
  "outcome": "",
  "regimes": [],
  "sectors": [],
  "other": []
}
"""


def _model_json(raw: str) -> Dict[str, Any]:
    text_value = (raw or "").strip()
    if text_value.startswith("```"):
        text_value = text_value.strip("`")
        if text_value.lower().startswith("json"):
            text_value = text_value[4:].strip()
    decoder = json.JSONDecoder()
    start = 0
    while True:
        index = text_value.find("{", start)
        if index < 0:
            return {}
        try:
            payload, _ = decoder.raw_decode(text_value[index:])
        except json.JSONDecodeError:
            start = index + 1
            continue
        if isinstance(payload, dict):
            return payload
        start = index + 1


def _regimes_in_text(text: str) -> List[str]:
    found: List[str] = []
    scrubbed = text or ""
    for phrase, label in REGIME_ALIASES:
        if phrase not in scrubbed:
            continue
        if label and label not in found:
            found.append(label)
        scrubbed = scrubbed.replace(phrase, "□" * len(phrase))
    return found


def _as_date(value: Any) -> date:
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


def _json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _num(value: Any) -> Any:
    if hasattr(value, "as_tuple"):
        return float(value)
    return value


def _pick_board(wanted: str, boards: Sequence[Dict[str, str]]) -> Tuple[Optional[Dict[str, str]], List[Dict[str, str]]]:
    hits = [board for board in boards if _name_hit(board.get("name"), wanted)]
    hits.sort(key=lambda board: (0 if board.get("name") == wanted else 1, len(str(board.get("name") or ""))))
    if not hits:
        return None, []
    return hits[0], hits[1:]


def _name_hit(name: Any, wanted: Any) -> bool:
    left = str(name or "").strip()
    right = str(wanted or "").strip()
    if not left or not right or left.startswith("非"):
        return False
    return left == right or left.startswith(right)


def _cmp(left: Any, op: str, right: Any) -> bool:
    if op == "in":
        choices = right if isinstance(right, list) else [right]
        return left in choices
    if op == "is":
        return _name_hit(left, right)
    if left is None or right is None:
        return False
    if op == ">":
        return left > right
    if op == ">=":
        return left >= right
    if op == "<":
        return left < right
    if op == "<=":
        return left <= right
    return False


def _normalize_conditions(raw: Any) -> List[Dict[str, Any]]:
    rows = raw if isinstance(raw, list) else []
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        observable = str(row.get("observable") or "").strip()
        op = str(row.get("op") or "").strip()
        item: Dict[str, Any] = {"observable": observable, "op": op, "value": row.get("value")}
        if observable == "sector_up_ratio":
            number = _f(row.get("value"))
            if number is not None and number > 1:
                item["value"] = round(number / 100, 4)
        if observable == "sector_excess_nd":
            item["n"] = int(row.get("n") or 3)
            item["benchmark"] = str(row.get("benchmark") or "000300.SH")
        if observable == "sector_amount_vs_ma":
            item["n"] = int(row.get("n") or 20)
        basis = str(row.get("basis") or "")
        if (observable == "market_regime" or basis == "market_regime") and isinstance(item["value"], str):
            item["value"] = [part.strip() for part in item["value"].replace("，", ",").split(",") if part.strip()]
        if observable == "sector_name" or str(row.get("basis") or "") == "sector_name":
            item["value"] = str(row.get("value") or "").strip()
        for key in ("label", "basis", "text", "resolved", "replay"):
            if key in row and row.get(key) not in (None, ""):
                item[key] = row.get(key)
        out.append(item)
    return out


def _condition_text(row: Dict[str, Any], catalog: Optional[Dict[str, Dict[str, Any]]] = None) -> str:
    meta = (catalog or OBSERVABLES).get(row.get("observable") or "", {})
    label = row.get("label") or meta.get("label") or row.get("observable") or "条件"
    op = row.get("op") or ""
    value = row.get("value")
    if row.get("observable") == "sector_up_ratio" and isinstance(value, (int, float)):
        value = f"{float(value) * 100:.0f}%"
    if row.get("observable") == "market_regime":
        return f"{label} 属于 {' / '.join(value or [])}"
    if row.get("observable") == "sector_name" or row.get("basis") == "sector_name":
        resolved = (row.get("resolved") or {}).get("name") if isinstance(row.get("resolved"), dict) else ""
        code = (row.get("resolved") or {}).get("ts_code") if isinstance(row.get("resolved"), dict) else ""
        if resolved:
            suffix = f"（{code}）" if code else ""
            return f"{label} 是 {row.get('text') or value}，对齐到 {resolved}{suffix}"
        return f"{label} 是 {value}"
    if row.get("observable") == "note":
        return f"原文提到，暂不能重放：{row.get('text') or value}"
    extra = ""
    if row.get("n"):
        extra = f"（N={row['n']}）"
    return f"{label}{extra} {op} {value}"


def _action_text(action: Dict[str, Any]) -> str:
    meta = ACTIONS.get(str(action.get("type") or ""), {})
    label = meta.get("label") or "动作"
    note = str(action.get("note") or "").strip()
    return f"{label}。{note}" if note else label


def _metrics(rows: Sequence[Dict[str, Any]], expect: str) -> Dict[str, Any]:
    done = [row for row in rows if row.get("forward_excess") is not None]
    excesses = [float(row["forward_excess"]) for row in done]
    if not excesses:
        return {"sample_size": 0, "win_rate": None, "expectancy": None, "profit_factor": None, "max_drawdown": None}
    if expect == "negative":
        wins = sum(1 for item in excesses if item < 0)
    else:
        wins = sum(1 for item in excesses if item > 0)
    gains = sum(item for item in excesses if item > 0)
    losses = sum(-item for item in excesses if item < 0)
    peak = 0.0
    cum = 0.0
    drawdown = 0.0
    for item in excesses:
        cum += item
        peak = max(peak, cum)
        drawdown = max(drawdown, peak - cum)
    return {
        "sample_size": len(excesses),
        "win_rate": round(wins / len(excesses), 4),
        "expectancy": round(sum(excesses) / len(excesses), 4),
        "profit_factor": None if losses <= 0 else round(gains / losses, 4),
        "max_drawdown": round(drawdown, 4),
    }


class RuleService:
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

    def _observable_map(self) -> Dict[str, Dict[str, Any]]:
        merged = {key: dict(meta) for key, meta in OBSERVABLES.items()}
        with self.engine.connect() as conn:
            rows = conn.execute(
                text("SELECT id, label, ops, hint, basis FROM rule_observable ORDER BY created_at")
            ).mappings().fetchall()
        for row in rows:
            basis = str(row["basis"] or "note")
            ops = _json(row["ops"]) or ["is"]
            merged[str(row["id"])] = {
                "label": row["label"],
                "ops": ops if isinstance(ops, list) else ["is"],
                "hint": row["hint"] or "",
                "basis": basis,
                "replay": basis in ("sector_name", "market_regime"),
                "custom": True,
            }
        return merged

    def add_observable(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """把用户补充的条件种类写入数据库，之后可以在规则里选用。"""
        self.ensure()
        label = str(body.get("label") or "").strip()
        if len(label) < 2:
            return {"ok": False, "error": "先写下条件名称"}
        basis = str(body.get("basis") or "sector_name")
        if basis not in ("sector_name", "market_regime", "note"):
            return {"ok": False, "error": "补充条件只能按板块名称、市场环境，或先记下来暂不回测"}
        defaults = {"sector_name": ["is"], "market_regime": ["in"], "note": ["is"]}
        ops = body.get("ops") or defaults[basis]
        if isinstance(ops, str):
            ops = [part.strip() for part in ops.replace("，", ",").split(",") if part.strip()]
        key = str(body.get("id") or "").strip()[:40]
        if not key:
            key = "custom_" + datetime.now().strftime("%H%M%S")
        if key in OBSERVABLES:
            return {"ok": False, "error": "这个条件编号已经内置"}
        hint = str(body.get("hint") or "").strip()
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO rule_observable (id, label, ops, hint, basis)
                    VALUES (:id, :label, CAST(:ops AS jsonb), :hint, :basis)
                    ON CONFLICT (id) DO UPDATE SET
                        label = EXCLUDED.label, ops = EXCLUDED.ops, hint = EXCLUDED.hint, basis = EXCLUDED.basis
                    """
                ),
                {
                    "id": key,
                    "label": label[:120],
                    "ops": json.dumps(ops, ensure_ascii=False),
                    "hint": hint,
                    "basis": basis,
                },
            )
        return {"ok": True, "item": {"id": key, "label": label, "ops": ops, "hint": hint, "basis": basis}}

    def catalog(self) -> Dict[str, Any]:
        self.ensure()
        with self.engine.connect() as conn:
            lessons = conn.execute(
                text("SELECT id, title, source_type, source_id, sample_size FROM lesson ORDER BY updated_at DESC LIMIT 100")
            ).mappings().fetchall()
            strategies = conn.execute(
                text("SELECT id, name FROM strategy ORDER BY name LIMIT 50")
            ).mappings().fetchall()
        return {
            "categories": [{"id": key, "label": label} for key, label in CATEGORIES],
            "statuses": [{"id": key, "label": label} for key, label in STATUSES],
            "rule_types": [{"id": key, "label": label} for key, label in RULE_TYPES],
            "scopes": [{"id": key, "label": label} for key, label in SCOPES],
            "observables": [
                {"id": key, **{k: v for k, v in meta.items() if k != "replay"}}
                for key, meta in self._observable_map().items()
            ],
            "actions": [{"id": key, "label": meta["label"], "expect": meta["expect"]} for key, meta in ACTIONS.items()],
            "lessons": [_public_row(dict(row)) for row in lessons],
            "strategies": [_public_row(dict(row)) for row in strategies],
            "validation": {
                "forward_days": FORWARD_DAYS,
                "out_sample_min": OUT_SAMPLE_MIN,
                "outcome": "信号日之后 5 个交易日的板块超额（相对沪深300）。禁止开仓、降仓、卖出则希望这 5 日超额为负。",
            },
        }

    def list_rules(self) -> Dict[str, Any]:
        self.ensure()
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT r.*, s.name AS strategy_name
                    FROM rule r
                    LEFT JOIN strategy s ON s.id = r.strategy_id
                    ORDER BY r.updated_at DESC NULLS LAST
                    LIMIT 200
                    """
                )
            ).mappings().fetchall()
        items = [self._pack(dict(row)) for row in rows]
        counts = {key: 0 for key, _label in CATEGORIES}
        status_counts = {key: 0 for key, _label in STATUSES}
        for item in items:
            if item.get("category") in counts:
                counts[item["category"]] += 1
            if item.get("status") in status_counts:
                status_counts[item["status"]] += 1
        return {"ok": True, "items": items, "category_counts": counts, "status_counts": status_counts}

    async def compose(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """把一句自然语言整理成 JSON，对齐板块和市场状态后写入规则。"""
        self.ensure()
        statement = str(body.get("statement") or "").strip()
        if len(statement) < 8:
            return {"ok": False, "error": "先用一句话写清在什么情况下怎么做"}
        drafted = await self._draft_json(statement)
        if not drafted:
            return {"ok": False, "error": "模型没有把这句话整理成结构，这次没有写入"}
        aligned = self._align_draft(drafted, statement)
        payload = {
            "name": aligned["name"],
            "category": aligned["category"],
            "rule_type": aligned["rule_type"],
            "scope": aligned["scope"],
            "lesson_ids": body.get("lesson_ids") or [],
            "strategy_id": body.get("strategy_id"),
            "conditions": aligned["conditions"],
            "action": aligned["action"],
            "exceptions": aligned["exceptions"],
            "statement": statement,
            "document": aligned["document"],
        }
        rule_id = str(body.get("id") or "").strip()
        if rule_id:
            return self.update(rule_id, payload)
        return self.create(payload)

    async def _draft_json(self, statement: str) -> Dict[str, Any]:
        from server.chat_service import ChatRequest, chat_service

        boards = self._board_names()
        names = "、".join(item["name"] for item in boards[:160])
        resp = await chat_service.chat(ChatRequest(
            query=f"规则原文：\n{statement[:4000]}\n\n库里的东财二级行业名称：{names}",
            model="deepseek-flash",
            provider="deepseek",
            temperature=0.1,
            max_tokens=2500,
            thinking="disabled",
            system_prompt=_COMPOSE,
        ))
        if resp.provider == "error":
            return {}
        return _model_json(resp.content or "")

    def _board_names(self) -> List[Dict[str, str]]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT DISTINCT ts_code, name
                    FROM sector_dc_index_daily
                    WHERE industry_level = :lv AND idx_type = '行业板块' AND name IS NOT NULL
                    """
                ),
                {"lv": LEVEL},
            ).fetchall()
        return [{"ts_code": str(row[0]), "name": str(row[1])} for row in rows if row[1]]

    def _align_draft(self, drafted: Dict[str, Any], statement: str) -> Dict[str, Any]:
        boards = self._board_names()
        regimes = []
        for label in list(drafted.get("regimes") or []) + _regimes_in_text(statement):
            mapped = dict((phrase, target) for phrase, target in REGIME_ALIASES if target).get(str(label).strip(), None)
            if str(label).strip() in ("牛", "熊", "震荡", "修复"):
                mapped = str(label).strip()
            if mapped and mapped not in regimes:
                regimes.append(mapped)
        conditions: List[Dict[str, Any]] = []
        unresolved: List[Dict[str, str]] = []
        if regimes:
            conditions.append({
                "observable": "market_regime",
                "op": "in",
                "label": "市场环境",
                "value": regimes,
                "text": "、".join(regimes),
                "replay": True,
            })
        wanted = []
        for name in drafted.get("sectors") or []:
            text_name = str(name or "").strip()
            if text_name and text_name not in wanted:
                wanted.append(text_name)
        skip_stems = {"市场", "板块", "条件", "规则", "收益", "超额", "个股", "龙头", "环境"}
        for board in sorted(boards, key=lambda item: len(item["name"]), reverse=True):
            stem = board["name"].replace("Ⅱ", "").replace("Ⅲ", "").replace("Ⅰ", "").strip()
            if len(stem) >= 2 and stem not in skip_stems and stem in statement and stem not in wanted and not stem.startswith("非"):
                wanted.append(stem)
        seen_codes = set()
        for name in wanted:
            query = name.replace("板块", "").replace("行业", "").strip() or name
            hit, rest = _pick_board(query, boards)
            if not hit:
                unresolved.append({"text": name, "reason": "库里的东财二级行业对不上这个名称"})
                continue
            if hit["ts_code"] in seen_codes:
                continue
            seen_codes.add(hit["ts_code"])
            conditions.append({
                "observable": "sector_name",
                "op": "is",
                "basis": "sector_name",
                "label": "指定板块",
                "text": name,
                "value": hit["name"],
                "replay": True,
                "resolved": {
                    "ts_code": hit["ts_code"],
                    "name": hit["name"],
                    "level": LEVEL,
                    "alternatives": [item["name"] for item in rest[:5]],
                },
            })
        outcome = str(drafted.get("outcome") or "").strip()
        exceptions = str(drafted.get("exceptions") or "").strip()
        for raw in drafted.get("other") or []:
            text_value = str(raw or "").strip()
            if not text_value:
                continue
            if any(word in text_value for word in ("停用", "暂停", "除非", "除外", "不适用")):
                exceptions = exceptions or text_value
                continue
            if any(word in text_value for word in ("超额", "收益", "涨幅")):
                outcome = outcome or text_value
                continue
            unresolved.append({"text": text_value, "reason": "这句话里的条件还不能用库内行情重放，只留在 JSON 里"})
        action = drafted.get("action") if isinstance(drafted.get("action"), dict) else {}
        action_type = str(action.get("type") or "")
        if action_type not in ACTIONS:
            action_type = "prefer_leaders" if any(row.get("observable") == "sector_name" for row in conditions) else "mark_priority"
        category = str(drafted.get("category") or "sector")
        if category not in CATEGORY_IDS:
            category = "sector" if any(row.get("observable") == "sector_name" for row in conditions) else "market"
        rule_type = str(drafted.get("rule_type") or "FILTER")
        if rule_type not in {item[0] for item in RULE_TYPES}:
            rule_type = "FILTER"
        scope = str(drafted.get("scope") or ("SECTOR" if any(row.get("observable") == "sector_name" for row in conditions) else "MARKET"))
        if scope not in {item[0] for item in SCOPES}:
            scope = "SECTOR"
        name = str(drafted.get("name") or "").strip() or statement[:24]
        document = {
            "statement": statement,
            "outcome": outcome,
            "conditions": conditions,
            "unresolved": unresolved,
        }
        return {
            "name": name[:255],
            "category": category,
            "rule_type": rule_type,
            "scope": scope,
            "conditions": conditions,
            "action": {"type": action_type, "note": str(action.get("note") or "").strip()},
            "exceptions": exceptions,
            "document": document,
        }

    def create(self, body: Dict[str, Any]) -> Dict[str, Any]:
        self.ensure()
        parsed = self._parse(body)
        if not parsed["ok"]:
            return parsed
        checked = self._check(parsed["rule"], None)
        if checked["verdict"] == "REJECT":
            return {"ok": False, "error": _verdict_text(checked), "check": checked}
        status = "TESTING" if checked["verdict"] == "PASS" else "DRAFT"
        rule_id = self._insert(parsed["rule"], checked, status, version=1)
        return {"ok": True, "item": self.get(rule_id)["item"]}

    def update(self, rule_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        self.ensure()
        current = self.get(rule_id)
        if not current.get("ok"):
            return current
        parsed = self._parse(body)
        if not parsed["ok"]:
            return parsed
        checked = self._check(parsed["rule"], rule_id)
        if checked["verdict"] == "REJECT":
            return {"ok": False, "error": _verdict_text(checked), "check": checked}
        previous = current["item"]
        history = list((previous.get("evidence") or {}).get("history") or [])
        history.append({
            "version": previous.get("version") or 1,
            "conditions": previous.get("conditions"),
            "action": previous.get("action"),
            "exceptions": previous.get("exceptions"),
            "status": previous.get("status"),
        })
        parsed["rule"]["evidence"]["history"] = history[-8:]
        status = "TESTING" if checked["verdict"] == "PASS" else "DRAFT"
        version = int(previous.get("version") or 1) + 1
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE rule SET
                        name = :name, category = :category, strategy_id = :strategy_id,
                        lesson_id = :lesson_id, description = :description,
                        conditions = CAST(:conditions AS jsonb), action = CAST(:action AS jsonb),
                        exceptions = CAST(:exceptions AS jsonb), evidence = CAST(:evidence AS jsonb),
                        source_lesson_ids = CAST(:lesson_ids AS jsonb),
                        statement = :statement, document = CAST(:document AS jsonb),
                        rule_type = :rule_type, scope = :scope,
                        check_verdict = :verdict, check_items = CAST(:items AS jsonb),
                        status = :status, version = :version,
                        sample_size = NULL, win_rate = NULL, expectancy = NULL,
                        profit_factor = NULL, max_drawdown = NULL,
                        in_sample_result = NULL, out_sample_result = NULL,
                        forward_result = NULL, recent_result = NULL, confidence = NULL,
                        updated_at = NOW()
                    WHERE id = CAST(:id AS uuid)
                    """
                ),
                {**self._params(parsed["rule"], checked, status, version), "id": rule_id},
            )
        return {"ok": True, "item": self.get(rule_id)["item"]}

    def revise(self, rule_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        """按页面上改过的原句和条件写回 JSON，不再调用模型。"""
        self.ensure()
        current = self.get(rule_id)
        if not current.get("ok"):
            return current
        item = current["item"]
        statement = str(body.get("statement") if body.get("statement") is not None else item.get("statement") or "").strip()
        if "exceptions" in body:
            exceptions = str(body.get("exceptions") or "").strip()
        else:
            exceptions = str(item.get("exception_line") or "").strip()
        document = dict(item.get("document") or {})
        if "outcome" in body:
            outcome = str(body.get("outcome") or "").strip()
        else:
            outcome = str(document.get("outcome") or "").strip()
        raw_conditions = body.get("conditions") if isinstance(body.get("conditions"), list) else (item.get("conditions") or [])
        conditions, unresolved = self._realign_kept(raw_conditions)
        if not conditions:
            return {"ok": False, "error": "至少留一条条件。整条规则不要了，就删除这条规则。"}
        document["statement"] = statement
        document["outcome"] = outcome
        document["conditions"] = conditions
        document["unresolved"] = unresolved
        return self.update(rule_id, {
            "name": item.get("name") or statement[:24],
            "category": item.get("category") or "sector",
            "rule_type": item.get("rule_type") or "FILTER",
            "scope": item.get("scope") or "SECTOR",
            "lesson_ids": item.get("lesson_ids") or [],
            "strategy_id": item.get("strategy_id"),
            "action": item.get("action") or {"type": "prefer_leaders", "note": ""},
            "exceptions": exceptions,
            "conditions": conditions,
            "statement": statement,
            "document": document,
        })

    def _realign_kept(self, rows: Sequence[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
        boards = self._board_names()
        kept: List[Dict[str, Any]] = []
        unresolved: List[Dict[str, str]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            observable = str(row.get("observable") or "")
            text_value = str(row.get("text") or "").strip()
            if observable == "market_regime":
                regimes = _regimes_in_text(text_value)
                if not regimes and isinstance(row.get("value"), list):
                    regimes = [item for item in row.get("value") if item in ("牛", "熊", "震荡", "修复")]
                if not regimes:
                    unresolved.append({"text": text_value or "市场状态", "reason": "对不上牛、熊、震荡、修复"})
                    continue
                kept.append({
                    "observable": "market_regime",
                    "op": "in",
                    "label": "市场环境",
                    "value": regimes,
                    "text": text_value or "、".join(regimes),
                    "replay": True,
                })
                continue
            if observable == "sector_name" or row.get("basis") == "sector_name":
                query = (text_value or str(row.get("value") or "")).replace("板块", "").replace("行业", "").strip()
                hit, rest = _pick_board(query, boards)
                if not hit:
                    unresolved.append({"text": text_value or query, "reason": "库里的东财二级行业对不上这个名称"})
                    continue
                kept.append({
                    "observable": "sector_name",
                    "op": "is",
                    "basis": "sector_name",
                    "label": "指定板块",
                    "text": text_value or hit["name"],
                    "value": hit["name"],
                    "replay": True,
                    "resolved": {
                        "ts_code": hit["ts_code"],
                        "name": hit["name"],
                        "level": LEVEL,
                        "alternatives": [item["name"] for item in rest[:5]],
                    },
                })
                continue
            if text_value or row.get("value") not in (None, "", []):
                kept.append(dict(row))
        return kept, unresolved

    def delete(self, rule_id: str) -> Dict[str, Any]:
        self.ensure()
        current = self.get(rule_id)
        if not current.get("ok"):
            return current
        if current["item"].get("status") == "PRODUCTION":
            return {"ok": False, "error": "生效中的规则先暂停，再删除"}
        with self.engine.begin() as conn:
            conn.execute(text("DELETE FROM rule WHERE id = CAST(:id AS uuid)"), {"id": rule_id})
        return {"ok": True}

    def pause(self, rule_id: str) -> Dict[str, Any]:
        self.ensure()
        current = self.get(rule_id)
        if not current.get("ok"):
            return current
        if current["item"].get("status") not in ("PRODUCTION", "TESTING"):
            return {"ok": False, "error": "只有测试中或生效中的规则可以暂停"}
        with self.engine.begin() as conn:
            conn.execute(
                text("UPDATE rule SET status = 'PAUSED', updated_at = NOW() WHERE id = CAST(:id AS uuid)"),
                {"id": rule_id},
            )
        return self.get(rule_id)

    def promote(self, rule_id: str) -> Dict[str, Any]:
        self.ensure()
        current = self.get(rule_id)
        if not current.get("ok"):
            return current
        item = current["item"]
        if item.get("check_verdict") != "PASS" or item.get("status") != "TESTING":
            return {"ok": False, "error": "只有检查通过、并且处在测试中的规则可以生效"}
        out = item.get("out_sample_result") or {}
        sample = int(out.get("sample_size") or 0)
        expectancy = _f(out.get("expectancy"))
        if sample < OUT_SAMPLE_MIN or expectancy is None or expectancy <= 0:
            return {
                "ok": False,
                "error": (
                    f"样本外已完成的信号需要至少 {OUT_SAMPLE_MIN} 个，且平均超额要大于 0。"
                    f"当前样本外 {sample} 个，平均超额 {('空' if expectancy is None else f'{expectancy:.2f} 个百分点')}。"
                ),
            }
        with self.engine.begin() as conn:
            conn.execute(
                text("UPDATE rule SET status = 'PRODUCTION', updated_at = NOW() WHERE id = CAST(:id AS uuid)"),
                {"id": rule_id},
            )
        return self.get(rule_id)

    def get(self, rule_id: str) -> Dict[str, Any]:
        self.ensure()
        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT r.*, s.name AS strategy_name
                    FROM rule r
                    LEFT JOIN strategy s ON s.id = r.strategy_id
                    WHERE r.id = CAST(:id AS uuid)
                    """
                ),
                {"id": rule_id},
            ).mappings().fetchone()
        if not row:
            return {"ok": False, "error": "这条规则不存在"}
        return {"ok": True, "item": self._pack(dict(row))}

    def validate(self, rule_id: str) -> Dict[str, Any]:
        """用信号日之后 5 个交易日的超额重放。不改写成生效。"""
        self.ensure()
        current = self.get(rule_id)
        if not current.get("ok"):
            return current
        item = current["item"]
        if item.get("status") == "PAUSED":
            return {"ok": False, "error": "已暂停的规则要先编辑并重新检查，再做验证"}
        conditions = item.get("conditions") or []
        action = item.get("action") or {}
        expect = ACTIONS.get(str(action.get("type") or ""), {}).get("expect", "positive")
        replay = self._replay(conditions, expect, _as_date(item.get("created_at") or date.today()))
        overall = replay["overall"]
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE rule SET
                        sample_size = :n, win_rate = :win, expectancy = :exp,
                        profit_factor = :pf, max_drawdown = :dd,
                        in_sample_result = CAST(:ins AS jsonb),
                        out_sample_result = CAST(:outs AS jsonb),
                        forward_result = CAST(:fwd AS jsonb),
                        recent_result = CAST(:recent AS jsonb),
                        updated_at = NOW()
                    WHERE id = CAST(:id AS uuid)
                    """
                ),
                {
                    "id": rule_id,
                    "n": overall["sample_size"],
                    "win": overall["win_rate"],
                    "exp": overall["expectancy"],
                    "pf": overall["profit_factor"],
                    "dd": overall["max_drawdown"],
                    "ins": json.dumps(replay["in_sample"], ensure_ascii=False),
                    "outs": json.dumps(replay["out_sample"], ensure_ascii=False),
                    "fwd": json.dumps(replay["forward"], ensure_ascii=False),
                    "recent": json.dumps(replay["recent"], ensure_ascii=False),
                },
            )
        packed = self.get(rule_id)
        packed["validation"] = replay
        return packed

    def _parse(self, body: Dict[str, Any]) -> Dict[str, Any]:
        name = str(body.get("name") or "").strip()
        category = str(body.get("category") or "").strip()
        rule_type = str(body.get("rule_type") or "FILTER").strip()
        scope = str(body.get("scope") or "SECTOR").strip()
        if not name:
            return {"ok": False, "error": "先写规则名称"}
        if category not in CATEGORY_IDS:
            return {"ok": False, "error": "先选择规则种类"}
        if rule_type not in {item[0] for item in RULE_TYPES}:
            return {"ok": False, "error": "规则类型无效"}
        if scope not in {item[0] for item in SCOPES}:
            return {"ok": False, "error": "作用范围无效"}
        lesson_ids = [str(item) for item in (body.get("lesson_ids") or []) if item]
        strategy_id = str(body.get("strategy_id") or "").strip() or None
        conditions = _normalize_conditions(body.get("conditions"))
        catalog = self._observable_map()
        for row in conditions:
            meta = catalog.get(row.get("observable") or "") or {}
            if meta.get("basis") and not row.get("basis"):
                row["basis"] = meta["basis"]
            if meta.get("label") and not row.get("label"):
                row["label"] = meta["label"]
        action = body.get("action") if isinstance(body.get("action"), dict) else {}
        exceptions = str(body.get("exceptions") or "").strip()
        document = body.get("document") if isinstance(body.get("document"), dict) else {}
        rule = {
            "name": name[:255],
            "category": category,
            "rule_type": rule_type,
            "scope": scope,
            "strategy_id": strategy_id,
            "lesson_ids": lesson_ids,
            "conditions": conditions,
            "action": {"type": str(action.get("type") or ""), "note": str(action.get("note") or "").strip()},
            "exceptions": exceptions,
            "statement": str(body.get("statement") or "").strip(),
            "document": document,
            "evidence": {"lesson_ids": lesson_ids},
        }
        return {"ok": True, "rule": rule}

    def _check(self, rule: Dict[str, Any], rule_id: Optional[str]) -> Dict[str, Any]:
        items = []

        def add(key: str, title: str, ok: bool, detail: str) -> None:
            items.append({"key": key, "title": title, "ok": ok, "detail": detail})

        catalog = self._observable_map()
        conditions = rule["conditions"]
        known = [row for row in conditions if row.get("observable") in catalog]
        notes = [row for row in conditions if row.get("observable") == "note" or row.get("replay") is False]
        usable = [row for row in conditions if row.get("value") not in (None, "", []) or row.get("text")]
        ops_ok = all(row.get("op") in catalog[row["observable"]]["ops"] for row in known)
        add(
            "explicit_conditions",
            "是否有明确条件",
            bool(usable) and ops_ok,
            "每条条件都要能对齐到可核对的内容" if not (usable and ops_ok) else f"{len(usable)} 个条件",
        )

        unknown = [row.get("observable") for row in conditions if row.get("observable") not in catalog and row.get("observable") != "note"]
        if unknown:
            observable_detail = "有无法观测的条件：" + "、".join(str(item) for item in unknown)
        elif not usable:
            observable_detail = "还没有写出条件"
        else:
            observable_detail = "条件已整理成 JSON，板块和市场状态按库里的名称对齐"
        add("observable", "条件是否可以观测", not unknown and bool(usable), observable_detail)

        action_type = rule["action"].get("type")
        add("explicit_action", "是否有明确行动", action_type in ACTIONS, ACTIONS.get(action_type, {}).get("label") or "要选择一个具体动作")

        add("exceptions", "是否有例外", len(rule["exceptions"]) >= 4, rule["exceptions"] or "写下什么情况下暂停这条规则")

        unresolved = (rule.get("document") or {}).get("unresolved") or []
        replayable = bool(known) and all(catalog[row["observable"]].get("replay") for row in known) and not unknown and not notes and not unresolved
        replay_detail = "这些条件可以用板块行情和沪深300重放" if replayable else "还有条件只留在 JSON 里，不能用库内行情重放"
        add("replayable", "是否可以历史验证", replayable, replay_detail)

        lessons = self._lesson_rows(rule["lesson_ids"])
        add("evidence_source", "是否有证据来源", len(lessons) > 0, f"来自 {len(lessons)} 条经验" if lessons else "还没有挂上经验。规则要能指回 EXP，而不是一段感想")

        sample_n = len(lessons)
        sample_ok = sample_n >= 3
        add("sample", "是否有足够样本", sample_ok, f"已关联 {sample_n} 条经验。进入测试至少 3 条不同经验" )

        leak = any("future" in str(row.get("observable")) or str(row.get("observable")).startswith("next_") for row in conditions)
        add("no_leak", "是否存在未来数据泄漏", not leak, "条件用了信号日之后才知道的数据" if leak else "条件只用信号日当天及以前的数据")

        conflict = self._conflict(rule, rule_id) if action_type in ACTIONS else None
        add("conflict", "是否与已有规则冲突", conflict is None, conflict or "没有和生效中的规则使用同一组条件")

        single = len(lessons) <= 1
        add("not_single_trade", "是否只是单笔交易产生的结论", not single, "只挂了一条来自单笔交易的经验" if single else "不是从单笔交易直接写成的")

        failed = [item for item in items if not item["ok"]]
        hard = [item for item in failed if item["key"] in HARD_FAILS]
        if hard:
            verdict = "REJECT"
        elif failed:
            verdict = "NEED_MORE_EVIDENCE"
        else:
            verdict = "PASS"
        return {"verdict": verdict, "items": items}

    def _lesson_rows(self, lesson_ids: Sequence[str]) -> List[Dict[str, Any]]:
        if not lesson_ids:
            return []
        with self.engine.connect() as conn:
            rows = conn.execute(
                text("SELECT id, title, source_type, source_id, sample_size FROM lesson WHERE id::text = ANY(:ids)"),
                {"ids": list(lesson_ids)},
            ).mappings().fetchall()
        return [dict(row) for row in rows]

    def _conflict(self, rule: Dict[str, Any], rule_id: Optional[str]) -> Optional[str]:
        signature = tuple(sorted(
            (row.get("observable"), row.get("op"), json.dumps(row.get("value"), ensure_ascii=False))
            for row in rule["conditions"]
        ))
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT id, rule_code, name, conditions, action
                    FROM rule
                    WHERE status = 'PRODUCTION' AND category = :category
                    """
                ),
                {"category": rule["category"]},
            ).mappings().fetchall()
        for row in rows:
            if rule_id and str(row["id"]) == str(rule_id):
                continue
            action = _json(row["action"]) or {}
            if action.get("type") != rule["action"].get("type"):
                continue
            other = _json(row["conditions"]) or []
            other_sig = tuple(sorted(
                (item.get("observable"), item.get("op"), json.dumps(item.get("value"), ensure_ascii=False))
                for item in other if isinstance(item, dict)
            ))
            if other_sig == signature:
                return f"与生效中的 {row['rule_code'] or row['name']} 条件相同，不会覆盖它"
        return None

    def _insert(self, rule: Dict[str, Any], checked: Dict[str, Any], status: str, version: int) -> str:
        code = self._next_code()
        with self.engine.begin() as conn:
            row = conn.execute(
                text(
                    """
                    INSERT INTO rule (
                        rule_code, name, category, strategy_id, lesson_id, description,
                        conditions, action, exceptions, evidence, source_lesson_ids,
                        statement, document,
                        rule_type, scope, check_verdict, check_items, status, version
                    ) VALUES (
                        :code, :name, :category, :strategy_id, :lesson_id, :description,
                        CAST(:conditions AS jsonb), CAST(:action AS jsonb), CAST(:exceptions AS jsonb),
                        CAST(:evidence AS jsonb), CAST(:lesson_ids AS jsonb),
                        :statement, CAST(:document AS jsonb),
                        :rule_type, :scope, :verdict, CAST(:items AS jsonb), :status, :version
                    ) RETURNING id
                    """
                ),
                {**self._params(rule, checked, status, version), "code": code},
            ).fetchone()
        return str(row[0])

    def _params(self, rule: Dict[str, Any], checked: Dict[str, Any], status: str, version: int) -> Dict[str, Any]:
        lines = [_condition_text(row, self._observable_map()) for row in rule["conditions"]]
        description = "当 " + "，并且 ".join(lines) + " 时，" + _action_text(rule["action"]) + "。除非：" + rule["exceptions"]
        lesson_id = rule["lesson_ids"][0] if rule["lesson_ids"] else None
        return {
            "name": rule["name"],
            "category": rule["category"],
            "strategy_id": rule["strategy_id"],
            "lesson_id": lesson_id,
            "description": description,
            "conditions": json.dumps(rule["conditions"], ensure_ascii=False),
            "action": json.dumps(rule["action"], ensure_ascii=False),
            "exceptions": json.dumps(rule["exceptions"], ensure_ascii=False),
            "evidence": json.dumps(rule["evidence"], ensure_ascii=False),
            "lesson_ids": json.dumps(rule["lesson_ids"], ensure_ascii=False),
            "statement": rule.get("statement") or "",
            "document": json.dumps(rule.get("document") or {}, ensure_ascii=False),
            "rule_type": rule["rule_type"],
            "scope": rule["scope"],
            "verdict": checked["verdict"],
            "items": json.dumps(checked["items"], ensure_ascii=False),
            "status": status,
            "version": version,
        }

    def _next_code(self) -> str:
        with self.engine.connect() as conn:
            rows = conn.execute(text("SELECT rule_code FROM rule WHERE rule_code LIKE 'RULE-%'")).fetchall()
        best = 0
        for row in rows:
            tail = str(row[0] or "").replace("RULE-", "")
            if tail.isdigit():
                best = max(best, int(tail))
        return f"RULE-{best + 1:03d}"

    def _replay(self, conditions: List[Dict[str, Any]], expect: str, created_on: date) -> Dict[str, Any]:
        sector_needed = any(str(row.get("observable") or "").startswith("sector_") for row in conditions)
        books, bench, regimes, updown, note = self._load_market()
        sector_days = sorted({day for book in books.values() for day in book})
        days = sector_days if sector_needed else sorted(bench)
        signals: List[Dict[str, Any]] = []
        skipped = 0
        targets = books if sector_needed else {"": {}}
        for day in days:
            for code, book in targets.items():
                ctx = self._context(code, day, book, days, bench, regimes, updown, conditions)
                verdict = self._match(conditions, ctx)
                if verdict is None:
                    skipped += 1
                    continue
                if not verdict:
                    continue
                forward = self._forward(code, day, days, book, bench, sector_needed)
                signals.append({
                    "trade_date": day.isoformat(),
                    "sector_code": code or None,
                    "forward_excess": None if forward is None else round(forward, 4),
                })
        done = [row for row in signals if row["forward_excess"] is not None]
        done.sort(key=lambda row: row["trade_date"])
        dates = sorted({row["trade_date"] for row in done})
        cut = int(len(dates) * SPLIT)
        if len(dates) >= 2:
            in_dates = set(dates[:max(cut, 1)])
            out_dates = set(dates[max(cut, 1):])
        else:
            in_dates = set(dates)
            out_dates = set()
        ins = _metrics([row for row in done if row["trade_date"] in in_dates], expect)
        outs = _metrics([row for row in done if row["trade_date"] in out_dates], expect)
        forward_rows = [row for row in done if row["trade_date"] > created_on.isoformat()]
        recent_rows = list(reversed(done))
        amount_rule = next((row for row in conditions if row.get("observable") == "sector_amount_vs_ma"), None)
        amount_note = ""
        if amount_rule and len(sector_days) <= int(amount_rule.get("n") or 20):
            amount_note = (
                f"成交额条件要该板块自己更早 {int(amount_rule.get('n') or 20)} 个交易日，"
                f"现在二级行业只有 {len(sector_days)} 天，所以这个条件还产生不了信号。"
            )
        coverage = (
            f"东财二级行业目前有 {len(sector_days)} 个交易日的行情，市场环境记录 {len(regimes)} 天。"
            + amount_note
            + "结果是信号日后 5 个交易日相对沪深300的超额，不是账户盈亏。"
        )
        return {
            "note": " ".join(part for part in (note, coverage) if part),
            "signals": len(signals),
            "completed": len(done),
            "skipped_incomplete": skipped,
            "overall": _metrics(done, expect),
            "in_sample": {**ins, "label": "样本内：按信号日排序的前 70%"},
            "out_sample": {**outs, "label": "样本外：按信号日排序的后 30%"},
            "forward": {**_metrics(forward_rows, expect), "label": "向前测试：规则写入日之后才出现的信号"},
            "recent": {
                "last_20": _metrics(recent_rows[:20], expect),
                "last_50": _metrics(recent_rows[:50], expect),
            },
            "promote": {
                "ready": outs["sample_size"] >= OUT_SAMPLE_MIN and (outs["expectancy"] or 0) > 0,
                "need": OUT_SAMPLE_MIN,
            },
        }

    def _match(self, conditions: List[Dict[str, Any]], ctx: Dict[str, Any]) -> Optional[bool]:
        if not conditions:
            return None
        for row in conditions:
            value = ctx.get(row["observable"])
            if value is None:
                return None
            if not _cmp(value, row.get("op") or "", row.get("value")):
                return False
        return True

    def _context(
        self,
        code: str,
        day: date,
        book: Dict[date, Dict[str, Any]],
        days: Sequence[date],
        bench: Dict[date, float],
        regimes: Dict[date, str],
        updown: Dict[Tuple[str, date], Tuple[int, int]],
        conditions: Sequence[Dict[str, Any]],
    ) -> Dict[str, Any]:
        ctx: Dict[str, Any] = {"market_regime": regimes.get(day)}
        bar = book.get(day) or {}
        ctx["sector_name"] = bar.get("name")
        prior = [item for item in days if item <= day]
        catalog = self._observable_map()
        for row in conditions:
            basis = (catalog.get(row.get("observable") or "") or {}).get("basis")
            if basis == "sector_name":
                ctx[row["observable"]] = bar.get("name")
            elif basis == "market_regime":
                ctx[row["observable"]] = regimes.get(day)
        excess = next((row for row in conditions if row.get("observable") == "sector_excess_nd"), None)
        if excess and bar.get("pct") is not None:
            n = int(excess.get("n") or 3)
            window = [item for item in prior if item in book and book[item].get("pct") is not None and item in bench]
            last = window[-n:]
            if len(last) == n:
                ctx["sector_excess_nd"] = sum(book[item]["pct"] - bench[item] for item in last)
        amount_rule = next((row for row in conditions if row.get("observable") == "sector_amount_vs_ma"), None)
        if amount_rule and bar.get("amount"):
            n = int(amount_rule.get("n") or 20)
            amounts = [book[item]["amount"] for item in prior if item < day and item in book and book[item].get("amount")]
            if len(amounts) >= n:
                ctx["sector_amount_vs_ma"] = bar["amount"] / (sum(amounts[-n:]) / n)
        pair = updown.get((code, day))
        if pair and (pair[0] + pair[1]) > 0:
            ctx["sector_up_ratio"] = pair[0] / (pair[0] + pair[1])
        return ctx

    def _forward(self, code: str, day: date, days: Sequence[date], book: Dict[date, Dict[str, Any]], bench: Dict[date, float], sector_needed: bool) -> Optional[float]:
        future = [item for item in days if item > day][:FORWARD_DAYS]
        if len(future) < FORWARD_DAYS:
            return None
        if not sector_needed:
            if any(item not in bench for item in future):
                return None
            return sum(bench[item] for item in future)
        if any(item not in book or book[item].get("pct") is None or item not in bench for item in future):
            return None
        return sum(book[item]["pct"] - bench[item] for item in future)

    def _load_market(self) -> Tuple[Dict[str, Dict[date, Dict[str, Any]]], Dict[date, float], Dict[date, str], Dict[Tuple[str, date], Tuple[int, int]], str]:
        notes = []
        with self.engine.connect() as conn:
            bars = conn.execute(
                text(
                    """
                    SELECT i.ts_code, i.name, i.trade_date, i.pct_change, d.amount
                    FROM sector_dc_index_daily i
                    LEFT JOIN sector_dc_daily d
                      ON d.ts_code = i.ts_code AND d.trade_date = i.trade_date
                    WHERE i.industry_level = :lv AND i.idx_type = '行业板块'
                    """
                ),
                {"lv": LEVEL},
            ).fetchall()
            bench_rows = conn.execute(
                text("SELECT trade_date, pct_chg FROM index_daily WHERE ts_code = '000300.SH'")
            ).fetchall()
            regime_rows = conn.execute(
                text("SELECT trade_date, regime_label FROM market_regime ORDER BY trade_date, created_at")
            ).fetchall()
            codes = sorted({str(row[0]) for row in bars})
            days = sorted({_as_date(row[2]) for row in bars})
            breadth_rows = []
            if codes and days:
                breadth_rows = conn.execute(
                    text(
                        """
                        WITH mem AS (
                            SELECT DISTINCT ON (m.ts_code, m.con_code)
                                   m.ts_code AS sector, m.con_code
                            FROM sector_dc_member m
                            WHERE m.ts_code = ANY(:codes)
                            ORDER BY m.ts_code, m.con_code, m.trade_date DESC
                        )
                        SELECT mem.sector, d.trade_date,
                               COUNT(*) FILTER (WHERE d.pct_chg > 0),
                               COUNT(*) FILTER (WHERE d.pct_chg < 0)
                        FROM mem
                        JOIN stock_daily d
                          ON d.ts_code = mem.con_code AND d.trade_date = ANY(:days)
                        GROUP BY mem.sector, d.trade_date
                        """
                    ),
                    {"codes": codes, "days": days},
                ).fetchall()
        books: Dict[str, Dict[date, Dict[str, Any]]] = {}
        for code, name, trade_date, pct, amount in bars:
            books.setdefault(str(code), {})[_as_date(trade_date)] = {
                "pct": _f(pct),
                "amount": _f(amount),
                "name": name,
            }
        bench = {_as_date(row[0]): _f(row[1]) for row in bench_rows if _f(row[1]) is not None}
        regimes = {_as_date(row[0]): str(row[1]) for row in regime_rows}
        updown = {(str(row[0]), _as_date(row[1])): (int(row[2] or 0), int(row[3] or 0)) for row in breadth_rows}
        if not bench:
            notes.append("库里没有沪深300（000300.SH）日线，带超额的条件无法重放。")
        if len(regimes) < 5:
            notes.append(f"市场环境只有 {len(regimes)} 天，带市场环境的规则大部分日期不会触发。")
        return books, bench, regimes, updown, "".join(notes)

    def _pack(self, row: Dict[str, Any]) -> Dict[str, Any]:
        item = {}
        for key, value in row.items():
            if hasattr(value, "isoformat"):
                item[key] = value.isoformat()
            elif hasattr(value, "hex") and hasattr(value, "int"):
                item[key] = str(value)
            else:
                item[key] = _num(_json(value) if key in {
                    "conditions", "action", "exceptions", "evidence", "check_items",
                    "in_sample_result", "out_sample_result", "forward_result", "recent_result",
                    "source_lesson_ids", "document",
                } else value)
        if isinstance(item.get("exceptions"), str):
            pass
        item["condition_lines"] = [
            _condition_text(row, self._observable_map())
            for row in (item.get("conditions") or [])
            if isinstance(row, dict)
        ]
        item["action_line"] = _action_text(item.get("action") or {})
        item["exception_line"] = item.get("exceptions") if isinstance(item.get("exceptions"), str) else ""
        item["status_label"] = dict(STATUSES).get(item.get("status") or "", item.get("status"))
        item["category_label"] = dict(CATEGORIES).get(item.get("category") or "", item.get("category"))
        item["rule_type_label"] = dict(RULE_TYPES).get(item.get("rule_type") or "", item.get("rule_type"))
        item["scope_label"] = dict(SCOPES).get(item.get("scope") or "", item.get("scope"))
        lessons = item.get("source_lesson_ids") or (item.get("evidence") or {}).get("lesson_ids") or []
        item["lesson_ids"] = lessons
        return item


def _verdict_text(checked: Dict[str, Any]) -> str:
    failed = [item["title"] + "：" + item["detail"] for item in checked["items"] if not item["ok"]]
    head = {"REJECT": "还不能作为规则保存", "NEED_MORE_EVIDENCE": "先作为草稿留下", "PASS": "可以进入测试"}.get(checked["verdict"], checked["verdict"])
    return head + "。" + "；".join(failed)


def _public_row(row: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for key, value in row.items():
        if hasattr(value, "isoformat"):
            out[key] = value.isoformat()
        elif hasattr(value, "hex") and hasattr(value, "int"):
            out[key] = str(value)
        else:
            out[key] = _num(value)
    return out


rule_service = RuleService()

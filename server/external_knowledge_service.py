"""外部知识：别人提出的观点和方法，只作为候选。

阅读之后提取观点、方法、条件和证据，可以送进实验室。
不会因为作者有名或回测好看就写成交易规则。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from data_layer.db_manager import storage_manager

USER_SYSTEM = "A股短线，T+1，题材与板块。周期以短线和波段为主。"

SOURCE_TYPES = (
    ("PAPER", "论文"),
    ("BOOK", "书籍"),
    ("TRADER", "交易员"),
    ("ARTICLE", "文章"),
    ("INTERVIEW", "访谈"),
    ("REPORT", "研究"),
    ("NEWS", "新闻"),
    ("SOCIAL_MEDIA", "社交媒体"),
    ("OTHER", "其他"),
)
KNOWLEDGE_TYPES = (
    ("STRATEGY", "交易策略"),
    ("ENTRY_METHOD", "买入方法"),
    ("EXIT_METHOD", "卖出方法"),
    ("STOCK_SELECTION", "选股"),
    ("SECTOR_ANALYSIS", "板块"),
    ("MARKET_REGIME", "市场环境"),
    ("POSITION_MANAGEMENT", "仓位"),
    ("RISK_MANAGEMENT", "风险"),
    ("PSYCHOLOGY", "心理"),
    ("CASE_STUDY", "案例"),
    ("SETUP", "交易形态"),
    ("MARKET_VIEW", "市场观点"),
    ("RESEARCH", "研究结果"),
    ("INDICATOR", "指标"),
    ("OTHER", "其他"),
)
EVIDENCE_LEVELS = (
    ("UNVERIFIED", "未经验证"),
    ("OBSERVATIONAL", "观察性"),
    ("BACKTEST", "回测"),
    ("OUT_OF_SAMPLE", "样本外"),
    ("FORWARD_TEST", "前瞻测试"),
    ("LIVE", "实盘验证"),
)
RELATIONS = (
    ("SUPPORTS", "支持"),
    ("INSPIRES", "启发"),
    ("CONTRADICTS", "矛盾"),
    ("EXTENDS", "延伸"),
    ("REJECTED", "不采用"),
)
KIND_LABELS = {
    "FACT": "事实",
    "OPINION": "作者观点",
    "INFERENCE": "作者推断",
    "HYPOTHESIS": "未经验证的假设",
}

_ALTERS = (
    "ALTER TABLE external_knowledge ADD COLUMN IF NOT EXISTS account_id UUID",
    "ALTER TABLE external_knowledge ADD COLUMN IF NOT EXISTS knowledge_type VARCHAR(40)",
    "ALTER TABLE external_knowledge ADD COLUMN IF NOT EXISTS key_findings JSONB",
    "ALTER TABLE external_knowledge ADD COLUMN IF NOT EXISTS methodology JSONB",
    "ALTER TABLE external_knowledge ADD COLUMN IF NOT EXISTS conditions JSONB",
    "ALTER TABLE external_knowledge ADD COLUMN IF NOT EXISTS risks JSONB",
    "ALTER TABLE external_knowledge ADD COLUMN IF NOT EXISTS limitations JSONB",
    "ALTER TABLE external_knowledge ADD COLUMN IF NOT EXISTS market VARCHAR(40)",
    "ALTER TABLE external_knowledge ADD COLUMN IF NOT EXISTS symbols JSONB",
    "ALTER TABLE external_knowledge ADD COLUMN IF NOT EXISTS topics JSONB",
    "ALTER TABLE external_knowledge ADD COLUMN IF NOT EXISTS credibility_score NUMERIC(5,4)",
    "ALTER TABLE external_knowledge ADD COLUMN IF NOT EXISTS relevance_score NUMERIC(5,4)",
    "ALTER TABLE external_knowledge ADD COLUMN IF NOT EXISTS applicability JSONB",
    "ALTER TABLE external_knowledge ADD COLUMN IF NOT EXISTS hypotheses JSONB",
    "ALTER TABLE external_knowledge ADD COLUMN IF NOT EXISTS status VARCHAR(30) NOT NULL DEFAULT 'CANDIDATE'",
    "ALTER TABLE external_knowledge ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    "ALTER TABLE experiment ADD COLUMN IF NOT EXISTS claim_id VARCHAR(40)",
    """
    CREATE TABLE IF NOT EXISTS external_knowledge_experience (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        external_knowledge_id UUID NOT NULL REFERENCES external_knowledge(id) ON DELETE CASCADE,
        experience_id UUID NOT NULL REFERENCES lesson(id) ON DELETE CASCADE,
        claim_id VARCHAR(40),
        relationship VARCHAR(30) NOT NULL,
        confidence NUMERIC(5,4),
        note TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
)

_AGENT = """你是 External Knowledge Agent。
你的任务不是推荐外部交易方法，而是把用户给的材料整理成可追溯的结构化记录。
用户的交易体系是：A股、短线、T+1、题材与板块。

请只根据原文输出 JSON，不要编造作者没写的数据、收益或样本。
区分事实、作者观点、作者推断、未经验证的假设。作者观点不要写成事实。
没有证据就标 UNVERIFIED。不要因为作者有名而提高证据等级。
回测收益高也不要当成方法有效，evidence_level 最多是 BACKTEST，并在 credibility_notes 写明回测可能过拟合、样本外可能衰减。
不要输出交易规则，不要建议用户按这个方法下单。

JSON 格式：
{
  "summary": "",
  "claims": [
    {
      "statement": "",
      "type": "HYPOTHESIS",
      "kind": "OPINION",
      "conditions": [],
      "expected_effect": "",
      "testable": false
    }
  ],
  "methods": [{"name": "", "entry": "", "exit": "", "position": "", "risk": ""}],
  "conditions": [],
  "risks": [],
  "limitations": [],
  "evidence": {"level": "UNVERIFIED", "notes": "", "what_author_provided": ""},
  "market_scope": {
    "market": "CN_A_SHARE",
    "universe": "STOCK",
    "style": ["SHORT_TERM"],
    "frequency": "DAILY",
    "holding_period": "",
    "market_regimes": []
  },
  "applicability": {"level": "LOW", "reason": ""},
  "relevance_score": 0.5,
  "credibility_score": 0.3,
  "hypotheses": [{"claim_statement": "", "statement": "", "why_testable": ""}]
}
kind 只能是 FACT、OPINION、INFERENCE、HYPOTHESIS。
evidence.level 只能是 UNVERIFIED、OBSERVATIONAL、BACKTEST、OUT_OF_SAMPLE、FORWARD_TEST、LIVE。
market 用 CN_A_SHARE、US、HK、OTHER。style 用 SHORT_TERM、SWING、POSITION。
"""


def _loads(value: Any) -> Any:
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


def _clip_score(value: Any, hi: float = 1.0) -> Optional[float]:
    number = None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(max(0.0, min(hi, number)), 4)


def _parse_json(raw: str) -> Dict[str, Any]:
    text_value = (raw or "").strip()
    if text_value.startswith("```"):
        text_value = re.sub(r"^```(?:json)?", "", text_value).strip()
        if text_value.endswith("```"):
            text_value = text_value[:-3].strip()
    decoder = json.JSONDecoder()
    start = 0
    fallback: Dict[str, Any] = {}
    while True:
        index = text_value.find("{", start)
        if index < 0:
            break
        try:
            payload, _ = decoder.raw_decode(text_value[index:])
        except json.JSONDecodeError:
            start = index + 1
            continue
        if isinstance(payload, dict) and (payload.get("summary") or payload.get("claims")):
            return payload
        if isinstance(payload, dict) and not fallback:
            fallback = payload
        start = index + 1
    if fallback:
        return fallback
    return _salvage_json(text_value)


def _salvage_json(raw: str) -> Dict[str, Any]:
    """回复被截断时，留下已经写完的摘要和观点。"""
    summary = ""
    found = re.search(r'"summary"\s*:\s*"((?:\\.|[^"\\])*)"', raw)
    if found:
        try:
            summary = json.loads('"' + found.group(1) + '"')
        except json.JSONDecodeError:
            summary = found.group(1)
    claims = []
    for block in re.finditer(r"\{[^{}]*\"statement\"\s*:\s*\"(?:\\.|[^\"\\])+\"[^{}]*\}", raw):
        try:
            row = json.loads(block.group(0))
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("statement"):
            claims.append(row)
    if summary or claims:
        return {"summary": summary, "claims": claims}
    return {}


def _kind(value: Any) -> str:
    text_value = str(value or "").strip().upper()
    if text_value in KIND_LABELS:
        return text_value
    mapping = {"事实": "FACT", "作者观点": "OPINION", "观点": "OPINION", "作者推断": "INFERENCE", "推断": "INFERENCE", "假设": "HYPOTHESIS"}
    return mapping.get(str(value or "").strip(), "OPINION")


def _claims(raw: Any) -> List[Dict[str, Any]]:
    rows = raw if isinstance(raw, list) else []
    out = []
    for index, row in enumerate(rows, start=1):
        if isinstance(row, str):
            row = {"statement": row}
        if not isinstance(row, dict):
            continue
        statement = str(row.get("statement") or "").strip()
        if not statement:
            continue
        conditions = row.get("conditions") if isinstance(row.get("conditions"), list) else []
        out.append({
            "id": str(row.get("id") or f"CLAIM-{index:03d}"),
            "statement": statement,
            "type": str(row.get("type") or "HYPOTHESIS"),
            "kind": _kind(row.get("kind") or row.get("type")),
            "kind_label": KIND_LABELS.get(_kind(row.get("kind") or row.get("type")), "作者观点"),
            "conditions": [str(item) for item in conditions if str(item).strip()],
            "expected_effect": str(row.get("expected_effect") or ""),
            "testable": bool(row.get("testable")) and bool(conditions),
        })
    return out


def _methods(raw: Any) -> List[Dict[str, Any]]:
    rows = raw if isinstance(raw, list) else []
    out = []
    for row in rows:
        if isinstance(row, str):
            row = {"name": row}
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name and not any(row.get(key) for key in ("entry", "exit", "position", "risk")):
            continue
        out.append({
            "name": name or "未命名方法",
            "entry": str(row.get("entry") or ""),
            "exit": str(row.get("exit") or ""),
            "position": str(row.get("position") or ""),
            "risk": str(row.get("risk") or ""),
        })
    return out


def _scope(raw: Any) -> Dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    market = str(data.get("market") or "").strip().upper().replace(" ", "_")
    aliases = {"A股": "CN_A_SHARE", "CN": "CN_A_SHARE", "CHINA": "CN_A_SHARE", "美股": "US", "港股": "HK"}
    market = aliases.get(market, market or "OTHER")
    if market in ("A股",):
        market = "CN_A_SHARE"
    styles = data.get("style") if isinstance(data.get("style"), list) else []
    style_alias = {"短线": "SHORT_TERM", "波段": "SWING", "中长线": "POSITION", "中线": "POSITION"}
    clean_styles = []
    for item in styles:
        key = style_alias.get(str(item).strip(), str(item).strip().upper())
        if key and key not in clean_styles:
            clean_styles.append(key)
    regimes = data.get("market_regimes") if isinstance(data.get("market_regimes"), list) else []
    return {
        "market": market,
        "universe": str(data.get("universe") or "STOCK"),
        "style": clean_styles,
        "frequency": str(data.get("frequency") or ""),
        "holding_period": str(data.get("holding_period") or ""),
        "market_regimes": [str(item) for item in regimes if str(item).strip()],
    }


def _applicability(scope: Dict[str, Any], suggested: Any) -> Dict[str, Any]:
    hinted = suggested if isinstance(suggested, dict) else {}
    level = str(hinted.get("level") or "MEDIUM").upper()
    if level not in ("HIGH", "MEDIUM", "LOW"):
        level = "MEDIUM"
    reasons = [str(hinted.get("reason") or "").strip()]
    market = scope.get("market") or ""
    styles = set(scope.get("style") or [])
    if market and market != "CN_A_SHARE":
        level = "LOW"
        reasons.append("材料面向的市场不是 A 股，和当前交易体系不一致。")
    elif styles and not (styles & {"SHORT_TERM", "SWING"}):
        level = "LOW"
        reasons.append("材料的持有周期偏中长线，和当前短线、波段体系不一致。")
    return {
        "level": level,
        "reason": " ".join(part for part in reasons if part),
        "user_system": USER_SYSTEM,
    }


def _evidence_level(raw: Any, notes: str) -> str:
    level = str(raw or "UNVERIFIED").upper()
    allowed = {item[0] for item in EVIDENCE_LEVELS}
    if level not in allowed:
        level = "UNVERIFIED"
    blob = (notes or "").lower()
    marks = {
        "OUT_OF_SAMPLE": ("样本外", "out-of-sample", "out of sample"),
        "FORWARD_TEST": ("前瞻", "forward test", "forward-test"),
        "LIVE": ("实盘", "live trading"),
        "BACKTEST": ("回测", "backtest"),
        "OBSERVATIONAL": ("观察", "样本描述", "案例"),
    }
    if level in marks and not any(token in blob for token in marks[level]):
        if any(token in blob for token in marks["BACKTEST"]):
            level = "BACKTEST"
        elif any(token in blob for token in marks["OBSERVATIONAL"]):
            level = "OBSERVATIONAL"
        else:
            level = "UNVERIFIED"
    return level


def _hypotheses(raw: Any, claims: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_statement = {row["statement"]: row["id"] for row in claims if row.get("testable")}
    rows = raw if isinstance(raw, list) else []
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        statement = str(row.get("statement") or "").strip()
        if not statement:
            continue
        claim_statement = str(row.get("claim_statement") or "").strip()
        claim_id = by_statement.get(claim_statement) or ""
        if not claim_id:
            continue
        out.append({
            "claim_id": claim_id,
            "statement": statement,
            "why_testable": str(row.get("why_testable") or ""),
        })
    if out:
        return out
    for row in claims:
        if not row.get("testable"):
            continue
        out.append({
            "claim_id": row["id"],
            "statement": row["statement"],
            "why_testable": "原文给出了可观察条件，可以放进实验室，还不是规则。",
        })
    return out


class ExternalKnowledgeService:
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
            lessons = conn.execute(
                text("SELECT id, title FROM lesson ORDER BY updated_at DESC LIMIT 100")
            ).mappings().fetchall()
        return {
            "source_types": [{"id": key, "label": label} for key, label in SOURCE_TYPES],
            "knowledge_types": [{"id": key, "label": label} for key, label in KNOWLEDGE_TYPES],
            "evidence_levels": [{"id": key, "label": label} for key, label in EVIDENCE_LEVELS],
            "relations": [{"id": key, "label": label} for key, label in RELATIONS],
            "lessons": [{"id": str(row["id"]), "title": row["title"]} for row in lessons],
            "user_system": USER_SYSTEM,
            "note": "外部知识只是候选。验证通过之前，不会写成我的经验，也不会写成交易规则。",
        }

    async def read_and_save(self, body: Dict[str, Any]) -> Dict[str, Any]:
        self.ensure()
        content = str(body.get("content") or "").strip()
        title = str(body.get("title") or "").strip()
        if len(content) < 40:
            return {"ok": False, "error": "先贴上原文。太短的内容提取不出可追溯的观点。"}
        source_type = str(body.get("source_type") or "ARTICLE").upper()
        knowledge_type = str(body.get("knowledge_type") or "OTHER").upper()
        if source_type not in {item[0] for item in SOURCE_TYPES}:
            return {"ok": False, "error": "来源类型无效"}
        if knowledge_type not in {item[0] for item in KNOWLEDGE_TYPES}:
            return {"ok": False, "error": "知识类型无效"}
        clipped = content[:12000]
        note = "" if len(content) <= 12000 else "原文超过 12000 字，这次只读了前 12000 字。"
        from server.chat_service import ChatRequest, chat_service

        resp = await chat_service.chat(ChatRequest(
            query=(
                f"标题：{title or '未命名'}\n"
                f"来源类型：{source_type}\n知识类型：{knowledge_type}\n"
                f"作者：{body.get('author') or '未知'}\n链接：{body.get('source_url') or ''}\n\n"
                f"{clipped}"
            ),
            model="deepseek-flash",
            provider="deepseek",
            temperature=0.1,
            max_tokens=8000,
            thinking="disabled",
            system_prompt=_AGENT,
        ))
        if resp.provider == "error":
            return {"ok": False, "error": "模型没有完成阅读，原文没有入库。请稍后再试。"}
        parsed = _parse_json(resp.content or "")
        if not parsed.get("summary") and not parsed.get("claims"):
            if resp.finish_reason == "length":
                return {"ok": False, "error": "模型的回复被长度截断，摘要和观点没有完整返回，原文没有入库。"}
            return {"ok": False, "error": "模型没有按原文抽出摘要或观点，这次没有入库。"}
        record = self._normalize(parsed, {
            "title": title or str(parsed.get("summary") or "")[:40],
            "source_type": source_type,
            "knowledge_type": knowledge_type,
            "author": str(body.get("author") or "").strip(),
            "source_url": str(body.get("source_url") or "").strip(),
            "content": content,
            "read_note": note,
        })
        saved = self._insert(record)
        return {"ok": True, "item": self.get(saved)["item"]}

    def list_items(self, source_type: str = "", knowledge_type: str = "", evidence_level: str = "") -> Dict[str, Any]:
        self.ensure()
        filters = ["1=1"]
        params: Dict[str, Any] = {}
        if source_type and source_type != "ALL":
            filters.append("source_type = :source_type")
            params["source_type"] = source_type
        if knowledge_type and knowledge_type != "ALL":
            filters.append("knowledge_type = :knowledge_type")
            params["knowledge_type"] = knowledge_type
        if evidence_level and evidence_level != "ALL":
            filters.append("evidence_level = :evidence_level")
            params["evidence_level"] = evidence_level
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    f"""
                    SELECT * FROM external_knowledge
                    WHERE {' AND '.join(filters)}
                    ORDER BY created_at DESC
                    LIMIT 100
                    """
                ),
                params,
            ).mappings().fetchall()
        items = [self._pack(dict(row), with_trace=False) for row in rows]
        return {"ok": True, "items": items}

    def get(self, knowledge_id: str) -> Dict[str, Any]:
        self.ensure()
        with self.engine.connect() as conn:
            row = conn.execute(
                text("SELECT * FROM external_knowledge WHERE id = CAST(:id AS uuid)"),
                {"id": knowledge_id},
            ).mappings().fetchone()
            if not row:
                return {"ok": False, "error": "这条外部知识不存在"}
            experiments = conn.execute(
                text(
                    """
                    SELECT id, strategy_name, hypothesis, status, claim_id, created_at
                    FROM experiment
                    WHERE source_knowledge_id = CAST(:id AS uuid)
                    ORDER BY created_at DESC
                    """
                ),
                {"id": knowledge_id},
            ).mappings().fetchall()
            links = conn.execute(
                text(
                    """
                    SELECT e.id, e.claim_id, e.relationship, e.note, e.created_at,
                           l.id AS experience_id, l.title
                    FROM external_knowledge_experience e
                    JOIN lesson l ON l.id = e.experience_id
                    WHERE e.external_knowledge_id = CAST(:id AS uuid)
                    ORDER BY e.created_at DESC
                    """
                ),
                {"id": knowledge_id},
            ).mappings().fetchall()
            rules = conn.execute(
                text(
                    """
                    SELECT DISTINCT r.rule_code, r.name, r.status, l.title
                    FROM external_knowledge_experience e
                    JOIN lesson l ON l.id = e.experience_id
                    JOIN rule r ON r.lesson_id = l.id
                    WHERE e.external_knowledge_id = CAST(:id AS uuid)
                    """
                ),
                {"id": knowledge_id},
            ).mappings().fetchall()
        item = self._pack(dict(row), with_trace=True)
        item["experiments"] = [_public(dict(row)) for row in experiments]
        item["experience_links"] = [_public(dict(row)) for row in links]
        item["rules"] = [_public(dict(row)) for row in rules]
        return {"ok": True, "item": item}

    def to_experiment(self, knowledge_id: str, claim_id: str) -> Dict[str, Any]:
        """可检验的观点进入实验室，状态是想法，不生成规则。"""
        self.ensure()
        current = self.get(knowledge_id)
        if not current.get("ok"):
            return current
        item = current["item"]
        claim = next((row for row in item.get("claims") or [] if row.get("id") == claim_id), None)
        if not claim:
            return {"ok": False, "error": "没有这条观点"}
        if not claim.get("testable"):
            return {"ok": False, "error": "这条观点还没有可观察的条件，先不进入实验室"}
        hypothesis = next((row for row in item.get("hypotheses") or [] if row.get("claim_id") == claim_id), None)
        statement = (hypothesis or {}).get("statement") or claim["statement"]
        with self.engine.begin() as conn:
            row = conn.execute(
                text(
                    """
                    INSERT INTO experiment (
                        strategy_name, hypothesis, hypothesis_source, source_knowledge_id,
                        claim_id, status, parameters
                    ) VALUES (
                        :name, :hyp, 'EXTERNAL', CAST(:kid AS uuid), :claim, 'IDEA', CAST(:params AS jsonb)
                    ) RETURNING id, hypothesis, status, claim_id
                    """
                ),
                {
                    "name": (item.get("title") or "外部知识实验")[:255],
                    "hyp": statement,
                    "kid": knowledge_id,
                    "claim": claim_id,
                    "params": json.dumps({
                        "claim_id": claim_id,
                        "conditions": claim.get("conditions") or [],
                        "expected_effect": claim.get("expected_effect") or "",
                        "knowledge_title": item.get("title"),
                    }, ensure_ascii=False),
                },
            ).mappings().fetchone()
        return {
            "ok": True,
            "experiment": _public(dict(row)),
            "message": "已放进实验室，状态是想法。验证通过之前，不会写成经验，也不会写成规则。",
        }

    def link_experience(self, knowledge_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        self.ensure()
        current = self.get(knowledge_id)
        if not current.get("ok"):
            return current
        experience_id = str(body.get("experience_id") or "").strip()
        relationship = str(body.get("relationship") or "").upper()
        if relationship not in {item[0] for item in RELATIONS}:
            return {"ok": False, "error": "先选择这条外部知识和经验的关系"}
        if not experience_id:
            return {"ok": False, "error": "先选择一条我的经验"}
        with self.engine.begin() as conn:
            found = conn.execute(
                text("SELECT id FROM lesson WHERE id = CAST(:id AS uuid)"),
                {"id": experience_id},
            ).fetchone()
            if not found:
                return {"ok": False, "error": "这条经验不存在"}
            conn.execute(
                text(
                    """
                    INSERT INTO external_knowledge_experience (
                        external_knowledge_id, experience_id, claim_id, relationship, note
                    ) VALUES (
                        CAST(:kid AS uuid), CAST(:eid AS uuid), :claim, :rel, :note
                    )
                    """
                ),
                {
                    "kid": knowledge_id,
                    "eid": experience_id,
                    "claim": str(body.get("claim_id") or "") or None,
                    "rel": relationship,
                    "note": str(body.get("note") or "")[:500],
                },
            )
        return self.get(knowledge_id)

    def _normalize(self, parsed: Dict[str, Any], source: Dict[str, Any]) -> Dict[str, Any]:
        evidence = parsed.get("evidence") if isinstance(parsed.get("evidence"), dict) else {}
        notes = " ".join(
            part for part in (
                str(evidence.get("notes") or ""),
                str(evidence.get("what_author_provided") or ""),
                str(parsed.get("credibility_notes") or ""),
                source.get("read_note") or "",
            ) if part
        )
        level = _evidence_level(evidence.get("level"), notes)
        credibility = _clip_score(parsed.get("credibility_score"), 1)
        if level == "UNVERIFIED":
            credibility = min(credibility if credibility is not None else 0.3, 0.4)
            if "没有证据" not in notes:
                notes = (notes + " 原文没有给出可核对的证据，标为未经验证。").strip()
        if level == "BACKTEST":
            credibility = min(credibility if credibility is not None else 0.5, 0.55)
            notes = (notes + " 回测收益不能当成方法有效，样本外可能衰减。").strip()
        claims = _claims(parsed.get("claims"))
        scope = _scope(parsed.get("market_scope"))
        return {
            "title": source["title"][:500],
            "source_type": source["source_type"],
            "knowledge_type": source["knowledge_type"],
            "author": source["author"][:255],
            "source_url": source["source_url"],
            "content": source["content"],
            "summary": str(parsed.get("summary") or "").strip(),
            "claims": claims,
            "key_findings": claims,
            "strategies": _methods(parsed.get("methods")),
            "methodology": _methods(parsed.get("methods")),
            "conditions": [str(item) for item in (parsed.get("conditions") or []) if str(item).strip()] if isinstance(parsed.get("conditions"), list) else [],
            "risks": [str(item) for item in (parsed.get("risks") or []) if str(item).strip()] if isinstance(parsed.get("risks"), list) else [],
            "limitations": [str(item) for item in (parsed.get("limitations") or []) if str(item).strip()] if isinstance(parsed.get("limitations"), list) else [],
            "market_scope": scope,
            "market": scope.get("market"),
            "symbols": [],
            "topics": [],
            "evidence_level": level,
            "credibility_score": credibility,
            "relevance_score": _clip_score(parsed.get("relevance_score"), 1),
            "credibility_notes": notes,
            "applicability": _applicability(scope, parsed.get("applicability")),
            "hypotheses": _hypotheses(parsed.get("hypotheses"), claims),
            "status": "CANDIDATE",
        }

    def _insert(self, record: Dict[str, Any]) -> str:
        with self.engine.begin() as conn:
            row = conn.execute(
                text(
                    """
                    INSERT INTO external_knowledge (
                        title, source_type, knowledge_type, author, source_url, content, summary,
                        claims, key_findings, strategies, methodology, conditions, risks, limitations,
                        market_scope, market, symbols, topics, evidence_level, credibility_score,
                        relevance_score, credibility_notes, applicability, hypotheses, status
                    ) VALUES (
                        :title, :source_type, :knowledge_type, :author, :source_url, :content, :summary,
                        CAST(:claims AS jsonb), CAST(:key_findings AS jsonb), CAST(:strategies AS jsonb),
                        CAST(:methodology AS jsonb), CAST(:conditions AS jsonb), CAST(:risks AS jsonb),
                        CAST(:limitations AS jsonb), CAST(:market_scope AS jsonb), :market,
                        CAST(:symbols AS jsonb), CAST(:topics AS jsonb), :evidence_level, :credibility_score,
                        :relevance_score, :credibility_notes, CAST(:applicability AS jsonb),
                        CAST(:hypotheses AS jsonb), :status
                    ) RETURNING id
                    """
                ),
                {
                    **{key: record[key] for key in (
                        "title", "source_type", "knowledge_type", "author", "source_url", "content",
                        "summary", "market", "evidence_level", "credibility_score", "relevance_score",
                        "credibility_notes", "status",
                    )},
                    "claims": json.dumps(record["claims"], ensure_ascii=False),
                    "key_findings": json.dumps(record["key_findings"], ensure_ascii=False),
                    "strategies": json.dumps(record["strategies"], ensure_ascii=False),
                    "methodology": json.dumps(record["methodology"], ensure_ascii=False),
                    "conditions": json.dumps(record["conditions"], ensure_ascii=False),
                    "risks": json.dumps(record["risks"], ensure_ascii=False),
                    "limitations": json.dumps(record["limitations"], ensure_ascii=False),
                    "market_scope": json.dumps(record["market_scope"], ensure_ascii=False),
                    "symbols": json.dumps(record["symbols"], ensure_ascii=False),
                    "topics": json.dumps(record["topics"], ensure_ascii=False),
                    "applicability": json.dumps(record["applicability"], ensure_ascii=False),
                    "hypotheses": json.dumps(record["hypotheses"], ensure_ascii=False),
                },
            ).fetchone()
        return str(row[0])

    def _pack(self, row: Dict[str, Any], with_trace: bool) -> Dict[str, Any]:
        item: Dict[str, Any] = {}
        json_keys = {
            "claims", "key_findings", "strategies", "methodology", "conditions", "risks",
            "limitations", "market_scope", "symbols", "topics", "applicability", "hypotheses",
        }
        for key, value in row.items():
            if key == "embedding":
                continue
            if hasattr(value, "isoformat"):
                item[key] = value.isoformat()
            elif hasattr(value, "hex") and hasattr(value, "int"):
                item[key] = str(value)
            else:
                item[key] = _num(_loads(value) if key in json_keys else value)
        item["source_label"] = dict(SOURCE_TYPES).get(item.get("source_type") or "", item.get("source_type"))
        item["knowledge_label"] = dict(KNOWLEDGE_TYPES).get(item.get("knowledge_type") or "", item.get("knowledge_type"))
        item["evidence_label"] = dict(EVIDENCE_LEVELS).get(item.get("evidence_level") or "", item.get("evidence_level"))
        item["status"] = item.get("status") or "CANDIDATE"
        if not with_trace:
            item.pop("content", None)
        return item


def _public(row: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for key, value in row.items():
        if hasattr(value, "isoformat"):
            out[key] = value.isoformat()
        elif hasattr(value, "hex") and hasattr(value, "int"):
            out[key] = str(value)
        else:
            out[key] = _num(value)
    if out.get("relationship"):
        out["relationship_label"] = dict(RELATIONS).get(out["relationship"], out["relationship"])
    return out


external_knowledge_service = ExternalKnowledgeService()

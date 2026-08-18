"""
Skill Registry - 技能注册中心
- SKILL_REGISTRY：结构化工具定义（前端技能面板 + Agent function calling）
- HANDBOOK.md：总目录手册（模型缺数据时优先阅读）
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
_HANDBOOK_PATH = os.path.join(_TOOLS_DIR, "HANDBOOK.md")

SKILL_REGISTRY: Dict[str, Dict[str, Any]] = {
    "sql-toolkit": {
        "id": "sql-toolkit",
        "name": "SQL 数据库工具箱",
        "icon": "database",
        "category": "数据分析",
        "desc": (
            "基于 LangChain SQLDatabaseToolkit，提供 PostgreSQL 关系型数据库的智能查询能力。"
            "支持查看表结构、列出所有表、SQL 校验和自然语言转 SQL 执行。"
        ),
        "enabled": True,
        "tools": [
            {
                "id": "list-tables",
                "name": "📋 列出所有表",
                "desc": "获取数据库中所有用户表的名称列表。不确定有哪些数据表时优先调用。",
                "params": [],
                "returns": "表名列表",
                "openai_parameters": {"type": "object", "properties": {}, "required": []},
            },
            {
                "id": "info-table",
                "name": "📊 查看表信息",
                "desc": "获取指定表的完整 schema：列名、类型、是否可空、主键",
                "params": [
                    {
                        "name": "table_names",
                        "label": "表名",
                        "type": "text",
                        "required": True,
                        "placeholder": "逗号分隔，如：stock_daily, ref_stock_basic",
                    }
                ],
                "returns": "各表的 CREATE TABLE / 字段信息",
                "openai_parameters": {
                    "type": "object",
                    "properties": {
                        "table_names": {
                            "type": "string",
                            "description": "逗号分隔的表名，如 stock_daily,ref_stock_basic",
                        }
                    },
                    "required": ["table_names"],
                },
            },
            {
                "id": "query-check",
                "name": "✅ SQL 校验",
                "desc": "检查 SQL 语句是否语法正确，并给出修正建议（仅只读）",
                "params": [
                    {
                        "name": "query",
                        "label": "SQL 语句",
                        "type": "textarea",
                        "required": True,
                        "placeholder": "SELECT * FROM stock_daily LIMIT 5",
                    }
                ],
                "returns": "校验结果",
                "openai_parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "待校验的 SQL"}
                    },
                    "required": ["query"],
                },
            },
            {
                "id": "query-exec",
                "name": "⚡ 执行 SQL",
                "desc": "在 PostgreSQL 中执行 SELECT 查询并返回结果（自动限制行数）",
                "params": [
                    {
                        "name": "query",
                        "label": "SQL 语句",
                        "type": "textarea",
                        "required": True,
                        "placeholder": (
                            "SELECT ts_code, trade_date, close, pct_chg "
                            "FROM stock_daily WHERE ts_code='000001.SZ' "
                            "ORDER BY trade_date DESC LIMIT 10"
                        ),
                    }
                ],
                "returns": "查询结果表格",
                "openai_parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "只读 SQL（SELECT/WITH）"},
                        "limit": {
                            "type": "integer",
                            "description": "最大返回行数，默认 100",
                            "default": 100,
                        },
                    },
                    "required": ["query"],
                },
            },
            {
                "id": "query-nl",
                "name": "💬 自然语言查询",
                "desc": "输入自然语言问题（能力有限时请改用 list/info + query-exec）",
                "params": [
                    {
                        "name": "question",
                        "label": "你的问题",
                        "type": "textarea",
                        "required": True,
                        "placeholder": "比如：最近5天成交量最高的10只股票是什么？",
                    }
                ],
                "returns": "SQL + 查询结果或降级提示",
                "openai_parameters": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string", "description": "自然语言问题"}
                    },
                    "required": ["question"],
                },
            },
        ],
        "instructions": """
**SQL 数据库工具箱** 提供对 PostgreSQL 的完整交互能力：

- **列出所有表** → 看看有什么数据可用
- **查看表信息** → 了解表的字段和结构
- **SQL 校验** → 写 SQL 前先检查语法
- **执行 SQL** → 直接执行 SELECT 查询
- **自然语言查询** → 用中文描述需求（可能降级）

⚠️ 安全限制：仅允许 SELECT 查询，禁止 INSERT/UPDATE/DELETE/DROP 等写操作。
""",
    }
}


def get_handbook(max_chars: int = 12000) -> str:
    """读取 tools/HANDBOOK.md 总目录手册"""
    try:
        with open(_HANDBOOK_PATH, "r", encoding="utf-8") as f:
            text = f.read()
        if len(text) > max_chars:
            return text[:max_chars] + "\n\n...（手册已截断）"
        return text
    except Exception as e:
        return f"（无法读取 HANDBOOK.md: {e}）"


def get_handbook_path() -> str:
    return _HANDBOOK_PATH


def get_skill(skill_id: str) -> Optional[Dict[str, Any]]:
    return SKILL_REGISTRY.get(skill_id)


def list_skills(category: str = None) -> List[Dict[str, Any]]:
    skills = []
    for skill in SKILL_REGISTRY.values():
        if not skill.get("enabled", True):
            continue
        if category and skill.get("category") != category:
            continue
        skills.append({
            "id": skill["id"],
            "name": skill["name"],
            "icon": skill["icon"],
            "category": skill["category"],
            "desc": skill["desc"],
            "tools_count": len(skill.get("tools", [])),
            "tools": skill.get("tools", []),
        })
    return skills


def _fn_name(skill_id: str, tool_id: str) -> str:
    return f"{skill_id.replace('-', '_')}__{tool_id.replace('-', '_')}"


def parse_fn_name(fn_name: str) -> Tuple[Optional[str], Optional[str]]:
    """sql_toolkit__list_tables → (sql-toolkit, list-tables)"""
    if "__" not in (fn_name or ""):
        return None, None
    left, right = fn_name.split("__", 1)
    skill_id = left.replace("_", "-")
    # tool ids may contain underscores that were hyphens
    # Prefer matching against registry
    skill = SKILL_REGISTRY.get(skill_id)
    if not skill:
        # try original underscore form mapped back only for known skills
        for sid in SKILL_REGISTRY:
            if sid.replace("-", "_") == left:
                skill_id = sid
                skill = SKILL_REGISTRY[sid]
                break
    if not skill:
        return skill_id, right.replace("_", "-")

    for t in skill.get("tools", []):
        tid = t["id"]
        if tid.replace("-", "_") == right or tid == right:
            return skill_id, tid
    return skill_id, right.replace("_", "-")


def get_openai_tools() -> List[Dict[str, Any]]:
    """生成 OpenAI / DeepSeek function calling tools 列表"""
    tools: List[Dict[str, Any]] = []
    for skill in SKILL_REGISTRY.values():
        if not skill.get("enabled", True):
            continue
        for t in skill.get("tools", []):
            tools.append({
                "type": "function",
                "function": {
                    "name": _fn_name(skill["id"], t["id"]),
                    "description": f"[{skill['name']}] {t.get('desc') or t.get('name')}",
                    "parameters": t.get("openai_parameters") or {
                        "type": "object",
                        "properties": {},
                        "required": [],
                    },
                },
            })
    return tools


def build_agent_system_prompt(extra: Optional[str] = None) -> str:
    handbook = get_handbook()
    catalog_lines = []
    for skill in list_skills():
        catalog_lines.append(f"### {skill['name']} (`{skill['id']}`)")
        catalog_lines.append(skill["desc"])
        for t in skill.get("tools", []):
            catalog_lines.append(
                f"- `{_fn_name(skill['id'], t['id'])}` — {t.get('name')}: {t.get('desc')}"
            )
        catalog_lines.append("")

    prompt = f"""你是量化投研助手。当你不确定或缺少具体数据（行情、表结构、成交量等）时，必须使用工具查询，禁止编造数据。

## 工具总目录手册
{handbook}

## 可调用函数一览
{chr(10).join(catalog_lines)}

## 行为要求
1. 缺数据 → 先选手册中的技能与工具 → 再 function call。
2. 不知道表/字段 → 先 list_tables / info_table。
3. 拿到工具结果后，用中文简洁回答用户；可引用关键数字。
4. 若工具失败，说明原因并给出下一步建议。
"""
    if extra:
        prompt += f"\n## 额外系统说明\n{extra}\n"
    return prompt

"""
Skill Router - 技能调用路由
GET  /skills                        → 列出所有技能
GET  /skills/{skill_id}             → 获取技能详情
POST /skills/{skill_id}/{tool_id}   → 执行指定技能的工具
"""

from fastapi import APIRouter, HTTPException, Body
from typing import Optional, Any

import tools
from tools.sql_toolkit import sql_toolkit

router = APIRouter(prefix="/skills", tags=["skills"])


# ─── 列出所有技能 ─────────────────────────────────────

@router.get("")
async def list_skills(category: Optional[str] = None):
    """获取所有已注册的技能列表"""
    skills = tools.list_skills(category=category)
    return {
        "skills": skills,
        "total": len(skills),
        "categories": list(set(s["category"] for s in skills)),
    }


# ─── 获取单个技能详情 ─────────────────────────────────

@router.get("/{skill_id}")
async def get_skill(skill_id: str):
    """获取技能详情（含工具列表和使用说明）"""
    skill = tools.get_skill(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail=f"技能 '{skill_id}' 不存在")

    return {
        "id": skill["id"],
        "name": skill["name"],
        "icon": skill["icon"],
        "category": skill["category"],
        "desc": skill["desc"],
        "tools": skill.get("tools", []),
        "instructions": skill.get("instructions", ""),
    }


# ─── 执行技能的工具 ───────────────────────────────────

@router.post("/{skill_id}/{tool_id}")
async def execute_skill_tool(
    skill_id: str,
    tool_id: str,
    payload: dict = Body(...),
):
    """
    执行指定技能的工具

    Payload 中传入工具所需的参数，如：
    - { "table_names": "stock_daily, ref_trade_cal" }
    - { "query": "SELECT * FROM stock_daily LIMIT 3" }
    - { "question": "最近5天哪些股票涨得最多" }
    """
    # 验证技能存在
    skill = tools.get_skill(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail=f"技能 '{skill_id}' 不存在")

    # 验证工具存在
    tool_def = None
    for t in skill.get("tools", []):
        if t["id"] == tool_id:
            tool_def = t
            break
    if not tool_def:
        raise HTTPException(status_code=404, detail=f"工具 '{tool_id}' 在技能 '{skill_id}' 中不存在")

    # ─── 路由到具体实现 ────────────────────────────
    if skill_id == "sql-toolkit":
        return await _execute_sql_toolkit(tool_id, payload)

    raise HTTPException(status_code=501, detail=f"技能 '{skill_id}' 暂未实现")


async def _execute_sql_toolkit(tool_id: str, payload: dict) -> dict:
    """执行 SQL Toolkit 的工具"""

    # Tool 1: 列出所有表
    if tool_id == "list-tables":
        return await sql_toolkit.list_tables()

    # Tool 2: 查看表信息
    elif tool_id == "info-table":
        table_names = payload.get("table_names", "")
        if not table_names:
            raise HTTPException(status_code=400, detail="参数 'table_names' 是必填的")
        return await sql_toolkit.info_table(table_names)

    # Tool 3: SQL 校验
    elif tool_id == "query-check":
        query = payload.get("query", "")
        if not query:
            raise HTTPException(status_code=400, detail="参数 'query' 是必填的")
        return await sql_toolkit.check_sql(query)

    # Tool 4: 执行 SQL
    elif tool_id == "query-exec":
        query = payload.get("query", "")
        limit = payload.get("limit", 100)
        if not query:
            raise HTTPException(status_code=400, detail="参数 'query' 是必填的")
        return await sql_toolkit.execute_query(query, limit=int(limit))

    # Tool 5: 自然语言查询
    elif tool_id == "query-nl":
        question = payload.get("question", "")
        if not question:
            raise HTTPException(status_code=400, detail="参数 'question' 是必填的")
        return await sql_toolkit.natural_language_query(question)

    else:
        raise HTTPException(status_code=501, detail=f"工具 '{tool_id}' 暂未实现")

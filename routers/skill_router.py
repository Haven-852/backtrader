"""
Skill Router - 技能调用路由
GET  /skills                        → 列出所有技能
GET  /skills/handbook               → 工具总目录手册
GET  /skills/{skill_id}             → 获取技能详情
POST /skills/{skill_id}/{tool_id}   → 执行指定技能的工具
"""

from fastapi import APIRouter, HTTPException, Body
from typing import Optional

import tools
from tools.executor import execute_tool

router = APIRouter(prefix="/skills", tags=["skills"])


@router.get("")
async def list_skills(category: Optional[str] = None):
    """获取所有已注册的技能列表"""
    skills = tools.list_skills(category=category)
    return {
        "skills": skills,
        "total": len(skills),
        "categories": list(set(s["category"] for s in skills)),
        "handbook_available": True,
    }


@router.get("/handbook")
async def get_handbook():
    """读取 tools/HANDBOOK.md 总目录手册"""
    return {
        "path": tools.get_handbook_path(),
        "content": tools.get_handbook(),
        "openai_tools": tools.get_openai_tools(),
    }


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


@router.post("/{skill_id}/{tool_id}")
async def execute_skill_tool(
    skill_id: str,
    tool_id: str,
    payload: dict = Body(default_factory=dict),
):
    """
    执行指定技能的工具（实现位于 tools/ 目录）

    Payload 示例：
    - { "table_names": "stock_daily, ref_trade_cal" }
    - { "query": "SELECT * FROM stock_daily LIMIT 3" }
    - { "question": "最近5天哪些股票涨得最多" }
    """
    skill = tools.get_skill(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail=f"技能 '{skill_id}' 不存在")

    tool_def = next((t for t in skill.get("tools", []) if t["id"] == tool_id), None)
    if not tool_def:
        raise HTTPException(status_code=404, detail=f"工具 '{tool_id}' 在技能 '{skill_id}' 中不存在")

    result = await execute_tool(skill_id, tool_id, payload or {})
    if result.get("ok") is False and "不存在" in str(result.get("error", "")):
        raise HTTPException(status_code=501, detail=result.get("error"))
    return result

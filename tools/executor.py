"""
统一工具执行器：按 skill_id / tool_id 分发到 tools 目录下的具体实现
"""

from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


async def execute_tool(skill_id: str, tool_id: str, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """执行已注册工具，返回统一结构 {ok, ...}"""
    payload = payload or {}
    skill_id = (skill_id or "").strip()
    tool_id = (tool_id or "").strip()

    if skill_id == "sql-toolkit":
        from tools.sql_toolkit import sql_toolkit
        return await _exec_sql_toolkit(sql_toolkit, tool_id, payload)

    return {"ok": False, "error": f"未知技能: {skill_id}"}


async def _exec_sql_toolkit(sql_toolkit, tool_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if tool_id == "list-tables":
        return await sql_toolkit.list_tables()

    if tool_id == "info-table":
        table_names = payload.get("table_names") or payload.get("table_name") or ""
        if not table_names:
            return {"ok": False, "error": "缺少参数 table_names"}
        return await sql_toolkit.info_table(str(table_names))

    if tool_id == "query-check":
        query = payload.get("query") or ""
        if not query:
            return {"ok": False, "error": "缺少参数 query"}
        return await sql_toolkit.check_sql(str(query))

    if tool_id == "query-exec":
        query = payload.get("query") or ""
        if not query:
            return {"ok": False, "error": "缺少参数 query"}
        limit = int(payload.get("limit") or 100)
        return await sql_toolkit.execute_query(str(query), limit=limit)

    if tool_id == "query-nl":
        question = payload.get("question") or ""
        if not question:
            return {"ok": False, "error": "缺少参数 question"}
        return await sql_toolkit.natural_language_query(str(question))

    return {"ok": False, "error": f"未知工具: {tool_id}"}

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

    if skill_id == "datahubco-data":
        from tools.datahubco_toolkit import datahubco_toolkit
        return await _exec_datahubco(datahubco_toolkit, tool_id, payload)

    if skill_id == "t0-intraday":
        from agents.strategy.strategy_t0.tools import execute as exec_t0
        return await exec_t0(tool_id, payload)

    # QuantSkills 外部包
    from tools.quantskill_runtime import PACKAGE_DIRS, execute_quantskill
    if skill_id in PACKAGE_DIRS:
        return await execute_quantskill(skill_id, tool_id, payload)

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


async def _exec_datahubco(toolkit, tool_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if tool_id == "list-datasets":
        return await toolkit.list_datasets_async()

    if tool_id == "coverage":
        ts_code = payload.get("ts_code") or ""
        if not ts_code:
            return {"ok": False, "error": "缺少参数 ts_code"}
        return await toolkit.coverage_async(
            ts_code=str(ts_code),
            dataset=str(payload.get("dataset") or "stock_daily"),
            start_date=payload.get("start_date"),
            end_date=payload.get("end_date"),
            trade_date=payload.get("trade_date"),
        )

    if tool_id == "get-bars":
        ts_code = payload.get("ts_code") or ""
        if not ts_code:
            return {"ok": False, "error": "缺少参数 ts_code"}
        write_back = payload.get("write_back")
        if write_back is None:
            write_back = True
        auto_sync = payload.get("auto_sync")
        if auto_sync is None:
            auto_sync = True
        include_auction = payload.get("include_auction")
        if include_auction is None:
            include_auction = True
        return await toolkit.get_bars_async(
            ts_code=str(ts_code),
            dataset=str(payload.get("dataset") or "stock_daily"),
            start_date=payload.get("start_date"),
            end_date=payload.get("end_date"),
            limit=int(payload.get("limit") or 120),
            source=str(payload.get("source") or "auto"),
            write_back=bool(write_back),
            auto_sync=bool(auto_sync),
            trade_date=payload.get("trade_date"),
            include_auction=bool(include_auction),
        )

    if tool_id == "get-mins":
        ts_code = payload.get("ts_code") or ""
        if not ts_code:
            return {"ok": False, "error": "缺少参数 ts_code"}
        write_back = payload.get("write_back")
        if write_back is None:
            write_back = True
        include_auction = payload.get("include_auction")
        if include_auction is None:
            include_auction = True
        return await toolkit.get_mins_async(
            ts_code=str(ts_code),
            trade_date=payload.get("trade_date"),
            limit=int(payload.get("limit") or 500),
            source=str(payload.get("source") or "auto"),
            write_back=bool(write_back),
            include_auction=bool(include_auction),
        )

    if tool_id == "ensure-and-get":
        ts_code = payload.get("ts_code") or ""
        if not ts_code:
            return {"ok": False, "error": "缺少参数 ts_code"}
        return await toolkit.ensure_and_get_async(
            ts_code=str(ts_code),
            dataset=str(payload.get("dataset") or "stock_daily"),
            limit=int(payload.get("limit") or 120),
            trade_date=payload.get("trade_date"),
        )

    if tool_id == "remote-query":
        api = payload.get("api") or ""
        if not api:
            return {"ok": False, "error": "缺少参数 api"}
        params = payload.get("params")
        if isinstance(params, str):
            import json
            try:
                params = json.loads(params) if params.strip() else {}
            except Exception:
                return {"ok": False, "error": "params 不是合法 JSON 对象"}
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return {"ok": False, "error": "params 必须是对象"}
        return await toolkit.remote_query_async(
            api=str(api),
            params=params,
            write_dataset=payload.get("write_dataset"),
            limit=int(payload.get("limit") or 200),
        )

    if tool_id == "get-sector-members":
        ts_code = payload.get("ts_code") or payload.get("sector_code") or ""
        if not ts_code:
            return {"ok": False, "error": "缺少参数 ts_code（板块代码）"}
        write_back = payload.get("write_back")
        if write_back is None:
            write_back = True
        include_flags = payload.get("include_flags")
        if include_flags is None:
            include_flags = True
        return await toolkit.get_sector_members_async(
            ts_code=str(ts_code),
            trade_date=payload.get("trade_date"),
            source=str(payload.get("source") or "auto"),
            write_back=bool(write_back),
            limit=int(payload.get("limit") or 500),
            include_flags=bool(include_flags),
        )

    if tool_id == "filter-st-suspend":
        exclude_st = payload.get("exclude_st")
        if exclude_st is None:
            exclude_st = True
        exclude_suspend = payload.get("exclude_suspend")
        if exclude_suspend is None:
            exclude_suspend = True
        write_back = payload.get("write_back")
        if write_back is None:
            write_back = True
        return_removed = payload.get("return_removed")
        if return_removed is None:
            return_removed = True
        ts_codes = payload.get("ts_codes")
        if isinstance(ts_codes, str):
            ts_codes = [x.strip() for x in ts_codes.replace("，", ",").split(",") if x.strip()]
        return await toolkit.filter_st_suspend_async(
            ts_code=payload.get("ts_code") or payload.get("sector_code"),
            trade_date=payload.get("trade_date"),
            ts_codes=ts_codes,
            members=payload.get("members"),
            exclude_st=bool(exclude_st),
            exclude_suspend=bool(exclude_suspend),
            source=str(payload.get("source") or "auto"),
            write_back=bool(write_back),
            limit=int(payload.get("limit") or 500),
            return_removed=bool(return_removed),
        )

    return {"ok": False, "error": f"未知工具: {tool_id}"}

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

    if skill_id == "paper-trade":
        return await _exec_paper_trade(tool_id, payload)

    if skill_id == "kb-market" and tool_id == "today":
        from tools.kb_market_toolkit import kb_market_toolkit
        return kb_market_toolkit.build_today(payload.get("trade_date"))

    # QuantSkills 外部包
    from tools.quantskill_runtime import PACKAGE_DIRS, execute_quantskill
    if skill_id in PACKAGE_DIRS:
        return await execute_quantskill(skill_id, tool_id, payload)

    return {"ok": False, "error": f"未知技能: {skill_id}"}


async def _exec_paper_trade(tool_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    from tools.paper_toolkit import paper_toolkit

    if tool_id == "create-account":
        return await paper_toolkit.create_account(
            name=str(payload.get("name") or "交易Agent账户"),
            initial_cash=float(payload.get("initial_cash") or 1_000_000),
            lookback_guard=bool(payload["lookback_guard"]) if "lookback_guard" in payload else True,
        )
    if tool_id == "delete-account":
        if payload.get("account_id") is None:
            return {"ok": False, "error": "缺少参数 account_id"}
        hard = payload.get("hard")
        if hard is None:
            hard = True
        return await paper_toolkit.delete_account(int(payload["account_id"]), hard=bool(hard))
    if tool_id == "roll-t1":
        aid = payload.get("account_id")
        return await paper_toolkit.roll_t1(
            account_id=int(aid) if aid is not None else None,
            asof=payload.get("asof"),
            force=bool(payload.get("force") or False),
        )
    if tool_id == "list-accounts":
        return await paper_toolkit.list_accounts(status=str(payload.get("status") or "active"))
    if tool_id == "get-account":
        if payload.get("account_id") is None:
            return {"ok": False, "error": "缺少参数 account_id"}
        mark = payload.get("mark")
        if mark is None:
            mark = True
        return await paper_toolkit.get_account(int(payload["account_id"]), mark=bool(mark))
    if tool_id == "list-positions":
        if payload.get("account_id") is None:
            return {"ok": False, "error": "缺少参数 account_id"}
        return await paper_toolkit.list_positions(int(payload["account_id"]))
    if tool_id == "get-journal":
        if payload.get("account_id") is None:
            return {"ok": False, "error": "缺少参数 account_id"}
        return await paper_toolkit.get_journal(
            int(payload["account_id"]),
            trade_date=payload.get("trade_date"),
        )
    if tool_id == "get-market-brief":
        ts_code = payload.get("ts_code") or ""
        if not ts_code:
            return {"ok": False, "error": "缺少参数 ts_code"}
        return await paper_toolkit.get_market_brief(
            ts_code=str(ts_code),
            limit=int(payload.get("limit") or 120),
            features=payload.get("features"),
        )
    if tool_id == "get-intraday-quotes":
        return await paper_toolkit.get_intraday_quotes(
            ts_code=payload.get("ts_code"),
            ts_codes=payload.get("ts_codes"),
            account_id=int(payload["account_id"]) if payload.get("account_id") is not None else None,
            trade_date=payload.get("trade_date"),
        )
    if tool_id == "place-order":
        for k in ("account_id", "ts_code", "side", "quantity"):
            if payload.get(k) is None or payload.get(k) == "":
                return {"ok": False, "error": f"缺少参数 {k}"}
        return await paper_toolkit.place_order(
            account_id=int(payload["account_id"]),
            ts_code=str(payload["ts_code"]),
            side=str(payload["side"]),
            quantity=float(payload["quantity"]),
            lookback_guard=payload.get("lookback_guard"),
            signal_date=payload.get("signal_date"),
            reason=payload.get("reason"),
        )
    if tool_id == "settle":
        aid = payload.get("account_id")
        return await paper_toolkit.settle(
            account_id=int(aid) if aid is not None else None,
            asof=payload.get("asof"),
        )
    if tool_id == "mark":
        if payload.get("account_id") is None:
            return {"ok": False, "error": "缺少参数 account_id"}
        write_equity = payload.get("write_equity")
        if write_equity is None:
            write_equity = True
        return await paper_toolkit.mark(
            account_id=int(payload["account_id"]),
            asof=payload.get("asof"),
            write_equity=bool(write_equity),
        )
    if tool_id == "get-stats":
        if payload.get("account_id") is None:
            return {"ok": False, "error": "缺少参数 account_id"}
        return await paper_toolkit.get_stats(int(payload["account_id"]))
    if tool_id == "list-orders":
        if payload.get("account_id") is None:
            return {"ok": False, "error": "缺少参数 account_id"}
        return await paper_toolkit.list_orders(
            int(payload["account_id"]),
            status=payload.get("status"),
            limit=int(payload.get("limit") or 50),
        )
    if tool_id == "list-fills":
        if payload.get("account_id") is None:
            return {"ok": False, "error": "缺少参数 account_id"}
        return await paper_toolkit.list_fills(
            int(payload["account_id"]),
            limit=int(payload.get("limit") or 50),
        )

    return {"ok": False, "error": f"未知工具: {tool_id}"}


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

    if tool_id == "get-fund-data":
        dataset = payload.get("dataset") or ""
        if not dataset:
            return {"ok": False, "error": "缺少参数 dataset"}
        write_back = payload.get("write_back")
        if write_back is None:
            write_back = True
        return await toolkit.get_fund_data_async(
            dataset=str(dataset),
            ts_code=payload.get("ts_code"),
            start_date=payload.get("start_date"),
            end_date=payload.get("end_date"),
            trade_date=payload.get("trade_date"),
            period=payload.get("period"),
            source=str(payload.get("source") or "auto"),
            write_back=bool(write_back),
            limit=int(payload.get("limit") or 500),
            content_type=str(payload.get("content_type") or "行业"),
        )

    if tool_id == "get-stock-sector-moneyflow":
        ts_code = payload.get("ts_code") or ""
        if not ts_code:
            return {"ok": False, "error": "缺少参数 ts_code"}
        write_back = payload.get("write_back")
        if write_back is None:
            write_back = True
        levels = payload.get("levels")
        if isinstance(levels, str):
            levels = [x.strip() for x in levels.replace("，", ",").split(",") if x.strip()]
        return await toolkit.get_stock_sector_moneyflow_async(
            ts_code=str(ts_code),
            trade_date=payload.get("trade_date"),
            levels=levels,
            source=str(payload.get("source") or "auto"),
            write_back=bool(write_back),
            limit=int(payload.get("limit") or 50),
        )

    return {"ok": False, "error": f"未知工具: {tool_id}"}

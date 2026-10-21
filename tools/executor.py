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

    if skill_id == "futures-market":
        return await _exec_futures_market(tool_id, payload)

    if skill_id == "kb-market" and tool_id == "today":
        from tools.kb_market_toolkit import kb_market_toolkit
        return kb_market_toolkit.build_today(payload.get("trade_date"))

    if skill_id == "auction-screen":
        return await _exec_auction_screen(tool_id, payload)

    if skill_id == "sector-intraday":
        return await _exec_sector_intraday(tool_id, payload)

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


async def _exec_futures_market(tool_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    from tools.futures_market_toolkit import futures_market_toolkit as tk

    if tool_id == "list-catalog":
        return await tk.list_catalog()
    if tool_id == "get-board":
        ensure = payload.get("ensure")
        if ensure is None:
            ensure = True
        return await tk.get_board(
            category=payload.get("category"),
            ensure=bool(ensure),
            force=bool(payload.get("force") or False),
        )
    if tool_id == "get-quotes":
        ensure = payload.get("ensure")
        if ensure is None:
            ensure = True
        return await tk.get_quotes(
            ts_codes=payload.get("ts_codes") or payload.get("ts_code"),
            category=payload.get("category"),
            ensure=bool(ensure),
        )
    if tool_id == "get-kline":
        ts_code = payload.get("ts_code")
        if not ts_code:
            return {"ok": False, "error": "缺少参数 ts_code"}
        ensure = payload.get("ensure")
        if ensure is None:
            ensure = True
        return await tk.get_kline(
            ts_code=str(ts_code),
            freq=str(payload.get("freq") or "daily"),
            start=payload.get("start"),
            end=payload.get("end"),
            limit=int(payload.get("limit") or 120),
            ensure=bool(ensure),
        )
    if tool_id == "check-coverage":
        return await tk.check_coverage(
            ts_codes=payload.get("ts_codes") or payload.get("ts_code"),
            category=payload.get("category"),
            asof=payload.get("asof"),
        )
    if tool_id == "sync-quotes":
        return await tk.sync_quotes(
            ts_codes=payload.get("ts_codes") or payload.get("ts_code"),
            category=payload.get("category"),
            asof=payload.get("asof"),
            lookback_days=int(payload.get("lookback_days") or 5),
            force=bool(payload.get("force") or False),
        )
    if tool_id == "ensure-and-get":
        return await tk.ensure_and_get(
            ts_codes=payload.get("ts_codes") or payload.get("ts_code"),
            category=payload.get("category"),
            force=bool(payload.get("force") or False),
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

    if tool_id in ("list-custom-tables", "list_custom_tables"):
        return await sql_toolkit.list_custom_tables()

    if tool_id in ("create-custom-table", "create_custom_table"):
        prompt = payload.get("prompt") or payload.get("question") or ""
        if not prompt:
            return {"ok": False, "error": "缺少参数 prompt（用自然语言描述要建的表）"}
        profile_id = payload.get("profile_id")
        dry_run = payload.get("dry_run")
        if isinstance(dry_run, str):
            dry_run = dry_run.strip().lower() in ("1", "true", "yes", "y")
        return await sql_toolkit.create_custom_table(
            str(prompt),
            profile_id=int(profile_id) if profile_id not in (None, "") else None,
            dry_run=bool(dry_run),
            table_name=payload.get("table_name"),
            user_id=str(payload.get("user_id") or "default"),
        )

    if tool_id in ("delete-custom-table", "delete_custom_table"):
        table_name = payload.get("table_name") or payload.get("name") or ""
        if not table_name:
            return {"ok": False, "error": "缺少参数 table_name（仅可删自己创建的 u_* 表）"}
        return await sql_toolkit.delete_custom_table(
            str(table_name),
            user_id=str(payload.get("user_id") or "default"),
        )

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

    if tool_id == "fill-sector-gap":
        trade_date = payload.get("trade_date") or ""
        if not trade_date:
            return {"ok": False, "error": "缺少参数 trade_date"}
        return await toolkit.fill_sector_gap_async(
            trade_date=str(trade_date),
            level=str(payload.get("level") or "东财二级行业"),
        )

    if tool_id == "fill-sector-history":
        ts_code = payload.get("ts_code") or ""
        start_date = payload.get("start_date") or ""
        end_date = payload.get("end_date") or ""
        if not ts_code or not start_date or not end_date:
            return {"ok": False, "error": "缺少 ts_code、start_date 或 end_date"}
        return await toolkit.fill_sector_history_async(
            ts_code=str(ts_code),
            start_date=str(start_date),
            end_date=str(end_date),
        )

    if tool_id == "fill-market-history":
        start_date = payload.get("start_date") or ""
        end_date = payload.get("end_date") or ""
        if not start_date or not end_date:
            return {"ok": False, "error": "缺少 start_date 或 end_date"}
        return await toolkit.fill_market_history_async(
            start_date=str(start_date),
            end_date=str(end_date),
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

    if tool_id in ("sanitize-ml-train-daily", "sanitize_ml_train_daily"):
        from tools.ml_train_sanitize_toolkit import ml_train_sanitize_toolkit

        return await ml_train_sanitize_toolkit.run_async(
            action=str(payload.get("action") or "audit"),
            limit=payload.get("limit"),
            offset=int(payload.get("offset") or 0),
            skip_gaps=payload.get("skip_gaps"),
            dry_run=payload.get("dry_run"),
            limit_rows=payload.get("limit_rows") or payload.get("limit"),
            from_row=payload.get("from_row"),
            fill_gaps_after=payload.get("fill_gaps_after"),
            max_batches=payload.get("max_batches"),
            reset=payload.get("reset"),
        )

    if tool_id in ("fill-market-universe", "fill_market_universe"):
        from tools.market_universe_toolkit import market_universe_toolkit

        sync_basic = payload.get("sync_stock_basic")
        if sync_basic is None:
            sync_basic = True
        return await market_universe_toolkit.run_async(
            action=str(payload.get("action") or "fill"),
            list_status=str(payload.get("list_status") or "L"),
            page_size=int(payload.get("page_size") or payload.get("limit") or 5000),
            sync_stock_basic=bool(sync_basic),
            max_pages=int(payload.get("max_pages") or 20),
        )

    if tool_id in ("fill-ml-train-daily", "fill_ml_train_daily"):
        from tools.ml_train_toolkit import ml_train_toolkit

        return await ml_train_toolkit.run_async(
            action=str(payload.get("action") or "check_env"),
            ts_code=payload.get("ts_code"),
            start_date=payload.get("start_date") or "2016-01-01",
            end_date=payload.get("end_date"),
            limit=int(payload.get("limit") or 1),
            offset=int(payload.get("offset") or 0),
            skip_env_check=bool(payload.get("skip_env_check") or False),
            skip_existing=False if payload.get("skip_existing") is False else True,
            only_mainboard=bool(payload.get("only_mainboard") or False),
            upsert_chunk=int(payload.get("upsert_chunk") or 500),
            fetch_index=False if payload.get("fetch_index") is False else True,
            require_ram_gb=payload.get("require_ram_gb"),
            ts_codes=payload.get("ts_codes"),
            limit_days=payload.get("limit_days"),
            allow_defaults=False if payload.get("allow_defaults") is False else True,
            fetch_sw_proxy=False if payload.get("fetch_sw_proxy") is False else True,
            from_row=payload.get("from_row"),
            reset=payload.get("reset"),
            auto_fill=payload.get("auto_fill"),
        )

    if tool_id in ("fill-mainboard-mins", "fill_mainboard_mins"):
        write_back = payload.get("write_back")
        if write_back is None:
            write_back = True
        include_auction = payload.get("include_auction")
        if include_auction is None:
            include_auction = True
        skip_existing = payload.get("skip_existing")
        if skip_existing is None:
            skip_existing = True
        only_traded = payload.get("only_traded")
        if only_traded is None:
            only_traded = True
        exclude_st = payload.get("exclude_st")
        if exclude_st is None:
            exclude_st = True
        limit = payload.get("limit")
        return await toolkit.fill_mainboard_mins_async(
            trade_date=payload.get("trade_date"),
            action=str(payload.get("action") or "fill"),
            source=str(payload.get("source") or "auto"),
            write_back=bool(write_back),
            include_auction=bool(include_auction),
            skip_existing=bool(skip_existing),
            min_bars=int(payload.get("min_bars") or 60),
            max_workers=int(payload.get("max_workers") or 6),
            limit=int(limit) if limit not in (None, "") else None,
            offset=int(payload.get("offset") or 0),
            only_traded=bool(only_traded),
            exclude_st=bool(exclude_st),
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


async def _exec_auction_screen(tool_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    import asyncio

    from tools.auction_screen_toolkit import auction_screen_toolkit

    if tool_id in ("mainboard-buy", "mainboard_buy"):
        return await asyncio.to_thread(
            auction_screen_toolkit.screen_mainboard_buy,
            payload.get("trade_date"),
            int(payload.get("top_n") or 20),
            float(payload.get("strong_pct") or 5.0),
            int(payload.get("strong_top_n") or 80),
            bool(payload["fetch_missing"]) if "fetch_missing" in payload else True,
        )
    return {"ok": False, "error": f"未知工具: {tool_id}"}


async def _exec_sector_intraday(tool_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    import asyncio

    from tools.sector_intraday_toolkit import sector_intraday_toolkit

    if tool_id in ("l2-tone", "l2_tone"):
        return await asyncio.to_thread(
            sector_intraday_toolkit.l2_intraday_tone,
            payload.get("trade_date"),
            int(payload.get("top_n") or 10),
            int(payload.get("member_limit") or 15),
            int(payload.get("sample_every_min") or 5),
        )
    return {"ok": False, "error": f"未知工具: {tool_id}"}

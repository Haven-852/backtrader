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

from agents.strategy.strategy_t0.skill_spec import T0_SKILL  # noqa: E402
from tools.quantskill_runtime import build_all_quantskill_entries  # noqa: E402

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
    },
    "datahubco-data": {
        "id": "datahubco-data",
        "name": "Datahubco 行情补数",
        "icon": "cloud-download",
        "category": "数据获取",
                "desc": (
                    "PostgreSQL 优先读取日线/分钟行情与资金基本面；库内缺失时自动用 Datahubco 拉取、入库并返回。"
                    "支持龙虎榜/大宗/两融/北向/资金流/现金流/业绩预告/十大股东及个股二级三级板块资金。"
                ),
        "enabled": True,
        "tools": [
            {
                "id": "list-datasets",
                "name": "📚 列出数据集",
                "desc": "查看支持的 dataset、对应 PG 表与 Datahubco API，以及 source 模式说明",
                "params": [],
                "returns": "datasets / sources",
                "openai_parameters": {"type": "object", "properties": {}, "required": []},
            },
            {
                "id": "coverage",
                "name": "🔎 覆盖检查",
                "desc": "只查 PostgreSQL 是否已有该代码的行情覆盖，不触发远端",
                "params": [
                    {"name": "ts_code", "label": "代码", "type": "text", "required": True, "placeholder": "000001.SZ"},
                    {"name": "dataset", "label": "数据集", "type": "text", "required": False, "placeholder": "stock_daily"},
                    {"name": "start_date", "label": "开始日", "type": "text", "required": False, "placeholder": "20240101"},
                    {"name": "end_date", "label": "结束日", "type": "text", "required": False, "placeholder": "20241231"},
                ],
                "returns": "coverage / sufficient",
                "openai_parameters": {
                    "type": "object",
                    "properties": {
                        "ts_code": {"type": "string", "description": "股票/指数/板块代码，如 000001.SZ"},
                        "dataset": {
                            "type": "string",
                            "description": "stock_daily/stock_weekly/stock_monthly/stock_mins/index_daily/etf_daily/sector_dc_index/sector_dc_daily",
                            "default": "stock_daily",
                        },
                        "start_date": {"type": "string", "description": "YYYYMMDD 或 YYYY-MM-DD"},
                        "end_date": {"type": "string", "description": "YYYYMMDD 或 YYYY-MM-DD"},
                        "trade_date": {"type": "string", "description": "分钟数据用交易日 YYYYMMDD"},
                    },
                    "required": ["ts_code"],
                },
            },
            {
                "id": "get-bars",
                "name": "📈 获取行情",
                "desc": (
                    "默认 source=auto：库内有则直接返回；不足则 Datahubco 拉取并写入 PG 再返回。"
                    "source=pg 只读库；source=datahubco 强制远端，write_back 控制是否入库。"
                    "分钟分时请优先用 get-mins，或 dataset=stock_mins。"
                ),
                "params": [
                    {"name": "ts_code", "label": "代码", "type": "text", "required": True, "placeholder": "600519.SH"},
                    {"name": "dataset", "label": "数据集", "type": "text", "required": False, "placeholder": "stock_daily"},
                    {"name": "start_date", "label": "开始日", "type": "text", "required": False, "placeholder": "20240101"},
                    {"name": "end_date", "label": "结束日", "type": "text", "required": False, "placeholder": "20241231"},
                    {"name": "limit", "label": "条数", "type": "number", "required": False, "placeholder": "120"},
                    {"name": "source", "label": "来源", "type": "text", "required": False, "placeholder": "auto|pg|datahubco"},
                    {"name": "write_back", "label": "写回库", "type": "boolean", "required": False, "placeholder": "true"},
                ],
                "returns": "data / source_used / upserted",
                "openai_parameters": {
                    "type": "object",
                    "properties": {
                        "ts_code": {"type": "string", "description": "代码，缺后缀会自动补 .SH/.SZ"},
                        "dataset": {
                            "type": "string",
                            "description": "stock_daily（默认）/ weekly / monthly / stock_mins / index_daily / etf_daily / sector_dc_index / sector_dc_daily",
                            "default": "stock_daily",
                        },
                        "start_date": {"type": "string", "description": "可选 YYYYMMDD"},
                        "end_date": {"type": "string", "description": "可选 YYYYMMDD"},
                        "trade_date": {"type": "string", "description": "dataset=stock_mins 时的交易日"},
                        "limit": {"type": "integer", "description": "返回条数上限，默认 120，最大 2000", "default": 120},
                        "source": {
                            "type": "string",
                            "enum": ["auto", "pg", "datahubco"],
                            "description": "auto=库优先缺则补；pg=只读库；datahubco=强制远端",
                            "default": "auto",
                        },
                        "write_back": {
                            "type": "boolean",
                            "description": "远端拉取后是否写入 PostgreSQL，默认 true",
                            "default": True,
                        },
                        "auto_sync": {
                            "type": "boolean",
                            "description": "auto 模式下是否在覆盖不足时自动补数，默认 true",
                            "default": True,
                        },
                    },
                    "required": ["ts_code"],
                },
            },
            {
                "id": "get-mins",
                "name": "⏱️ 获取分钟分时",
                "desc": (
                    "获取个股 1 分钟分时（stock_intraday_mins）。"
                    "默认 source=auto：库内足够直接返回；不足则 Datahubco/当日实时源拉取并写入 Timescale。"
                    "source=pg 只读库；source=datahubco 强制远端。"
                ),
                "params": [
                    {"name": "ts_code", "label": "代码", "type": "text", "required": True, "placeholder": "000001.SZ"},
                    {"name": "trade_date", "label": "交易日", "type": "text", "required": False, "placeholder": "20260917"},
                    {"name": "limit", "label": "条数", "type": "number", "required": False, "placeholder": "500"},
                    {"name": "source", "label": "来源", "type": "text", "required": False, "placeholder": "auto|pg|datahubco"},
                    {"name": "write_back", "label": "写回库", "type": "boolean", "required": False, "placeholder": "true"},
                    {"name": "include_auction", "label": "含集合竞价", "type": "boolean", "required": False, "placeholder": "true"},
                ],
                "returns": "data / source_used / upserted",
                "openai_parameters": {
                    "type": "object",
                    "properties": {
                        "ts_code": {"type": "string", "description": "股票代码，如 000001.SZ 或 000001"},
                        "trade_date": {
                            "type": "string",
                            "description": "交易日 YYYYMMDD / YYYY-MM-DD；省略则用当前会话交易日",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "返回分钟根数上限，默认 500（约一天），最大 2500",
                            "default": 500,
                        },
                        "source": {
                            "type": "string",
                            "enum": ["auto", "pg", "datahubco"],
                            "description": "auto=库优先缺则补；pg=只读库；datahubco=强制远端",
                            "default": "auto",
                        },
                        "write_back": {
                            "type": "boolean",
                            "description": "远端拉取后是否写入 stock_intraday_mins，默认 true",
                            "default": True,
                        },
                        "include_auction": {
                            "type": "boolean",
                            "description": "是否包含集合竞价分钟，默认 true",
                            "default": True,
                        },
                    },
                    "required": ["ts_code"],
                },
            },
            {
                "id": "ensure-and-get",
                "name": "✅ 确保并获取",
                "desc": "快捷：等价于 get-bars/get-mins(source=auto, write_back=true)，确保有数据后返回",
                "params": [
                    {"name": "ts_code", "label": "代码", "type": "text", "required": True, "placeholder": "000001.SZ"},
                    {"name": "dataset", "label": "数据集", "type": "text", "required": False, "placeholder": "stock_daily 或 stock_mins"},
                    {"name": "limit", "label": "条数", "type": "number", "required": False, "placeholder": "120"},
                    {"name": "trade_date", "label": "交易日", "type": "text", "required": False, "placeholder": "分钟数据用"},
                ],
                "returns": "data / upserted",
                "openai_parameters": {
                    "type": "object",
                    "properties": {
                        "ts_code": {"type": "string", "description": "代码"},
                        "dataset": {
                            "type": "string",
                            "description": "默认 stock_daily；分钟用 stock_mins",
                            "default": "stock_daily",
                        },
                        "limit": {"type": "integer", "description": "默认 120（分钟建议 500）", "default": 120},
                        "trade_date": {"type": "string", "description": "dataset=stock_mins 时的交易日"},
                    },
                    "required": ["ts_code"],
                },
            },
            {
                "id": "get-sector-members",
                "name": "🏢 板块成分股",
                "desc": (
                    "读取东财行业板块成分股（dc_member）。"
                    "默认库优先，缺则 Datahubco 拉取并写入 sector_dc_member；"
                    "返回成分列表并标注 is_st / is_suspended。"
                ),
                "params": [
                    {"name": "ts_code", "label": "板块代码", "type": "text", "required": True, "placeholder": "BK1035.DC"},
                    {"name": "trade_date", "label": "交易日", "type": "text", "required": False, "placeholder": "20260918"},
                    {"name": "source", "label": "来源", "type": "text", "required": False, "placeholder": "auto|pg|datahubco"},
                    {"name": "write_back", "label": "写回库", "type": "boolean", "required": False, "placeholder": "true"},
                    {"name": "limit", "label": "条数", "type": "number", "required": False, "placeholder": "500"},
                ],
                "returns": "members / st_count / suspended_count",
                "openai_parameters": {
                    "type": "object",
                    "properties": {
                        "ts_code": {
                            "type": "string",
                            "description": "东财板块代码，如 BK1035.DC 或 BK1035",
                        },
                        "trade_date": {
                            "type": "string",
                            "description": "交易日 YYYYMMDD / YYYY-MM-DD；省略用会话 asof",
                        },
                        "source": {
                            "type": "string",
                            "enum": ["auto", "pg", "datahubco"],
                            "description": "auto=库优先缺则补；pg=只读库；datahubco=强制远端",
                            "default": "auto",
                        },
                        "write_back": {
                            "type": "boolean",
                            "description": "远端拉取后是否写入 sector_dc_member，默认 true",
                            "default": True,
                        },
                        "limit": {
                            "type": "integer",
                            "description": "返回成分上限，默认 500",
                            "default": 500,
                        },
                        "include_flags": {
                            "type": "boolean",
                            "description": "是否标注 is_st / is_suspended，默认 true",
                            "default": True,
                        },
                    },
                    "required": ["ts_code"],
                },
            },
            {
                "id": "filter-st-suspend",
                "name": "🧹 剔除ST与停牌",
                "desc": (
                    "从板块成分或股票列表中剔除 ST 股与停牌股。"
                    "可传板块 ts_code，或 ts_codes / members；"
                    "返回 kept（可交易）与 removed（被剔除）。"
                ),
                "params": [
                    {"name": "ts_code", "label": "板块代码", "type": "text", "required": False, "placeholder": "BK1035.DC"},
                    {"name": "trade_date", "label": "交易日", "type": "text", "required": False, "placeholder": "20260918"},
                    {"name": "exclude_st", "label": "剔除ST", "type": "boolean", "required": False, "placeholder": "true"},
                    {"name": "exclude_suspend", "label": "剔除停牌", "type": "boolean", "required": False, "placeholder": "true"},
                ],
                "returns": "kept / removed / ts_codes_kept",
                "openai_parameters": {
                    "type": "object",
                    "properties": {
                        "ts_code": {
                            "type": "string",
                            "description": "东财板块代码；与 ts_codes/members 三选一",
                        },
                        "ts_codes": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "股票代码列表，如 [\"000001.SZ\",\"600519.SH\"]",
                        },
                        "members": {
                            "type": "array",
                            "items": {"type": "object"},
                            "description": "已有成分对象列表（需含 ts_code，可选 name）",
                        },
                        "trade_date": {
                            "type": "string",
                            "description": "判断停牌用的交易日",
                        },
                        "exclude_st": {
                            "type": "boolean",
                            "description": "是否剔除 ST/*ST，默认 true",
                            "default": True,
                        },
                        "exclude_suspend": {
                            "type": "boolean",
                            "description": "是否剔除停牌，默认 true",
                            "default": True,
                        },
                        "source": {
                            "type": "string",
                            "enum": ["auto", "pg", "datahubco"],
                            "description": "按板块拉取成分时的来源，默认 auto",
                            "default": "auto",
                        },
                        "write_back": {
                            "type": "boolean",
                            "description": "按板块缺数补拉时是否入库，默认 true",
                            "default": True,
                        },
                        "limit": {
                            "type": "integer",
                            "description": "返回条数上限，默认 500",
                            "default": 500,
                        },
                        "return_removed": {
                            "type": "boolean",
                            "description": "是否返回被剔除列表，默认 true",
                            "default": True,
                        },
                    },
                    "required": [],
                },
            },
            {
                "id": "get-fund-data",
                "name": "💰 资金与基本面",
                "desc": (
                    "拉取龙虎榜/大宗交易/融资融券/北向持股/个股资金流/"
                    "现金流量表/业绩预告/十大股东/板块资金，并写入对应 PostgreSQL 表。"
                    "默认 source=auto：库优先，不足则 Datahubco 补数入库。"
                ),
                "params": [
                    {"name": "dataset", "label": "数据集", "type": "text", "required": True, "placeholder": "moneyflow|top_list|block_trade|..."},
                    {"name": "ts_code", "label": "代码", "type": "text", "required": False, "placeholder": "000001.SZ"},
                    {"name": "trade_date", "label": "交易日", "type": "text", "required": False, "placeholder": "20260918"},
                    {"name": "start_date", "label": "开始日", "type": "text", "required": False, "placeholder": "20240101"},
                    {"name": "end_date", "label": "结束日", "type": "text", "required": False, "placeholder": "20241231"},
                    {"name": "source", "label": "来源", "type": "text", "required": False, "placeholder": "auto|pg|datahubco"},
                    {"name": "write_back", "label": "写回库", "type": "boolean", "required": False, "placeholder": "true"},
                ],
                "returns": "data / upserted / table",
                "openai_parameters": {
                    "type": "object",
                    "properties": {
                        "dataset": {
                            "type": "string",
                            "description": (
                                "top_list(龙虎榜)/block_trade(大宗)/margin(两融汇总)/"
                                "margin_detail(两融明细)/moneyflow(资金流)/hk_hold(北向持股)/"
                                "cashflow(现金流量表)/forecast(业绩预告)/top10_holders(十大股东)/"
                                "sector_moneyflow(板块资金)"
                            ),
                            "enum": [
                                "top_list", "block_trade", "margin", "margin_detail",
                                "moneyflow", "hk_hold", "cashflow", "forecast",
                                "top10_holders", "sector_moneyflow",
                            ],
                        },
                        "ts_code": {
                            "type": "string",
                            "description": "个股代码；margin/sector_moneyflow/top_list全日可不传",
                        },
                        "trade_date": {"type": "string", "description": "交易日 YYYYMMDD（龙虎榜/大宗/两融汇总等）"},
                        "start_date": {"type": "string", "description": "开始日 YYYYMMDD"},
                        "end_date": {"type": "string", "description": "结束日 YYYYMMDD"},
                        "period": {"type": "string", "description": "财报/股东报告期，如 20241231"},
                        "source": {
                            "type": "string",
                            "enum": ["auto", "pg", "datahubco"],
                            "description": "auto=库优先缺则补；pg=只读库；datahubco=强制远端",
                            "default": "auto",
                        },
                        "write_back": {
                            "type": "boolean",
                            "description": "远端拉取后是否写入 PostgreSQL，默认 true",
                            "default": True,
                        },
                        "limit": {"type": "integer", "description": "返回条数上限，默认 500", "default": 500},
                        "content_type": {
                            "type": "string",
                            "description": "板块资金 content_type，默认 行业",
                            "default": "行业",
                        },
                    },
                    "required": ["dataset"],
                },
            },
            {
                "id": "get-stock-sector-moneyflow",
                "name": "🏷 个股板块资金",
                "desc": (
                    "根据成分关系找出个股所属东财二级/三级行业板块，"
                    "返回这些板块在 sector_moneyflow_daily 中的资金走向；缺数时尝试补拉入库。"
                ),
                "params": [
                    {"name": "ts_code", "label": "股票代码", "type": "text", "required": True, "placeholder": "000001.SZ"},
                    {"name": "trade_date", "label": "交易日", "type": "text", "required": False, "placeholder": "20260918"},
                    {"name": "source", "label": "来源", "type": "text", "required": False, "placeholder": "auto|pg|datahubco"},
                    {"name": "write_back", "label": "写回库", "type": "boolean", "required": False, "placeholder": "true"},
                ],
                "returns": "sectors / moneyflow",
                "openai_parameters": {
                    "type": "object",
                    "properties": {
                        "ts_code": {"type": "string", "description": "股票代码，如 000001.SZ"},
                        "trade_date": {"type": "string", "description": "交易日 YYYYMMDD；省略用会话 asof"},
                        "levels": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "默认 [\"东财二级行业\",\"东财三级行业\"]",
                        },
                        "source": {
                            "type": "string",
                            "enum": ["auto", "pg", "datahubco"],
                            "default": "auto",
                        },
                        "write_back": {"type": "boolean", "default": True},
                        "limit": {"type": "integer", "default": 50},
                    },
                    "required": ["ts_code"],
                },
            },
            {
                "id": "remote-query",
                "name": "🌐 直接调 Datahubco",
                "desc": "按 api 名直接请求 Datahubco OpenAPI；可选 write_dataset 写回对应 PG 表",
                "params": [
                    {"name": "api", "label": "API", "type": "text", "required": True, "placeholder": "daily 或 stk_mins"},
                    {"name": "params", "label": "参数 JSON", "type": "textarea", "required": False, "placeholder": "{\"ts_code\":\"000001.SZ\",\"start_date\":\"20240101\"}"},
                    {"name": "write_dataset", "label": "写回数据集", "type": "text", "required": False, "placeholder": "stock_daily"},
                    {"name": "limit", "label": "返回条数", "type": "number", "required": False, "placeholder": "200"},
                ],
                "returns": "data / upserted",
                "openai_parameters": {
                    "type": "object",
                    "properties": {
                        "api": {"type": "string", "description": "Datahubco API，如 daily / weekly / stk_mins / index_daily / dc_index / dc_daily / dc_member"},
                        "params": {
                            "type": "object",
                            "description": "查询参数对象，如 {\"ts_code\":\"000001.SZ\",\"start_date\":\"20240101\",\"end_date\":\"20241231\"}",
                        },
                        "write_dataset": {
                            "type": "string",
                            "description": "可选，写入的 dataset 名（如 stock_daily / stock_mins）",
                        },
                        "limit": {"type": "integer", "description": "返回记录上限，默认 200", "default": 200},
                    },
                    "required": ["api"],
                },
            },
        ],
        "instructions": """
**Datahubco 行情补数**：需要个股/指数/ETF/板块行情或分钟分时时优先用本技能，不要先 list-tables。

1. 日线/周线/月线：`get-bars` / `ensure-and-get`（source=auto）：库里有就直接返回；没有或过旧则拉 Datahubco → 写入 PG → 返回。
2. **1 分钟分时**：优先 `get-mins`（或 `get-bars` + dataset=stock_mins）。
3. 东财板块成分：`get-sector-members` → 可选 `filter-st-suspend`。
4. 龙虎榜/大宗/两融/北向/资金流/现金流/业绩预告/十大股东：`get-fund-data`（dataset=...）。
5. 个股所属二级/三级板块资金：`get-stock-sector-moneyflow`。
6. 只要库内结果：`source=pg`；强制远端：`source=datahubco`；不入库设 `write_back=false`。
7. 先确认覆盖可用 `coverage`；任意 OpenAPI 用 `remote-query`。
""",
    },
    "t0-intraday": T0_SKILL,
    "paper-trade": {
        "id": "paper-trade",
        "name": "纸面交易",
        "icon": "wallet",
        "category": "模拟交易",
        "desc": (
            "虚拟账户模拟交易：开户、查仓、用 1 分钟分时盯市盈亏、感知日线、下单（次日开盘/当日收盘）、"
            "结算 pending、成交统计。盘中盈亏禁止用日线估算。仅纸面成交，不连接真实券商。"
        ),
        "enabled": True,
        "tools": [
            {
                "id": "create-account",
                "name": "开户",
                "desc": "创建纸面账户，设定初始资金与默认 lookback_guard",
                "params": [
                    {"name": "name", "label": "账户名", "type": "text", "required": False},
                    {"name": "initial_cash", "label": "初始资金", "type": "number", "required": False},
                    {"name": "lookback_guard", "label": "前视保护", "type": "boolean", "required": False},
                ],
                "returns": "账户对象",
                "openai_parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "账户名称"},
                        "initial_cash": {"type": "number", "description": "初始资金，默认 1000000"},
                        "lookback_guard": {
                            "type": "boolean",
                            "description": "True=次日开盘成交；False=当日收盘成交",
                        },
                    },
                    "required": [],
                },
            },
            {
                "id": "delete-account",
                "name": "删除账户",
                "desc": "删除纸面账户。hard=true 物理删除并级联清理；hard=false 仅关闭",
                "params": [
                    {"name": "account_id", "label": "账户ID", "type": "number", "required": True},
                    {"name": "hard", "label": "物理删除", "type": "boolean", "required": False},
                ],
                "returns": "删除结果",
                "openai_parameters": {
                    "type": "object",
                    "properties": {
                        "account_id": {"type": "integer", "description": "要删除的账户 ID"},
                        "hard": {
                            "type": "boolean",
                            "description": "true=物理删除（默认）；false=仅关闭",
                        },
                    },
                    "required": ["account_id"],
                },
            },
            {
                "id": "roll-t1",
                "name": "T+1日切",
                "desc": "将冻结仓(frozen_qty)转为可卖仓(sellable_qty)；新交易日开盘前调用",
                "params": [
                    {"name": "account_id", "label": "账户ID", "type": "number", "required": False},
                    {"name": "asof", "label": "日切日期", "type": "text", "required": False},
                ],
                "returns": "日切结果",
                "openai_parameters": {
                    "type": "object",
                    "properties": {
                        "account_id": {"type": "integer", "description": "不传则全部活跃账户"},
                        "asof": {"type": "string", "description": "YYYY-MM-DD"},
                        "force": {"type": "boolean", "description": "同日已切过是否强制"},
                    },
                    "required": [],
                },
            },
            {
                "id": "list-accounts",
                "name": "账户列表",
                "desc": "列出纸面账户",
                "params": [{"name": "status", "label": "状态", "type": "text", "required": False}],
                "returns": "账户列表",
                "openai_parameters": {
                    "type": "object",
                    "properties": {
                        "status": {"type": "string", "description": "active/closed，空=全部"},
                    },
                    "required": [],
                },
            },
            {
                "id": "get-account",
                "name": "账户详情",
                "desc": "查询账户现金/净值及持仓。盯市默认用 1 分钟分时末价；无分时则 pnl_ready=false，不会用收盘价冒充。",
                "params": [
                    {"name": "account_id", "label": "账户ID", "type": "number", "required": True},
                ],
                "returns": "账户+持仓",
                "openai_parameters": {
                    "type": "object",
                    "properties": {
                        "account_id": {"type": "integer", "description": "纸面账户 ID"},
                        "mark": {"type": "boolean", "description": "是否用分时末价盯市，默认 true；无分时不计盘中盈亏"},
                    },
                    "required": ["account_id"],
                },
            },
            {
                "id": "list-positions",
                "name": "持仓列表",
                "desc": "查询账户持仓：用对应分时末价计算浮动盈亏；无分时报 missing，不用日线估算",
                "params": [
                    {"name": "account_id", "label": "账户ID", "type": "number", "required": True},
                ],
                "returns": "持仓列表",
                "openai_parameters": {
                    "type": "object",
                    "properties": {
                        "account_id": {"type": "integer"},
                    },
                    "required": ["account_id"],
                },
            },
            {
                "id": "get-journal",
                "name": "当日操作日志",
                "desc": "查看账户当日日志：一键日课还是股价/对话唤醒、模型观察状态、唤醒次数、成交与持仓",
                "params": [
                    {"name": "account_id", "label": "账户ID", "type": "number", "required": True},
                    {"name": "trade_date", "label": "交易日", "type": "text", "required": False},
                ],
                "returns": "当日摘要+事件+持仓",
                "openai_parameters": {
                    "type": "object",
                    "properties": {
                        "account_id": {"type": "integer"},
                        "trade_date": {"type": "string", "description": "YYYY-MM-DD，默认上海当天"},
                    },
                    "required": ["account_id"],
                },
            },
            {
                "id": "get-market-brief",
                "name": "市场感知",
                "desc": "读取标的日线感知：regime、均线/RSI/MACD、近端K线。这不是盘中价，盈亏请用 get-intraday-quotes / get-account。",
                "params": [
                    {"name": "ts_code", "label": "标的代码", "type": "text", "required": True},
                ],
                "returns": "感知摘要",
                "openai_parameters": {
                    "type": "object",
                    "properties": {
                        "ts_code": {"type": "string", "description": "如 000001.SZ / 300750.SZ"},
                        "limit": {"type": "integer", "description": "回看日线根数，默认 120"},
                        "features": {
                            "type": "string",
                            "description": "逗号分隔特征，默认 ma,rsi,macd,vol_pctile",
                        },
                    },
                    "required": ["ts_code"],
                },
            },
            {
                "id": "get-intraday-quotes",
                "name": "分时报价",
                "desc": "取 1 分钟分时末价（盘中截至当前分钟）。用于持仓盘中盈亏。无分时报缺，禁止用日线估算。",
                "params": [
                    {"name": "account_id", "label": "账户ID", "type": "number", "required": False},
                    {"name": "ts_code", "label": "标的代码", "type": "text", "required": False},
                    {"name": "ts_codes", "label": "多个代码", "type": "text", "required": False},
                ],
                "returns": "分时末价列表",
                "openai_parameters": {
                    "type": "object",
                    "properties": {
                        "account_id": {"type": "integer", "description": "传入则对该账户全部持仓取分时价"},
                        "ts_code": {"type": "string", "description": "单个标的，如 000001.SZ"},
                        "ts_codes": {"type": "string", "description": "逗号分隔多个代码"},
                        "trade_date": {"type": "string", "description": "交易日 YYYY-MM-DD，默认当前分时交易日"},
                    },
                    "required": [],
                },
            },
            {
                "id": "place-order",
                "name": "下单",
                "desc": "纸面下单。buy/sell；买入数量须为100整数倍；reason 写清决策理由",
                "params": [
                    {"name": "account_id", "label": "账户ID", "type": "number", "required": True},
                    {"name": "ts_code", "label": "标的", "type": "text", "required": True},
                    {"name": "side", "label": "方向", "type": "text", "required": True},
                    {"name": "quantity", "label": "数量", "type": "number", "required": True},
                    {"name": "reason", "label": "理由", "type": "text", "required": False},
                ],
                "returns": "订单/成交",
                "openai_parameters": {
                    "type": "object",
                    "properties": {
                        "account_id": {"type": "integer"},
                        "ts_code": {"type": "string"},
                        "side": {"type": "string", "enum": ["buy", "sell"]},
                        "quantity": {"type": "number", "description": "股数；买入须为100整数倍"},
                        "lookback_guard": {
                            "type": "boolean",
                            "description": "覆盖账户默认；True次日开盘，False当日收盘",
                        },
                        "signal_date": {"type": "string", "description": "信号日 YYYY-MM-DD"},
                        "reason": {"type": "string", "description": "决策理由（审计用，建议必填）"},
                    },
                    "required": ["account_id", "ts_code", "side", "quantity"],
                },
            },
            {
                "id": "settle",
                "name": "结算挂单",
                "desc": "结算 lookback_guard 产生的 pending（次日开盘成交）",
                "params": [
                    {"name": "account_id", "label": "账户ID", "type": "number", "required": False},
                ],
                "returns": "结算结果",
                "openai_parameters": {
                    "type": "object",
                    "properties": {
                        "account_id": {"type": "integer", "description": "不传则结算全部账户"},
                        "asof": {"type": "string", "description": "仅结算 exec_date<=asof"},
                    },
                    "required": [],
                },
            },
            {
                "id": "mark",
                "name": "盯市",
                "desc": "按 1 分钟分时末价更新净值。没有分时的持仓不计盘中盈亏，不会回退到日线收盘价。",
                "params": [
                    {"name": "account_id", "label": "账户ID", "type": "number", "required": True},
                ],
                "returns": "盯市结果",
                "openai_parameters": {
                    "type": "object",
                    "properties": {
                        "account_id": {"type": "integer"},
                        "asof": {"type": "string"},
                        "write_equity": {"type": "boolean"},
                    },
                    "required": ["account_id"],
                },
            },
            {
                "id": "get-stats",
                "name": "交易统计",
                "desc": "收益、回撤、胜率、买卖笔数等",
                "params": [
                    {"name": "account_id", "label": "账户ID", "type": "number", "required": True},
                ],
                "returns": "统计",
                "openai_parameters": {
                    "type": "object",
                    "properties": {"account_id": {"type": "integer"}},
                    "required": ["account_id"],
                },
            },
            {
                "id": "list-orders",
                "name": "委托列表",
                "desc": "查询委托（可按 status 过滤）",
                "params": [
                    {"name": "account_id", "label": "账户ID", "type": "number", "required": True},
                ],
                "returns": "委托列表",
                "openai_parameters": {
                    "type": "object",
                    "properties": {
                        "account_id": {"type": "integer"},
                        "status": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                    "required": ["account_id"],
                },
            },
            {
                "id": "list-fills",
                "name": "成交列表",
                "desc": "查询成交记录",
                "params": [
                    {"name": "account_id", "label": "账户ID", "type": "number", "required": True},
                ],
                "returns": "成交列表",
                "openai_parameters": {
                    "type": "object",
                    "properties": {
                        "account_id": {"type": "integer"},
                        "limit": {"type": "integer"},
                    },
                    "required": ["account_id"],
                },
            },
        ],
    },
    "kb-market": {
        "id": "kb-market",
        "name": "知识库-市场记忆-今日",
        "icon": "book",
        "category": "知识库",
        "desc": "读取当日行情事实，生成可写入市场记忆的草稿。只生成，不直接入库。",
        "enabled": True,
        "tools": [
            {
                "id": "today",
                "name": "知识库-市场记忆-今日",
                "desc": "从 Datahubco 读取当天全市场指数、成交额、涨跌家数、涨跌停和行业热点，生成市场记忆草稿。确认后再由对话里的按钮写入。",
                "params": [
                    {
                        "name": "trade_date",
                        "label": "交易日",
                        "type": "text",
                        "required": False,
                        "placeholder": "留空=今天；也可填 YYYY-MM-DD",
                    }
                ],
                "returns": "市场记忆草稿 memory_draft",
                "openai_parameters": {
                    "type": "object",
                    "properties": {
                        "trade_date": {
                            "type": "string",
                            "description": "交易日 YYYY-MM-DD，留空表示上海时区今天",
                        }
                    },
                    "required": [],
                },
            },
        ],
    },
}

# QuantSkills 外部技能包（市场复盘 / 期货 DeepView / A股个股档案）
SKILL_REGISTRY.update(build_all_quantskill_entries())


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


def get_openai_tools(skill_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """生成 OpenAI / DeepSeek function calling tools 列表；可按 skill_ids 过滤"""
    allow = set(skill_ids) if skill_ids else None
    tools: List[Dict[str, Any]] = []
    for skill in SKILL_REGISTRY.values():
        if not skill.get("enabled", True):
            continue
        if allow is not None and skill["id"] not in allow:
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


def build_agent_system_prompt(
    extra: Optional[str] = None,
    *,
    skill_ids: Optional[List[str]] = None,
    mode: Optional[str] = None,
) -> str:
    if mode == "paper_trade":
        from tools.paper_toolkit import PAPER_TRADE_SYSTEM_PROMPT

        skill_ids = skill_ids or ["paper-trade"]
        catalog_lines = []
        for skill in list_skills():
            if skill["id"] not in skill_ids:
                continue
            catalog_lines.append(f"### {skill['name']} (`{skill['id']}`)")
            catalog_lines.append(skill["desc"])
            for t in skill.get("tools", []):
                catalog_lines.append(
                    f"- `{_fn_name(skill['id'], t['id'])}` — {t.get('name')}: {t.get('desc')}"
                )
            catalog_lines.append("")

        prompt = f"""{PAPER_TRADE_SYSTEM_PROMPT}

## 可调用函数一览
{chr(10).join(catalog_lines)}
"""
        if extra:
            prompt += f"\n## 额外系统说明\n{extra}\n"
        return prompt

    handbook = get_handbook()
    catalog_lines = []
    for skill in list_skills():
        if skill_ids is not None and skill["id"] not in skill_ids:
            continue
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
1. 缺数据 → 先选手册中的**专用技能**。测某日高低命中：先 `t0_intraday__load_day`，再 `t0_intraday__hit_test`（含 A/B/C/D 组合）。禁止先 list_tables，禁止用 SQL 代替 hit-test。结论看 combo_ranking。
2. 需要个股/指数/ETF/板块日线或 **1 分钟分时**：优先 `datahubco_data__get_bars` / `datahubco_data__get_mins`（默认库优先，缺则 Datahubco 补数入库）。不要先 list_tables。
2b. 龙虎榜/大宗/两融/北向/资金流/现金流/业绩预告/十大股东：`datahubco_data__get_fund_data`；个股二级/三级板块资金：`datahubco_data__get_stock_sector_moneyflow`。
2c. 用户要生成今日市场记忆、今日行情复盘并准备写入知识库时，调用 `kb_market__today`（知识库-市场记忆-今日）。用工具返回的事实写给用户看。禁止编造涨停家数和成交额。禁止声称已经写入知识库，写入由用户点击「填入市场记忆」完成。
3. 只有没有专用技能、且不知道表名时，才 list_tables / info_table。
4. 拿到工具结果后必须用中文给出结论（卖点、依据、捕获率）；不要为了「再确认」继续查 SQL。
5. 若工具失败，说明原因并给出下一步建议。
"""
    if extra:
        prompt += f"\n## 额外系统说明\n{extra}\n"
    return prompt

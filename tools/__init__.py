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
                "id": "list-custom-tables",
                "name": "🧩 自定义表目录",
                "desc": "列出用户通过「AI 建表」创建、可供大模型查询的表（u_ 前缀）",
                "params": [],
                "returns": "table_name / label / columns",
                "openai_parameters": {"type": "object", "properties": {}, "required": []},
            },
            {
                "id": "create-custom-table",
                "name": "✨ AI 建表",
                "desc": (
                    "用自然语言描述业务表需求，调用已保存大模型设计 DDL 并在 PostgreSQL 创建 "
                    "（表名自动 u_ 前缀）。建好后可用 list-tables / info-table / query-exec。"
                    "系统表不可创建覆盖。"
                ),
                "params": [
                    {
                        "name": "prompt",
                        "label": "需求描述",
                        "type": "textarea",
                        "required": True,
                        "placeholder": "例如：记录每日盯盘笔记，含股票代码、日期、情绪分、备注",
                    },
                    {
                        "name": "dry_run",
                        "label": "仅预览",
                        "type": "text",
                        "required": False,
                        "placeholder": "true=只设计不建表",
                    },
                ],
                "returns": "table_name / ddl / columns",
                "openai_parameters": {
                    "type": "object",
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "description": "自然语言描述要创建的表（用途与字段）",
                        },
                        "dry_run": {
                            "type": "boolean",
                            "description": "true 时只返回设计不落库",
                        },
                        "table_name": {
                            "type": "string",
                            "description": "可选强制表名，须 u_ 开头",
                        },
                        "profile_id": {
                            "type": "integer",
                            "description": "可选大模型配置 ID",
                        },
                    },
                    "required": ["prompt"],
                },
            },
            {
                "id": "delete-custom-table",
                "name": "🗑️ 删除自定义表",
                "desc": (
                    "仅删除用户自己通过 AI 建表创建的表（u_* 且已登记）。"
                    "系统内置表一律拒绝，无 DROP/ALTER 权限。"
                ),
                "params": [
                    {
                        "name": "table_name",
                        "label": "表名",
                        "type": "text",
                        "required": True,
                        "placeholder": "u_my_notes",
                    }
                ],
                "returns": "deleted / hint",
                "openai_parameters": {
                    "type": "object",
                    "properties": {
                        "table_name": {
                            "type": "string",
                            "description": "要删除的自定义表名，必须是 u_ 前缀且为自己创建",
                        }
                    },
                    "required": ["table_name"],
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
                    "支持龙虎榜/席位/大宗/两融/北向/资金流/"
                    "利润表/资产负债/现金流/财务指标/主营构成/审计/分红/快报/披露日程/业绩预告/十大股东、"
                    "指数主档/周月线/估值/行业分类/权重、基金主档/净值/分红/持仓/复权、"
                    "限售解禁/质押/增减持/筹码/研报/同花顺概念成分/可转债/期货主档、"
                    "每日指标/复权因子/涨跌停价/涨跌停榜/停复牌/新股，及个股二级三级板块资金。"
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
                "id": "fill-sector-gap",
                "name": "补板块缺日",
                "desc": (
                    "某一天的东财行业板块行情不在库里时使用。"
                    "按该层级已有板块代码，从 Datahubco 或东财日K补涨跌幅和成交额并写入 PostgreSQL。"
                    "上涨家数、领涨股、主力净额若来源没有，就留空，不要估算。"
                ),
                "params": [
                    {"name": "trade_date", "label": "交易日", "type": "text", "required": True, "placeholder": "2026-09-23"},
                    {"name": "level", "label": "层级", "type": "text", "required": False, "placeholder": "东财二级行业"},
                ],
                "returns": "index_rows / daily_rows / source",
                "openai_parameters": {
                    "type": "object",
                    "properties": {
                        "trade_date": {"type": "string", "description": "缺少行情的交易日，YYYY-MM-DD 或 YYYYMMDD"},
                        "level": {
                            "type": "string",
                            "description": "东财一级行业、东财二级行业或东财三级行业",
                            "default": "东财二级行业",
                        },
                    },
                    "required": ["trade_date"],
                },
            },
            {
                "id": "fill-sector-history",
                "name": "补板块历史",
                "desc": (
                    "一个东财板块在一段日期里行情不够时使用。"
                    "拉取该板块东财日K、成分股日线和沪深300，写入数据库。"
                    "上涨家数由成分股涨跌统计。已有的市场环境标签不覆盖。"
                ),
                "params": [
                    {"name": "ts_code", "label": "板块代码", "type": "text", "required": True, "placeholder": "BK1277.DC"},
                    {"name": "start_date", "label": "开始日", "type": "text", "required": True, "placeholder": "20200101"},
                    {"name": "end_date", "label": "结束日", "type": "text", "required": True, "placeholder": "20260930"},
                ],
                "returns": "index_rows / breadth_rows / regime_rows",
                "openai_parameters": {
                    "type": "object",
                    "properties": {
                        "ts_code": {"type": "string", "description": "东财板块代码，如 BK1277.DC"},
                        "start_date": {"type": "string", "description": "开始日 YYYYMMDD 或 YYYY-MM-DD"},
                        "end_date": {"type": "string", "description": "结束日 YYYYMMDD 或 YYYY-MM-DD"},
                    },
                    "required": ["ts_code", "start_date", "end_date"],
                },
            },
            {
                "id": "fill-market-history",
                "name": "补全市场板块历史",
                "desc": (
                    "要在全部东财二级行业上验证一条规则、而多数板块缺少整段行情时使用。"
                    "按申万同名行业写入涨跌，再用成分股日线补上涨家数。"
                    "已有较长历史的板块不覆盖。名称对不上的板块留空。"
                ),
                "params": [
                    {"name": "start_date", "label": "开始日", "type": "text", "required": True, "placeholder": "20200101"},
                    {"name": "end_date", "label": "结束日", "type": "text", "required": True, "placeholder": "20260930"},
                ],
                "returns": "index_rows / breadth_rows / unmatched_count",
                "openai_parameters": {
                    "type": "object",
                    "properties": {
                        "start_date": {"type": "string", "description": "开始日 YYYYMMDD"},
                        "end_date": {"type": "string", "description": "结束日 YYYYMMDD"},
                    },
                    "required": ["start_date", "end_date"],
                },
            },
            {
                "id": "get-mins",
                "name": "⏱️ 获取分钟分时",
                "desc": (
                    "获取个股 1 分钟分时。主缓存在 Redis，Timescale stock_intraday_mins 为持久化。"
                    "source=auto：先 Redis → 再 PG → 都没有才 Datahubco 补数并写回。"
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
                "id": "fill-market-universe",
                "name": "📋 全市场股票清单",
                "desc": (
                    "从 Datahubco stock_basic 分页拉取全市场 list_status=L 代码，"
                    "写入 ref_market_universe（含 board_type=主板/科创板/创业板/北交所/ST），"
                    "并同步 ref_stock_basic。fill_batch 从此表按 offset/limit 读股票池。"
                    "先 action=fill，再去 fill_batch。"
                ),
                "params": [
                    {"name": "action", "label": "动作", "type": "text", "required": False, "placeholder": "fill|status|ensure_schema"},
                    {"name": "list_status", "label": "上市状态", "type": "text", "required": False, "placeholder": "L"},
                    {"name": "page_size", "label": "分页大小", "type": "number", "required": False, "placeholder": "5000"},
                    {"name": "sync_stock_basic", "label": "同步主档", "type": "text", "required": False, "placeholder": "默认true"},
                ],
                "returns": "listed_rows / by_board_type / ready_for_fill_batch",
                "openai_parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["fill", "status", "ensure_schema"],
                            "description": "fill=分页拉全量写入；status=看行数与板块分布；ensure_schema=建表",
                        },
                        "list_status": {
                            "type": "string",
                            "description": "上市状态，默认 L",
                        },
                        "page_size": {
                            "type": "integer",
                            "description": "单页行数，默认5000（Datahubco上限）",
                        },
                        "sync_stock_basic": {
                            "type": "boolean",
                            "description": "是否同步写入 ref_stock_basic，默认 true",
                        },
                        "max_pages": {
                            "type": "integer",
                            "description": "最多分页数，默认20",
                        },
                    },
                    "required": [],
                },
            },
            {
                "id": "fill-ml-train-daily",
                "name": "🧠 训练表采集",
                "desc": (
                    "采集 ml_train_daily（千万级友好：先 check_env，再按股票循环）。"
                    "P01–P11：次日标签、表内截面、滚动不填0、一字/停牌、停牌占位、train_ready。"
                    "全市场前请先 fill-market-universe；推荐 fill_complete / fill_batch（读 ref_market_universe）。"
                    "训练只用 train_ready=1。board_type=主板/科创板/创业板/北交所/ST。"
                ),
                "params": [
                    {"name": "action", "label": "动作", "type": "text", "required": False, "placeholder": "check_env|fill_complete|fill_one|fill_batch|local_polish|check_null|status"},
                    {"name": "ts_code", "label": "股票代码", "type": "text", "required": False, "placeholder": "000001.SZ"},
                    {"name": "start_date", "label": "开始日", "type": "text", "required": False, "placeholder": "2016-01-01"},
                    {"name": "end_date", "label": "结束日", "type": "text", "required": False, "placeholder": "留空=今天"},
                    {"name": "limit", "label": "批大小", "type": "number", "required": False, "placeholder": "fill_batch股票数 / check_null本批行数"},
                    {"name": "offset", "label": "偏移", "type": "number", "required": False, "placeholder": "0"},
                    {"name": "from_row", "label": "起始行号", "type": "number", "required": False, "placeholder": "check_null用，留空=续查"},
                    {"name": "reset", "label": "重置检查", "type": "text", "required": False, "placeholder": "check_null时填 true 从头查"},
                    {"name": "auto_fill", "label": "自动补数", "type": "text", "required": False, "placeholder": "check_null默认true；填false只检查"},
                    {"name": "limit_days", "label": "截面补全天数", "type": "number", "required": False, "placeholder": "enrich时限制天数"},
                ],
                "returns": "ready / rows_upserted / clean_from_row / clean_to_row / next_row / null_rows",
                "openai_parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": [
                                "check_env",
                                "fill_complete",
                                "fill_one",
                                "fill_batch",
                                "local_polish",
                                "enrich_cross_section",
                                "enrich_industry_rank",
                                "check_null",
                                "validate_no_nulls",
                                "status",
                            ],
                            "description": (
                                "check_env；fill_complete=采集+表内polish+train_ready；"
                                "fill_one；fill_batch；local_polish=表内截面/广播/train_ready；"
                                "enrich_* 为可选远端补充；check_null；status"
                            ),
                            "default": "check_env",
                        },
                        "from_row": {
                            "type": "integer",
                            "description": "check_null 从第几行开始（1-based）；默认续查进度",
                        },
                        "reset": {
                            "type": "boolean",
                            "description": "check_null 时 true=清空进度从头检查",
                        },
                        "auto_fill": {
                            "type": "boolean",
                            "description": "check_null 发现NULL时自动调用补数（默认 true）",
                            "default": True,
                        },
                        "limit_days": {
                            "type": "integer",
                            "description": "enrich_cross_section 最多补多少个交易日（防超时）",
                        },
                        "ts_code": {
                            "type": "string",
                            "description": "fill_one/status 用，如 000001.SZ",
                        },
                        "start_date": {
                            "type": "string",
                            "description": "开始日 YYYY-MM-DD，默认 2016-01-01",
                            "default": "2016-01-01",
                        },
                        "end_date": {
                            "type": "string",
                            "description": "结束日，默认今天",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "fill_batch 本批股票数，默认 1，最大 1000",
                            "default": 1,
                        },
                        "offset": {
                            "type": "integer",
                            "description": "fill_batch 股票池偏移",
                            "default": 0,
                        },
                        "skip_env_check": {
                            "type": "boolean",
                            "description": "跳过环境检查（不推荐）",
                            "default": False,
                        },
                        "skip_existing": {
                            "type": "boolean",
                            "description": "fill_batch 时跳过已有较多行的股票",
                            "default": True,
                        },
                        "only_mainboard": {
                            "type": "boolean",
                            "description": "fill_batch 仅主板代码",
                            "default": False,
                        },
                    },
                    "required": [],
                },
            },
            {
                "id": "sanitize-ml-train-daily",
                "name": "🧹 训练表体检修复",
                "desc": (
                    "检查并修复 ml_train_daily：把 y_high/y_low 改成「次日高低/当日收盘」；"
                    "用表内截面补行业分位和涨跌家数；一字/停牌写入 session_flag 供 LightGBM 学习；"
                    "停牌日补占位行保证连续；train_ready=1 才可训练。"
                    "先 action=catalog 看问题清单，再 audit 看数量，最后 fix。"
                ),
                "params": [
                    {"name": "action", "label": "动作", "type": "text", "required": False, "placeholder": "catalog|audit|fix"},
                    {"name": "limit", "label": "行数/股票数", "type": "number", "required": False, "placeholder": "fix 时≤500=前N行；fill_gaps=股票数"},
                    {"name": "limit_rows", "label": "修复行数", "type": "number", "required": False, "placeholder": "如 200，只修前N行"},
                    {"name": "from_row", "label": "起始行", "type": "number", "required": False, "placeholder": "0"},
                    {"name": "offset", "label": "偏移", "type": "number", "required": False, "placeholder": "0"},
                    {"name": "skip_gaps", "label": "跳过补停牌", "type": "text", "required": False, "placeholder": "fix 时填 true 可先不插停牌行"},
                    {"name": "dry_run", "label": "只统计缺口", "type": "text", "required": False, "placeholder": "fill_gaps 时 true=不写入"},
                ],
                "returns": "active_problems / train_ready_rows / steps",
                "openai_parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": [
                                "catalog",
                                "audit",
                                "ensure_schema",
                                "clamp_ohlc",
                                "fix_close_location",
                                "broadcast_market",
                                "fill_cross_section",
                                "fill_gaps",
                                "mark_session",
                                "fix_labels",
                                "mark_train_ready",
                                "fix",
                                "preview_rows",
                                "fix_batches",
                            ],
                            "description": (
                                "catalog=问题与解决办法；audit=全表计数；"
                                "fix=按顺序执行全部本地修复；也可单步 clamp_ohlc/"
                                "fill_cross_section/fill_gaps/mark_session/fix_labels"
                            ),
                            "default": "audit",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "fix 时 1~500 表示只修前 N 行；fill_gaps 表示股票数",
                        },
                        "limit_rows": {
                            "type": "integer",
                            "description": "只修复 ORDER BY trade_date,symbol 的前 N 行，便于预览",
                        },
                        "from_row": {
                            "type": "integer",
                            "description": "limit_rows 起始偏移，默认 0",
                            "default": 0,
                        },
                        "offset": {
                            "type": "integer",
                            "description": "fill_gaps 股票偏移",
                            "default": 0,
                        },
                        "skip_gaps": {
                            "type": "boolean",
                            "description": "fix 时跳过插入停牌占位行",
                            "default": False,
                        },
                        "dry_run": {
                            "type": "boolean",
                            "description": "fill_gaps 只统计不写入",
                            "default": False,
                        },
                    },
                    "required": [],
                },
            },
            {
                "id": "fill-mainboard-mins",
                "name": "📦 批量主板分时",
                "desc": (
                    "批量拉取当日（或指定日）全部主板股票 1 分钟分时，写入 Redis + PG，"
                    "供 LightGBM 等训练批量读取。"
                    "action=fill 补数；status 查覆盖；index 返回 Redis 训练代码索引。"
                    "可用 limit/offset 分批，避免一次超时。"
                ),
                "params": [
                    {"name": "trade_date", "label": "交易日", "type": "text", "required": False, "placeholder": "留空=今日"},
                    {"name": "action", "label": "动作", "type": "text", "required": False, "placeholder": "fill|status|index"},
                    {"name": "limit", "label": "本批最多只数", "type": "number", "required": False, "placeholder": "200"},
                    {"name": "offset", "label": "偏移", "type": "number", "required": False, "placeholder": "0"},
                    {"name": "skip_existing", "label": "跳过已有", "type": "boolean", "required": False, "placeholder": "true"},
                    {"name": "max_workers", "label": "并发", "type": "number", "required": False, "placeholder": "6"},
                    {"name": "only_traded", "label": "仅当日有成交", "type": "boolean", "required": False, "placeholder": "true"},
                    {"name": "include_auction", "label": "含集合竞价", "type": "boolean", "required": False, "placeholder": "true"},
                ],
                "returns": "ok_count / index_count / redis_index_key / next_offset",
                "openai_parameters": {
                    "type": "object",
                    "properties": {
                        "trade_date": {
                            "type": "string",
                            "description": "交易日 YYYY-MM-DD；留空=当前会话交易日",
                        },
                        "action": {
                            "type": "string",
                            "enum": ["fill", "status", "index"],
                            "description": "fill=批量补数；status=只查覆盖；index=返回 Redis 训练索引 codes",
                            "default": "fill",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "本批最多处理多少只；省略或0表示从 offset 到末尾（可能很慢）",
                        },
                        "offset": {
                            "type": "integer",
                            "description": "股票池偏移，默认 0；配合 limit 分批",
                            "default": 0,
                        },
                        "source": {
                            "type": "string",
                            "enum": ["auto", "pg", "datahubco"],
                            "description": "同 get-mins；默认 auto",
                            "default": "auto",
                        },
                        "write_back": {
                            "type": "boolean",
                            "description": "是否写 Redis/PG，默认 true",
                            "default": True,
                        },
                        "include_auction": {
                            "type": "boolean",
                            "description": "是否含集合竞价分钟，默认 true",
                            "default": True,
                        },
                        "skip_existing": {
                            "type": "boolean",
                            "description": "本地已有足够分钟则跳过，默认 true",
                            "default": True,
                        },
                        "min_bars": {
                            "type": "integer",
                            "description": "判定「已有足够分时」的最少分钟根数，默认 60",
                            "default": 60,
                        },
                        "max_workers": {
                            "type": "integer",
                            "description": "并发数 1-12，默认 6",
                            "default": 6,
                        },
                        "only_traded": {
                            "type": "boolean",
                            "description": "true=仅当日 stock_daily 有成交的主板；false=上市主板全列表",
                            "default": True,
                        },
                        "exclude_st": {
                            "type": "boolean",
                            "description": "排除 ST/*ST，默认 true",
                            "default": True,
                        },
                    },
                    "required": [],
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
                    "利润表/资产负债表/现金流量表/财务指标(ROE·EPS)/"
                    "主营构成/审计意见/分红送转/业绩快报/财报披露日程/"
                    "业绩预告/十大股东/板块资金/"
                    "指数主档/周月线/每日估值/行业分类/成分权重/"
                    "基金主档/净值/分红/持仓/复权因子/"
                    "限售解禁/股权质押/股东增减持/筹码摘要与明细/券商研报/"
                    "同花顺概念成分/可转债基础与日线/期货主档/"
                    "每日指标/复权因子/涨跌停价/涨跌停榜/停复牌/龙虎榜席位/新股，"
                    "并写入对应 PostgreSQL 表。"
                    "默认 source=auto：库优先，不足则 Datahubco 补数入库。"
                ),
                "params": [
                    {"name": "dataset", "label": "数据集", "type": "text", "required": True, "placeholder": "index_basic|fund_nav|index_weight|..."},
                    {"name": "ts_code", "label": "代码", "type": "text", "required": False, "placeholder": "000001.SZ / 000300.SH / 510300.SH"},
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
                                "top_list(龙虎榜)/top_inst(龙虎榜席位)/block_trade(大宗)/"
                                "margin(两融汇总)/margin_detail(两融明细)/moneyflow(资金流)/"
                                "hk_hold(北向持股)/income(利润表)/balancesheet(资产负债表)/"
                                "cashflow(现金流量表)/fina_indicator(财务指标ROE·EPS)/"
                                "fina_mainbz(主营构成)/fina_audit(审计意见)/dividend(分红送转)/"
                                "express(业绩快报)/disclosure_date(财报披露日程)/"
                                "forecast(业绩预告)/top10_holders(十大股东)/sector_moneyflow(板块资金)/"
                                "index_basic(指数主档)/index_weekly(指数周线)/index_monthly(指数月线)/"
                                "index_dailybasic(指数每日估值)/index_classify(行业分类申万)/"
                                "index_weight(指数权重)/fund_basic(基金主档)/fund_nav(基金净值)/"
                                "fund_div(基金分红)/fund_portfolio(基金持仓)/fund_adj(基金复权)/"
                                "share_float(限售解禁)/pledge_stat(股权质押)/stk_holdertrade(增减持)/"
                                "cyq_perf(筹码摘要)/cyq_chips(筹码明细)/report_rc(券商研报)/"
                                "ths_member(同花顺概念成分)/cb_basic(可转债基础)/cb_daily(可转债日线)/"
                                "fut_basic(期货主档)/"
                                "daily_basic(每日指标)/adj_factor(复权因子)/stk_limit(涨跌停价)/"
                                "limit_list(涨跌停榜)/suspend_d(停复牌)/new_share(新股)"
                            ),
                            "enum": [
                                "top_list", "top_inst", "block_trade", "margin", "margin_detail",
                                "moneyflow", "hk_hold",
                                "income", "balancesheet", "cashflow", "fina_indicator",
                                "fina_mainbz", "fina_audit", "dividend", "express",
                                "disclosure_date", "forecast",
                                "top10_holders", "sector_moneyflow",
                                "index_basic", "index_weekly", "index_monthly",
                                "index_dailybasic", "index_classify", "index_weight",
                                "fund_basic", "fund_nav", "fund_div", "fund_portfolio", "fund_adj",
                                "share_float", "pledge_stat", "stk_holdertrade",
                                "cyq_perf", "cyq_chips", "report_rc",
                                "ths_member", "cb_basic", "cb_daily", "fut_basic",
                                "daily_basic", "adj_factor", "stk_limit",
                                "limit_list", "suspend_d", "new_share",
                            ],
                        },
                        "ts_code": {
                            "type": "string",
                            "description": (
                                "个股/指数/基金/可转债/期货/概念代码；"
                                "margin/sector_moneyflow/top_list/limit_list/new_share/"
                                "disclosure_date/index_basic/fund_basic/index_classify/"
                                "index_dailybasic/cyq_perf/report_rc/cb_basic/cb_daily/fut_basic "
                                "全日可不传。index_weight 传指数代码；ths_member 传概念如 885556.TI"
                            ),
                        },
                        "trade_date": {"type": "string", "description": "交易日 YYYYMMDD（龙虎榜/涨跌停榜/两融汇总/指数估值/筹码/可转债日线等）"},
                        "start_date": {"type": "string", "description": "开始日 YYYYMMDD"},
                        "end_date": {"type": "string", "description": "结束日 YYYYMMDD"},
                        "period": {"type": "string", "description": "财报/持仓/披露报告期如 20241231；index_classify 可传 L1/L2/L3"},
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
                            "description": (
                                "板块资金用「行业/概念」；涨跌停榜 U/D/Z；fina_mainbz 用 P/D/I；"
                                "index_basic 用市场 SSE/SZSE；fund_basic 用 E/O；"
                                "index_classify 用 SW2021 或 L1/L2/L3；"
                                "fut_basic 用交易所 DCE/SHFE/CZCE/CFFEX/INE/GFEX"
                            ),
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
2. **1 分钟分时**：优先 `get-mins`（或 `get-bars` + dataset=stock_mins）。顺序：Redis → PG → Datahubco 补数。
3. 东财板块成分：`get-sector-members` → 可选 `filter-st-suspend`。
4. 龙虎榜/席位/大宗/两融/北向/资金流；利润表/资产负债/现金流/财务指标/主营构成/审计/分红/快报/披露日程/预告/十大股东；指数主档/周月线/估值/行业分类/权重；基金主档/净值/分红/持仓/复权；限售解禁/质押/增减持/筹码/研报/同花顺概念成分/可转债/期货主档；每日指标/复权/涨跌停价/涨跌停榜/停复牌/新股：`get-fund-data`（dataset=...）。
5. 个股所属二级/三级板块资金：`get-stock-sector-moneyflow`。
6. 只要库内结果：`source=pg`；强制远端：`source=datahubco`；不入库设 `write_back=false`。
7. 先确认覆盖可用 `coverage`；任意 OpenAPI 用 `remote-query`。
""",
    },
    "futures-market": {
        "id": "futures-market",
        "name": "期货行情中心",
        "icon": "chart-area",
        "category": "行情数据",
        "desc": (
            "读取行情中心期货主连报价（贵金属/有色/能源/黑色/农产品/股指/广期所等）；"
            "检查是否缺失或落后于上一交易日；从 Datahubco 补日线并写入 fut_daily；支持日/周/月K线。"
        ),
        "enabled": True,
        "tools": [
            {
                "id": "list-catalog",
                "name": "主连目录",
                "desc": "列出行情中心期货分类与合约代码",
                "params": [],
                "returns": "categories",
                "openai_parameters": {"type": "object", "properties": {}, "required": []},
            },
            {
                "id": "get-board",
                "name": "主连看板",
                "desc": "读取行情中心分类看板；默认先对齐上一交易日",
                "params": [
                    {"name": "category", "label": "分类", "type": "text", "required": False},
                    {"name": "ensure", "label": "先补数", "type": "boolean", "required": False},
                    {"name": "force", "label": "强制重拉", "type": "boolean", "required": False},
                ],
                "returns": "sections/asof",
                "openai_parameters": {
                    "type": "object",
                    "properties": {
                        "category": {
                            "type": "string",
                            "description": "贵金属/有色金属/能源化工/黑色建材/农产品/股指国债/广期所/国际贵金属，空=全部",
                        },
                        "ensure": {"type": "boolean", "description": "是否先对齐上一交易日，默认 true"},
                        "force": {"type": "boolean", "description": "强制重拉远端"},
                    },
                    "required": [],
                },
            },
            {
                "id": "get-quotes",
                "name": "读取报价",
                "desc": "读取指定合约或分类的最新主连价格（现价/涨跌/持仓等）",
                "params": [
                    {"name": "ts_codes", "label": "合约", "type": "text", "required": False},
                    {"name": "category", "label": "分类", "type": "text", "required": False},
                    {"name": "ensure", "label": "先补数", "type": "boolean", "required": False},
                ],
                "returns": "quotes",
                "openai_parameters": {
                    "type": "object",
                    "properties": {
                        "ts_codes": {
                            "type": "string",
                            "description": "合约代码，逗号分隔，如 AU.SHF,SC.INE",
                        },
                        "category": {"type": "string", "description": "分类过滤"},
                        "ensure": {"type": "boolean", "default": True},
                    },
                    "required": [],
                },
            },
            {
                "id": "get-kline",
                "name": "期货K线",
                "desc": "日/周/月K线；缺数时先补日线入库再返回",
                "params": [
                    {"name": "ts_code", "label": "合约", "type": "text", "required": True},
                    {"name": "freq", "label": "周期", "type": "text", "required": False},
                    {"name": "limit", "label": "条数", "type": "number", "required": False},
                ],
                "returns": "bars/stats",
                "openai_parameters": {
                    "type": "object",
                    "properties": {
                        "ts_code": {"type": "string", "description": "如 AU.SHF / SC.INE / IF.CFX"},
                        "freq": {"type": "string", "enum": ["daily", "weekly", "monthly"], "default": "daily"},
                        "start": {"type": "string"},
                        "end": {"type": "string"},
                        "limit": {"type": "integer", "default": 120},
                        "ensure": {"type": "boolean", "default": True},
                    },
                    "required": ["ts_code"],
                },
            },
            {
                "id": "check-coverage",
                "name": "覆盖检查",
                "desc": "检查主连是否缺失或日期落后于上一交易日",
                "params": [
                    {"name": "ts_codes", "label": "合约", "type": "text", "required": False},
                    {"name": "category", "label": "分类", "type": "text", "required": False},
                    {"name": "asof", "label": "目标日", "type": "text", "required": False},
                ],
                "returns": "missing/stale",
                "openai_parameters": {
                    "type": "object",
                    "properties": {
                        "ts_codes": {"type": "string"},
                        "category": {"type": "string"},
                        "asof": {"type": "string", "description": "YYYY-MM-DD，默认上一交易日"},
                    },
                    "required": [],
                },
            },
            {
                "id": "sync-quotes",
                "name": "补数入库",
                "desc": "从 Datahubco 拉取缺失/过旧的主连日线并写入 fut_daily",
                "params": [
                    {"name": "ts_codes", "label": "合约", "type": "text", "required": False},
                    {"name": "category", "label": "分类", "type": "text", "required": False},
                    {"name": "asof", "label": "目标日", "type": "text", "required": False},
                    {"name": "force", "label": "强制", "type": "boolean", "required": False},
                ],
                "returns": "filled/missing/upserted",
                "openai_parameters": {
                    "type": "object",
                    "properties": {
                        "ts_codes": {"type": "string", "description": "空=行情中心全部主连"},
                        "category": {"type": "string"},
                        "asof": {"type": "string"},
                        "lookback_days": {"type": "integer", "default": 5},
                        "force": {"type": "boolean", "default": False},
                    },
                    "required": [],
                },
            },
            {
                "id": "ensure-and-get",
                "name": "补数并读取",
                "desc": "一站式：检查→补数入库→返回最新报价。缺数或日期不对时优先用本工具",
                "params": [
                    {"name": "ts_codes", "label": "合约", "type": "text", "required": False},
                    {"name": "category", "label": "分类", "type": "text", "required": False},
                    {"name": "force", "label": "强制", "type": "boolean", "required": False},
                ],
                "returns": "sync+quotes",
                "openai_parameters": {
                    "type": "object",
                    "properties": {
                        "ts_codes": {"type": "string"},
                        "category": {"type": "string"},
                        "force": {"type": "boolean", "default": False},
                    },
                    "required": [],
                },
            },
        ],
        "instructions": """
**期货行情中心**：问黄金/原油/橡胶/股指期货等主连价格、涨跌、持仓时用本技能。

1. 先 `get-quotes` / `get-board`（默认会对齐上一交易日）。
2. 若返回缺失、日期不对、或用户说“数据没有/过期” → `check-coverage` 再 `sync-quotes`，或直接 `ensure-and-get`。
3. 看走势用 `get-kline`（freq=daily|weekly|monthly）。
4. 不要编造期货价格；A股指数仍用行情看板/datahubco 指数接口。
""",
    },
    "t0-intraday": T0_SKILL,
    "paper-trade": {
        "id": "paper-trade",
        "name": "纸面交易",
        "icon": "wallet",
        "category": "模拟交易",
        "desc": (
            "虚拟账户模拟交易：开户、查仓、分时盯市（无分时回退上一交易日收盘价计入净值）、感知日线、下单（次日开盘/当日收盘）、"
            "结算 pending、成交统计。仅纸面成交，不连接真实券商。"
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
                "desc": "查询账户现金/净值及持仓。优先分时末价；无分时则用上一交易日收盘价（quote_source=daily_close）计入市值。",
                "params": [
                    {"name": "account_id", "label": "账户ID", "type": "number", "required": True},
                ],
                "returns": "账户+持仓",
                "openai_parameters": {
                    "type": "object",
                    "properties": {
                        "account_id": {"type": "integer", "description": "纸面账户 ID"},
                        "mark": {"type": "boolean", "description": "是否盯市，默认 true；无分时回退上一交易日收盘价"},
                    },
                    "required": ["account_id"],
                },
            },
            {
                "id": "list-positions",
                "name": "持仓列表",
                "desc": "查询账户持仓：优先分时末价；无分时用上一交易日收盘价计价（daily_close）",
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
                "desc": "更新净值：优先 1 分钟分时末价；无分时（如非交易日）回退上一交易日收盘价计入市值。",
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
    "auction-screen": {
        "id": "auction-screen",
        "name": "主板集合竞价强买",
        "icon": "bolt",
        "category": "盘前扫描",
        "desc": "在昨日强势主板∪昨日涨停池中，筛选今日09:20–09:25竞价成交额达P95且价升的股票，并附近3日股价与规则原因。",
        "enabled": True,
        "tools": [
            {
                "id": "mainboard-buy",
                "name": "主板集合竞价强买",
                "desc": (
                    "宇宙=昨日涨幅偏强主板股+昨日涨停/炸板池；"
                    "取09:20-09:25竞价成交额≥宇宙P95且窗内价升者推荐。"
                    "若 incomplete=true，必须先执行 suggested_tool_calls 用 Datahubco 补竞价分钟后再重跑。"
                    "禁止编造成交额。"
                ),
                "params": [
                    {
                        "name": "trade_date",
                        "label": "交易日",
                        "type": "text",
                        "required": False,
                        "placeholder": "YYYY-MM-DD，留空=今日会话日",
                    },
                    {
                        "name": "top_n",
                        "label": "返回条数",
                        "type": "number",
                        "required": False,
                        "placeholder": "20",
                    },
                    {
                        "name": "strong_pct",
                        "label": "昨强涨幅阈值%",
                        "type": "number",
                        "required": False,
                        "placeholder": "5",
                    },
                    {
                        "name": "strong_top_n",
                        "label": "昨强名额上限",
                        "type": "number",
                        "required": False,
                        "placeholder": "80",
                    },
                    {
                        "name": "fetch_missing",
                        "label": "缺数时拉东财竞价",
                        "type": "boolean",
                        "required": False,
                        "placeholder": "true",
                    },
                ],
                "returns": "candidates 列表 + p95_amount + reason",
                "openai_parameters": {
                    "type": "object",
                    "properties": {
                        "trade_date": {
                            "type": "string",
                            "description": "交易日 YYYY-MM-DD，留空=今日",
                        },
                        "top_n": {
                            "type": "integer",
                            "description": "返回推荐数量，默认 20",
                        },
                        "strong_pct": {
                            "type": "number",
                            "description": "昨日涨幅达到该百分比视为强势，默认 5",
                        },
                        "strong_top_n": {
                            "type": "integer",
                            "description": "昨日强势池名额上限，默认 80",
                        },
                        "fetch_missing": {
                            "type": "boolean",
                            "description": "本地无竞价分钟时是否拉东财（仅当天），默认 true",
                        },
                    },
                    "required": [],
                },
            },
        ],
    },
    "sector-intraday": {
        "id": "sector-intraday",
        "name": "二级行业分时情绪",
        "icon": "chart-area",
        "category": "盘中扫描",
        "desc": "东财二级行业先日线排序，再对 Top 板块合成成分分时，输出近3日涨跌与今日分时形态/市场情绪。",
        "enabled": True,
        "tools": [
            {
                "id": "l2-tone",
                "name": "二级行业分时情绪",
                "desc": (
                    "读取东财二级行业日线，按|涨跌幅|取 TopN；"
                    "对 Top 板块用成分股分时等权合成今日曲线；"
                    "若 incomplete=true，必须先执行 suggested_tool_calls（fill_sector_gap/get_sector_members/get_mins）再重跑。"
                    "禁止编造涨跌幅，禁止把日线说成分时。"
                ),
                "params": [
                    {
                        "name": "trade_date",
                        "label": "交易日",
                        "type": "text",
                        "required": False,
                        "placeholder": "YYYY-MM-DD，留空=今日",
                    },
                    {
                        "name": "top_n",
                        "label": "Top板块数",
                        "type": "number",
                        "required": False,
                        "placeholder": "10",
                    },
                    {
                        "name": "member_limit",
                        "label": "每板块成分上限",
                        "type": "number",
                        "required": False,
                        "placeholder": "15",
                    },
                    {
                        "name": "sample_every_min",
                        "label": "分时采样间隔(分钟)",
                        "type": "number",
                        "required": False,
                        "placeholder": "5",
                    },
                ],
                "returns": "market_tone + sectors(含 prev_3d / intraday_points)",
                "openai_parameters": {
                    "type": "object",
                    "properties": {
                        "trade_date": {
                            "type": "string",
                            "description": "交易日 YYYY-MM-DD，留空=今日",
                        },
                        "top_n": {
                            "type": "integer",
                            "description": "日线排序后取前 N 个板块做分时合成，默认 10",
                        },
                        "member_limit": {
                            "type": "integer",
                            "description": "每个板块参与合成的成分股上限，默认 15",
                        },
                        "sample_every_min": {
                            "type": "integer",
                            "description": "分时曲线采样间隔分钟，默认 5",
                        },
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
2. 需要个股/指数/ETF/板块日线或 **1 分钟分时**：优先 `datahubco_data__get_bars` / `datahubco_data__get_mins`（日线 PG 优先；**分时先 Redis 再 PG**，缺则 Datahubco 补数入库）。不要先 list_tables。
2f. **批量拉取今日全部主板分时（训练 LightGBM）**：调用 `datahubco_data__fill_mainboard_mins`。先 `action=status` 看覆盖；`action=fill` 用 `limit`/`offset` 分批写入 Redis+PG；完成后 `action=index` 取 codes，按 `intraday:{{ts_code}}:{{ymd}}` 读 Redis bars。禁止编造分时。
2f2. **全市场股票清单**：先 `datahubco_data__fill_market_universe` `action=fill`，写入 `ref_market_universe`（约 5575 只，含 board_type）并同步 `ref_stock_basic`；`action=status` 验收。
2g. **采集 ml_train_daily 训练特征**：先 `datahubco_data__fill_ml_train_daily` `action=check_env`；通过后用 `fill_complete`（如 ts_code=000001.SZ, start_date=2016-01-04）：次日标签、表内截面、停牌占位、`train_ready`（P01–P11）。全市场 `fill_batch`+`limit`/`offset`（从 `ref_market_universe` 读池，批末自动 polish）。训练过滤 `train_ready=1`。禁止一次加载全市场进内存。`board_type` 为主板/科创板/创业板/北交所/ST。
2h. **修复 ml_train_daily 训练质量**：`datahubco_data__sanitize_ml_train_daily`。先 `catalog` 看问题与办法，再 `audit`，最后 `fix`。`y_high`/`y_low` 必须是次日高低相对当日收盘；一字/停牌用 `session_flag` 学习，不要删这些行。训练过滤 `train_ready=1`。
2a. **期货主连 / 行情中心价格**：`futures_market__get_quotes` / `futures_market__ensure_and_get`；缺数或日期不对 → `futures_market__sync_quotes`。禁止编造期货价格。
2b. 龙虎榜/席位/大宗/两融/北向/资金流；利润表/资产负债/现金流/财务指标/主营构成/审计/分红/快报/披露日程/预告/十大股东；指数主档/周月线/估值/行业分类/权重；基金主档/净值/分红/持仓/复权；限售解禁/质押/增减持/筹码/研报/同花顺概念成分/可转债/期货主档；每日指标/复权/涨跌停价/涨跌停榜/停复牌/新股：`datahubco_data__get_fund_data`；个股二级/三级板块资金：`datahubco_data__get_stock_sector_moneyflow`。
2c. 用户要生成今日市场记忆、今日行情复盘并准备写入知识库时，调用 `kb_market__today`（知识库-市场记忆-今日）。用工具返回的事实写给用户看。禁止编造涨停家数和成交额。禁止声称已经写入知识库，写入由用户点击「填入市场记忆」完成。
2d. **盘前集合竞价强买 / 9:20–9:25 放量**：调用 `auction_screen__mainboard_buy`。若返回 `incomplete=true` 或带 `suggested_tool_calls`：必须先按列表逐个调用 Datahubco（通常是 `datahubco_data__get_mins` include_auction=true、`get_fund_data` limit_list）补数，全部完成后再重跑 `auction_screen__mainboard_buy`。覆盖不足时禁止下「今日无强买」的最终结论。禁止编造量额。
2e. **二级行业分时情绪 / 板块今日走势**：调用 `sector_intraday__l2_tone`。若 `incomplete=true` 或 `intraday_shape=数据不足`：必须先执行返回的 `suggested_tool_calls`（`fill_sector_gap` / `get_sector_members` / `get_mins`），再重跑本工具。补齐前可讲日线，但必须标明分时未就绪，禁止把日线说成分时形态。禁止编造涨跌幅。
3. 只有没有专用技能、且不知道表名时，才 list_tables / info_table。
4. 拿到工具结果后必须用中文给出结论；若结果含 `agent_instruction` / `suggested_tool_calls`，优先执行补数再给最终结论，不要停在「建议补数」。
5. 若工具失败，说明原因并给出下一步建议。
"""
    if extra:
        prompt += f"\n## 额外系统说明\n{extra}\n"
    return prompt

"""技能注册表条目：日内做T策略（4 个方法工具 + 组合对比）。"""

from __future__ import annotations

from typing import Any, Dict

_TS = {
    "type": "string",
    "description": "股票代码，逗号分隔，如 000001.SZ,600519.SH。最多 15 只。",
}
_DATE = {
    "type": "string",
    "description": "交易日 YYYY-MM-DD 或 YYYYMMDD。可与 start_date/end_date 二选一。",
}
_COMMON_PROPS: Dict[str, Any] = {
    "ts_codes": _TS,
    "trade_date": _DATE,
    "start_date": {"type": "string", "description": "区间起始交易日"},
    "end_date": {"type": "string", "description": "区间结束交易日，与 start_date 最多 15 个交易日"},
    "as_of": {
        "type": "string",
        "description": "因果截断时刻，如 2026-09-15 10:31:00。盘中判断必填或默认现在。",
    },
    "lookback_days": {
        "type": "integer",
        "description": "同时点量能回看天数，默认 20",
        "default": 20,
    },
    "include_bars": {
        "type": "boolean",
        "description": "是否返回每分钟明细，默认 false",
        "default": False,
    },
}

_COMMON_REQ = ["ts_codes"]

T0_SKILL: Dict[str, Any] = {
    "id": "t0-intraday",
    "name": "日内做T策略",
    "icon": "activity",
    "category": "交易策略",
    "desc": (
        "不依赖盘口的日内做T。标准测试流程："
        "① load-day 从数据库取出当天全部分时（价格+成交量）；"
        "② hit-test 用 A/B/C/D 及其组合在这些数据上做因果判断，再用当天真实最高最低打分。"
        "全市场分态势测试用 market-scan：30 日日线定上升/下跌/横盘，当日分钟做组合。"
        "另有 method-a/b/c/d 看单法状态，combo-run 做组合成交对比。"
    ),
    "enabled": True,
    "tools": [
        {
            "id": "load-day",
            "name": "加载当日全部分时",
            "desc": (
                "从 PostgreSQL stock_intraday_mins 取出指定股票当天全部 1 分钟"
                "开高低收、成交量、均价。测试四法能否打中高低时，第一步必须调这个。"
                "600118 请写成 600118.SH。"
            ),
            "params": [
                {"name": "ts_codes", "label": "股票代码", "type": "text", "required": True},
                {"name": "trade_date", "label": "交易日", "type": "text", "required": True},
            ],
            "returns": "summary（开高低收、最高最低时刻、成交量）+ 全部分钟 bars",
            "openai_parameters": {
                "type": "object",
                "properties": {**_COMMON_PROPS},
                "required": ["ts_codes", "trade_date"],
            },
        },
        {
            "id": "hit-test",
            "name": "四法组合命中高低测试",
            "desc": (
                "第二步：用当天价格和成交量跑 A/B/C/D，再按组合发买卖点（盘中不用未来高低）。"
                "组合含单法、或、且、vote2、A底线+提前。"
                "收盘后用真实最高最低打分。结论先看 combo_ranking / combo_summary。"
                "C 不单独下单，只作为 B/D 滤镜。"
            ),
            "params": [
                {"name": "ts_codes", "label": "股票代码", "type": "text", "required": True},
                {"name": "trade_date", "label": "交易日", "type": "text", "required": True},
                {"name": "hit_tol_pct", "label": "命中容差%", "type": "number", "required": False},
                {"name": "combos", "label": "组合列表", "type": "text", "required": False,
                 "placeholder": "all 或 a,b_c,vote2"},
            ],
            "returns": "combo_ranking、best_hit_both、各组合 hit_high/hit_low、单法对照",
            "openai_parameters": {
                "type": "object",
                "properties": {
                    **_COMMON_PROPS,
                    "hit_tol_pct": {
                        "type": "number",
                        "description": "相对真实极值的命中容差，单位百分数，默认 1 即 1%",
                        "default": 1,
                    },
                    "combos": {
                        "type": "string",
                        "description": (
                            "要测试的组合，逗号分隔或 all。"
                            "可选：a,b,b_c,d_c,a_or_b,a_or_bc,a_or_d,bc_or_d,"
                            "a_and_b,a_and_bc,a_and_d,bc_and_d,vote2,"
                            "a_floor_b,a_floor_bc,a_floor_d。"
                            "或=任一触发；且=同一分钟同时触发；vote2=A/B+C/D 至少两法同意。"
                        ),
                        "default": "all",
                    },
                },
                "required": ["ts_codes", "trade_date"],
            },
        },
        {
            "id": "method-a",
            "name": "方法A 回撤+剩余时间",
            "desc": (
                "Peskir：现价相对截至目前最高的回撤，以及剩余连续竞价分钟。"
                "只开 A 时调用本工具。创出新高时分数会压低。"
            ),
            "params": [
                {"name": "ts_codes", "label": "股票代码", "type": "text", "required": True},
                {"name": "trade_date", "label": "交易日", "type": "text", "required": False},
                {"name": "as_of", "label": "截至时刻", "type": "text", "required": False},
            ],
            "returns": "last 状态、sell_now、以及若按 A 成交的捕获率",
            "openai_parameters": {
                "type": "object",
                "properties": {**_COMMON_PROPS},
                "required": _COMMON_REQ,
            },
        },
        {
            "id": "method-b",
            "name": "方法B 冲击衰竭",
            "desc": (
                "买盘涌入→量价受阻→方向翻面。默认不加 C（只开 B）。"
                "B+C 时设 use_clock=true，或改用 combo-run 的 combo=b_c。"
            ),
            "params": [
                {"name": "ts_codes", "label": "股票代码", "type": "text", "required": True},
                {"name": "trade_date", "label": "交易日", "type": "text", "required": False},
                {"name": "use_clock", "label": "叠加C", "type": "boolean", "required": False},
            ],
            "returns": "flow_state、score_b、sell_now",
            "openai_parameters": {
                "type": "object",
                "properties": {
                    **_COMMON_PROPS,
                    "use_clock": {
                        "type": "boolean",
                        "description": "true=B+C，涌入须相对同时点放量；默认 false=只开B",
                        "default": False,
                    },
                },
                "required": _COMMON_REQ,
            },
        },
        {
            "id": "method-c",
            "name": "方法C U型量时钟",
            "desc": (
                "不产生卖点。用过去 lookback_days 个交易日同一分钟的成交量中位数，"
                "计算今日该分钟放量倍数 x。开盘尾盘天然量大，必须同时点比。"
            ),
            "params": [
                {"name": "ts_codes", "label": "股票代码", "type": "text", "required": True},
                {"name": "trade_date", "label": "交易日", "type": "text", "required": False},
            ],
            "returns": "x、vol_median、是否相对放量",
            "openai_parameters": {
                "type": "object",
                "properties": {**_COMMON_PROPS},
                "required": _COMMON_REQ,
            },
        },
        {
            "id": "method-d",
            "name": "方法D 峰岭谷",
            "desc": (
                "同时点喷发且上一分钟也喷发=岭；孤立喷发=峰。"
                "岭+新高则等，岭+推不动或冲高后出峰则卖。自身已用 C 的同时点尺子。"
            ),
            "params": [
                {"name": "ts_codes", "label": "股票代码", "type": "text", "required": True},
                {"name": "trade_date", "label": "交易日", "type": "text", "required": False},
            ],
            "returns": "vol_state、score_d、sell_now",
            "openai_parameters": {
                "type": "object",
                "properties": {**_COMMON_PROPS},
                "required": _COMMON_REQ,
            },
        },
        {
            "id": "combo-run",
            "name": "组合对比（同一纪律）",
            "desc": (
                "同一批票、同一日期、同一成交纪律，只换信号。"
                "combo=a / b / b_c / d_c / a_floor_b / a_floor_d / all。"
                "a_floor_*：提前信号卖 50%，A 或 14:55 卖剩余。"
            ),
            "params": [
                {"name": "ts_codes", "label": "股票代码", "type": "text", "required": True},
                {"name": "combo", "label": "组合", "type": "text", "required": False,
                 "placeholder": "all 或 a / b / b_c / d_c / a_floor_b / a_floor_d"},
                {"name": "start_date", "label": "起始日", "type": "text", "required": False},
                {"name": "end_date", "label": "结束日", "type": "text", "required": False},
            ],
            "returns": "各组合 capture / early_rate / late_rate",
            "openai_parameters": {
                "type": "object",
                "properties": {
                    **_COMMON_PROPS,
                    "combo": {
                        "type": "string",
                        "description": (
                            "a=只开A; b=只开B不加C; b_c=B+C; d_c=D+C; "
                            "a_floor_b=A底线+B提前; a_floor_d=A底线+D提前; all=全部对比"
                        ),
                        "enum": ["a", "b", "b_c", "d_c", "a_floor_b", "a_floor_d", "all"],
                    },
                    "include_fills": {
                        "type": "boolean",
                        "description": "是否返回成交明细，默认 true",
                        "default": True,
                    },
                    "floor_use_clock": {
                        "type": "boolean",
                        "description": "a_floor_b 的 B 是否叠加 C，默认 false",
                        "default": False,
                    },
                },
                "required": _COMMON_REQ,
            },
        },
        {
            "id": "market-scan",
            "name": "全市场分态势命中扫描",
            "desc": (
                "对当日有分时的全部股票：用测试日之前最多 30 根日线判断上升/下跌/横盘，"
                "再用当日分钟因果跑 A/B/C/D 组合。"
                "结论看 recommended_by_regime 与 by_regime。"
                "collect=true 会从东财补当日分时，耗时长，默认不要开。"
            ),
            "params": [
                {"name": "trade_date", "label": "交易日", "type": "text", "required": True},
                {"name": "collect", "label": "是否补拉分时", "type": "boolean", "required": False},
                {"name": "max_codes", "label": "最多股票数", "type": "number", "required": False},
            ],
            "returns": "分态势推荐组合、命中率表",
            "openai_parameters": {
                "type": "object",
                "properties": {
                    "trade_date": _DATE,
                    "lookback_days": {
                        "type": "integer",
                        "description": "态势用的日线根数，不含测试日，默认 30",
                        "default": 30,
                    },
                    "hit_tol_pct": {
                        "type": "number",
                        "description": "第一次信号命中容差%，默认 1",
                        "default": 1,
                    },
                    "collect": {
                        "type": "boolean",
                        "description": "是否补拉当日分时，默认 false",
                        "default": False,
                    },
                    "max_codes": {
                        "type": "integer",
                        "description": "最多扫描只数；不填则扫库内全部有分时的股票",
                    },
                    "include_stocks": {
                        "type": "boolean",
                        "description": "是否在返回里带每只股票明细，默认 false",
                        "default": False,
                    },
                },
                "required": ["trade_date"],
            },
        },
    ],
    "instructions": """
**日内做T策略**（无盘口，1 分钟分时）

测试「四法及其组合能不能打中当天高低」必须两步，不要用 sql-toolkit 代替：
1. t0_intraday__load_day — 从数据库取当天全部价格和成交量
2. t0_intraday__hit_test — 跑 A/B/C/D 再组合成买卖点，对照真实最高最低打分
   结论先看 combo_ranking / best_hit_both / combo_summary，不要只报单法。
   或=任一方法触发；且=同一分钟同时触发；vote2=A、B+C、D 至少两法同意。
   C 不单独下单。

代码写成 600118 时用 600118.SH。trade_date 用 2026-09-17。

全市场、按上升/下跌/横盘分层 → t0_intraday__market_scan（trade_date=2026-09-17）。
   不要用当日收盘后高低当特征。mark=高低那一分钟当时有没有信号；nearest 不能写进实盘。
   C 不单独下单。
""",
}

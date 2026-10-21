"""
Storage Service - 多后端存储层状态监控
支持: PostgreSQL · InfluxDB · Redis · MinIO
"""

import asyncio
import csv
import io
import sys, os, logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from typing import Dict, Any, List, Callable, TypeVar
from datetime import datetime, date, time

import pandas as pd
from sqlalchemy import text as sa_text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data_layer.db_manager import storage_manager
from server.column_labels import enrich_columns
from server.hidden_tables import is_hidden_pg_table

logger = logging.getLogger(__name__)

T = TypeVar("T")
_PROBE_TIMEOUT_S = 2.5


def _with_timeout(fn: Callable[[], T], timeout: float = _PROBE_TIMEOUT_S, fallback: T = None) -> T:
    """单后端探测超时，避免 MinIO/Influx 拖死整个 /storage/status。"""
    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(fn)
        try:
            return fut.result(timeout=timeout)
        except FuturesTimeout:
            logger.warning("storage probe timeout after %.1fs: %s", timeout, getattr(fn, "__name__", fn))
            return fallback
        except Exception as e:
            logger.warning("storage probe error: %s", e)
            return fallback

# ─── PostgreSQL 表描述 ─────────────────────────────────
PG_TABLES_INFO: Dict[str, Dict[str, str]] = {
    "ref_trade_cal": {"name":"交易日历","category":"参照数据","desc":"各交易所开休市日期","source":"Tushare trade_cal"},
    "ref_stock_basic": {"name":"股票主档","category":"参照数据","desc":"全市场证券基本信息","source":"Tushare stock_basic"},
    "ref_market_universe": {"name":"全市场股票清单","category":"参照数据","desc":"在册股票代码+board_type，供 fill_batch 读池","source":"Datahubco stock_basic"},
    "ref_stock_namechange": {"name":"股票更名记录","category":"参照数据","desc":"历史名称变更记录","source":"Tushare namechange"},
    "ref_new_share": {"name":"新股发行","category":"参照数据","desc":"IPO发行信息","source":"Tushare new_share"},
    "ref_index_basic": {"name":"指数主档","category":"参照数据","desc":"指数基本信息","source":"Tushare index_basic"},
    "ref_etf_basic": {"name":"ETF主档","category":"参照数据","desc":"ETF基金基本信息","source":"Tushare fund_basic"},
    "ref_fund_basic": {"name":"基金主档","category":"参照数据","desc":"公募基金基本信息","source":"Tushare fund_basic"},
    "ref_policy_law": {"name":"政策法规库","category":"参照数据","desc":"监管政策与法律法规","source":"手动采集"},
    "ref_stk_limit_daily": {"name":"涨跌停价","category":"参照数据","desc":"每日涨跌停限制价格","source":"Tushare stk_limit"},
    "ref_suspend_d": {"name":"停复牌信息","category":"参照数据","desc":"停牌/复牌记录","source":"Tushare suspend_d"},
    "dash_watchlist": {"name":"看板自选股","category":"参照数据","desc":"行情看板自选及持续日更名单","source":"用户自选"},
    "strategy_screener_run": {"name":"选股运行","category":"策略分析","desc":"A观察池/B升势回踩选股任务记录","source":"策略选股器"},
    "strategy_screener_hit": {"name":"选股命中","category":"策略分析","desc":"选股命中标的、类型与得分","source":"策略选股器"},
    "strategy_observe_pool": {"name":"策略观察池","category":"策略分析","desc":"策略A分数≥70写入，上限50，同步看板自选","source":"推荐池流水线"},
    "strategy_recommend_pool": {"name":"策略推荐池","category":"策略分析","desc":"起飞入池；动能衰竭后移出","source":"推荐池流水线"},
    "news_article": {"name":"新闻资讯","category":"新闻资讯","desc":"财经新闻与资讯","source":"Tushare news"},
    "news_ann": {"name":"公司公告","category":"新闻资讯","desc":"上市公司公告披露","source":"Tushare anns"},
    "board_secretary_interact": {"name":"董秘互动","category":"互动与研究","desc":"投资者与董秘问答","source":"Tushare"},
    "broker_report": {"name":"券商研报","category":"互动与研究","desc":"券商研究报告","source":"Tushare broker_reports"},
    "broker_report_consensus": {"name":"一致预期","category":"互动与研究","desc":"分析师共识评级","source":"数据聚合"},
    "fin_income": {"name":"利润表","category":"财务数据","desc":"营业收入/成本/利润/EPS","source":"Tushare income"},
    "fin_balancesheet": {"name":"资产负债表","category":"财务数据","desc":"资产/负债/股东权益","source":"Tushare balancesheet"},
    "fin_cashflow": {"name":"现金流量表","category":"财务数据","desc":"经营/投资/筹资现金流","source":"Tushare cashflow"},
    "fin_fina_indicator": {"name":"财务指标","category":"财务数据","desc":"ROE/PE/PB/毛利率等","source":"Tushare fina_indicator"},
    "fin_forecast": {"name":"业绩预告","category":"财务数据","desc":"净利润变动预测","source":"Tushare forecast"},
    "fin_express": {"name":"业绩快报","category":"财务数据","desc":"年度业绩快报","source":"Tushare express"},
    "fin_dividend": {"name":"分红送股","category":"财务数据","desc":"现金分红/送转方案","source":"Tushare dividend"},
    "fin_mainbz": {"name":"主营构成","category":"财务数据","desc":"按产品/地区/行业的主营业务构成","source":"Tushare fina_mainbz"},
    "fin_audit": {"name":"审计意见","category":"财务数据","desc":"年报审计意见与会计师事务所","source":"Tushare fina_audit"},
    "fin_disclosure_date": {"name":"财报披露日程","category":"财务数据","desc":"预约披露日与实际披露日","source":"Tushare disclosure_date"},
    "index_weekly": {"name":"指数周K线","category":"行情K线","desc":"指数周线 OHLCV","source":"Datahubco index_weekly"},
    "index_monthly": {"name":"指数月K线","category":"行情K线","desc":"指数月线 OHLCV","source":"Datahubco index_monthly"},
    "index_daily_basic": {"name":"指数每日估值","category":"特色指标","desc":"指数 PE/PB/市值/换手","source":"Datahubco index_dailybasic"},
    "ref_index_classify": {"name":"行业分类","category":"参照数据","desc":"申万等行业分类层级","source":"Datahubco index_classify"},
    "fund_div": {"name":"基金分红","category":"财务数据","desc":"基金分红送配","source":"Datahubco fund_div"},
    "fund_portfolio": {"name":"基金持仓","category":"财务数据","desc":"基金重仓股持仓明细","source":"Datahubco fund_portfolio"},
    "fund_adj": {"name":"基金复权因子","category":"特色指标","desc":"基金复权因子","source":"Datahubco fund_adj"},
    "stock_share_float": {"name":"限售解禁","category":"特色指标","desc":"限售股解禁明细","source":"Datahubco share_float"},
    "stock_pledge_stat": {"name":"股权质押统计","category":"特色指标","desc":"股权质押比例与数量","source":"Datahubco pledge_stat"},
    "stock_holdertrade": {"name":"股东增减持","category":"特色指标","desc":"股东增持/减持公告","source":"Datahubco stk_holdertrade"},
    "stock_cyq_perf": {"name":"筹码分布摘要","category":"特色指标","desc":"获利盘与成本分位","source":"Datahubco cyq_perf"},
    "stock_cyq_chips": {"name":"筹码分布明细","category":"特色指标","desc":"价格档位筹码占比","source":"Datahubco cyq_chips"},
    "stock_report_rc": {"name":"券商研报","category":"互动与研究","desc":"券商研报评级与盈利预测","source":"Datahubco report_rc"},
    "ths_member": {"name":"同花顺概念成分","category":"板块行情","desc":"同花顺概念板块成分股","source":"Datahubco ths_member"},
    "cb_basic": {"name":"可转债基础","category":"参照数据","desc":"可转债基本信息与转股条款","source":"Datahubco cb_basic"},
    "cb_daily": {"name":"可转债日线","category":"行情K线","desc":"可转债日行情","source":"Datahubco cb_daily"},
    "fut_basic": {"name":"期货主档","category":"参照数据","desc":"期货合约基本信息","source":"Datahubco fut_basic"},
    "stock_daily": {"name":"股票日K线","category":"行情K线","desc":"A股日线开高低收/成交量/成交额","source":"Tushare daily"},
    "stock_weekly": {"name":"股票周K线","category":"行情K线","desc":"A股周线行情","source":"日K聚合"},
    "stock_monthly": {"name":"股票月K线","category":"行情K线","desc":"A股月线行情","source":"日K聚合"},
    "index_daily": {"name":"指数日K线","category":"行情K线","desc":"大盘指数日线","source":"Tushare index_daily"},
    "etf_daily": {"name":"ETF日K线","category":"行情K线","desc":"ETF日线行情","source":"Tushare fund_daily"},
    "fund_daily": {"name":"基金日行情","category":"行情K线","desc":"公募基金日线","source":"Tushare fund_daily"},
    "fund_nav": {"name":"基金净值","category":"行情K线","desc":"单位/累计/复权净值","source":"Tushare fund_nav"},
    "stock_daily_basic": {"name":"每日指标","category":"特色指标","desc":"PE/PB/PS/换手率/市值","source":"Tushare daily_basic"},
    "stock_adj_factor": {"name":"复权因子","category":"特色指标","desc":"前复权/后复权系数","source":"Tushare adj_factor"},
    "stock_auction_daily": {"name":"集合竞价","category":"特色指标","desc":"盘前集合竞价数据","source":"Tushare"},
    "stock_moneyflow_daily": {"name":"资金流向","category":"特色指标","desc":"大中小单买卖额","source":"Tushare moneyflow"},
    "stock_top_list_daily": {"name":"龙虎榜","category":"特色指标","desc":"涨跌幅偏离上榜","source":"Tushare top_list"},
    "margin_summary_daily": {"name":"两融汇总","category":"特色指标","desc":"融资融券余额","source":"Tushare margin"},
    "margin_detail_daily": {"name":"两融明细","category":"特色指标","desc":"个股融资融券","source":"Tushare margin_detail"},
    "stock_block_trade": {"name":"大宗交易","category":"特色指标","desc":"大额交易记录","source":"Tushare block_trade"},
    "index_weight": {"name":"指数权重","category":"特色指标","desc":"成分股权重数据","source":"Tushare index_weight"},
    "sector_dc_index_daily": {"name":"东财行业板块指数","category":"板块行情","desc":"涨跌幅/领涨股/涨跌家数/层级","source":"Datahubco dc_index"},
    "sector_dc_daily": {"name":"东财行业板块日K","category":"板块行情","desc":"板块OHLC/成交额","source":"Datahubco dc_daily"},
    "sector_moneyflow_daily": {"name":"东财板块资金流","category":"板块行情","desc":"主力净流入等","source":"Datahubco moneyflow_ind_dc"},
    "sector_dc_member": {"name":"东财板块成分","category":"板块行情","desc":"板块成分股列表","source":"Datahubco dc_member / 东财 clist"},
    "sector_dc_hierarchy": {"name":"东财板块层级","category":"板块行情","desc":"东财行业板块父子层级","source":"Datahubco"},
    "ref_sector_dc_basic": {"name":"东财行业板块目录","category":"板块行情","desc":"一/二/三级板块代码、名称、层级","source":"sector_dc_index_daily"},
    "stock_intraday_mins": {"name":"个股分钟分时","category":"行情K线","desc":"1分钟分时OHLC/成交量，盘中盈亏用此价","source":"Datahubco / 东财腾讯"},
    "ml_train_daily": {
        "name": "训练表",
        "category": "模型训练",
        "desc": "LightGBM 日频特征：价格/量能/行业市场相对；y_high/y_low=次日高低相对当日收盘；session_flag 标一字/停牌；训练请筛 train_ready=1",
        "source": "由日线/基本面/板块合成",
    },
    "stock_hk_hold": {"name":"北向持股","category":"特色指标","desc":"沪深股通持股数量与占比","source":"Datahubco hk_hold"},
    "stock_top10_holders": {"name":"十大股东","category":"财务数据","desc":"上市公司十大股东明细","source":"Datahubco top10_holders"},
    "stock_limit_list_daily": {"name":"涨跌停榜","category":"特色指标","desc":"涨停/跌停/炸板明细","source":"Datahubco limit_list_d"},
    "stock_top_inst_daily": {"name":"龙虎榜席位","category":"特色指标","desc":"龙虎榜营业部买卖明细","source":"Datahubco top_inst"},
    "paper_account": {"name":"模拟账户","category":"模拟交易","desc":"纸面账户现金、净值与开户设置","source":"模拟交易"},
    "paper_position": {"name":"模拟持仓","category":"模拟交易","desc":"持仓数量、成本、分时市价与浮动盈亏","source":"模拟交易"},
    "paper_order": {"name":"模拟委托","category":"模拟交易","desc":"买卖委托与成交模式（次日开盘/当日收盘/分时）","source":"模拟交易"},
    "paper_fill": {"name":"模拟成交","category":"模拟交易","desc":"成交记录、佣金与印花税","source":"模拟交易"},
    "paper_equity_daily": {"name":"模拟净值曲线","category":"模拟交易","desc":"每日现金、市值、净值与日收益","source":"模拟交易"},
    "paper_decision_log": {"name":"模拟决策日志","category":"模拟交易","desc":"Agent 感知-决策-下单审计","source":"模拟交易"},
    "paper_activity_log": {"name":"模拟当日操作","category":"模拟交易","desc":"日课、分时扫描、模型唤醒等当日流水","source":"模拟交易"},
    "paper_cycle_config": {"name":"模拟循环配置","category":"模拟交易","desc":"一键日课、仓位上限、调度开关等键值配置","source":"模拟交易"},
    "paper_cycle_run": {"name":"模拟循环批次","category":"模拟交易","desc":"晚间选股、日课、分时做T等各步骤运行记录","source":"模拟交易"},
    "paper_cycle_action": {"name":"模拟循环动作","category":"模拟交易","desc":"循环中每笔买卖/观察动作与理由","source":"模拟交易"},
    "trade_watchlist": {"name":"盯盘名单","category":"模拟交易","desc":"日课与分时做T使用的观察股票池","source":"模拟交易"},
    "trade_scan_event": {"name":"盯盘扫描事件","category":"模拟交易","desc":"规则巡检触发的价格/形态事件","source":"模拟交易"},
    "users": {"name":"知识库·用户","category":"知识库","desc":"用户","source":"知识库"},
    "trading_accounts": {"name":"知识库·交易账户","category":"知识库","desc":"实盘/模拟/测试账户","source":"知识库"},
    "stocks": {"name":"知识库·股票主表","category":"知识库","desc":"股票主数据","source":"知识库"},
    "strategy": {"name":"知识库·策略","category":"知识库","desc":"规则与实验的父对象","source":"知识库"},
    "market_snapshot": {"name":"知识库·市场快照","category":"知识库","desc":"当日市场事实：涨跌、成交、涨停跌停，不含状态判断","source":"知识库"},
    "market_memory": {"name":"知识库·市场记忆","category":"知识库","desc":"一个交易日的完整市场记忆","source":"知识库"},
    "market_regime": {"name":"知识库·市场状态","category":"知识库","desc":"对事实的状态判断与依据","source":"知识库"},
    "market_event": {"name":"知识库·市场事件","category":"知识库","desc":"当天盘中事件","source":"知识库"},
    "market_hypothesis": {"name":"知识库·待验证判断","category":"知识库","desc":"尚未当成经验的市场假设","source":"知识库"},
    "opportunity": {"name":"知识库·机会","category":"知识库","desc":"发现但未必成交的机会","source":"知识库"},
    "trade_thesis": {"name":"知识库·交易假设","category":"知识库","desc":"当时为什么值得做","source":"知识库"},
    "trade_execution": {"name":"知识库·执行","category":"知识库","desc":"实际成交","source":"知识库"},
    "trade_outcome": {"name":"知识库·结果","category":"知识库","desc":"盈亏与逻辑是否正确分开","source":"知识库"},
    "attribution": {"name":"知识库·归因","category":"知识库","desc":"这笔结果的主因","source":"知识库"},
    "lesson": {"name":"知识库·经验","category":"知识库","desc":"从交易里抽出的经验","source":"知识库"},
    "rule": {"name":"知识库·规则","category":"知识库","desc":"可执行的个人交易规则","source":"知识库"},
    "external_knowledge": {"name":"知识库·外部方法","category":"知识库","desc":"别人的方法，不直接当规则","source":"知识库"},
    "experiment": {"name":"知识库·实验","category":"知识库","desc":"新想法先进入实验","source":"知识库"},
}


def _ser(val):
    """序列化任意值为 JSON 安全类型"""
    if pd.isna(val): return None
    if isinstance(val, (datetime, date, time)): return str(val)
    if hasattr(val, "item"): return val.item()
    if isinstance(val, bytes): return val.decode("utf-8", errors="replace")
    if isinstance(val, (int, float, str, bool, type(None))): return val
    return str(val)


# ═══════════════════════════════════════════════════════
# Storage Service
# ═══════════════════════════════════════════════════════

class StorageService:
    def __init__(self):
        self._engine = None

    @property
    def engine(self):
        if self._engine is None:
            try:
                self._engine = storage_manager.get_postgres_engine()
            except Exception as e:
                logger.warning(f"PG 引擎获取失败: {e}")
        return self._engine

    # ─── 统一状态检查 ─────────────────────────────────

    def _probe_storage_status(self) -> Dict[str, Any]:
        """同步探测（须在线程池跑，避免堵死 uvicorn 事件循环）。"""
        services: Dict[str, Any] = {}

        # PostgreSQL
        try:
            pg = self.engine
            if not pg:
                services["postgresql"] = {
                    "name": "PostgreSQL + TimescaleDB", "status": "disconnected",
                    "port": 15432, "desc": "引擎未初始化（检查 POSTGRES_* 与依赖）",
                }
            else:
                with pg.connect() as c:
                    row = c.execute(sa_text("SELECT current_database(), current_user")).fetchone()
                services["postgresql"] = {
                    "name": "PostgreSQL + TimescaleDB", "status": "online",
                    "port": 15432, "desc": f"结构化数据 & 时序K线 · {row[0]}@{row[1]}",
                }
        except Exception as e:
            services["postgresql"] = {
                "name": "PostgreSQL + TimescaleDB", "status": "error",
                "port": 15432, "desc": f"连接失败: {str(e)[:80]}",
            }

        # InfluxDB（短超时，避免 health/鉴权卡住）
        def _probe_influx() -> Dict[str, Any]:
            influx = storage_manager.get_influx_client()
            if not influx:
                return {
                    "name": "InfluxDB", "status": "disconnected",
                    "port": 8086, "desc": "未初始化",
                }
            health = influx.health()
            ok = getattr(health, "status", None) == "pass" or str(
                getattr(health, "status", "")
            ).lower() in ("pass", "ok")
            return {
                "name": "InfluxDB",
                "status": "online" if ok else "error",
                "port": 8086,
                "desc": "高频行情 & 分钟线" if ok else f"health={getattr(health, 'status', health)}",
            }

        services["influxdb"] = _with_timeout(
            _probe_influx,
            fallback={
                "name": "InfluxDB", "status": "error",
                "port": 8086, "desc": "探测超时（可能 token/网络异常）",
            },
        )

        # Redis
        def _probe_redis() -> Dict[str, Any]:
            redis = storage_manager.get_redis_client()
            if not redis:
                return {
                    "name": "Redis", "status": "disconnected",
                    "port": 16379, "desc": "未初始化或无法连接",
                }
            redis.ping()
            return {
                "name": "Redis", "status": "online",
                "port": 16379, "desc": "实时缓存",
            }

        services["redis"] = _with_timeout(
            _probe_redis,
            fallback={
                "name": "Redis", "status": "error",
                "port": 16379, "desc": "探测超时",
            },
        )

        # MinIO（容器未起时 TCP 默认超时很长，必须限时）
        def _probe_minio() -> Dict[str, Any]:
            minio = storage_manager.get_minio_client()
            if not minio:
                return {
                    "name": "MinIO", "status": "disconnected",
                    "port": 9000, "desc": "未初始化或无法连接",
                }
            count = len(minio.list_buckets())
            return {
                "name": "MinIO", "status": "online",
                "port": 9000, "desc": f"数据湖 ({count} buckets)",
            }

        services["minio"] = _with_timeout(
            _probe_minio,
            timeout=2.0,
            fallback={
                "name": "MinIO", "status": "disconnected",
                "port": 9000, "desc": "未启动或探测超时（可选服务）",
            },
        )

        return services

    async def get_storage_status(self) -> Dict[str, Any]:
        """获取所有存储后端的连接状态（各后端独立探测，互不拖死）"""
        return await asyncio.to_thread(self._probe_storage_status)

    # ─── PostgreSQL ───────────────────────────────────

    def _list_pg_tables_sync(self) -> List[Dict]:
        """列表只用估算行数，禁止对超表做全表 COUNT（会堵死 API）。"""
        engine = self.engine
        if not engine:
            return self._pg_fallback()

        try:
            tsql = (
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name"
            )
            tdf = pd.read_sql(sa_text(tsql), engine)
            if tdf.empty:
                return self._pg_fallback()

            # 普通表：pg_class.reltuples；Timescale 超表父表常为 0，改用 chunk 估算之和
            esql = """
                SELECT c.relname AS t,
                       COALESCE(
                         (
                           SELECT SUM(GREATEST(cc.reltuples, 0))::bigint
                           FROM timescaledb_information.chunks ch
                           JOIN pg_class cc
                             ON cc.relname = ch.chunk_name
                           JOIN pg_namespace nn
                             ON nn.oid = cc.relnamespace AND nn.nspname = ch.chunk_schema
                           WHERE ch.hypertable_schema = 'public'
                             AND ch.hypertable_name = c.relname
                         ),
                         GREATEST(c.reltuples, 0)::bigint
                       ) AS n
                FROM pg_class c
                JOIN pg_namespace ns ON ns.oid = c.relnamespace
                WHERE ns.nspname = 'public' AND c.relkind = 'r'
            """
            try:
                edf = pd.read_sql(sa_text(esql), engine)
            except Exception:
                edf = pd.read_sql(
                    sa_text(
                        "SELECT c.relname AS t, GREATEST(c.reltuples, 0)::bigint AS n "
                        "FROM pg_class c JOIN pg_namespace ns ON ns.oid=c.relnamespace "
                        "WHERE ns.nspname='public' AND c.relkind='r'"
                    ),
                    engine,
                )
            emap = {r["t"]: int(r["n"] or 0) for _, r in edf.iterrows()}

            custom_meta = {}
            try:
                from server.custom_table_service import custom_table_service

                custom_meta = custom_table_service.meta_map()
            except Exception:
                custom_meta = {}

            results = []
            for _, row in tdf.iterrows():
                tn = row["table_name"]
                if is_hidden_pg_table(tn):
                    continue
                approx = max(0, int(emap.get(tn, 0)))
                info = PG_TABLES_INFO.get(tn) or custom_meta.get(tn) or {}
                results.append({
                    "name": tn,
                    "label": info.get("name", tn),
                    "category": info.get("category", "其他"),
                    "desc": info.get("desc", ""),
                    "source": info.get("source", ""),
                    "rows": approx,
                    "has_data": approx > 0,
                    "rows_approx": True,
                    "custom": tn in custom_meta,
                })
            results.sort(key=lambda x: (
                0 if x.get("custom") else 1,
                x["category"],
                not x["has_data"],
                -x["rows"],
            ))
            return results
        except Exception as e:
            logger.error(f"PG: {e}")
            return self._pg_fallback()

    async def get_pg_tables(self) -> List[Dict]:
        return await asyncio.to_thread(self._list_pg_tables_sync)

    def _pg_fallback(self):
        return [{"name": tn, "label": i.get("name",tn), "category": i.get("category","其他"),
                 "desc": i.get("desc",""), "source": i.get("source",""), "rows":-1, "has_data":False}
                for tn, i in PG_TABLES_INFO.items() if not is_hidden_pg_table(tn)]

    def _pg_preview_sync(self, table: str, limit: int = 50, offset: int = 0):
        engine = self.engine
        if not engine:
            return {"error": "数据库未连接"}
        if is_hidden_pg_table(table):
            return {"error": "内部配置表不在存储层展示"}
        if not table.replace("_", "").isalnum():
            return {"error": f"非法表名: {table}"}
        limit = max(1, min(limit, 1000))
        offset = max(0, offset)

        try:
            col_sql = (
                "SELECT column_name,data_type,is_nullable FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=:t ORDER BY ordinal_position"
            )
            cdf = pd.read_sql(sa_text(col_sql), engine, params={"t": table})
            if cdf.empty:
                return {"error": f"表 {table} 不存在"}
            cols = [
                {"name": r["column_name"], "type": r["data_type"], "nullable": r["is_nullable"] == "YES"}
                for _, r in cdf.iterrows()
            ]
            cols = enrich_columns(cols, table)

            try:
                rsql = f'SELECT * FROM "{table}" ORDER BY 1 LIMIT {limit} OFFSET {offset}'
                rdf = pd.read_sql(sa_text(rsql), engine)
            except Exception:
                rsql = f'SELECT * FROM "{table}" LIMIT {limit} OFFSET {offset}'
                rdf = pd.read_sql(sa_text(rsql), engine)

            rows = [{c: _ser(r[c]) for c in rdf.columns} for _, r in rdf.iterrows()]

            # 大表/超表避免精确 COUNT(*)；用估算，必要时再精确小表
            total = 0
            try:
                est = pd.read_sql(
                    sa_text(
                        """
                        SELECT COALESCE(
                          (
                            SELECT SUM(GREATEST(cc.reltuples, 0))::bigint
                            FROM timescaledb_information.chunks ch
                            JOIN pg_class cc ON cc.relname = ch.chunk_name
                            JOIN pg_namespace nn
                              ON nn.oid = cc.relnamespace AND nn.nspname = ch.chunk_schema
                            WHERE ch.hypertable_schema = 'public' AND ch.hypertable_name = :t
                          ),
                          (
                            SELECT GREATEST(c.reltuples, 0)::bigint
                            FROM pg_class c
                            JOIN pg_namespace ns ON ns.oid = c.relnamespace
                            WHERE ns.nspname = 'public' AND c.relname = :t
                          ),
                          0
                        ) AS n
                        """
                    ),
                    engine,
                    params={"t": table},
                )
                total = int(est["n"].iloc[0] or 0)
            except Exception:
                total = 0
            if total <= 0 and len(rows) < limit:
                total = offset + len(rows)
            elif total <= 0:
                # 小表才允许精确计数
                try:
                    total = int(
                        pd.read_sql(sa_text(f'SELECT COUNT(*) AS c FROM "{table}"'), engine)["c"].iloc[0]
                    )
                except Exception:
                    total = offset + len(rows) + (1 if len(rows) >= limit else 0)

            info = PG_TABLES_INFO.get(table)
            if not info:
                try:
                    from server.custom_table_service import custom_table_service

                    info = custom_table_service.meta_map().get(table)
                except Exception:
                    info = None
            info = info or {"name": table, "category": "其他", "desc": "", "source": ""}
            return {
                "name": table,
                "label": info.get("name", table),
                "info": info,
                "columns": cols,
                "rows": rows,
                "total_rows": total,
                "preview_count": len(rows),
                "offset": offset,
                "limit": limit,
                "has_more": (offset + limit) < total or (len(rows) >= limit and total <= 0),
            }
        except Exception as e:
            return {"error": str(e)}

    async def get_pg_preview(self, table: str, limit: int = 50, offset: int = 0):
        return await asyncio.to_thread(self._pg_preview_sync, table, limit, offset)

    # ─── InfluxDB ─────────────────────────────────────

    async def get_influx_items(self) -> List[Dict]:
        """获取 InfluxDB 的 buckets 和 measurements"""
        client = storage_manager.get_influx_client()
        if not client: return []
        try:
            from influxdb_client import BucketsApi, QueryApi
            items = []
            # Buckets
            ba = client.buckets_api()
            buckets = ba.find_buckets().buckets
            for b in buckets:
                items.append({
                    "name": f"bucket:{b.name}", "label": f"📦 {b.name}",
                    "category": "Bucket", "desc": f"InfluxDB Bucket (ID: {b.id})",
                    "source": "InfluxDB", "rows": -1, "has_data": True,
                })
                # Measurements in each bucket
                try:
                    qa = client.query_api()
                    q = f'import "influxdata/influxdb/schema" schema.measurements(bucket: "{b.name}")'
                    tables = qa.query(q, org=storage_manager._connections.get('influx_org', 'quant'))
                    for table in tables:
                        for record in table.records:
                            mname = record.values.get("_value","")
                            if mname:
                                items.append({
                                    "name": f"measurement:{b.name}/{mname}",
                                    "label": f"📊 {mname}",
                                    "category": f"Bucket: {b.name}",
                                    "desc": f"Measurement in {b.name}",
                                    "source": "InfluxDB", "rows": -1, "has_data": True,
                                })
                except: pass
            return items
        except Exception as e:
            logger.warning(f"InfluxDB 列表失败: {e}")
            return [{"name":"influx:info","label":"InfluxDB","category":"信息","desc":f"连接正常但无法列出: {e}","source":"InfluxDB","rows":-1,"has_data":False}]

    async def get_influx_preview(self, item: str, limit: int=50):
        """预览 InfluxDB measurement 最近N条"""
        client = storage_manager.get_influx_client()
        if not client: return {"error":"InfluxDB 未连接"}
        try:
            # 解析 bucket/measurement
            if item.startswith("measurement:"):
                _, rest = item.split(":", 1)
                bucket, measurement = rest.split("/", 1)
            elif item.startswith("bucket:"):
                bucket = item.split(":", 1)[1]
                return {"name": item, "label": bucket, "info": {"desc": f"Bucket: {bucket}"},
                        "columns": [], "rows": [], "total_rows": 0, "has_more": False}
            else:
                return {"error": f"无法解析: {item}"}

            qa = client.query_api()
            q = f'from(bucket: "{bucket}") |> range(start: -30d) |> filter(fn: (r) => r._measurement == "{measurement}") |> limit(n: {limit})'
            tables = qa.query(q, org=storage_manager._connections.get('influx_org', 'quant') or 'quant')

            rows = []
            cols_set = set()
            for table in tables:
                for record in table.records:
                    row = {
                        "_time": str(record.get_time()),
                        "_field": record.get_field(),
                        "_value": _ser(record.get_value()),
                    }
                    for k, v in record.values.items():
                        if k not in ("_time","_field","_value","_measurement","_start","_stop","result","table"):
                            row[k] = _ser(v)
                    rows.append(row)
                    cols_set.update(row.keys())

            cols = [{"name": c, "type": "string", "nullable": True} for c in sorted(cols_set)]
            cols = enrich_columns(cols, "influx")
            return {"name": item, "label": f"{bucket}/{measurement}", "info": {"desc": f"Measurement: {measurement} in {bucket}", "category": "InfluxDB"},
                    "columns": cols, "rows": rows[:limit], "total_rows": len(rows), "preview_count": len(rows),
                    "offset": 0, "limit": limit, "has_more": False}
        except Exception as e:
            return {"error": str(e)}

    # ─── Redis ───────────────────────────────────────

    async def get_redis_items(self) -> List[Dict]:
        """获取 Redis 中的 key 列表（按模式分组）"""
        client = storage_manager.get_redis_client()
        if not client: return []
        try:
            patterns = ["*"]
            items = []
            seen = set()
            for pat in patterns:
                try:
                    for key in client.scan_iter(match=pat, count=100):
                        if key in seen: continue
                        seen.add(key)
                        ktype = client.type(key) or "string"
                        ttl = client.ttl(key)
                        items.append({
                            "name": key, "label": key,
                            "category": f"类型: {ktype}",
                            "desc": f"TTL: {ttl if ttl>0 else '永久'}",
                            "source": "Redis", "rows": -1, "has_data": True,
                        })
                        if len(items) >= 200: break
                except: pass
                if len(items) >= 200: break

            if not items:
                items = [{"name":"redis:empty","label":"Redis 暂无 Key","category":"信息",
                          "desc":"Redis 已连接但暂无数据","source":"Redis","rows":0,"has_data":False}]
            return items
        except Exception as e:
            logger.warning(f"Redis 列表失败: {e}")
            return []

    async def get_redis_preview(self, key: str, limit: int=50):
        """预览 Redis key 的值"""
        client = storage_manager.get_redis_client()
        if not client: return {"error":"Redis 未连接"}
        try:
            ktype = client.type(key)
            ttl = client.ttl(key)
            val = None

            if ktype == "string" or ktype == "none":
                val = client.get(key)
            elif ktype == "hash":
                val = client.hgetall(key)
            elif ktype == "list":
                val = client.lrange(key, 0, limit-1)
            elif ktype == "set":
                val = list(client.smembers(key))[:limit]
            elif ktype == "zset":
                val = client.zrange(key, 0, limit-1, withscores=True)

            rows = []
            if isinstance(val, dict):
                rows = [{"field": k, "value": _ser(v)} for k, v in val.items()]
            elif isinstance(val, list):
                if val and isinstance(val[0], tuple):
                    rows = [{"member": _ser(v[0]), "score": v[1]} for v in val]
                else:
                    rows = [{"index": i, "value": _ser(v)} for i, v in enumerate(val)]
            elif val is not None:
                rows = [{"key": key, "value": _ser(val)}]

            cols = [{"name": c, "type": "string", "nullable": True} for c in (rows[0].keys() if rows else ["key","value"])]
            cols = enrich_columns(cols, "redis")

            return {"name": key, "label": key, "info": {"desc": f"类型: {ktype}, TTL: {ttl if ttl>0 else '永久'}", "category": "Redis"},
                    "columns": cols, "rows": rows, "total_rows": len(rows), "preview_count": len(rows),
                    "offset": 0, "limit": limit, "has_more": False}
        except Exception as e:
            return {"error": str(e)}

    # ─── MinIO ───────────────────────────────────────

    async def get_minio_items(self) -> List[Dict]:
        """获取 MinIO 的 buckets"""
        client = storage_manager.get_minio_client()
        if not client: return []
        try:
            buckets = client.list_buckets()
            items = []
            for b in buckets:
                items.append({
                    "name": b.name, "label": f"🪣 {b.name}",
                    "category": "Bucket",
                    "desc": f"创建于 {b.creation_date.strftime('%Y-%m-%d') if b.creation_date else '未知'}",
                    "source": "MinIO", "rows": -1, "has_data": True,
                })
            if not items:
                items = [{"name":"minio:empty","label":"暂无 Bucket","category":"信息",
                          "desc":"MinIO 已连接但无 Bucket","source":"MinIO","rows":0,"has_data":False}]
            return items
        except Exception as e:
            logger.warning(f"MinIO 列表失败: {e}")
            return []

    async def get_minio_preview(self, bucket: str, limit: int=50):
        """预览 MinIO bucket 中的对象列表"""
        client = storage_manager.get_minio_client()
        if not client: return {"error":"MinIO 未连接"}
        try:
            objects = list(client.list_objects(bucket, recursive=False))[:limit]
            rows = []
            for obj in objects:
                rows.append({
                    "object_name": obj.object_name,
                    "size": obj.size,
                    "last_modified": str(obj.last_modified) if obj.last_modified else "",
                    "etag": obj.etag or "",
                    "content_type": obj.content_type or "",
                })
            cols = [{"name":"object_name","type":"text","nullable":False},
                    {"name":"size","type":"bigint","nullable":True},
                    {"name":"last_modified","type":"timestamp","nullable":True},
                    {"name":"content_type","type":"text","nullable":True}]
            cols = enrich_columns(cols, "minio")

            return {"name": bucket, "label": f"🪣 {bucket}", "info": {"desc": f"MinIO Bucket 对象列表", "category": "MinIO"},
                    "columns": cols, "rows": rows, "total_rows": len(rows), "preview_count": len(rows),
                    "offset": 0, "limit": limit, "has_more": len(objects) >= limit}
        except Exception as e:
            return {"error": str(e)}

    # ─── 统一入口 ─────────────────────────────────────

    async def get_items(self, backend: str) -> List[Dict]:
        items = []
        if backend == "postgresql":
            items = await self.get_pg_tables()
        elif backend == "influxdb":
            items = await self.get_influx_items()
        elif backend == "redis":
            items = await self.get_redis_items()
        elif backend == "minio":
            items = await self.get_minio_items()
        return items

    async def get_preview(self, backend: str, name: str, limit: int=50, offset: int=0):
        if backend == "postgresql":
            return await self.get_pg_preview(name, limit, offset)
        elif backend == "influxdb":
            return await self.get_influx_preview(name, limit)
        elif backend == "redis":
            return await self.get_redis_preview(name, limit)
        elif backend == "minio":
            return await self.get_minio_preview(name, limit)
        return {"error": f"未知后端: {backend}"}

    def _pg_export_rows_sync(self, table: str, limit: int = 50000) -> Dict[str, Any]:
        """拉取用于导出的行（上限保护，避免超大表撑爆内存）。"""
        engine = self.engine
        if not engine:
            return {"error": "数据库未连接"}
        if is_hidden_pg_table(table):
            return {"error": "内部配置表不在存储层展示"}
        if not table.replace("_", "").isalnum():
            return {"error": f"非法表名: {table}"}
        limit = max(1, min(int(limit or 50000), 100_000))
        try:
            col_sql = (
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=:t ORDER BY ordinal_position"
            )
            cdf = pd.read_sql(sa_text(col_sql), engine, params={"t": table})
            if cdf.empty:
                return {"error": f"表 {table} 不存在"}
            columns = [str(x) for x in cdf["column_name"].tolist()]
            # 多取 1 行判断是否截断
            fetch_n = limit + 1
            try:
                rsql = f'SELECT * FROM "{table}" ORDER BY 1 LIMIT {fetch_n}'
                rdf = pd.read_sql(sa_text(rsql), engine)
            except Exception:
                rsql = f'SELECT * FROM "{table}" LIMIT {fetch_n}'
                rdf = pd.read_sql(sa_text(rsql), engine)
            truncated = len(rdf) > limit
            if truncated:
                rdf = rdf.iloc[:limit]
            rows = [{c: _ser(r[c]) for c in rdf.columns} for _, r in rdf.iterrows()]
            info = PG_TABLES_INFO.get(table)
            if not info:
                try:
                    from server.custom_table_service import custom_table_service

                    info = custom_table_service.meta_map().get(table)
                except Exception:
                    info = None
            label = (info or {}).get("name") or table
            return {
                "name": table,
                "label": label,
                "columns": columns,
                "rows": rows,
                "row_count": len(rows),
                "truncated": truncated,
                "limit": limit,
            }
        except Exception as e:
            return {"error": str(e)}

    @staticmethod
    def _cell_text(val: Any) -> str:
        if val is None:
            return ""
        if isinstance(val, bool):
            return "true" if val else "false"
        if isinstance(val, (dict, list)):
            import json

            return json.dumps(val, ensure_ascii=False)
        return str(val)

    def _format_csv(self, columns: List[str], rows: List[Dict[str, Any]]) -> str:
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        writer.writerow(columns)
        for row in rows:
            writer.writerow([self._cell_text(row.get(c)) for c in columns])
        # Excel 友好：UTF-8 BOM
        return "\ufeff" + buf.getvalue()

    def _format_markdown(
        self,
        *,
        name: str,
        label: str,
        columns: List[str],
        rows: List[Dict[str, Any]],
        truncated: bool,
        limit: int,
    ) -> str:
        def esc(s: str) -> str:
            return s.replace("|", "\\|").replace("\n", "<br>")

        lines = [
            f"# {label}",
            "",
            f"- 表名: `{name}`",
            f"- 导出行数: {len(rows)}" + (f"（已截断，上限 {limit}）" if truncated else ""),
            f"- 导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
        ]
        if not columns:
            lines.append("_无列_")
            return "\n".join(lines) + "\n"
        lines.append("| " + " | ".join(esc(c) for c in columns) + " |")
        lines.append("| " + " | ".join("---" for _ in columns) + " |")
        for row in rows:
            lines.append("| " + " | ".join(esc(self._cell_text(row.get(c))) for c in columns) + " |")
        lines.append("")
        return "\n".join(lines)

    async def export_table(
        self,
        backend: str,
        name: str,
        fmt: str = "csv",
        limit: int = 50000,
    ) -> Dict[str, Any]:
        """
        导出数据项为 csv / markdown。
        返回: {content, filename, media_type, row_count, truncated} 或 {error}
        """
        fmt_n = (fmt or "csv").strip().lower()
        if fmt_n in ("md", "markdown"):
            fmt_n = "markdown"
        if fmt_n not in ("csv", "markdown"):
            return {"error": "format 仅支持 csv 或 markdown"}

        if backend == "postgresql":
            data = await asyncio.to_thread(self._pg_export_rows_sync, name, limit)
        else:
            # 非 PG：复用预览（上限仍受各后端自身限制）
            preview = await self.get_preview(backend, name, limit=min(limit, 1000), offset=0)
            if "error" in preview:
                data = preview
            else:
                cols = [c["name"] if isinstance(c, dict) else str(c) for c in (preview.get("columns") or [])]
                if not cols and preview.get("rows"):
                    cols = list(preview["rows"][0].keys())
                data = {
                    "name": preview.get("name") or name,
                    "label": preview.get("label") or name,
                    "columns": cols,
                    "rows": preview.get("rows") or [],
                    "row_count": len(preview.get("rows") or []),
                    "truncated": bool(preview.get("has_more")),
                    "limit": limit,
                }

        if "error" in data:
            return data

        columns = data["columns"]
        rows = data["rows"]
        table = data["name"]
        label = data.get("label") or table
        truncated = bool(data.get("truncated"))
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in table) or "export"

        if fmt_n == "csv":
            content = self._format_csv(columns, rows)
            filename = f"{safe}.csv"
            media_type = "text/csv; charset=utf-8"
        else:
            content = self._format_markdown(
                name=table,
                label=label,
                columns=columns,
                rows=rows,
                truncated=truncated,
                limit=int(data.get("limit") or limit),
            )
            filename = f"{safe}.md"
            media_type = "text/markdown; charset=utf-8"

        return {
            "content": content,
            "filename": filename,
            "media_type": media_type,
            "row_count": len(rows),
            "truncated": truncated,
            "format": fmt_n,
            "name": table,
        }

    async def get_backend_stats(self, backend: str) -> Dict:
        """获取指定后端的统计信息"""
        items = await self.get_items(backend)
        total = len(items)
        has_data = sum(1 for i in items if i.get("has_data"))
        categories = sorted(set(i.get("category","") for i in items))
        return {
            "backend": backend, "total_items": total,
            "items_with_data": has_data, "categories": categories,
            "categories_count": len(categories),
        }


storage_service = StorageService()

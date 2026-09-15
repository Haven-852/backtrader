"""
Storage Service - 多后端存储层状态监控
支持: PostgreSQL · InfluxDB · Redis · MinIO
"""

import sys, os, logging
from typing import Dict, Any, List
from datetime import datetime, date, time

import pandas as pd
from sqlalchemy import text as sa_text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data_layer.db_manager import storage_manager
from server.column_labels import enrich_columns

logger = logging.getLogger(__name__)

# ─── PostgreSQL 表描述 ─────────────────────────────────
PG_TABLES_INFO: Dict[str, Dict[str, str]] = {
    "ref_trade_cal": {"name":"交易日历","category":"参照数据","desc":"各交易所开休市日期","source":"Tushare trade_cal"},
    "ref_stock_basic": {"name":"股票主档","category":"参照数据","desc":"全市场证券基本信息","source":"Tushare stock_basic"},
    "ref_stock_namechange": {"name":"股票更名记录","category":"参照数据","desc":"历史名称变更记录","source":"Tushare namechange"},
    "ref_new_share": {"name":"新股发行","category":"参照数据","desc":"IPO发行信息","source":"Tushare new_share"},
    "ref_index_basic": {"name":"指数主档","category":"参照数据","desc":"指数基本信息","source":"Tushare index_basic"},
    "ref_etf_basic": {"name":"ETF主档","category":"参照数据","desc":"ETF基金基本信息","source":"Tushare fund_basic"},
    "ref_fund_basic": {"name":"基金主档","category":"参照数据","desc":"公募基金基本信息","source":"Tushare fund_basic"},
    "ref_policy_law": {"name":"政策法规库","category":"参照数据","desc":"监管政策与法律法规","source":"手动采集"},
    "ref_stk_limit_daily": {"name":"涨跌停价","category":"参照数据","desc":"每日涨跌停限制价格","source":"Tushare stk_limit"},
    "ref_suspend_d": {"name":"停复牌信息","category":"参照数据","desc":"停牌/复牌记录","source":"Tushare suspend_d"},
    "dash_watchlist": {"name":"看板自选股","category":"参照数据","desc":"行情看板自选及持续日更名单","source":"用户自选"},
    "strategy_screener_run": {"name":"选股运行","category":"策略分析","desc":"低于均线等选股任务记录","source":"策略选股器"},
    "strategy_screener_hit": {"name":"选股命中","category":"策略分析","desc":"选股命中标的及偏离幅度","source":"策略选股器"},
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
    "sector_dc_member": {"name":"东财板块成分","category":"板块行情","desc":"板块成分股列表","source":"Datahubco dc_member"},
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

    async def get_storage_status(self) -> Dict[str, Any]:
        """获取所有存储后端的连接状态（各后端独立探测，互不拖死）"""
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

        # InfluxDB
        try:
            influx = storage_manager.get_influx_client()
            if not influx:
                services["influxdb"] = {
                    "name": "InfluxDB", "status": "disconnected",
                    "port": 8086, "desc": "未初始化",
                }
            else:
                health = influx.health()
                ok = getattr(health, "status", None) == "pass" or str(getattr(health, "status", "")).lower() in ("pass", "ok")
                services["influxdb"] = {
                    "name": "InfluxDB",
                    "status": "online" if ok else "error",
                    "port": 8086,
                    "desc": "高频行情 & 分钟线" if ok else f"health={getattr(health, 'status', health)}",
                }
        except Exception as e:
            services["influxdb"] = {
                "name": "InfluxDB", "status": "error",
                "port": 8086, "desc": f"连接失败: {str(e)[:60]}",
            }

        # Redis
        try:
            redis = storage_manager.get_redis_client()
            if not redis:
                services["redis"] = {
                    "name": "Redis", "status": "disconnected",
                    "port": 16379, "desc": "未初始化或无法连接",
                }
            else:
                redis.ping()
                services["redis"] = {
                    "name": "Redis", "status": "online",
                    "port": 16379, "desc": "实时缓存",
                }
        except Exception as e:
            services["redis"] = {
                "name": "Redis", "status": "error",
                "port": 16379, "desc": str(e)[:60],
            }

        # MinIO
        try:
            minio = storage_manager.get_minio_client()
            if not minio:
                services["minio"] = {
                    "name": "MinIO", "status": "disconnected",
                    "port": 9000, "desc": "未初始化或无法连接",
                }
            else:
                count = len(minio.list_buckets())
                services["minio"] = {
                    "name": "MinIO", "status": "online",
                    "port": 9000, "desc": f"数据湖 ({count} buckets)",
                }
        except Exception as e:
            services["minio"] = {
                "name": "MinIO", "status": "error",
                "port": 9000, "desc": str(e)[:60],
            }

        return services

    # ─── PostgreSQL ───────────────────────────────────

    async def get_pg_tables(self) -> List[Dict]:
        engine = self.engine
        if not engine: return self._pg_fallback()

        try:
            tsql = "SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name"
            tdf = pd.read_sql(sa_text(tsql), engine)
            if tdf.empty: return self._pg_fallback()

            esql = "SELECT c.relname AS t, c.reltuples::bigint AS n FROM pg_class c JOIN pg_namespace ns ON ns.oid=c.relnamespace WHERE ns.nspname='public' AND c.relkind='r'"
            edf = pd.read_sql(sa_text(esql), engine)
            emap = {r["t"]: int(r["n"]) if r["n"] and r["n"]>0 else 0 for _, r in edf.iterrows()}

            results = []
            for _, row in tdf.iterrows():
                tn = row["table_name"]
                approx = emap.get(tn, 0)
                actual = approx
                if actual <= 0:
                    try:
                        actual = int(pd.read_sql(sa_text(f'SELECT COUNT(*) AS c FROM "{tn}"'), engine)["c"].iloc[0])
                    except: pass
                info = PG_TABLES_INFO.get(tn, {})
                results.append({"name": tn, "label": info.get("name", tn),
                                "category": info.get("category","其他"),
                                "desc": info.get("desc",""), "source": info.get("source",""),
                                "rows": actual, "has_data": actual>0})
            results.sort(key=lambda x: (x["category"], not x["has_data"], -x["rows"]))
            return results
        except Exception as e:
            logger.error(f"PG: {e}")
            return self._pg_fallback()

    def _pg_fallback(self):
        return [{"name": tn, "label": i.get("name",tn), "category": i.get("category","其他"),
                 "desc": i.get("desc",""), "source": i.get("source",""), "rows":-1, "has_data":False}
                for tn, i in PG_TABLES_INFO.items()]

    async def get_pg_preview(self, table: str, limit: int=50, offset: int=0):
        engine = self.engine
        if not engine: return {"error":"数据库未连接"}
        if not table.replace("_","").isalnum(): return {"error":f"非法表名: {table}"}
        limit = max(1, min(limit, 1000))
        offset = max(0, offset)

        try:
            col_sql = "SELECT column_name,data_type,is_nullable FROM information_schema.columns WHERE table_schema='public' AND table_name=:t ORDER BY ordinal_position"
            cdf = pd.read_sql(sa_text(col_sql), engine, params={"t": table})
            if cdf.empty: return {"error": f"表 {table} 不存在"}
            cols = [{"name":r["column_name"],"type":r["data_type"],"nullable":r["is_nullable"]=="YES"} for _,r in cdf.iterrows()]
            cols = enrich_columns(cols, table)

            try:
                rsql = f'SELECT * FROM "{table}" ORDER BY 1 LIMIT {limit} OFFSET {offset}'
                rdf = pd.read_sql(sa_text(rsql), engine)
            except:
                rsql = f'SELECT * FROM "{table}" LIMIT {limit} OFFSET {offset}'
                rdf = pd.read_sql(sa_text(rsql), engine)

            rows = [{c: _ser(r[c]) for c in rdf.columns} for _, r in rdf.iterrows()]
            total = int(pd.read_sql(sa_text(f'SELECT COUNT(*) AS c FROM "{table}"'), engine)["c"].iloc[0])

            info = PG_TABLES_INFO.get(table, {"name":table,"category":"其他","desc":"","source":""})
            return {"name": table, "label": info.get("name",table), "info": info,
                    "columns": cols, "rows": rows, "total_rows": total,
                    "preview_count": len(rows), "offset": offset, "limit": limit,
                    "has_more": (offset+limit)<total}
        except Exception as e:
            return {"error": str(e)}

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

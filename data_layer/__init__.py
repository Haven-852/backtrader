"""
Backtrader 存储层 v1.0
提供完整的多存储引擎支持架构 — 9大数据域全覆盖

架构依据:
    doc/architecture/02-agent-data-access.md v1.0

数据域:
    ├─ 基础行情 (日线/分钟线/复权/停复牌)
    ├─ 盘前股本/每日指标 (daily_basic)
    ├─ 集合竞价 (stk_auction)
    ├─ ETF分钟线 (fund_bar_mins)
    ├─ 财务基本面 (三表 + 指标 + 预告)
    ├─ 资金行为 (资金流向/龙虎榜/两融/大宗)
    ├─ 新闻资讯 (news + 全文搜索)
    ├─ 公司公告 (anns)
    ├─ 券商研报 (broker_report + 一致预期)
    ├─ 董秘互动 (board_secretary_interact)
    ├─ 政策法规库 (ref_policy_law)
    ├─ 指数 (index_daily/weight)
    └─ 基金ETF (fund_nav/daily/portfolio)

使用方式:
    from data_layer import storage_manager, config, tushare_collector
    from data_layer.db_manager import StorageManager

    storage_manager.initialize()
    storage_manager.ensure_schema()  # 自动建表

    # 查询分钟线
    df = storage_manager.query_minute_bars("000001.SZ", freq="1min")

    # 查询新闻
    df = storage_manager.query_news(keywords=["新能源"])

    # 查询研报
    df = storage_manager.query_broker_reports(symbol="000001.SZ")
"""

from .config import config, DatabaseConfig
from .db_manager import storage_manager, StorageManager
from .schema import SchemaManager

# Optional imports - collectors may not have all dependencies installed
try:
    from .data_loader import AShareDataLoader
except ImportError:
    AShareDataLoader = None

try:
    from .collectors.tushare_collector import TushareCollector
    tushare_collector = TushareCollector()
except ImportError:
    TushareCollector = None
    tushare_collector = None

__all__ = [
    # 核心
    "config",
    "storage_manager",
    "StorageManager",
    "DatabaseConfig",
    "SchemaManager",
    # 数据加载
    "AShareDataLoader",
    # 采集器
    "tushare_collector",
    "TushareCollector",
]

# 版本信息
__version__ = "1.0.0"
__description__ = (
    "完整存储层架构 v1.0 — 9大数据域全覆盖 "
    "(股票/ETF/财务/资金/新闻/公告/研报/指数/基金)"
)

__author__ = "QuantHub Spoke"

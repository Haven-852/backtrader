"""
存储层配置管理
基于02-agent-data-access.md架构设计，支持 InfluxDB, PostgreSQL+TimescaleDB, Redis, MinIO
新增多Token分级配置（每个独立权限模块使用独立Token接口）
"""
from dataclasses import dataclass
from typing import Dict, Any, Optional
import os
import logging
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger(__name__)


@dataclass
class StorageConfig:
    """存储配置基类"""
    host: str = "localhost"
    port: int = 8086
    username: str = "admin"
    password: str = "backtrader123"
    database: str = "market_data"


class DatabaseConfig:
    """完整存储层配置 + Tushare多Token分级管理"""

    # 接口组 → Token 环境变量名 映射
    TOKEN_GROUP_MAP: Dict[str, str] = {
        "basic":      "TUSHARE_TOKEN_BASIC",      # 基础行情 (日线/周线/月线/复权因子)
        "mins":       "TUSHARE_TOKEN_MINS",       # 分钟行情 (stk_mins 高频)
        "financial":  "TUSHARE_TOKEN_FINANCIAL",   # 财务基本面 (三大表+指标)
        "flow":       "TUSHARE_TOKEN_FLOW",        # 资金行为 (moneyflow/top_list/margin/block_trade)
        "index":      "TUSHARE_TOKEN_INDEX",       # 指数 (index_basic/daily/weight)
        "fund":       "TUSHARE_TOKEN_FUND",        # 基金ETF (fund_basic/nav/daily/portfolio)
        "news":       "TUSHARE_TOKEN_NEWS",        # 新闻公告 (news/major_news/anns)
        "report":     "TUSHARE_TOKEN_REPORT",      # 研报 (broker_reports)
        "default":    "TUSHARE_TOKEN",             # 通用兜底 Token
    }

    # 占位符标记（未配置真实Token）
    PLACEHOLDER_PREFIX = "your_tushare_token_"

    def __init__(self):
        self.base_dir = Path("E:/demo/backtrader")

        # --- InfluxDB 配置 (高频行情) ---
        self.influx = StorageConfig(
            host=os.getenv("INFLUX_HOST", "localhost"),
            port=int(os.getenv("INFLUX_PORT", 8086)),
            username=os.getenv("INFLUX_USER", "admin"),
            password=os.getenv("INFLUX_PASSWORD", "backtrader123"),
            database=os.getenv("INFLUX_BUCKET", "market_data")
        )
        # InfluxDB v2 额外配置
        self.influx_url = os.getenv("INFLUX_URL", "http://localhost:8086")
        self.influx_token = os.getenv("INFLUX_TOKEN", "")
        self.influx_org = os.getenv("INFLUX_ORG", "quant")
        self.influx_bucket = os.getenv("INFLUX_BUCKET", "market_data")

        # --- PostgreSQL + TimescaleDB (结构化数据 + 时序) ---
        self.postgres = {
            "host": os.getenv("POSTGRES_HOST", os.getenv("PG_HOST", "localhost")),
            "port": int(os.getenv("POSTGRES_PORT", os.getenv("PG_PORT", 15432))),
            "user": os.getenv("POSTGRES_USER", os.getenv("PG_USER", "quant")),
            "password": os.getenv("POSTGRES_PASSWORD", os.getenv("PG_PASSWORD", "backtrader123")),
            "database": os.getenv("POSTGRES_DB", os.getenv("PG_DB", "backtrader")),
            "timescaledb": True
        }

        # --- Redis (实时缓存) ---
        self.redis = {
            "host": os.getenv("REDIS_HOST", "localhost"),
            "port": int(os.getenv("REDIS_PORT", 16379)),
            "password": os.getenv("REDIS_PASSWORD", None),
            "db": 0
        }

        # --- MinIO (S3 兼容数据湖) ---
        self.minio = {
            "endpoint": os.getenv("MINIO_ENDPOINT", "localhost:9000"),
            "access_key": os.getenv("MINIO_ACCESS_KEY", "quant"),
            "secret_key": os.getenv("MINIO_SECRET_KEY", "backtrader123"),
            "secure": False,
            "bucket": "backtrader-data"
        }

        # --- Tushare API 地址 (小德发代理) ---
        self.tushare_api_url = os.getenv(
            "TUSHARE_API_URL",
            "http://tsy.xiaodefa.cn"
        )
        # --- Tushare 多Token缓存 ---
        self._tushare_tokens: Dict[str, Optional[str]] = {}

        # 预加载所有Token
        self._load_all_tushare_tokens()

    # ─── Tushare 多Token分级管理 ───────────────────────────────

    def _load_all_tushare_tokens(self) -> None:
        """从 .env 预加载所有 Tushare Token（支持多Token体系）"""
        for group, env_var in self.TOKEN_GROUP_MAP.items():
            token = os.getenv(env_var, "")
            if token and not token.startswith(self.PLACEHOLDER_PREFIX):
                self._tushare_tokens[group] = token
                logger.debug(f"Tushare Token [{group}] 已加载: {token[:8]}...")
            else:
                self._tushare_tokens[group] = None
                if token:
                    logger.warning(f"⚠️ Tushare Token [{group}] 未配置真实值 (占位符: {token})")
                else:
                    logger.info(f"Tushare Token [{group}] 未设置环境变量 {env_var}")

    def get_tushare_token(self, interface_group: str = "default") -> Optional[str]:
        """
        根据接口组返回对应的 Tushare Token
        支持分级回落：指定组 → default 组

        Args:
            interface_group: 接口组名称
                basic / mins / financial / flow / index / fund / news / report / default

        Returns:
            Token 字符串，如果未配置则返回 None
        """
        token = self._tushare_tokens.get(interface_group)
        if token:
            return token
        # 回落至 default
        fallback = self._tushare_tokens.get("default")
        if fallback and interface_group != "default":
            logger.debug(f"Tushare Token [{interface_group}] 未配置，回落至 default")
        return fallback

    def is_token_configured(self, interface_group: str = "default") -> bool:
        """检查指定接口组的 Token 是否已配置（非占位符）"""
        token = self.get_tushare_token(interface_group)
        return token is not None and not token.startswith(self.PLACEHOLDER_PREFIX)

    def list_configured_groups(self) -> list:
        """列出所有已配置真实 Token 的接口组"""
        return [
            g for g, t in self._tushare_tokens.items()
            if t is not None and not t.startswith(self.PLACEHOLDER_PREFIX)
        ]

    # ─── 数据库连接字符串 ─────────────────────────────────────

    def get_connection_string(self, db_type: str) -> str:
        """获取不同数据库的连接字符串"""
        if db_type == "postgres":
            return (
                f"postgresql://{self.postgres['user']}:{self.postgres['password']}"
                f"@{self.postgres['host']}:{self.postgres['port']}/{self.postgres['database']}"
            )
        elif db_type == "influx":
            return self.influx_url
        return ""

    def get_postgres_url(self) -> str:
        """获取 SQLAlchemy 兼容的 PostgreSQL 连接 URL"""
        return self.get_connection_string("postgres")

    def get_influx_url(self) -> str:
        """获取 InfluxDB 连接 URL"""
        return self.influx_url


# 单例配置
config = DatabaseConfig()

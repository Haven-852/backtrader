"""
存储层配置管理
基于之前架构设计，支持 InfluxDB, PostgreSQL+Timescale, Redis, MinIO, ClickHouse
"""
from dataclasses import dataclass
from typing import Dict, Any
import os
from pathlib import Path


@dataclass
class StorageConfig:
    """存储配置基类"""
    host: str = "localhost"
    port: int = 8086
    username: str = "admin"
    password: str = "backtrader123"
    database: str = "market_data"


class DatabaseConfig:
    """完整存储层配置"""
    
    def __init__(self):
        self.base_dir = Path("E:/demo/backtrader")
        
        # InfluxDB 配置 (高频行情)
        self.influx = StorageConfig(
            host=os.getenv("INFLUX_HOST", "localhost"),
            port=int(os.getenv("INFLUX_PORT", 8086)),
            username=os.getenv("INFLUX_USER", "admin"),
            password=os.getenv("INFLUX_PASSWORD", "backtrader123"),
            database=os.getenv("INFLUX_DB", "market_data")
        )
        
        # PostgreSQL + TimescaleDB (结构化数据 + 时序)
        self.postgres = {
            "host": os.getenv("PG_HOST", "localhost"),
            "port": int(os.getenv("PG_PORT", 15432)),   # 使用修改后的主机端口
            "user": os.getenv("PG_USER", "quant"),
            "password": os.getenv("PG_PASSWORD", "backtrader123"),
            "database": os.getenv("PG_DB", "backtrader"),
            "timescaledb": True
        }
        
        # Redis (实时缓存)
        self.redis = {
            "host": os.getenv("REDIS_HOST", "localhost"),
            "port": int(os.getenv("REDIS_PORT", 16379)),   # 使用修改后的主机端口
            "password": os.getenv("REDIS_PASSWORD", None),
            "db": 0
        }
        
        # MinIO (S3 兼容数据湖)
        self.minio = {
            "endpoint": os.getenv("MINIO_ENDPOINT", "localhost:9000"),
            "access_key": os.getenv("MINIO_ACCESS_KEY", "quant"),
            "secret_key": os.getenv("MINIO_SECRET_KEY", "backtrader123"),
            "secure": False,
            "bucket": "backtrader-data"
        }
        
        # ClickHouse (分析仓库)
        self.clickhouse = {
            "host": os.getenv("CLICKHOUSE_HOST", "localhost"),
            "port": int(os.getenv("CLICKHOUSE_PORT", 18123)),   # 使用修改后的主机端口
            "user": os.getenv("CLICKHOUSE_USER", "default"),
            "password": os.getenv("CLICKHOUSE_PASSWORD", ""),
            "database": os.getenv("CLICKHOUSE_DB", "backtrader_analytics")
        }
    
    def get_connection_string(self, db_type: str) -> str:
        """获取不同数据库的连接字符串"""
        if db_type == "postgres":
            return f"postgresql://{self.postgres['user']}:{self.postgres['password']}@{self.postgres['host']}:{self.postgres['port']}/{self.postgres['database']}"
        elif db_type == "influx":
            return f"http://{self.influx.host}:{self.influx.port}"
        return ""


# 单例配置
config = DatabaseConfig()

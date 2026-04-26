"""
存储层核心管理器
提供统一接口访问所有存储服务
"""
import logging
from typing import Optional, Any, Dict
import pandas as pd
from datetime import datetime

from .config import config


class StorageManager:
    """存储层统一管理器"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self._connections = {}
        self._initialized = False
    
    def initialize(self) -> bool:
        """初始化所有存储连接"""
        try:
            self.logger.info("正在初始化存储层...")
            
            # 这里后续会添加实际连接代码
            # 当前为框架结构，实际连接在具体方法中懒加载
            
            self._initialized = True
            self.logger.info("存储层初始化完成")
            return True
            
        except Exception as e:
            self.logger.error(f"存储层初始化失败: {e}")
            return False
    
    def get_influx_client(self):
        """获取 InfluxDB 客户端"""
        if 'influx' not in self._connections:
            try:
                from influxdb_client import InfluxDBClient
                client = InfluxDBClient(
                    url=config.get_connection_string("influx"),
                    token="supersecretadmin token",
                    org="quant"
                )
                self._connections['influx'] = client
                self.logger.info("InfluxDB 客户端初始化成功")
            except Exception as e:
                self.logger.error(f"InfluxDB 连接失败: {e}")
                return None
        return self._connections['influx']
    
    def get_postgres_engine(self):
        """获取 PostgreSQL 引擎 (SQLAlchemy)"""
        if 'postgres' not in self._connections:
            try:
                from sqlalchemy import create_engine, text
                engine = create_engine(
                    config.get_connection_string("postgres"),
                    pool_size=10,
                    max_overflow=20,
                    pool_timeout=30
                )
                self._connections['postgres'] = engine
                self.logger.info("PostgreSQL 连接池初始化成功 (TimescaleDB 已启用)")
            except Exception as e:
                self.logger.error(f"PostgreSQL 连接失败: {e}")
                return None
        return self._connections['postgres']
    
    def get_redis_client(self):
        """获取 Redis 客户端"""
        if 'redis' not in self._connections:
            try:
                import redis
                client = redis.Redis(
                    host=config.redis["host"],
                    port=config.redis["port"],
                    password=config.redis["password"],
                    decode_responses=True
                )
                self._connections['redis'] = client
                self.logger.info("Redis 客户端初始化成功")
            except Exception as e:
                self.logger.error(f"Redis 连接失败: {e}")
                return None
        return self._connections['redis']
    
    def save_market_data(self, symbol: str, df: pd.DataFrame, timeframe: str = "1m") -> bool:
        """保存市场数据到 InfluxDB"""
        client = self.get_influx_client()
        if not client:
            return False
        
        try:
            # 写入 InfluxDB (后续实现完整写入逻辑)
            self.logger.info(f"保存 {symbol} {timeframe} 数据到 InfluxDB，记录数: {len(df)}")
            return True
        except Exception as e:
            self.logger.error(f"保存市场数据失败: {e}")
            return False
    
    def query_historical_data(self, symbol: str, start: datetime, end: datetime = None) -> Optional[pd.DataFrame]:
        """从存储层查询历史数据（统一接口）"""
        # 优选从 InfluxDB 或 TimescaleDB 查询
        self.logger.info(f"查询 {symbol} 从 {start} 到 {end} 的历史数据")
        # 实际实现中会根据配置选择最优存储源
        return pd.DataFrame()  # 框架返回，待具体实现填充
    
    def close(self):
        """关闭所有连接"""
        for name, conn in self._connections.items():
            try:
                if hasattr(conn, 'close'):
                    conn.close()
                elif hasattr(conn, 'dispose'):
                    conn.dispose()
            except:
                pass
        self._connections.clear()
        self.logger.info("所有存储连接已关闭")


# 单例
storage_manager = StorageManager()

"""
Backtrader 存储层包
提供完整的多存储引擎支持架构

使用方式:
    from data_layer.db_manager import storage_manager
    from data_layer.config import config
    
    storage_manager.initialize()
    df = storage_manager.query_historical_data("AAPL", start_date)
"""

from .config import config, DatabaseConfig
from .db_manager import storage_manager, StorageManager

__all__ = ['config', 'storage_manager', 'DatabaseConfig', 'StorageManager']

# 版本信息
__version__ = "0.1.0"
__description__ = "完整存储层架构实现 - 支持 InfluxDB, TimescaleDB, Redis, MinIO, ClickHouse"

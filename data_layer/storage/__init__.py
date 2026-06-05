"""
PostgreSQL ORM 模型与管理器
仿照 Yuxi storage/postgres 架构，基于 SQLAlchemy 2.x async

结构:
    models.py   - 全部 30 张表的 ORM 模型定义
    manager.py  - 数据库连接池管理与 CRUD 封装

使用方式:
    from data_layer.storage import pg_manager
    from data_layer.storage.models import StockDaily

    await pg_manager.initialize()

    async with pg_manager.get_async_session_context() as session:
        result = await session.execute(
            select(StockDaily).where(StockDaily.ts_code == "000001.SZ")
        )
        ...

    # 或使用便捷查询方法
    df = await pg_manager.query("stock_daily", ts_code="000001.SZ")
"""

from .manager import pg_manager, PostgresManager
from .models import Base as MarketDataBase

__all__ = [
    "pg_manager",
    "PostgresManager",
    "MarketDataBase",
]

__version__ = "1.0.0"

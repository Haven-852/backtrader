"""
PostgreSQL 数据库管理器 —— 市场数据专用

仿照 Yuxi storage/postgres/manager.py 架构：
    - 基于 SQLAlchemy 2.x async + asyncpg
    - 连接池管理 (create_async_engine + async_sessionmaker)
    - 自动从 data_layer.config 读取 PostgreSQL 连接信息
    - 提供便捷的 DataFrame 查询接口

使用方式:
    from data_layer.storage import pg_manager
    from data_layer.storage.models import StockDaily

    await pg_manager.initialize()
    df = await pg_manager.query("stock_daily", ts_code="000001.SZ", limit=100)
"""

import logging
import os
from contextlib import asynccontextmanager
from typing import Any, Optional

import pandas as pd
from sqlalchemy import text as sa_text, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .models import TABLE_MODEL_MAP, Base as MarketDataBase

logger = logging.getLogger(__name__)

# 环境变量名（与 config.py 一致）
_POSTGRES_HOST = os.getenv("POSTGRES_HOST", os.getenv("PG_HOST", "localhost"))
_POSTGRES_PORT = os.getenv("POSTGRES_PORT", os.getenv("PG_PORT", "15432"))
_POSTGRES_USER = os.getenv("POSTGRES_USER", os.getenv("PG_USER", "quant"))
_POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", os.getenv("PG_PASSWORD", "backtrader123"))
_POSTGRES_DB = os.getenv("POSTGRES_DB", os.getenv("PG_DB", "backtrader"))


def _build_db_url() -> str:
    """构建 asyncpg 连接 URL"""
    return (
        f"postgresql+asyncpg://{_POSTGRES_USER}:{_POSTGRES_PASSWORD}"
        f"@{_POSTGRES_HOST}:{_POSTGRES_PORT}/{_POSTGRES_DB}"
    )


class PostgresManager:
    """PostgreSQL 异步数据库管理器（市场数据专用）"""

    def __init__(self):
        self.async_engine = None
        self.AsyncSession: Optional[async_sessionmaker] = None
        self._initialized = False

    # ─── 初始化 ─────────────────────────────────────────────────

    def initialize(self, db_url: str = None):
        """初始化异步数据库连接池

        Args:
            db_url: 可选，完整的数据库连接 URL；不传则从环境变量自动构建
        """
        if self._initialized:
            return

        url = db_url or _build_db_url()

        try:
            self.async_engine = create_async_engine(
                url,
                pool_size=10,
                max_overflow=20,
                pool_pre_ping=True,
                pool_recycle=1800,
                echo=False,
            )

            self.AsyncSession = async_sessionmaker(
                bind=self.async_engine,
                class_=AsyncSession,
                expire_on_commit=False,
            )

            self._initialized = True
            masked = url.split("@")[0] if "@" in url else url
            logger.info(f"PostgreSQL 异步管理器初始化完成: {masked}://***")

        except Exception as e:
            logger.error(f"PostgreSQL 初始化失败: {e}")
            raise

    def _check_initialized(self):
        if not self._initialized:
            raise RuntimeError(
                "PostgresManager 未初始化，请先调用 await pg_manager.initialize()"
            )

    # ─── 表管理 ─────────────────────────────────────────────────

    async def create_tables(self):
        """根据 ORM 模型创建所有表（如果不存在）"""
        self._check_initialized()
        async with self.async_engine.begin() as conn:
            await conn.run_sync(MarketDataBase.metadata.create_all)
        logger.info(f"✅ 所有 ORM 表已确保存在 ({len(TABLE_MODEL_MAP)} 张)")

    async def drop_tables(self, confirmed: bool = False):
        """删除所有 ORM 表 ⚠️ 危险操作"""
        if not confirmed:
            logger.warning("drop_tables 需要 confirmed=True 才执行")
            return
        self._check_initialized()
        async with self.async_engine.begin() as conn:
            await conn.run_sync(MarketDataBase.metadata.drop_all)
        logger.warning("⚠️ 所有 ORM 表已删除")

    async def table_exists(self, table_name: str) -> bool:
        """检查指定表是否存在"""
        self._check_initialized()
        async with self.async_engine.connect() as conn:
            result = await conn.execute(
                sa_text(
                    "SELECT EXISTS (SELECT FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name = :name)"
                ),
                {"name": table_name},
            )
            return result.scalar()

    async def list_tables(self) -> list[str]:
        """列出所有 public schema 下的表"""
        self._check_initialized()
        async with self.async_engine.connect() as conn:
            result = await conn.execute(
                sa_text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' ORDER BY table_name"
                )
            )
            return [row[0] for row in result.fetchall()]

    # ─── 会话管理 ───────────────────────────────────────────────

    async def get_async_session(self) -> AsyncSession:
        """获取异步会话"""
        self._check_initialized()
        return self.AsyncSession()

    @asynccontextmanager
    async def get_async_session_context(self):
        """异步会话上下文管理器（自动 commit/rollback）"""
        self._check_initialized()
        session = self.AsyncSession()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    async def execute(self, statement):
        """直接执行 SQL 语句（用于迁移脚本等）"""
        self._check_initialized()
        async with self.get_async_session_context() as session:
            return await session.execute(statement)

    async def add(self, instance):
        """添加 ORM 实例并提交"""
        self._check_initialized()
        async with self.get_async_session_context() as session:
            session.add(instance)

    async def add_all(self, instances: list):
        """批量添加 ORM 实例并提交"""
        self._check_initialized()
        async with self.get_async_session_context() as session:
            session.add_all(instances)

    # ─── 便捷查询 ───────────────────────────────────────────────

    async def query(
        self,
        table: str,
        columns: list[str] = None,
        ts_code: str = None,
        symbol: str = None,
        start_date: str = None,
        end_date: str = None,
        limit: int = None,
        offset: int = 0,
        order_by: str = None,
        order_desc: bool = True,
        **filters,
    ) -> pd.DataFrame:
        """通用查询接口 → 返回 pandas DataFrame

        Args:
            table:      表名（支持 ORM 已注册的所有表）
            columns:    指定列名列表，None=全部列
            ts_code:    股票代码过滤（快捷参数）
            symbol:     标的代码过滤（用于 bars_daily 等表）
            start_date: 起始日期（>=）
            end_date:   截止日期（<=）
            limit:      返回行数限制
            offset:     偏移量
            order_by:   排序字段
            order_desc: 是否降序
            **filters:  额外过滤条件 (column=value)

        Returns:
            pd.DataFrame
        """
        self._check_initialized()

        model = TABLE_MODEL_MAP.get(table)
        if model is None:
            raise ValueError(
                f"未知表名 '{table}'，可用表: {list(TABLE_MODEL_MAP.keys())}"
            )

        # 构建列选择
        if columns:
            cols = [getattr(model, c) for c in columns]
        else:
            cols = [model]

        stmt = select(*cols)

        # ts_code 过滤
        if ts_code and hasattr(model, "ts_code"):
            stmt = stmt.where(model.ts_code == ts_code)

        # symbol 过滤
        if symbol and hasattr(model, "symbol"):
            stmt = stmt.where(model.symbol == symbol)

        # trade_date 范围
        if start_date and hasattr(model, "trade_date"):
            stmt = stmt.where(model.trade_date >= start_date)
        if end_date and hasattr(model, "trade_date"):
            stmt = stmt.where(model.trade_date <= end_date)

        # nav_date 范围（fund_nav）
        if start_date and hasattr(model, "nav_date"):
            stmt = stmt.where(model.nav_date >= start_date)
        if end_date and hasattr(model, "nav_date"):
            stmt = stmt.where(model.nav_date <= end_date)

        # time 范围（bars_daily）
        if start_date and hasattr(model, "time"):
            stmt = stmt.where(model.time >= start_date)
        if end_date and hasattr(model, "time"):
            stmt = stmt.where(model.time <= end_date)

        # calc_date 范围（broker_report_consensus）
        if start_date and hasattr(model, "calc_date"):
            stmt = stmt.where(model.calc_date >= start_date)
        if end_date and hasattr(model, "calc_date"):
            stmt = stmt.where(model.calc_date <= end_date)

        # end_date 范围（财务表）
        if start_date and hasattr(model, "end_date"):
            stmt = stmt.where(model.end_date >= start_date)
        if end_date and hasattr(model, "end_date"):
            stmt = stmt.where(model.end_date <= end_date)

        # cal_date 范围（ref_trade_cal）
        if start_date and hasattr(model, "cal_date"):
            stmt = stmt.where(model.cal_date >= start_date)
        if end_date and hasattr(model, "cal_date"):
            stmt = stmt.where(model.cal_date <= end_date)

        # 额外过滤
        for key, val in filters.items():
            if hasattr(model, key):
                stmt = stmt.where(getattr(model, key) == val)

        # 排序
        if order_by and hasattr(model, order_by):
            order_col = getattr(model, order_by)
            stmt = stmt.order_by(order_col.desc() if order_desc else order_col.asc())
        elif hasattr(model, "trade_date"):
            stmt = stmt.order_by(
                model.trade_date.desc() if order_desc else model.trade_date.asc()
            )
        elif hasattr(model, "time"):
            stmt = stmt.order_by(
                model.time.desc() if order_desc else model.time.asc()
            )

        # 分页
        if limit:
            stmt = stmt.limit(limit)
        stmt = stmt.offset(offset)

        # 执行
        async with self.get_async_session_context() as session:
            result = await session.execute(stmt)
            rows = result.fetchall()

        if not rows:
            return pd.DataFrame()

        # SQLAlchemy 对单列查询时返回标量而非 Row
        if columns and len(columns) == 1:
            return pd.DataFrame({columns[0]: rows})

        # 获取列名
        col_names = columns if columns else model._cols
        return pd.DataFrame([dict(zip(col_names, row)) for row in rows], columns=col_names)

    async def query_sql(
        self, sql: str, params: dict = None
    ) -> pd.DataFrame:
        """直接执行原生 SQL → 返回 DataFrame

        Args:
            sql:    原生 SQL 语句（参数化用 :param_name）
            params: 参数字典

        Returns:
            pd.DataFrame
        """
        self._check_initialized()
        async with self.async_engine.connect() as conn:
            result = await conn.execute(sa_text(sql), params or {})
            rows = result.fetchall()
            if not rows:
                return pd.DataFrame()
            return pd.DataFrame(rows, columns=list(result.keys()))

    # ─── DataFrame 批量写入 ─────────────────────────────────────

    async def insert_dataframe(
        self,
        table: str,
        df: pd.DataFrame,
        if_exists: str = "append",
        chunksize: int = 5000,
    ) -> int:
        """将 DataFrame 写入 PostgreSQL 表（使用原生 asyncpg COPY）

        Args:
            table:     目标表名
            df:        要写入的 DataFrame
            if_exists: 'append' 或 'replace' (replace 会先 truncate)
            chunksize: 每批写入行数

        Returns:
            写入的总行数
        """
        self._check_initialized()

        if df.empty:
            return 0

        import asyncio

        async def _write():
            raw_conn = await self.async_engine.raw_connection()
            try:
                conn = raw_conn.driver_connection  # asyncpg connection

                if if_exists == "replace":
                    await conn.execute(f"TRUNCATE TABLE {table}")

                # 使用 asyncpg COPY 高速写入
                columns = df.columns.tolist()
                placeholders = ", ".join(f"${i+1}" for i in range(len(columns)))
                col_names = ", ".join(columns)

                total = 0
                for start in range(0, len(df), chunksize):
                    chunk = df.iloc[start : start + chunksize]
                    records = [
                        tuple(
                            None if pd.isna(v) else
                            v.isoformat() if hasattr(v, "isoformat") else
                            v
                            for v in row
                        )
                        for row in chunk.itertuples(index=False)
                    ]
                    await conn.copy_records_to_table(
                        table, records=records, columns=columns
                    )
                    total += len(chunk)

                return total
            finally:
                await raw_conn.close()

        return await asyncio.ensure_future(_write())

    # ─── CRUD 快捷方法 ──────────────────────────────────────────

    async def get_by_pk(self, table: str, **pk_values) -> Optional[Any]:
        """根据主键获取单条记录

        Args:
            table: 表名
            **pk_values: 主键列=值

        Returns:
            ORM 实例或 None
        """
        self._check_initialized()
        model = TABLE_MODEL_MAP.get(table)
        if model is None:
            raise ValueError(f"未知表名 '{table}'")

        async with self.get_async_session_context() as session:
            return await session.get(model, pk_values)

    async def count(self, table: str, **filters) -> int:
        """统计行数"""
        self._check_initialized()
        model = TABLE_MODEL_MAP.get(table)
        if model is None:
            raise ValueError(f"未知表名 '{table}'")

        from sqlalchemy import func

        stmt = select(func.count()).select_from(model)
        for key, val in filters.items():
            if hasattr(model, key):
                stmt = stmt.where(getattr(model, key) == val)

        async with self.get_async_session_context() as session:
            result = await session.execute(stmt)
            return result.scalar() or 0

    async def get_table_stats(self) -> pd.DataFrame:
        """获取所有表的行数统计"""
        self._check_initialized()
        stats = []
        for name, model in TABLE_MODEL_MAP.items():
            cnt = await self.count(name)
            stats.append({"table": name, "rows": cnt, "model": model.__name__})
        return pd.DataFrame(stats)

    # ─── 清理 ───────────────────────────────────────────────────

    async def close(self):
        """关闭连接池"""
        if self.async_engine:
            await self.async_engine.dispose()
            self._initialized = False
            logger.info("PostgreSQL 连接池已关闭")


# 单例
pg_manager = PostgresManager()

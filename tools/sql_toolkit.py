"""
SQL Toolkit - LangChain SQLDatabaseToolkit 封装
提供 5 个核心工具：List/Info/Check/Query/自然语言
"""

import sys
import os
import logging
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text as sa_text
from sqlalchemy.engine import Engine

from data_layer.db_manager import storage_manager

logger = logging.getLogger(__name__)

# ─── 安全白名单：只允许 SELECT / EXPLAIN / SHOW ───────
ALLOWED_SQL_PREFIXES = ("SELECT", "EXPLAIN", "SHOW", "WITH", "DESCRIBE", "DESC")
FORBIDDEN_KEYWORDS = (
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER",
    "CREATE", "TRUNCATE", "GRANT", "REVOKE", "VACUUM",
)


def _try_import_langchain():
    """延迟导入，避免未安装 langchain 时整模块不可用"""
    from langchain_community.utilities.sql_database import SQLDatabase
    from langchain_community.tools.sql_database.tool import (
        QuerySQLDataBaseTool,
        InfoSQLDatabaseTool,
        ListSQLDatabaseTool,
        QuerySQLCheckerTool,
    )
    return SQLDatabase, QuerySQLDataBaseTool, InfoSQLDatabaseTool, ListSQLDatabaseTool, QuerySQLCheckerTool



class SQLToolkitManager:
    """
    LangChain SQLDatabaseToolkit 的管理器

    基于 langchain_community 的 5 个工具：
    1. ListSQLDatabaseTool        → 列出所有表
    2. InfoSQLDatabaseTool        → 获取表结构信息
    3. QuerySQLCheckerTool        → 校验 SQL 语法
    4. QuerySQLDataBaseTool       → 执行 SQL 查询
    5. 自然语言转 SQL (额外封装)
    """

    def __init__(self):
        self._db: Any = None
        self._engine: Engine | None = None
        self._lc_ready: bool | None = None

    @property
    def engine(self) -> Engine | None:
        """懒加载 PostgreSQL 引擎"""
        if self._engine is None:
            try:
                pg_engine = storage_manager.get_postgres_engine()
                if pg_engine:
                    self._engine = pg_engine
            except Exception as e:
                logger.warning(f"无法获取 PG 引擎: {e}")
        return self._engine

    @property
    def db(self) -> Any:
        """懒加载 LangChain SQLDatabase（可选依赖）"""
        if self._db is None and self.engine is not None:
            try:
                SQLDatabase, *_ = _try_import_langchain()
                self._db = SQLDatabase(engine=self.engine)
                self._lc_ready = True
                logger.info("LangChain SQLDatabase 初始化成功")
            except Exception as e:
                self._lc_ready = False
                logger.warning(f"SQLDatabase/LangChain 不可用，将使用原生 SQL 回退: {e}")
        return self._db

    @staticmethod
    def _validate_readonly_sql(query: str) -> str | None:
        """
        验证 SQL 是否为只读查询，返回 None 表示合法，否则返回错误信息
        """
        stripped = query.strip()
        if not stripped:
            return "SQL 语句不能为空"

        # 去掉注释和多余空白
        sql_upper = stripped.upper()

        # 检查前缀
        is_allowed = any(sql_upper.startswith(prefix) for prefix in ALLOWED_SQL_PREFIXES)
        if not is_allowed:
            return f"仅允许 SELECT/EXPLAIN/SHOW/WITH 语句，当前语句以 '{stripped.split()[0].upper()}' 开头"

        # 检查禁止关键字
        for kw in FORBIDDEN_KEYWORDS:
            if kw in sql_upper.split():
                return f"禁止使用 {kw} 操作。仅允许只读查询。"

        return None

    # ─────────────────────────────────────────────────
    # Tool 1: 列出所有表
    # ─────────────────────────────────────────────────

    async def list_tables(self) -> dict:
        """列出数据库中的所有用户表"""
        if not self.engine:
            return {"ok": False, "error": "数据库未连接", "tables": []}

        try:
            sql = """
            SELECT relname AS tablename,
                   pg_size_pretty(pg_total_relation_size(quote_ident(relname))) AS size,
                   n_live_tup AS approx_rows
            FROM pg_stat_user_tables
            WHERE schemaname = 'public'
            ORDER BY n_live_tup DESC, relname
            """
            import pandas as pd
            df = pd.read_sql(sa_text(sql), self.engine)
            tables = df.to_dict(orient="records")
            # 转换 numpy 类型
            for t in tables:
                for k, v in t.items():
                    if hasattr(v, "item"):
                        t[k] = v.item()

            return {"ok": True, "tables": tables, "count": len(tables)}
        except Exception as e:
            logger.error(f"列出表失败: {e}")

            # 再降级：information_schema
            try:
                import pandas as pd
                df = pd.read_sql(
                    sa_text(
                        "SELECT table_name AS tablename FROM information_schema.tables "
                        "WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name"
                    ),
                    self.engine,
                )
                tables = df.to_dict(orient="records")
                return {"ok": True, "tables": tables, "count": len(tables), "fallback": True}
            except Exception as e0:
                logger.warning(f"information_schema 列表示败: {e0}")

            # 降级：用 LangChain 的 ListSQLDatabaseTool
            if self.db:
                try:
                    _, _, _, ListSQLDatabaseTool, _ = _try_import_langchain()
                    tool = ListSQLDatabaseTool(db=self.db)
                    result = await tool.arun(tool_input="")
                    tables = [t.strip() for t in result.split(",") if t.strip()]
                    return {"ok": True, "tables": [{"tablename": t} for t in tables], "count": len(tables)}
                except Exception as e2:
                    return {"ok": False, "error": str(e2), "tables": []}

            return {"ok": False, "error": str(e), "tables": []}

    # ─────────────────────────────────────────────────
    # Tool 2: 查看表信息 (schema)
    # ─────────────────────────────────────────────────

    async def info_table(self, table_names: str) -> dict:
        """获取指定表的 schema 信息"""
        if not self.engine and not self.db:
            return {"ok": False, "error": "数据库未连接"}

        # 优先原生 information_schema（不依赖 langchain）
        if self.engine:
            try:
                tnames = [t.strip() for t in table_names.split(",") if t.strip()]
                all_cols = []
                for tname in tnames:
                    sql = """
                    SELECT column_name, data_type, is_nullable,
                           column_default, ordinal_position
                    FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = :tname
                    ORDER BY ordinal_position
                    """
                    import pandas as pd
                    df = pd.read_sql(sa_text(sql), self.engine, params={"tname": tname})
                    cols = df.to_dict(orient="records")
                    for c in cols:
                        c["table"] = tname
                        for k, v in list(c.items()):
                            if hasattr(v, "item"):
                                c[k] = v.item()
                    all_cols.extend(cols)
                return {"ok": True, "columns": all_cols, "tables": tnames, "fallback": True}
            except Exception as e:
                logger.warning(f"information_schema 查询失败: {e}")

        if self.db:
            try:
                _, _, InfoSQLDatabaseTool, _, _ = _try_import_langchain()
                tool = InfoSQLDatabaseTool(db=self.db)
                result = await tool.arun(table_names=table_names)
                return {"ok": True, "schema_ddl": result, "tables": [t.strip() for t in table_names.split(",")]}
            except Exception as e:
                return {"ok": False, "error": str(e)}

        return {"ok": False, "error": "数据库未连接"}

    # ─────────────────────────────────────────────────
    # Tool 3: SQL 校验
    # ─────────────────────────────────────────────────

    async def check_sql(self, query: str) -> dict:
        """校验 SQL 语句语法"""
        safety_err = self._validate_readonly_sql(query)
        if safety_err:
            return {"ok": False, "error": safety_err, "valid": False}

        if self.db:
            try:
                _, _, _, _, QuerySQLCheckerTool = _try_import_langchain()
                tool = QuerySQLCheckerTool(db=self.db, llm=None)
                result = await tool.arun(query=query)
                return {"ok": True, "valid": True, "message": result}
            except Exception as e:
                return {"ok": True, "valid": False, "message": str(e)}

        # 无 langchain：仅返回安全校验通过
        return {"ok": True, "valid": True, "message": "只读安全校验通过（未启用 LangChain 语法检查）"}

    # ─────────────────────────────────────────────────
    # Tool 4: 执行 SQL 查询
    # ─────────────────────────────────────────────────

    async def execute_query(self, query: str, limit: int = 100) -> dict:
        """执行只读 SQL 查询并返回结果"""
        # 安全校验
        safety_err = self._validate_readonly_sql(query)
        if safety_err:
            return {"ok": False, "error": safety_err, "rows": [], "columns": []}

        if not self.engine:
            return {"ok": False, "error": "数据库未连接", "rows": [], "columns": []}

        try:
            import pandas as pd

            # 自动加 LIMIT 限制（如果查询没有 LIMIT）
            sql_upper = query.upper()
            if "LIMIT" not in sql_upper:
                query = query.rstrip(";").strip() + f" LIMIT {limit}"

            df = pd.read_sql(sa_text(query), self.engine)

            # 序列化行
            from datetime import datetime
            rows = []
            for _, r in df.iterrows():
                row = {}
                for c in df.columns:
                    val = r[c]
                    if pd.isna(val):
                        row[c] = None
                    elif isinstance(val, (pd.Timestamp, datetime)):
                        row[c] = str(val)
                    elif hasattr(val, "item"):
                        row[c] = val.item()
                    else:
                        row[c] = val
                rows.append(row)

            return {
                "ok": True,
                "rows": rows,
                "columns": list(df.columns),
                "row_count": len(rows),
            }
        except Exception as e:
            logger.error(f"执行 SQL 失败: {e}")
            return {"ok": False, "error": str(e), "rows": [], "columns": []}

    # ─────────────────────────────────────────────────
    # Tool 5: 自然语言查询（使用 LangChain SQL agent）
    # ─────────────────────────────────────────────────

    async def natural_language_query(self, question: str) -> dict:
        """
        自然语言转 SQL 并执行

        优先尝试 LangChain chain，失败时返回提示让用户手动执行
        """
        if not self.db:
            return {"ok": False, "error": "数据库未连接"}

        try:
            from langchain_classic.chains import create_sql_query_chain
            from langchain_classic_community.llms import BaseLLM

            # 尝试创建 SQL 查询链
            chain = create_sql_query_chain(self.db) if hasattr(self, "db") else None

            if chain is not None:
                # 直接生成 SQL（需要 LLM，这里 fallback 到提示用户手动写 SQL）
                pass

            # 降级：返回表结构让用户自己写 SQL
            return {
                "ok": False,
                "error": "自然语言转 SQL 需要配置 LLM 模型。"
                         "请使用「📋 列出所有表」和「📊 查看表信息」了解数据结构后，"
                         "用「⚡ 执行 SQL」直接查询。",
                "suggestion": "手动写 SQL",
            }

        except ImportError as e:
            return {
                "ok": False,
                "error": f"缺少依赖: {e}。请使用「⚡ 执行 SQL」直接查询。",
                "suggestion": "手动写 SQL",
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}


# ─── 全局单例 ────────────────────────────────────────
sql_toolkit = SQLToolkitManager()

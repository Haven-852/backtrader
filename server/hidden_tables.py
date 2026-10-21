"""存储层 / SQL 工具不可展示的内部表（含 API Key 等敏感配置）。"""

from __future__ import annotations

from typing import Iterable

# 大模型用户配置 / 自定义表元数据：密钥与内部登记表不进存储层预览
HIDDEN_PG_TABLES = frozenset({"user_llm_profile", "user_custom_table"})


def is_hidden_pg_table(name: str | None) -> bool:
    return str(name or "").strip().strip('"').lower() in HIDDEN_PG_TABLES


def sql_touches_hidden(sql: str | None) -> bool:
    text = (sql or "").lower().replace('"', "").replace("'", "")
    return any(table in text for table in HIDDEN_PG_TABLES)


def drop_hidden_table_rows(rows: Iterable[dict], name_key: str = "tablename") -> list:
    out = []
    for row in rows:
        name = row.get(name_key) or row.get("name") or row.get("table_name")
        if is_hidden_pg_table(str(name) if name is not None else ""):
            continue
        out.append(row)
    return out

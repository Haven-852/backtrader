"""
日配额（MVP）：chat / tool / export。
管理员与 is_pro 可提高限额；超限返回明确错误供路由转 429。
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict

from sqlalchemy import text as sa_text

from data_layer.db_manager import storage_manager
from server.auth_service import auth_service

logger = logging.getLogger(__name__)

DEFAULT_LIMITS = {
    "free": {"chat": 20, "tool": 10, "export": 5},
    "pro": {"chat": 200, "tool": 100, "export": 50},
    "admin": {"chat": 10_000, "tool": 10_000, "export": 10_000},
}


class QuotaExceeded(Exception):
    def __init__(self, kind: str, used: int, limit: int):
        self.kind = kind
        self.used = used
        self.limit = limit
        super().__init__(f"今日 {kind} 配额已用尽（{used}/{limit}）")


class QuotaService:
    def __init__(self) -> None:
        self._engine = None
        self._ready = False

    @property
    def engine(self):
        if self._engine is None:
            self._engine = storage_manager.get_postgres_engine()
        return self._engine

    def ensure(self) -> None:
        if self._ready:
            return
        if not self.engine:
            raise RuntimeError("PostgreSQL 未连接")
        with self.engine.begin() as conn:
            conn.execute(
                sa_text(
                    """
                    CREATE TABLE IF NOT EXISTS app_usage_daily (
                        user_id     VARCHAR(36) NOT NULL,
                        usage_date  DATE NOT NULL,
                        chat_count  INT NOT NULL DEFAULT 0,
                        tool_count  INT NOT NULL DEFAULT 0,
                        export_count INT NOT NULL DEFAULT 0,
                        PRIMARY KEY (user_id, usage_date)
                    )
                    """
                )
            )
        self._ready = True

    def _limits_for(self, user_id: str) -> Dict[str, int]:
        user = auth_service.get_user_by_id(user_id) or {}
        if user.get("role") == "admin":
            return dict(DEFAULT_LIMITS["admin"])
        sub = auth_service.get_subscription(user_id)
        if sub.get("is_pro"):
            return dict(DEFAULT_LIMITS["pro"])
        return dict(DEFAULT_LIMITS["free"])

    def today_usage(self, user_id: str) -> Dict[str, Any]:
        self.ensure()
        d = date.today()
        with self.engine.connect() as conn:
            row = conn.execute(
                sa_text(
                    "SELECT chat_count, tool_count, export_count "
                    "FROM app_usage_daily WHERE user_id = :u AND usage_date = :d"
                ),
                {"u": user_id, "d": d},
            ).mappings().first()
        used = {
            "chat": int(row["chat_count"]) if row else 0,
            "tool": int(row["tool_count"]) if row else 0,
            "export": int(row["export_count"]) if row else 0,
        }
        limits = self._limits_for(user_id)
        return {"date": d.isoformat(), "used": used, "limits": limits}

    def check_and_incr(self, user_id: str, kind: str) -> Dict[str, Any]:
        """kind: chat | tool | export"""
        if kind not in ("chat", "tool", "export"):
            raise ValueError(f"未知配额类型: {kind}")
        self.ensure()
        limits = self._limits_for(user_id)
        col = f"{kind}_count"
        d = date.today()
        with self.engine.begin() as conn:
            conn.execute(
                sa_text(
                    """
                    INSERT INTO app_usage_daily (user_id, usage_date, chat_count, tool_count, export_count)
                    VALUES (:u, :d, 0, 0, 0)
                    ON CONFLICT (user_id, usage_date) DO NOTHING
                    """
                ),
                {"u": user_id, "d": d},
            )
            row = conn.execute(
                sa_text(
                    f"SELECT {col} AS c FROM app_usage_daily "
                    "WHERE user_id = :u AND usage_date = :d FOR UPDATE"
                ),
                {"u": user_id, "d": d},
            ).mappings().first()
            used = int(row["c"] if row else 0)
            limit = int(limits.get(kind) or 0)
            if used >= limit:
                raise QuotaExceeded(kind, used, limit)
            conn.execute(
                sa_text(
                    f"UPDATE app_usage_daily SET {col} = {col} + 1 "
                    "WHERE user_id = :u AND usage_date = :d"
                ),
                {"u": user_id, "d": d},
            )
        return self.today_usage(user_id)


quota_service = QuotaService()

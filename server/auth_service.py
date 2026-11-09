"""
应用账号：注册 / 登录 / JWT。
表：app_user、app_subscription（MVP 手动开通订阅）。
"""
from __future__ import annotations

import hashlib
import logging
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import jwt
from sqlalchemy import text as sa_text

from data_layer.db_manager import storage_manager

logger = logging.getLogger(__name__)

JWT_ALG = "HS256"
TOKEN_EXPIRE_HOURS = int(os.getenv("AUTH_TOKEN_EXPIRE_HOURS", "168"))  # 7 天


def _jwt_secret() -> str:
    secret = (os.getenv("AUTH_JWT_SECRET") or "").strip()
    if not secret:
        # 开发兜底；生产务必设置 AUTH_JWT_SECRET
        secret = (os.getenv("DEEPSEEK_API_KEY") or "dev-auth-secret-change-me")[:64]
        logger.warning("AUTH_JWT_SECRET 未设置，使用弱默认密钥（仅适合本地）")
    return secret


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000)
    return f"pbkdf2$120000${salt}${dk.hex()}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algo, rounds_s, salt, hexdigest = (password_hash or "").split("$", 3)
        if algo != "pbkdf2":
            return False
        rounds = int(rounds_s)
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt.encode("utf-8"), rounds
        )
        return secrets.compare_digest(dk.hex(), hexdigest)
    except Exception:
        return False


class AuthService:
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
            raise RuntimeError("PostgreSQL 未连接，无法初始化账号表")
        ddl = [
            """
            CREATE TABLE IF NOT EXISTS app_user (
                id            VARCHAR(36) PRIMARY KEY,
                email         VARCHAR(200) NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                display_name  VARCHAR(80),
                role          VARCHAR(20) NOT NULL DEFAULT 'user',
                status        VARCHAR(20) NOT NULL DEFAULT 'active',
                created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS app_subscription (
                user_id    VARCHAR(36) PRIMARY KEY REFERENCES app_user(id) ON DELETE CASCADE,
                plan       VARCHAR(32) NOT NULL DEFAULT 'free',
                status     VARCHAR(20) NOT NULL DEFAULT 'active',
                expire_at  TIMESTAMPTZ,
                note       TEXT,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_app_user_email ON app_user (email)",
        ]
        with self.engine.begin() as conn:
            for sql in ddl:
                conn.execute(sa_text(sql))
        self._ready = True

    def _row_user(self, r) -> Dict[str, Any]:
        return {
            "id": r["id"],
            "email": r["email"],
            "display_name": r["display_name"] or r["email"],
            "role": r["role"],
            "status": r["status"],
            "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
        }

    def count_users(self) -> int:
        self.ensure()
        with self.engine.connect() as conn:
            return int(conn.execute(sa_text("SELECT COUNT(*) FROM app_user")).scalar() or 0)

    def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        self.ensure()
        with self.engine.connect() as conn:
            row = conn.execute(
                sa_text("SELECT * FROM app_user WHERE id = :id"),
                {"id": user_id},
            ).mappings().first()
        return self._row_user(row) if row else None

    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        self.ensure()
        email_n = (email or "").strip().lower()
        with self.engine.connect() as conn:
            row = conn.execute(
                sa_text("SELECT * FROM app_user WHERE email = :e"),
                {"e": email_n},
            ).mappings().first()
        return dict(row) if row else None

    def get_subscription(self, user_id: str) -> Dict[str, Any]:
        self.ensure()
        with self.engine.connect() as conn:
            row = conn.execute(
                sa_text("SELECT * FROM app_subscription WHERE user_id = :u"),
                {"u": user_id},
            ).mappings().first()
        if not row:
            return {"plan": "free", "status": "active", "expire_at": None, "is_pro": False}
        expire_at = row.get("expire_at")
        is_pro = False
        if row.get("plan") == "pro" and row.get("status") == "active":
            if expire_at is None or expire_at > datetime.now(timezone.utc).replace(tzinfo=expire_at.tzinfo):
                is_pro = True
            elif expire_at.tzinfo is None and expire_at > datetime.utcnow():
                is_pro = True
        return {
            "plan": row["plan"],
            "status": row["status"],
            "expire_at": expire_at.isoformat() if expire_at else None,
            "is_pro": bool(is_pro),
            "note": row.get("note"),
        }

    def register(
        self,
        email: str,
        password: str,
        display_name: Optional[str] = None,
        invite_code: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.ensure()
        email_n = (email or "").strip().lower()
        if not email_n or "@" not in email_n:
            raise ValueError("请输入有效邮箱")
        if not password or len(password) < 6:
            raise ValueError("密码至少 6 位")

        required_invite = (os.getenv("AUTH_INVITE_CODE") or "").strip()
        is_first = self.count_users() == 0
        if required_invite and not is_first:
            if (invite_code or "").strip() != required_invite:
                raise ValueError("邀请码无效")

        if self.get_user_by_email(email_n):
            raise ValueError("该邮箱已注册")

        user_id = str(uuid.uuid4())
        role = "admin" if is_first else "user"
        plan = "pro" if is_first else "free"
        name = (display_name or "").strip() or email_n.split("@")[0]

        with self.engine.begin() as conn:
            conn.execute(
                sa_text(
                    """
                    INSERT INTO app_user (id, email, password_hash, display_name, role, status)
                    VALUES (:id, :email, :ph, :name, :role, 'active')
                    """
                ),
                {
                    "id": user_id,
                    "email": email_n,
                    "ph": hash_password(password),
                    "name": name[:80],
                    "role": role,
                },
            )
            expire = None
            if plan == "pro":
                expire = datetime.utcnow() + timedelta(days=3650)
            conn.execute(
                sa_text(
                    """
                    INSERT INTO app_subscription (user_id, plan, status, expire_at, note)
                    VALUES (:u, :plan, 'active', :exp, :note)
                    """
                ),
                {
                    "u": user_id,
                    "plan": plan,
                    "exp": expire,
                    "note": "bootstrap admin" if is_first else None,
                },
            )

        user = self.get_user_by_id(user_id)
        assert user
        token = self.issue_token(user)
        return {"user": user, "token": token, "subscription": self.get_subscription(user_id)}

    def login(self, email: str, password: str) -> Dict[str, Any]:
        self.ensure()
        raw = self.get_user_by_email(email)
        if not raw or not verify_password(password, raw.get("password_hash") or ""):
            raise ValueError("邮箱或密码错误")
        if raw.get("status") != "active":
            raise ValueError("账号已停用")
        user = self._row_user(raw)
        token = self.issue_token(user)
        return {"user": user, "token": token, "subscription": self.get_subscription(user["id"])}

    def issue_token(self, user: Dict[str, Any]) -> str:
        now = datetime.now(timezone.utc)
        payload = {
            "sub": user["id"],
            "email": user["email"],
            "role": user.get("role") or "user",
            "iat": now,
            "exp": now + timedelta(hours=TOKEN_EXPIRE_HOURS),
        }
        return jwt.encode(payload, _jwt_secret(), algorithm=JWT_ALG)

    def decode_token(self, token: str) -> Dict[str, Any]:
        try:
            return jwt.decode(token, _jwt_secret(), algorithms=[JWT_ALG])
        except jwt.ExpiredSignatureError as e:
            raise ValueError("登录已过期，请重新登录") from e
        except jwt.InvalidTokenError as e:
            raise ValueError("无效的登录凭证") from e

    def me(self, user_id: str) -> Dict[str, Any]:
        user = self.get_user_by_id(user_id)
        if not user:
            raise ValueError("用户不存在")
        sub = self.get_subscription(user_id)
        if user.get("role") == "admin":
            sub["is_pro"] = True
            sub["plan"] = sub.get("plan") or "pro"
        return {"user": user, "subscription": sub}


auth_service = AuthService()

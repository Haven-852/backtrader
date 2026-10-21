"""
用户大模型配置：每人可保存多套厂商 / 模型 / API Key / 参数，勾选后用于对话。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
from sqlalchemy import text as sa_text

from data_layer.db_manager import storage_manager
from server.chat_service import (
    PROVIDER_DEFAULTS,
    AnthropicProvider,
    OllamaProvider,
    chat_service,
    make_provider,
    normalize_provider,
)

logger = logging.getLogger(__name__)

PRESETS: List[Dict[str, Any]] = [
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "provider": "deepseek",
        "model": "deepseek-chat",
        "base_url": "https://api.deepseek.com/v1",
        "hint": "api.deepseek.com，模型 deepseek-chat / deepseek-reasoner",
    },
    {
        "id": "openai",
        "name": "OpenAI",
        "provider": "openai",
        "model": "gpt-4o-mini",
        "base_url": "https://api.openai.com/v1",
        "hint": "官方 GPT-4o / GPT-4.1 等",
    },
    {
        "id": "anthropic",
        "name": "Anthropic Claude",
        "provider": "anthropic",
        "model": "claude-3-5-sonnet-latest",
        "base_url": "https://api.anthropic.com",
        "hint": "需要 x-api-key，不是 Bearer",
    },
    {
        "id": "grok",
        "name": "xAI Grok",
        "provider": "grok",
        "model": "grok-2",
        "base_url": "https://api.x.ai/v1",
        "hint": "OpenAI 兼容协议",
    },
    {
        "id": "ollama",
        "name": "Ollama 本地",
        "provider": "ollama",
        "model": "qwen2.5:7b",
        "base_url": "http://localhost:11434",
        "hint": "本机 ollama serve，一般不需要 API Key",
    },
    {
        "id": "openai_compat",
        "name": "OpenAI 兼容",
        "provider": "openai_compat",
        "model": "",
        "base_url": "",
        "hint": "通义 / 智谱 / Kimi / 硅基流动等，填兼容 Base URL 和模型名",
    },
]


def mask_key(key: Optional[str]) -> str:
    text = (key or "").strip()
    if not text:
        return ""
    if len(text) <= 8:
        return "****"
    return f"{text[:4]}****{text[-4:]}"


class LlmProfileService:
    def __init__(self):
        self._engine = None
        self._ready = False

    @property
    def engine(self):
        if self._engine is None:
            self._engine = storage_manager.get_postgres_engine()
        return self._engine

    def ensure(self) -> None:
        """启动时建表。配置含 API Key，不进入存储层预览。"""
        self._ensure()

    def _ensure(self) -> None:
        if self._ready:
            return
        if not self.engine:
            raise RuntimeError("PostgreSQL 未连接")
        ddl = [
            """
            CREATE TABLE IF NOT EXISTS user_llm_profile (
                id            SERIAL PRIMARY KEY,
                user_id       VARCHAR(64) NOT NULL DEFAULT 'default',
                name          VARCHAR(80) NOT NULL,
                provider      VARCHAR(32) NOT NULL,
                model         VARCHAR(128) NOT NULL,
                api_key       TEXT,
                base_url      VARCHAR(256),
                temperature   NUMERIC(4,2) DEFAULT 0.7,
                max_tokens    INT DEFAULT 4096,
                enabled       BOOLEAN DEFAULT TRUE,
                is_active     BOOLEAN DEFAULT FALSE,
                extra         JSONB,
                created_at    TIMESTAMPTZ DEFAULT NOW(),
                updated_at    TIMESTAMPTZ DEFAULT NOW()
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_user_llm_profile_user ON user_llm_profile (user_id, is_active DESC, id)",
        ]
        with self.engine.begin() as conn:
            for sql in ddl:
                conn.execute(sa_text(sql))
        self._ready = True

    def _row(self, r: Any, reveal_key: bool = False) -> Dict[str, Any]:
        api_key = r.api_key or ""
        return {
            "id": int(r.id),
            "user_id": r.user_id,
            "name": r.name,
            "provider": r.provider,
            "model": r.model,
            "api_key": api_key if reveal_key else "",
            "api_key_masked": mask_key(api_key),
            "has_api_key": bool(str(api_key).strip()),
            "base_url": r.base_url or "",
            "temperature": float(r.temperature) if r.temperature is not None else 0.7,
            "max_tokens": int(r.max_tokens) if r.max_tokens is not None else 4096,
            "enabled": bool(r.enabled),
            "is_active": bool(r.is_active),
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        }

    def list_profiles(self, user_id: str = "default") -> List[Dict[str, Any]]:
        self._ensure()
        with self.engine.connect() as conn:
            rows = conn.execute(
                sa_text(
                    "SELECT * FROM user_llm_profile WHERE user_id = :u "
                    "ORDER BY is_active DESC, enabled DESC, id ASC"
                ),
                {"u": user_id},
            ).fetchall()
        return [self._row(r) for r in rows]

    def get_raw(self, profile_id: int, user_id: str = "default") -> Optional[Dict[str, Any]]:
        self._ensure()
        with self.engine.connect() as conn:
            r = conn.execute(
                sa_text("SELECT * FROM user_llm_profile WHERE id = :id AND user_id = :u"),
                {"id": profile_id, "u": user_id},
            ).fetchone()
        if not r:
            return None
        return self._row(r, reveal_key=True)

    def get_public(self, profile_id: int, user_id: str = "default") -> Optional[Dict[str, Any]]:
        raw = self.get_raw(profile_id, user_id)
        if not raw:
            return None
        raw["api_key"] = ""
        return raw

    def create(self, payload: Dict[str, Any], user_id: str = "default") -> Dict[str, Any]:
        self._ensure()
        name = (payload.get("name") or "").strip()
        provider = normalize_provider(payload.get("provider"))
        model = (payload.get("model") or "").strip()
        if not name:
            name = f"{provider} / {model or '未命名'}"
        if not model:
            raise ValueError("请填写模型名，例如 deepseek-chat 或 gpt-4o-mini")
        defaults = PROVIDER_DEFAULTS.get(provider, {})
        base_url = (payload.get("base_url") or defaults.get("base_url") or "").strip()
        api_key = (payload.get("api_key") or "").strip()
        temperature = float(payload.get("temperature") if payload.get("temperature") is not None else 0.7)
        max_tokens = int(payload.get("max_tokens") or 4096)
        enabled = bool(payload.get("enabled", True))
        make_active = bool(payload.get("is_active", False))
        with self.engine.begin() as conn:
            if make_active:
                conn.execute(
                    sa_text("UPDATE user_llm_profile SET is_active = FALSE WHERE user_id = :u"),
                    {"u": user_id},
                )
            rid = conn.execute(
                sa_text(
                    "INSERT INTO user_llm_profile "
                    "(user_id, name, provider, model, api_key, base_url, temperature, max_tokens, enabled, is_active, updated_at) "
                    "VALUES (:user_id, :name, :provider, :model, :api_key, :base_url, :temperature, :max_tokens, :enabled, :is_active, NOW()) "
                    "RETURNING id"
                ),
                {
                    "user_id": user_id,
                    "name": name[:80],
                    "provider": provider,
                    "model": model[:128],
                    "api_key": api_key,
                    "base_url": base_url[:256],
                    "temperature": max(0.0, min(2.0, temperature)),
                    "max_tokens": max(1, min(128000, max_tokens)),
                    "enabled": enabled,
                    "is_active": make_active,
                },
            ).scalar()
        created = self.get_public(int(rid), user_id)
        if not created or not created.get("id"):
            raise RuntimeError("写入后未能读回配置")
        return created

    def update(self, profile_id: int, payload: Dict[str, Any], user_id: str = "default") -> Optional[Dict[str, Any]]:
        self._ensure()
        current = self.get_raw(profile_id, user_id)
        if not current:
            return None
        name = (payload.get("name") if "name" in payload else current["name"]) or current["name"]
        provider = normalize_provider(payload.get("provider") if "provider" in payload else current["provider"])
        model = (payload.get("model") if "model" in payload else current["model"]) or current["model"]
        incoming_key = (payload.get("api_key") or "").strip()
        api_key = incoming_key if incoming_key else current.get("api_key") or ""
        base_url = payload.get("base_url") if "base_url" in payload else current.get("base_url")
        temperature = payload.get("temperature") if "temperature" in payload else current.get("temperature")
        max_tokens = payload.get("max_tokens") if "max_tokens" in payload else current.get("max_tokens")
        enabled = payload.get("enabled") if "enabled" in payload else current.get("enabled")
        is_active = payload.get("is_active") if "is_active" in payload else current.get("is_active")
        with self.engine.begin() as conn:
            if is_active:
                conn.execute(
                    sa_text("UPDATE user_llm_profile SET is_active = FALSE WHERE user_id = :u AND id <> :id"),
                    {"u": user_id, "id": profile_id},
                )
            conn.execute(
                sa_text(
                    "UPDATE user_llm_profile SET "
                    "name=:name, provider=:provider, model=:model, api_key=:api_key, base_url=:base_url, "
                    "temperature=:temperature, max_tokens=:max_tokens, enabled=:enabled, is_active=:is_active, "
                    "updated_at=NOW() WHERE id=:id AND user_id=:u"
                ),
                {
                    "id": profile_id,
                    "u": user_id,
                    "name": str(name)[:80],
                    "provider": provider,
                    "model": str(model)[:128],
                    "api_key": api_key,
                    "base_url": str(base_url or "")[:256],
                    "temperature": max(0.0, min(2.0, float(temperature if temperature is not None else 0.7))),
                    "max_tokens": max(1, min(128000, int(max_tokens or 4096))),
                    "enabled": bool(enabled),
                    "is_active": bool(is_active),
                },
            )
        return self.get_public(profile_id, user_id)

    def delete(self, profile_id: int, user_id: str = "default") -> bool:
        self._ensure()
        with self.engine.begin() as conn:
            res = conn.execute(
                sa_text("DELETE FROM user_llm_profile WHERE id = :id AND user_id = :u"),
                {"id": profile_id, "u": user_id},
            )
            return bool(res.rowcount)

    def activate(self, profile_id: int, user_id: str = "default") -> Optional[Dict[str, Any]]:
        current = self.get_raw(profile_id, user_id)
        if not current:
            return None
        with self.engine.begin() as conn:
            conn.execute(
                sa_text("UPDATE user_llm_profile SET is_active = FALSE WHERE user_id = :u"),
                {"u": user_id},
            )
            conn.execute(
                sa_text(
                    "UPDATE user_llm_profile SET is_active = TRUE, enabled = TRUE, updated_at = NOW() "
                    "WHERE id = :id AND user_id = :u"
                ),
                {"id": profile_id, "u": user_id},
            )
        return self.get_public(profile_id, user_id)

    def catalog(self, user_id: str = "default") -> Dict[str, Any]:
        profiles = self.list_profiles(user_id)
        active = next((p for p in profiles if p.get("is_active") and p.get("enabled")), None)
        if not active:
            active = next((p for p in profiles if p.get("enabled")), None)
        system_models = []
        try:
            for m in chat_service.AVAILABLE_MODELS:
                item = dict(m)
                name = chat_service.MODEL_PROVIDER_MAP.get(m["id"], "ollama")
                item["available"] = name in chat_service._providers
                system_models.append(item)
        except Exception:
            system_models = []
        return {
            "ok": True,
            "presets": PRESETS,
            "profiles": profiles,
            "active": active,
            "system_models": system_models,
            "providers": list(PROVIDER_DEFAULTS.keys()),
        }

    async def test_config(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        provider = normalize_provider(payload.get("provider") or "deepseek")
        model = (payload.get("model") or "").strip()
        api_key = (payload.get("api_key") or "").strip()
        base_url = (payload.get("base_url") or "").strip()
        if not model:
            return {"ok": False, "status": "error", "message": "请填写模型名"}
        if provider != "ollama" and not api_key:
            return {"ok": False, "status": "error", "message": "请填写 API Key（Ollama 除外）"}
        client = make_provider(provider, api_key=api_key, base_url=base_url, default_model=model)
        start = datetime.now()
        url = client.get_chat_url()
        if isinstance(client, AnthropicProvider):
            body: Dict[str, Any] = {
                "model": model,
                "max_tokens": 8,
                "messages": [{"role": "user", "content": "ping"}],
            }
        elif isinstance(client, OllamaProvider):
            body = {
                "model": model,
                "stream": False,
                "messages": [{"role": "user", "content": "ping"}],
            }
        else:
            body = {
                "model": model,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 8,
                "temperature": 0,
            }
        try:
            timeout = httpx.Timeout(12.0, connect=6.0)
            async with httpx.AsyncClient(timeout=timeout, trust_env=False) as http:
                resp = await http.post(url, headers=client.get_headers(), json=body)
            elapsed = int((datetime.now() - start).total_seconds() * 1000)
            raw_text = (resp.text or "")[:800]
            data: Any
            try:
                data = resp.json()
            except Exception:
                data = {"raw": raw_text}
            if resp.status_code >= 400:
                err = ""
                if isinstance(data, dict):
                    err = str(
                        ((data.get("error") or {}) if isinstance(data.get("error"), dict) else {}).get("message")
                        or data.get("error")
                        or data.get("message")
                        or raw_text
                        or f"HTTP {resp.status_code}"
                    )
                return {
                    "ok": False,
                    "status": resp.status_code,
                    "message": err[:400],
                    "model": model,
                    "provider": provider,
                    "base_url": client.base_url,
                    "latency_ms": elapsed,
                    "response": data,
                }
            choice = (data.get("choices") or [{}])[0] if isinstance(data, dict) else {}
            message = choice.get("message") or {}
            content = message.get("content") or message.get("reasoning_content") or ""
            if isinstance(data, dict) and not content:
                content = str(data.get("content") or "")[:400]
            return {
                "ok": True,
                "status": resp.status_code,
                "message": f"{model} 连通正常",
                "model": (data.get("model") if isinstance(data, dict) else None) or model,
                "provider": provider,
                "base_url": client.base_url,
                "latency_ms": elapsed,
                "finish_reason": choice.get("finish_reason") or choice.get("stop_reason") or "",
                "content": str(content or "")[:400],
                "usage": (data.get("usage") if isinstance(data, dict) else None) or {},
            }
        except httpx.TimeoutException:
            elapsed = int((datetime.now() - start).total_seconds() * 1000)
            return {
                "ok": False,
                "status": "timeout",
                "message": "连接超时（12 秒内未响应）。请检查 Base URL、API Key 或本机网络/代理。",
                "model": model,
                "provider": provider,
                "base_url": client.base_url,
                "latency_ms": elapsed,
            }
        except Exception as e:
            elapsed = int((datetime.now() - start).total_seconds() * 1000)
            return {
                "ok": False,
                "status": "error",
                "message": str(e)[:400],
                "model": model,
                "provider": provider,
                "base_url": client.base_url,
                "latency_ms": elapsed,
            }


llm_profile_service = LlmProfileService()

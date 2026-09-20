"""
对话持久化仓储（PostgreSQL）

由 agents/conversation.py 调用；依赖 pg_manager 异步连接池。
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, select, update
from sqlalchemy.orm import selectinload

from .chat_models import AgentChatMessage, AgentChatSession, CHAT_MODELS
from .manager import pg_manager
from .models import _fmt

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.utcnow()


def _title_from_text(text: str, max_len: int = 40) -> str:
    raw = (text or "").strip().replace("\n", " ")
    if not raw:
        return "新对话"
    return raw if len(raw) <= max_len else raw[: max_len - 1] + "…"


class ConversationRepository:
    """agent_chat_session / agent_chat_message CRUD"""

    def __init__(self):
        self._tables_ready = False

    def _ensure_manager(self):
        if not pg_manager._initialized:
            pg_manager.initialize()

    async def ensure_tables(self) -> None:
        """确保对话表存在（幂等）"""
        if self._tables_ready:
            return
        self._ensure_manager()
        from .models import Base

        # 仅创建对话相关表
        tables = [m.__table__ for m in CHAT_MODELS]
        async with pg_manager.async_engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=tables))
        self._tables_ready = True
        logger.info("agent_chat_session / agent_chat_message 表已就绪")

    async def create_session(
        self,
        *,
        title: Optional[str] = None,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        user_id: str = "default",
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        await self.ensure_tables()
        sid = session_id or str(uuid.uuid4())
        row = AgentChatSession(
            id=sid,
            title=(title or "新对话")[:200],
            model=model,
            provider=provider,
            user_id=user_id or "default",
            message_count=0,
            is_archived=False,
            created_at=_now(),
            updated_at=_now(),
        )
        async with pg_manager.get_async_session_context() as session:
            session.add(row)
        return row.to_dict()

    async def list_sessions(
        self,
        *,
        user_id: str = "default",
        limit: int = 50,
        include_archived: bool = False,
    ) -> List[Dict[str, Any]]:
        await self.ensure_tables()
        limit = max(1, min(int(limit or 50), 200))
        stmt = (
            select(AgentChatSession)
            .where(AgentChatSession.user_id == (user_id or "default"))
            .order_by(AgentChatSession.updated_at.desc())
            .limit(limit)
        )
        if not include_archived:
            stmt = stmt.where(AgentChatSession.is_archived.is_(False))
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(stmt)
            rows = result.scalars().all()
        return [r.to_dict() for r in rows]

    async def get_session(self, session_id: str, *, with_messages: bool = True) -> Optional[Dict[str, Any]]:
        await self.ensure_tables()
        if not session_id:
            return None
        stmt = select(AgentChatSession).where(AgentChatSession.id == session_id)
        if with_messages:
            stmt = stmt.options(selectinload(AgentChatSession.messages))
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if not row:
                return None
            data = row.to_dict()
            if with_messages:
                msgs = sorted(row.messages or [], key=lambda m: (m.seq, m.id or 0))
                data["messages"] = [self._message_to_api(m) for m in msgs]
            return data

    async def update_session(
        self,
        session_id: str,
        *,
        title: Optional[str] = None,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        is_archived: Optional[bool] = None,
    ) -> Optional[Dict[str, Any]]:
        await self.ensure_tables()
        values: Dict[str, Any] = {"updated_at": _now()}
        if title is not None:
            values["title"] = title[:200]
        if model is not None:
            values["model"] = model
        if provider is not None:
            values["provider"] = provider
        if is_archived is not None:
            values["is_archived"] = bool(is_archived)
        async with pg_manager.get_async_session_context() as session:
            await session.execute(
                update(AgentChatSession)
                .where(AgentChatSession.id == session_id)
                .values(**values)
            )
        return await self.get_session(session_id, with_messages=False)

    async def delete_session(self, session_id: str) -> bool:
        await self.ensure_tables()
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                delete(AgentChatSession).where(AgentChatSession.id == session_id)
            )
            return (result.rowcount or 0) > 0

    async def append_message(
        self,
        session_id: str,
        *,
        role: str,
        content: str,
        skill: Optional[str] = None,
        data: Any = None,
        data_type: Optional[str] = None,
        tool_trace: Optional[List[Any]] = None,
    ) -> Dict[str, Any]:
        await self.ensure_tables()
        role = (role or "user").strip().lower()
        if role not in ("user", "assistant", "system"):
            role = "user"

        payload: Any = data
        if tool_trace is not None:
            if isinstance(payload, dict):
                payload = {**payload, "tool_trace": tool_trace}
            elif payload is None:
                payload = {"tool_trace": tool_trace}
            else:
                payload = {"data": payload, "tool_trace": tool_trace}

        async with pg_manager.get_async_session_context() as session:
            # next seq
            result = await session.execute(
                select(AgentChatMessage.seq)
                .where(AgentChatMessage.session_id == session_id)
                .order_by(AgentChatMessage.seq.desc())
                .limit(1)
            )
            last = result.scalar_one_or_none()
            seq = int(last or 0) + 1

            msg = AgentChatMessage(
                session_id=session_id,
                role=role,
                content=content or "",
                skill=skill,
                data_json=payload,
                data_type=data_type,
                seq=seq,
                created_at=_now(),
            )
            session.add(msg)

            sess = await session.get(AgentChatSession, session_id)
            if sess:
                sess.message_count = int(sess.message_count or 0) + 1
                sess.updated_at = _now()
                # 首条用户消息自动定标题
                if role == "user" and (not sess.title or sess.title == "新对话") and content:
                    sess.title = _title_from_text(content)

            await session.flush()
            out = self._message_to_api(msg)

        return out

    async def append_turn(
        self,
        session_id: str,
        *,
        user_content: str,
        assistant_content: str,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        tool_trace: Optional[List[Any]] = None,
    ) -> Dict[str, Any]:
        """写入一轮 user + assistant，并更新会话 model"""
        await self.append_message(session_id, role="user", content=user_content)
        await self.append_message(
            session_id,
            role="assistant",
            content=assistant_content,
            tool_trace=tool_trace,
        )
        if model or provider:
            await self.update_session(session_id, model=model, provider=provider)
        return await self.get_session(session_id, with_messages=True) or {}

    async def get_history_for_llm(
        self,
        session_id: str,
        *,
        limit: int = 20,
    ) -> List[Dict[str, str]]:
        """取最近若干条 user/assistant，供模型上下文"""
        detail = await self.get_session(session_id, with_messages=True)
        if not detail:
            return []
        msgs = [
            {"role": m["role"], "content": m["content"]}
            for m in (detail.get("messages") or [])
            if m.get("role") in ("user", "assistant") and m.get("content")
        ]
        return msgs[-max(1, min(int(limit or 20), 100)) :]

    @staticmethod
    def _message_to_api(m: AgentChatMessage) -> Dict[str, Any]:
        data_json = m.data_json
        tool_trace = None
        data = None
        if isinstance(data_json, dict) and "tool_trace" in data_json:
            tool_trace = data_json.get("tool_trace")
            # 若仅有 tool_trace，不把整个 dict 当 data
            rest = {k: v for k, v in data_json.items() if k != "tool_trace"}
            if "data" in rest and len(rest) == 1:
                data = rest.get("data")
            elif rest:
                data = rest
            else:
                data = None
        else:
            data = data_json

        return {
            "id": m.id,
            "session_id": m.session_id,
            "role": m.role,
            "content": m.content or "",
            "skill": m.skill,
            "data": data,
            "data_type": m.data_type,
            "tool_trace": tool_trace,
            "seq": m.seq,
            "time": _fmt(m.created_at) if m.created_at else None,
            "created_at": _fmt(m.created_at) if m.created_at else None,
        }


conversation_repo = ConversationRepository()

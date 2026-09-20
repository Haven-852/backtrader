"""
Agent 对话存储服务

业务层入口（供 routers / chat 使用）：
  - 创建 / 列出会话
  - 读取历史并继续对话
  - 追加消息 / 删除会话

底层写入: data_layer.storage.conversation_repo → PostgreSQL
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from data_layer.storage.conversation_repo import conversation_repo

logger = logging.getLogger(__name__)


class ConversationStore:
    """大模型对话持久化门面"""

    def __init__(self, repo=conversation_repo):
        self.repo = repo

    async def ensure_ready(self) -> None:
        await self.repo.ensure_tables()

    async def create(
        self,
        *,
        title: Optional[str] = None,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        user_id: str = "default",
    ) -> Dict[str, Any]:
        return await self.repo.create_session(
            title=title,
            model=model,
            provider=provider,
            user_id=user_id,
        )

    async def list(
        self,
        *,
        user_id: str = "default",
        limit: int = 50,
        include_archived: bool = False,
    ) -> List[Dict[str, Any]]:
        return await self.repo.list_sessions(
            user_id=user_id,
            limit=limit,
            include_archived=include_archived,
        )

    async def get(self, session_id: str) -> Optional[Dict[str, Any]]:
        return await self.repo.get_session(session_id, with_messages=True)

    async def rename(self, session_id: str, title: str) -> Optional[Dict[str, Any]]:
        return await self.repo.update_session(session_id, title=title)

    async def archive(self, session_id: str, archived: bool = True) -> Optional[Dict[str, Any]]:
        return await self.repo.update_session(session_id, is_archived=archived)

    async def delete(self, session_id: str) -> bool:
        return await self.repo.delete_session(session_id)

    async def add_message(
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
        return await self.repo.append_message(
            session_id,
            role=role,
            content=content,
            skill=skill,
            data=data,
            data_type=data_type,
            tool_trace=tool_trace,
        )

    async def save_chat_turn(
        self,
        session_id: Optional[str],
        *,
        user_content: str,
        assistant_content: str,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        tool_trace: Optional[List[Any]] = None,
        user_id: str = "default",
    ) -> Dict[str, Any]:
        """
        保存一轮对话。session_id 为空时自动建会话。
        返回 {session, user_message, assistant_message} 结构的会话详情。
        """
        if not session_id:
            created = await self.create(
                title=None,
                model=model,
                provider=provider,
                user_id=user_id,
            )
            session_id = created["id"]

        detail = await self.repo.append_turn(
            session_id,
            user_content=user_content,
            assistant_content=assistant_content,
            model=model,
            provider=provider,
            tool_trace=tool_trace,
        )
        return detail

    async def history_for_llm(self, session_id: str, limit: int = 20) -> List[Dict[str, str]]:
        return await self.repo.get_history_for_llm(session_id, limit=limit)


conversation_store = ConversationStore()

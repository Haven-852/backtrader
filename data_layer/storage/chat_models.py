"""
Agent 对话持久化 ORM 模型

表:
  - agent_chat_session  会话元数据
  - agent_chat_message  消息明细
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from .models import Base, _fmt, _row_to_dict


class AgentChatSession(Base):
    """对话会话"""

    __tablename__ = "agent_chat_session"

    id = Column(String(36), primary_key=True, comment="会话 UUID")
    title = Column(String(200), nullable=False, default="新对话", comment="标题")
    model = Column(String(64), comment="最近使用模型")
    provider = Column(String(32), comment="提供商")
    user_id = Column(String(64), nullable=False, default="default", comment="用户标识")
    message_count = Column(Integer, nullable=False, default=0)
    is_archived = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    messages = relationship(
        "AgentChatMessage",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="AgentChatMessage.seq",
    )

    _cols = [
        "id", "title", "model", "provider", "user_id",
        "message_count", "is_archived", "created_at", "updated_at",
    ]

    def to_dict(self) -> dict[str, Any]:
        return _row_to_dict(self, self._cols)


class AgentChatMessage(Base):
    """单条对话消息"""

    __tablename__ = "agent_chat_message"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    session_id = Column(
        String(36),
        ForeignKey("agent_chat_session.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属会话",
    )
    role = Column(String(20), nullable=False, comment="user/assistant/system")
    content = Column(Text, nullable=False, default="")
    skill = Column(String(64), comment="技能名（system 消息）")
    data_json = Column(JSONB, comment="结构化附加数据 / tool_trace")
    data_type = Column(String(32), comment="前端渲染类型")
    seq = Column(Integer, nullable=False, default=0, comment="会话内序号")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    session = relationship("AgentChatSession", back_populates="messages")

    _cols = [
        "id", "session_id", "role", "content", "skill",
        "data_json", "data_type", "seq", "created_at",
    ]

    def to_dict(self) -> dict[str, Any]:
        d = _row_to_dict(self, self._cols)
        # 前端字段名兼容
        if d.get("data_json") is not None:
            d["data"] = d["data_json"]
        return d


CHAT_MODELS = [AgentChatSession, AgentChatMessage]
CHAT_TABLE_MODEL_MAP = {m.__tablename__: m for m in CHAT_MODELS}

"""
Chat Router - 对话路由层
POST /chat/messages              - 发送消息（非流式，自动持久化）
POST /chat/stream                - 发送消息（流式 SSE）
GET  /chat/models                - 获取可用模型列表
GET  /chat/sessions              - 会话列表
POST /chat/sessions              - 新建会话
GET  /chat/sessions/{id}         - 读取会话（含消息，可继续）
PATCH /chat/sessions/{id}        - 重命名 / 归档
DELETE /chat/sessions/{id}       - 删除会话
POST /chat/sessions/{id}/messages - 追加单条消息（技能面板等）
"""

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import List, Dict, Optional, Any
from datetime import datetime

from server.chat_service import chat_service, ChatRequest
from server.model_service import model_service

router = APIRouter(prefix="/chat", tags=["chat"])


# ─── 请求/响应模型 ────────────────────────────────────

class MessageRequest(BaseModel):
    query: str = Field(..., description="用户输入的消息", min_length=1)
    model: str = Field(default="deepseek-chat", description="模型 ID")
    provider: str = Field(default="deepseek", description="模型提供商")
    history: List[Dict] = Field(default_factory=list, description="对话历史（无 session 时兜底）")
    system_prompt: Optional[str] = Field(None, description="系统提示词")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0, description="温度参数")
    max_tokens: int = Field(default=4096, ge=1, le=128000, description="最大 token 数")
    use_tools: bool = Field(default=True, description="是否启用 tools/ 技能工具自动调用")
    session_id: Optional[str] = Field(None, description="会话 ID；空则新建并落库")
    persist: bool = Field(default=True, description="是否写入 PostgreSQL")
    user_id: str = Field(default="default", description="用户标识")
    agent_mode: Optional[str] = Field(
        None, description="Agent 模式：paper_trade=纸面交易（仅纸面工具）"
    )
    tool_skills: Optional[List[str]] = Field(
        None, description="限制可用技能 ID 列表，如 ['paper-trade']"
    )
    account_id: Optional[int] = Field(
        None, description="纸面账户 ID（paper_trade 模式注入工具上下文）"
    )
    context: Dict[str, Any] = Field(default_factory=dict, description="额外上下文")


class MessageResponse(BaseModel):
    content: str
    model: str
    provider: str
    usage: Dict = {}
    finish_reason: str = "stop"
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    tool_trace: List[Dict] = Field(default_factory=list, description="工具调用轨迹")
    session_id: Optional[str] = None


class CreateSessionRequest(BaseModel):
    title: Optional[str] = "新对话"
    model: Optional[str] = None
    provider: Optional[str] = None
    user_id: str = "default"


class UpdateSessionRequest(BaseModel):
    title: Optional[str] = None
    is_archived: Optional[bool] = None
    model: Optional[str] = None
    provider: Optional[str] = None


class AppendMessageRequest(BaseModel):
    role: str = Field(..., description="user/assistant/system")
    content: str = Field(..., description="消息内容")
    skill: Optional[str] = None
    data: Optional[Any] = None
    data_type: Optional[str] = None
    tool_trace: Optional[List[Any]] = None


# ─── 路由 ─────────────────────────────────────────────

@router.post("/messages", response_model=MessageResponse)
async def send_message(request: MessageRequest):
    """发送消息（非流式）- 支持多模型；默认落库并可续聊"""
    try:
        from agents.conversation import conversation_store

        history = list(request.history or [])
        session_id = request.session_id

        # 有 session 时优先用库内历史
        if session_id:
            db_hist = await conversation_store.history_for_llm(session_id, limit=20)
            if db_hist:
                history = db_hist

        ctx = dict(request.context or {})
        if request.account_id is not None:
            ctx["account_id"] = request.account_id

        chat_req = ChatRequest(
            query=request.query,
            model=request.model,
            provider=request.provider,
            stream=False,
            history=history,
            system_prompt=request.system_prompt,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            agent_mode=request.agent_mode,
            tool_skills=request.tool_skills,
            context=ctx,
        )

        if request.use_tools:
            from server.tool_agent_service import tool_agent_service
            response = await tool_agent_service.chat_with_tools(chat_req)
        else:
            response = await chat_service.chat(chat_req)

        usage = response.usage or {}
        tool_trace = usage.get("tool_trace") or []
        order_ids = usage.get("order_ids") or []

        if request.persist:
            try:
                detail = await conversation_store.save_chat_turn(
                    session_id,
                    user_content=request.query,
                    assistant_content=response.content or "",
                    model=response.model,
                    provider=response.provider,
                    tool_trace=tool_trace,
                    user_id=request.user_id,
                )
                session_id = detail.get("id") or session_id
            except Exception as pe:
                # 落库失败不影响本次回复
                import logging
                logging.getLogger(__name__).warning("对话落库失败: %s", pe)

        # 纸面交易模式：写入决策审计
        if request.agent_mode == "paper_trade" and ctx.get("account_id") is not None:
            try:
                from server.paper_broker_service import paper_broker_service
                paper_broker_service.log_decision(
                    int(ctx["account_id"]),
                    session_id=session_id,
                    user_query=request.query,
                    assistant_summary=response.content or "",
                    tool_trace=tool_trace,
                    order_ids=[int(x) for x in order_ids],
                    meta={
                        "model": response.model,
                        "provider": response.provider,
                        "agent_mode": "paper_trade",
                    },
                )
            except Exception as de:
                import logging
                logging.getLogger(__name__).warning("决策日志写入失败: %s", de)

        return MessageResponse(
            content=response.content,
            model=response.model,
            provider=response.provider,
            usage={k: v for k, v in usage.items() if k not in ("tool_trace",)},
            finish_reason=response.finish_reason,
            timestamp=response.timestamp,
            tool_trace=tool_trace,
            session_id=session_id,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"对话失败: {str(e)}")


@router.post("/stream")
async def send_message_stream(request: MessageRequest):
    """发送消息（流式 SSE）- 支持多模型切换"""
    chat_req = ChatRequest(
        query=request.query,
        model=request.model,
        provider=request.provider,
        stream=True,
        history=request.history,
        system_prompt=request.system_prompt,
        temperature=request.temperature,
        max_tokens=request.max_tokens,
    )

    async def event_generator():
        try:
            async for chunk in chat_service.chat_stream(chat_req):
                yield f"data: {chunk}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            yield f"data: [ERROR] {str(e)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/models")
async def get_models():
    """获取所有可用模型列表及其状态"""
    try:
        models = await model_service.get_models()
        providers = await model_service.get_providers()
        return {
            "models": models,
            "providers": providers,
            "total": len(models),
            "available_count": sum(1 for m in models if m["available"]),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取模型列表失败: {str(e)}")


@router.get("/sessions")
async def list_sessions(
    user_id: str = Query(default="default"),
    limit: int = Query(default=50, ge=1, le=200),
    include_archived: bool = Query(default=False),
):
    """会话列表（按更新时间倒序）"""
    try:
        from agents.conversation import conversation_store
        sessions = await conversation_store.list(
            user_id=user_id,
            limit=limit,
            include_archived=include_archived,
        )
        return {"ok": True, "sessions": sessions, "count": len(sessions)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取会话列表失败: {str(e)}")


@router.post("/sessions")
async def create_session(request: CreateSessionRequest):
    """新建空会话"""
    try:
        from agents.conversation import conversation_store
        session = await conversation_store.create(
            title=request.title,
            model=request.model,
            provider=request.provider,
            user_id=request.user_id,
        )
        return {"ok": True, "session": session}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"创建会话失败: {str(e)}")


@router.get("/sessions/{session_id}")
async def get_session(session_id: str):
    """读取会话及全部消息（点击历史后继续对话）"""
    try:
        from agents.conversation import conversation_store
        detail = await conversation_store.get(session_id)
        if not detail:
            raise HTTPException(status_code=404, detail="会话不存在")
        return {"ok": True, "session": detail}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取会话失败: {str(e)}")


@router.patch("/sessions/{session_id}")
async def update_session(session_id: str, request: UpdateSessionRequest):
    """重命名或归档会话"""
    try:
        from agents.conversation import conversation_store
        from data_layer.storage.conversation_repo import conversation_repo
        detail = await conversation_repo.update_session(
            session_id,
            title=request.title,
            model=request.model,
            provider=request.provider,
            is_archived=request.is_archived,
        )
        if not detail:
            raise HTTPException(status_code=404, detail="会话不存在")
        return {"ok": True, "session": detail}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"更新会话失败: {str(e)}")


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    """删除会话及其消息"""
    try:
        from agents.conversation import conversation_store
        ok = await conversation_store.delete(session_id)
        if not ok:
            raise HTTPException(status_code=404, detail="会话不存在")
        return {"ok": True, "deleted": session_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除会话失败: {str(e)}")


@router.post("/sessions/{session_id}/messages")
async def append_message(session_id: str, request: AppendMessageRequest):
    """向已有会话追加一条消息（技能面板结果等）"""
    try:
        from agents.conversation import conversation_store
        detail = await conversation_store.get(session_id)
        if not detail:
            raise HTTPException(status_code=404, detail="会话不存在")
        msg = await conversation_store.add_message(
            session_id,
            role=request.role,
            content=request.content,
            skill=request.skill,
            data=request.data,
            data_type=request.data_type,
            tool_trace=request.tool_trace,
        )
        return {"ok": True, "message": msg}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"追加消息失败: {str(e)}")


@router.get("/history")
async def get_history(user_id: str = Query(default="default"), limit: int = Query(default=50, ge=1, le=200)):
    """兼容旧接口：等同 sessions 列表"""
    try:
        from agents.conversation import conversation_store
        sessions = await conversation_store.list(user_id=user_id, limit=limit)
        return {"ok": True, "sessions": sessions, "count": len(sessions)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取对话历史失败: {str(e)}")

"""
Chat Router - 对话路由层
POST /chat/messages    - 发送消息（非流式）
POST /chat/stream      - 发送消息（流式 SSE）
GET  /chat/models      - 获取可用模型列表
GET  /chat/history     - 获取对话历史（预留）

参考 Yuxi 项目 server/routers/chat_router.py
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import List, Dict, Optional
from datetime import datetime

from server.chat_service import chat_service, ChatRequest
from server.model_service import model_service

router = APIRouter(prefix="/chat", tags=["chat"])


# ─── 请求/响应模型 ────────────────────────────────────

class MessageRequest(BaseModel):
    query: str = Field(..., description="用户输入的消息", min_length=1)
    model: str = Field(default="deepseek-chat", description="模型 ID")
    provider: str = Field(default="deepseek", description="模型提供商")
    history: List[Dict] = Field(default_factory=list, description="对话历史")
    system_prompt: Optional[str] = Field(None, description="系统提示词")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0, description="温度参数")
    max_tokens: int = Field(default=4096, ge=1, le=128000, description="最大 token 数")


class MessageResponse(BaseModel):
    content: str
    model: str
    provider: str
    usage: Dict = {}
    finish_reason: str = "stop"
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())


# ─── 路由 ─────────────────────────────────────────────

@router.post("/messages", response_model=MessageResponse)
async def send_message(request: MessageRequest):
    """发送消息（非流式）- 支持多模型切换"""
    try:
        chat_req = ChatRequest(
            query=request.query,
            model=request.model,
            provider=request.provider,
            stream=False,
            history=request.history,
            system_prompt=request.system_prompt,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
        )
        response = await chat_service.chat(chat_req)
        return MessageResponse(
            content=response.content,
            model=response.model,
            provider=response.provider,
            usage=response.usage,
            finish_reason=response.finish_reason,
            timestamp=response.timestamp,
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


@router.get("/history")
async def get_history():
    """获取对话历史（预留接口）"""
    return {
        "message": "对话历史功能将在数据层集成后实现",
        "sessions": [],
    }

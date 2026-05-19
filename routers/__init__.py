"""
Routers Layer - FastAPI APIRouter 路由层
按功能模块划分路由，负责接收 HTTP 请求、参数校验、调用 service 层、返回响应
参考 Yuxi 项目 server/routers/ 结构
"""

from .chat_router import router as chat_router
from .model_router import router as model_router
from .system_router import router as system_router
from .dash_router import router as dash_router

__all__ = ["chat_router", "model_router", "system_router", "dash_router"]

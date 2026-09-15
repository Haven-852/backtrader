"""
AIquant Backtrader Agent Platform v3.0 - FastAPI 入口
架构：web → routers → server → data_layer
仿 Yuxi 项目的分层架构设计

Yuxi 风格后端 - FastAPI (Vue3 Frontend)
提供多模型对话、连通性测试、数据库测试、多智能体管理 API
"""

import os
import sys
import logging
import asyncio

# Add parent directory (project root) to sys.path so routers/server/data_layer can be imported
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ─── 创建 FastAPI 应用 ─────────────────────────────────

app = FastAPI(
    title="AIquant · Backtrader Agent Platform",
    version="3.0.0",
    description=(
        "仿 Yuxi 风格的量化智能体平台\n\n"
        "**架构**: web (Vue3) → routers (FastAPI APIRouter) → server (Service) → data_layer (Storage)\n\n"
        "**特性**:\n"
        "- 多模型对话：OpenAI / DeepSeek / Anthropic / Ollama / Grok\n"
        "- 13 个量化智能体：信号、风控、组合优化、回测\n"
        "- 5 种存储引擎：InfluxDB / PostgreSQL+Timescale / Redis / MinIO / ClickHouse\n"
        "- 流式对话支持 (SSE)"
    ),
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS - 允许前端跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── 注册路由模块 ─────────────────────────────────────
# 分层：routers（路由层）→ server（服务层）→ data_layer（数据层）

from routers.chat_router import router as chat_router
from routers.model_router import router as model_router
from routers.system_router import router as system_router
from routers.dash_router import router as dash_router
from routers.storage_router import router as storage_router
from routers.skill_router import router as skill_router
from routers.market_data_router import router as market_data_router
from routers.strategy_router import router as strategy_router
from routers.data_router import router as data_router

app.include_router(system_router)          # /, /health, /agents
app.include_router(chat_router)            # /chat/*
app.include_router(model_router)           # /models/*
app.include_router(dash_router)            # /dash/*
app.include_router(storage_router)         # /storage/*
app.include_router(skill_router)           # /skills/*
app.include_router(market_data_router)     # /market/*
app.include_router(strategy_router)        # /strategy/*
app.include_router(data_router)            # /data/selective/*, /data/continuous/*

# ─── 启动日志 ─────────────────────────────────────────

@app.on_event("startup")
async def startup_event():
    """应用启动时初始化"""
    logger.info("=" * 60)
    logger.info("🚀 AIquant Backtrader Agent Platform v3.0 启动中...")
    logger.info("架构: web (Vue3) → routers → server → data_layer")
    logger.info("=" * 60)

    # 预热服务
    from server.chat_service import chat_service
    models = await chat_service.get_available_models()
    providers = await chat_service.get_available_providers()

    # 初始化市场数据 PostgreSQL 连接池（行情中心 /market/* 依赖）
    try:
        from data_layer.storage import pg_manager
        pg_manager.initialize()
        logger.info("PostgreSQL 市场数据管理器 (pg_manager) 初始化成功")
    except Exception as e:
        logger.warning(f"PostgreSQL 市场数据管理器初始化失败: {e}")

    logger.info(f"已注册路由: /, /health, /agents, /chat/*, /models/*, /dash/* (含 etf-board), /storage/*, /skills/*, /market/*")
    logger.info(f"可用模型: {len(models)} 个 ({sum(1 for m in models if m.get('available'))} 个已配置)")
    logger.info(f"可用提供商: {[p['id'] for p in providers]}")
    logger.info("=" * 60)

    try:
        from server.selective_data_service import watchlist_continuous_loop
        app.state.watchlist_task = asyncio.create_task(watchlist_continuous_loop())
        logger.info("自选股持续日更循环已启动")
    except Exception as e:
        logger.warning(f"自选股持续日更循环启动失败: {e}")


@app.on_event("shutdown")
async def shutdown_event():
    """应用关闭时清理"""
    logger.info("AIquant Backtrader Agent Platform 正在关闭...")
    task = getattr(app.state, "watchlist_task", None)
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# ─── 直接运行 ─────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("BACKEND_PORT", "8000"))
    host = os.getenv("BACKEND_HOST", "0.0.0.0")
    debug = os.getenv("DEBUG", "true").lower() == "true"

    logger.info(f"启动服务器: {host}:{port} (debug={debug})")
    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=debug,
        log_level="info",
    )

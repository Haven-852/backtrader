"""
System Router - 系统管理路由层
GET  /            - 平台根信息
GET  /health      - 系统健康检查
GET  /agents      - 智能体列表
GET  /agents/{id} - 智能体详情
GET  /agents/categories - 智能体分类

参考 Yuxi 项目 server/routers/system_router.py
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Dict, Any

from server.model_service import model_service
from server.agent_service import agent_service

router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    status: str
    version: str
    services: Dict = {}


# ─── 路由 ─────────────────────────────────────────────

@router.get("/")
async def root():
    """平台根接口"""
    return {
        "message": "🚀 AIquant Backtrader Agent Platform v3.0 已启动",
        "status": "healthy",
        "version": "3.0.0",
        "architecture": "web → routers → server → data_layer",
        "features": {
            "chat": "多模型对话（OpenAI / DeepSeek / Anthropic / Ollama / Grok）",
            "agents": "13 个量化智能体",
            "storage": "InfluxDB + PostgreSQL/TimescaleDB + Redis + MinIO",
            "backtest": "Backtrader 回测引擎",
        },
        "docs": "/docs",
    }


@router.get("/health", response_model=HealthResponse)
async def health_check():
    """系统健康检查"""
    try:
        health = await model_service.get_health()
        return HealthResponse(
            status=health.get("status", "unknown"),
            version=health.get("version", "3.0.0"),
            services=health.get("services", {}),
        )
    except Exception as e:
        return HealthResponse(
            status="degraded",
            version="3.0.0",
            services={"error": str(e)},
        )


@router.get("/agents")
async def list_agents():
    """获取所有智能体列表"""
    try:
        agents = agent_service.get_agents()
        categories = agent_service.get_agent_categories()
        return {
            "agents": agents,
            "total": len(agents),
            "categories": categories,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取智能体列表失败: {str(e)}")


@router.get("/agents/{agent_id}")
async def get_agent_detail(agent_id: str):
    """获取指定智能体的详细信息"""
    agent = agent_service.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"智能体 '{agent_id}' 不存在")
    return agent


@router.get("/agents/categories")
async def get_agent_categories():
    """获取智能体分类"""
    try:
        return agent_service.get_agent_categories()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取分类失败: {str(e)}")


@router.get("/architecture")
async def get_architecture():
    """获取系统架构概览（总体架构层 + 总体流程图 + 数据层）"""
    try:
        agents = agent_service.BUILTIN_AGENTS
    except Exception:
        agents = []

    return {
        "layers": {
            "agents": {
                "name": "智能体层 agents/",
                "description": "市场与概念模型 Market & Conceptual Models",
                "icon": "brain",
                "color": "#6366f1",
                "items": [
                    {"id": a["id"], "name": a["name"], "category": a.get("category", ""),
                     "status": a.get("status", "online")}
                    for a in agents
                ],
            },
            "backend": {
                "name": "后端逻辑层 backend/",
                "description": "适配器 Adapters · 调度器 Scheduler · 处理器 Processors",
                "icon": "gears",
                "color": "#22c55e",
                "items": [
                    {"id": "adapters", "name": "数据适配器 Adapters", "desc": "统一数据源接口，封装 Tushare/AKShare/FRED"},
                    {"id": "scheduler", "name": "任务调度器 Scheduler", "desc": "定时采集、数据同步、状态监控"},
                    {"id": "processors", "name": "数据处理器 Processors", "desc": "格式清洗、指标计算、因子工程"},
                ],
            },
            "server": {
                "name": "接口服务层 server/",
                "description": "HTTP API 层 (FastAPI / Uvicorn / Pydantic)",
                "icon": "server",
                "color": "#f59e0b",
                "items": [
                    {"id": "dash_service", "name": "行情看板服务 dash_service", "desc": "K线查询、技术指标、股票搜索"},
                    {"id": "chat_service", "name": "对话服务 chat_service", "desc": "多模型对话、流式响应"},
                    {"id": "model_service", "name": "模型服务 model_service", "desc": "LLM 模型管理与切换"},
                    {"id": "agent_service", "name": "智能体服务 agent_service", "desc": "13 个量化智能体编排"},
                ],
            },
            "routers": {
                "name": "路由层 routers/",
                "description": "API 端点管理（dash / chat / model / system）",
                "icon": "route",
                "color": "#ef4444",
                "items": [
                    {"id": "dash_router", "name": "dash_router", "desc": "/dash/* — 行情看板接口"},
                    {"id": "chat_router", "name": "chat_router", "desc": "/chat/* — 对话接口"},
                    {"id": "model_router", "name": "model_router", "desc": "/models/* — 模型测试接口"},
                    {"id": "system_router", "name": "system_router", "desc": "/ — 系统与健康检查"},
                ],
            },
            "web": {
                "name": "可视化层 web/",
                "description": "前端界面 (Vue 3 + Vite + Tailwind CSS + TypeScript + ECharts)",
                "icon": "display",
                "color": "#06b6d4",
                "items": [
                    {"id": "dash_view", "name": "行情看板 DashView", "desc": "K线图 · 技术指标 · 实时行情"},
                    {"id": "chat_view", "name": "大模型对话 ChatView", "desc": "多模型 AI 对话分析"},
                    {"id": "system_view", "name": "系统架构 SystemView", "desc": "架构总览 · 数据流 · 数据层"},
                ],
            },
            "data_layer": {
                "name": "数据仓库层 data_layer/",
                "description": "数据采集、存储与分发 (Tushare · AKShare · InfluxDB · PostgreSQL)",
                "icon": "database",
                "color": "#a855f7",
                "providers": {
                    "tushare": {"name": "Tushare Pro", "status": "configured", "desc": "沪深A股行情、财务、基金"},
                    "akshare": {"name": "AKShare", "status": "configured", "desc": "免费开源金融数据接口"},
                    "fred": {"name": "FRED", "status": "configured", "desc": "美联储宏观经济数据"},
                },
                "collectors": {
                    "tushare_collector": {"name": "Tushare采集器", "desc": "股票日/周/月线、财务数据、基金净值"},
                    "akshare_collector": {"name": "AKShare采集器", "desc": "实时行情、行业板块、资金流向"},
                    "fred_collector": {"name": "FRED采集器", "desc": "利率、GDP、CPI、失业率等宏观指标"},
                    "advanced_collector": {"name": "高级采集器", "desc": "多源融合、增量更新、断点续传"},
                },
                "engines": {
                    "postgresql": {"name": "PostgreSQL + TimescaleDB", "desc": "结构化数据 & 时序K线", "port": 15432},
                    "influxdb": {"name": "InfluxDB", "desc": "高频行情 & 分钟线Tick数据", "port": 8086},
                    "redis": {"name": "Redis", "desc": "实时缓存 & 消息队列", "port": 16379},
                    "minio": {"name": "MinIO", "desc": "S3兼容数据湖 & 回测结果存储", "port": 9000},
                },
            },
        },
        "data_flow": {
            "pipeline": [
                {"from": "数据源", "to": "data_layer", "label": "Tushare/AKShare/FRED\n采集器拉取原始数据"},
                {"from": "data_layer", "to": "PostgreSQL", "label": "结构化数据\n股票主档/K线/财务"},
                {"from": "data_layer", "to": "InfluxDB", "label": "高频时序数据\n分钟线/Tick行情"},
                {"from": "data_layer", "to": "Redis", "label": "实时缓存\n热门行情/计算结果"},
                {"from": "PostgreSQL", "to": "backend", "label": "SQL查询\n历史数据分析"},
                {"from": "InfluxDB", "to": "backend", "label": "Flux查询\n实时行情推送"},
                {"from": "Redis", "to": "backend", "label": "缓存读取\n低延迟数据获取"},
                {"from": "backend", "to": "server", "label": "服务调用\nAdapter/Scheduler"},
                {"from": "server", "to": "web", "label": "HTTP API\nRESTful / SSE流式"},
                {"from": "web", "to": "用户", "label": "Vue 3 UI\n交互式看板"},
            ],
        },
        "connections": {
            "web_to_server": {
                "direction": "web ↔ server",
                "mode": "请求对答 call-and-response",
                "detail": "web 通过 vite proxy 转发 /api 请求到 server",
            },
            "server_to_routers": {
                "direction": "server → routers",
                "mode": "路由分发",
                "detail": "server 通过 FastAPI router 分发请求到具体功能模块",
            },
            "routers_to_services": {
                "direction": "routers → service",
                "mode": "服务调度",
                "detail": "路由层调用对应 service 的业务逻辑",
            },
        },
    }

"""
Model Service - 模型提供商管理服务
负责：模型列表、连通性测试、模型提供商配置管理

参考 Yuxi 项目 package/yuxi/services/model_provider_service.py
"""

import os
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


class ModelService:
    """模型提供商管理服务"""

    def __init__(self):
        self._chat_svc = None

    @property
    def chat_service(self):
        if self._chat_svc is None:
            from .chat_service import chat_service
            self._chat_svc = chat_service
        return self._chat_svc

    async def get_models(self) -> List[Dict]:
        """获取所有可用模型及其状态"""
        return await self.chat_service.get_available_models()

    async def get_providers(self) -> List[Dict]:
        """获取已配置的模型提供商"""
        return await self.chat_service.get_available_providers()

    async def test_model(self, model_id: str) -> Dict[str, Any]:
        """测试模型连通性"""
        start = datetime.now()

        # 检查提供商是否配置
        from .chat_service import ChatService
        provider_name = ChatService.MODEL_PROVIDER_MAP.get(model_id, "unknown")
        providers = await self.chat_service.get_available_providers()
        provider_ids = [p["id"] for p in providers]

        if provider_name not in provider_ids:
            return {
                "status": "unavailable",
                "message": f"模型 {model_id} 的提供商 '{provider_name}' 未配置 API Key",
                "provider": provider_name,
                "suggestion": f"请在 .env 中配置 {provider_name.upper()}_API_KEY",
            }

        # 尝试调用模型
        from .chat_service import ChatRequest
        request = ChatRequest(
            query="ping",
            model=model_id,
            provider=provider_name,
            stream=False,
            max_tokens=10,
            temperature=0.0,
        )

        try:
            response = await self.chat_service.chat(request)
            elapsed = (datetime.now() - start).total_seconds()

            if response.provider == "error":
                return {
                    "status": "error",
                    "message": f"模型调用异常",
                    "error": response.usage.get("error", "未知错误"),
                    "latency_ms": int(elapsed * 1000),
                }
            elif response.provider == "fallback":
                return {
                    "status": "unavailable",
                    "message": "返回了模拟回复，API 未正确配置",
                    "latency_ms": int(elapsed * 1000),
                }
            else:
                return {
                    "status": "success",
                    "message": f"✅ {model_id} 连通正常",
                    "model": response.model,
                    "provider": response.provider,
                    "latency_ms": int(elapsed * 1000),
                    "usage": response.usage,
                }
        except Exception as e:
            elapsed = (datetime.now() - start).total_seconds()
            return {
                "status": "error",
                "message": f"模型测试失败",
                "error": str(e),
                "latency_ms": int(elapsed * 1000),
                "suggestion": "请检查网络连接和 API Key 是否正确",
            }

    async def test_database(self) -> Dict[str, Any]:
        """测试数据库连通性 (PostgreSQL + Redis + InfluxDB)"""
        results = {}

        # Test PostgreSQL
        try:
            from sqlalchemy import create_engine, text
            pg_host = os.getenv("POSTGRES_HOST", "localhost")
            pg_port = os.getenv("POSTGRES_PORT", "15432")
            pg_user = os.getenv("POSTGRES_USER", "quant")
            pg_pass = os.getenv("POSTGRES_PASSWORD", "backtrader123")
            pg_db = os.getenv("POSTGRES_DB", "backtrader")

            engine = create_engine(
                f"postgresql://{pg_user}:{pg_pass}@{pg_host}:{pg_port}/{pg_db}",
                connect_args={"connect_timeout": 5},
            )
            with engine.connect() as conn:
                result = conn.execute(text("SELECT 1 as test")).scalar()
                results["postgres"] = {
                    "status": "connected",
                    "message": f"✅ PostgreSQL 连接成功 ({pg_host}:{pg_port})",
                }
            engine.dispose()
        except Exception as e:
            results["postgres"] = {
                "status": "error",
                "message": f"❌ PostgreSQL 连接失败: {str(e)[:80]}",
            }

        # Test Redis
        try:
            import redis
            redis_host = os.getenv("REDIS_HOST", "localhost")
            redis_port = int(os.getenv("REDIS_PORT", "16379"))
            r = redis.Redis(host=redis_host, port=redis_port, db=0, socket_connect_timeout=3)
            r.ping()
            results["redis"] = {
                "status": "connected",
                "message": f"✅ Redis 连接成功 ({redis_host}:{redis_port})",
            }
        except Exception as e:
            results["redis"] = {
                "status": "error",
                "message": f"❌ Redis 连接失败: {str(e)[:80]}",
            }

        # Test InfluxDB
        try:
            influx_url = os.getenv("INFLUX_URL", "http://localhost:8086")
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{influx_url}/health")
                if resp.status_code == 200:
                    results["influx"] = {
                        "status": "connected",
                        "message": f"✅ InfluxDB 连接成功",
                    }
                else:
                    results["influx"] = {
                        "status": "warning",
                        "message": f"⚠️ InfluxDB 响应异常: {resp.status_code}",
                    }
        except Exception as e:
            results["influx"] = {
                "status": "disconnected",
                "message": f"❌ InfluxDB 连接失败: {str(e)[:80]}",
            }

        all_connected = all(
            v.get("status") == "connected" for v in results.values()
        )
        return {
            "status": "healthy" if all_connected else "degraded",
            "timestamp": datetime.now().isoformat(),
            "services": results,
        }

    async def get_health(self) -> Dict[str, Any]:
        """获取系统健康状况"""
        db_status = await self.test_database()
        providers = await self.get_providers()

        return {
            "status": "healthy",
            "version": "3.0.0",
            "architecture": "web → routers → server → data_layer",
            "timestamp": datetime.now().isoformat(),
            "services": {
                "database": db_status,
                "model_providers": {
                    "total": len(providers),
                    "available": providers,
                },
            },
        }


# 全局实例
model_service = ModelService()

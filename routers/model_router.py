"""
Model Router - 模型管理路由层
GET  /models              - 获取所有模型列表
GET  /models/providers    - 获取模型提供商列表
POST /models/test/{id}    - 测试指定模型连通性
GET  /models/test/database - 测试数据库连通性

参考 Yuxi 项目 server/routers/model_provider_router.py
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Dict, Any

from server.model_service import model_service

router = APIRouter(prefix="/models", tags=["models"])


class TestResult(BaseModel):
    status: str
    message: str
    details: Dict = {}


# ─── 路由 ─────────────────────────────────────────────

@router.get("")
async def list_models():
    """获取所有可用模型"""
    try:
        models = await model_service.get_models()
        return {
            "models": models,
            "total": len(models),
            "available": sum(1 for m in models if m["available"]),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取模型列表失败: {str(e)}")


@router.get("/providers")
async def list_providers():
    """获取已配置的模型提供商"""
    try:
        providers = await model_service.get_providers()
        return {"providers": providers, "total": len(providers)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取提供商列表失败: {str(e)}")


@router.post("/test/{model_id}", response_model=TestResult)
async def test_model_connectivity(model_id: str):
    """测试指定模型的连通性"""
    try:
        result = await model_service.test_model(model_id)
        return TestResult(
            status=result.get("status", "unknown"),
            message=result.get("message", ""),
            details=result,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"模型测试失败: {str(e)}")


@router.get("/test/database", response_model=TestResult)
async def test_database_connectivity():
    """测试存储层连通性"""
    try:
        result = await model_service.test_database()
        return TestResult(
            status=result.get("status", "unknown"),
            message="数据库连通性测试完成",
            details=result.get("services", {}),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"数据库测试失败: {str(e)}")

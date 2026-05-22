"""
Storage Router - 多后端存储层路由
支持: PostgreSQL · InfluxDB · Redis · MinIO

GET  /storage/status                  → 所有后端连接状态
GET  /storage/tables?backend=xxx      → 指定后端的数据项列表
GET  /storage/tables/{name}?backend=xxx → 指定后端的预览
"""

from fastapi import APIRouter, HTTPException, Query
from typing import Optional

from server.storage_service import storage_service

router = APIRouter(prefix="/storage", tags=["storage"])

BACKENDS = ["postgresql", "influxdb", "redis", "minio"]


@router.get("/status")
async def get_storage_status():
    """获取所有存储后端的连接状态"""
    try:
        services = await storage_service.get_storage_status()
        # 统计
        online = sum(1 for s in services.values() if s.get("status") == "online")
        total = len(services)
        return {"status": "ok", "services": services, "online": online, "total": total}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tables")
async def list_items(
    backend: str = Query("postgresql", description="后端: postgresql | influxdb | redis | minio"),
    category: Optional[str] = Query(None),
    has_data: Optional[bool] = Query(None),
):
    """获取指定后端的数据项列表（表 / Bucket / Measurement / Key）"""
    if backend not in BACKENDS:
        raise HTTPException(status_code=400, detail=f"后端必须是: {', '.join(BACKENDS)}")

    try:
        items = await storage_service.get_items(backend)

        if category:
            items = [i for i in items if i.get("category") == category]
        if has_data is not None:
            items = [i for i in items if i.get("has_data") == has_data]

        categories = sorted(set(i.get("category","") for i in items))
        return {
            "backend": backend, "items": items,
            "total": len(items), "categories": categories,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tables/{name:path}")
async def get_preview(
    name: str,
    backend: str = Query("postgresql", description="后端"),
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """获取指定数据项的预览"""
    if backend not in BACKENDS:
        raise HTTPException(status_code=400, detail=f"后端必须是: {', '.join(BACKENDS)}")

    try:
        result = await storage_service.get_preview(backend, name, limit, offset)
        if "error" in result:
            st = 404 if "不存在" in result.get("error","") or "未连接" in result.get("error","") else 500
            raise HTTPException(status_code=st, detail=result["error"])
        return {"backend": backend, **result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats/{backend}")
async def get_backend_stats(backend: str):
    """获取指定后端的统计信息"""
    if backend not in BACKENDS:
        raise HTTPException(status_code=400, detail=f"后端必须是: {', '.join(BACKENDS)}")
    try:
        return await storage_service.get_backend_stats(backend)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

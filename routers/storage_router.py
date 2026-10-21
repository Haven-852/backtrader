"""
Storage Router - 多后端存储层路由
支持: PostgreSQL · InfluxDB · Redis · MinIO

GET  /storage/status                  → 所有后端连接状态
GET  /storage/tables?backend=xxx      → 指定后端的数据项列表
GET  /storage/tables/{name}?backend=xxx → 指定后端的预览（limit/offset，前端滚动追加）
GET  /storage/export/{name}?format=csv|markdown → 导出表数据为文件
POST /storage/ai-create-table         → 自然语言 + 大模型建表
GET  /storage/custom-tables           → 用户自定义表清单
POST /storage/custom-tables/{name}/rows → 自定义表插入一行
PATCH /storage/custom-tables/{name}/rows/{pk} → 自定义表按主键更新
"""

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from typing import Any, Dict, Optional
from urllib.parse import quote

from server.storage_service import storage_service

router = APIRouter(prefix="/storage", tags=["storage"])

BACKENDS = ["postgresql", "influxdb", "redis", "minio"]


class AiCreateTableBody(BaseModel):
    prompt: str = Field(..., description="用自然语言描述需要的表")
    profile_id: Optional[int] = Field(None, description="大模型配置 ID，默认用已激活配置")
    user_id: str = Field("default")
    dry_run: bool = Field(False, description="仅设计 DDL，不真正建表")
    for_llm: bool = Field(True, description="是否允许大模型工具发现并查询此表")
    table_name: Optional[str] = Field(None, description="可选：强制表名（须 u_ 前缀）")


class CustomRowBody(BaseModel):
    values: Dict[str, Any] = Field(default_factory=dict)
    user_id: str = Field("default")
    pk_column: str = Field("id", description="更新时的主键列名")


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


@router.get("/export/{name:path}")
async def export_table(
    name: str,
    backend: str = Query("postgresql", description="后端"),
    format: str = Query("csv", description="csv | markdown | md"),
    limit: int = Query(50000, ge=1, le=100000, description="最多导出行数"),
):
    """将表/数据项导出为 CSV 或 Markdown 文件下载。"""
    if backend not in BACKENDS:
        raise HTTPException(status_code=400, detail=f"后端必须是: {', '.join(BACKENDS)}")
    try:
        result = await storage_service.export_table(backend, name, format, limit)
        if "error" in result:
            st = 404 if "不存在" in result.get("error", "") or "未连接" in result.get("error", "") else 400
            raise HTTPException(status_code=st, detail=result["error"])
        content = result["content"]
        filename = result["filename"]
        # RFC 5987：兼容中文文件名
        disp = f"attachment; filename=\"{filename}\"; filename*=UTF-8''{quote(filename)}"
        headers = {
            "Content-Disposition": disp,
            "X-Export-Rows": str(result.get("row_count", 0)),
            "X-Export-Truncated": "1" if result.get("truncated") else "0",
        }
        return Response(
            content=content.encode("utf-8"),
            media_type=result.get("media_type") or "application/octet-stream",
            headers=headers,
        )
    except HTTPException:
        raise
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


@router.get("/custom-tables")
async def list_custom_tables(user_id: str = Query(default="default")):
    """列出用户通过大模型创建的自定义表"""
    try:
        from server.custom_table_service import custom_table_service

        custom_table_service.ensure()
        items = custom_table_service.list_meta(user_id=user_id)
        return {"ok": True, "items": items, "total": len(items)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ai-create-table")
async def ai_create_table(body: AiCreateTableBody):
    """
    用已保存的大模型配置，根据自然语言设计并创建 PostgreSQL 表。
    创建后会出现在存储层列表，且可被 sql-toolkit / 对话工具调用。
    """
    try:
        from server.custom_table_service import custom_table_service

        result = await custom_table_service.ai_create(
            body.prompt,
            profile_id=body.profile_id,
            user_id=body.user_id or "default",
            dry_run=bool(body.dry_run),
            for_llm=bool(body.for_llm),
            table_name_override=body.table_name,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/custom-tables/{name}/rows")
async def insert_custom_table_row(name: str, body: CustomRowBody):
    """向 AI 创建的自定义表插入一行，写入真实 PostgreSQL。"""
    try:
        from server.custom_table_service import custom_table_service

        return custom_table_service.insert_row(
            name,
            body.values or {},
            user_id=body.user_id or "default",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/custom-tables/{name}/rows/{pk}")
async def update_custom_table_row(name: str, pk: str, body: CustomRowBody):
    """按主键更新自定义表一行。"""
    try:
        from server.custom_table_service import custom_table_service

        return custom_table_service.update_row(
            name,
            pk,
            body.values or {},
            user_id=body.user_id or "default",
            pk_column=body.pk_column or "id",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/custom-tables/{name}")
async def delete_custom_table(
    name: str,
    user_id: str = Query(default="default"),
):
    """
    仅删除用户自己通过 AI 建表创建的表（须在 user_custom_table 登记且为 u_ 前缀）。
    系统表一律拒绝。
    """
    try:
        from server.custom_table_service import custom_table_service

        return custom_table_service.delete_table(name, user_id=user_id or "default")
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

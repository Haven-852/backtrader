"""
挑选式 / 持续获取数据路由
POST /data/selective/ensure  - 自选/刷新/向前续拉
GET  /data/selective/meta    - 查 PG 覆盖
GET  /data/continuous/plan   - 持续获取计划（骨架）
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional

from server.selective_data_service import selective_data_service

router = APIRouter(prefix="/data", tags=["data"])


class EnsureRequest(BaseModel):
    ts_code: str = Field(..., description="如 603986.SH 或 603986")
    mode: str = Field(
        "fresh",
        description="watchlist=加自选一季度; fresh=检查最新; extend=向前续日线",
    )
    before_date: Optional[str] = Field(None, description="extend 时截止日 YYYY-MM-DD")
    extend_days: int = Field(60, ge=10, le=400)


@router.post("/selective/ensure")
async def selective_ensure(body: EnsureRequest):
    """挑选式：查 PG → 不足则 Datahubco 拉取并 upsert"""
    try:
        result = await selective_data_service.ensure(
            ts_code=body.ts_code,
            mode=body.mode,
            before_date=body.before_date,
            extend_days=body.extend_days,
        )
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error") or "ensure 失败")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/selective/meta")
async def selective_meta(
    ts_code: str = Query(...),
    freq: str = Query("daily"),
):
    """查询标的在 PG 中的日/周/月覆盖"""
    try:
        meta = await __import__("asyncio").to_thread(
            selective_data_service.fetcher.get_bar_meta, ts_code, freq
        )
        return meta
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/continuous/plan")
async def continuous_plan():
    """持续获取（板块/ETF/指数）计划骨架"""
    from data_layer.collectors.datahubco import continuous_fetcher

    return continuous_fetcher.plan()

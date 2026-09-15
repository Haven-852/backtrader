"""
挑选式 / 持续获取数据路由
POST /data/selective/ensure        - 自选/刷新/向前续拉
POST /data/selective/ensure-range  - 按起止日期检查缺口并补入 PostgreSQL
GET  /data/selective/meta          - 查 PG 覆盖
GET  /data/continuous/plan         - 持续获取计划（骨架）
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from typing import List, Optional

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


class EnsureRangeRequest(BaseModel):
    ts_code: str = Field(..., description="标的代码，如 000001.SZ")
    start: str = Field(..., description="开始日期 YYYY-MM-DD 或 YYYYMMDD")
    end: str = Field(..., description="结束日期 YYYY-MM-DD 或 YYYYMMDD")
    extra_codes: Optional[List[str]] = Field(
        None,
        description="额外一并补齐的代码，如基准指数 000300.SH",
    )


@router.post("/selective/ensure-range")
async def selective_ensure_range(body: EnsureRangeRequest):
    """按起止日期对照交易日历检查 PostgreSQL，缺口则从 Datahubco 拉取并 upsert"""
    try:
        result = await selective_data_service.ensure_range(
            ts_code=body.ts_code,
            start=body.start,
            end=body.end,
            extra_codes=body.extra_codes,
        )
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error") or "ensure-range 失败")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class EnsureRecentRequest(BaseModel):
    ts_code: str = Field(..., description="标的代码")
    trading_days: int = Field(30, ge=5, le=400, description="最近交易日数量")


@router.post("/selective/ensure-recent")
async def selective_ensure_recent(body: EnsureRecentRequest):
    """搜索用：只保证最近 N 个交易日在 PostgreSQL"""
    try:
        result = await selective_data_service.ensure_recent(
            ts_code=body.ts_code,
            trading_days=body.trading_days,
        )
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error") or "ensure-recent 失败")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class WatchlistItemRequest(BaseModel):
    ts_code: str
    name: Optional[str] = None
    short_name: Optional[str] = None
    kind: str = "stock"
    industry: Optional[str] = None


@router.get("/selective/watchlist")
async def selective_watchlist_list():
    """行情看板自选股名单（持续日更以该表为准）"""
    try:
        items = await selective_data_service.watchlist_list()
        return {"ok": True, "items": items, "count": len(items)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/selective/watchlist")
async def selective_watchlist_add(body: WatchlistItemRequest):
    """加入自选并开启持续日更"""
    try:
        result = await selective_data_service.watchlist_add(
            ts_code=body.ts_code,
            name=body.name,
            short_name=body.short_name,
            kind=body.kind,
            industry=body.industry,
        )
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error") or "加入自选失败")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/selective/watchlist")
async def selective_watchlist_remove(ts_code: str = Query(...)):
    """移出自选并停止该标的持续获取"""
    try:
        result = await selective_data_service.watchlist_remove(ts_code)
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error") or "移出自选失败")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/selective/watchlist/sync")
async def selective_watchlist_sync():
    """立即对当前自选股做一轮日更（后台循环也会定时跑）"""
    try:
        return await selective_data_service.sync_watchlist_continuous()
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
    """持续获取（板块/ETF/指数）计划"""
    from data_layer.collectors.datahubco import continuous_fetcher

    return continuous_fetcher.plan()


class SectorSyncRequest(BaseModel):
    trade_date: Optional[str] = Field(None, description="YYYYMMDD，默认按会话 asof")
    with_members: bool = Field(True, description="是否同步一级行业成分股")
    member_limit: int = Field(40, ge=0, le=200)


class IndexSyncRequest(BaseModel):
    trade_date: Optional[str] = Field(None, description="YYYYMMDD，默认按会话 asof")
    lookback_days: int = Field(40, ge=5, le=400, description="库内无数据时回看天数")


@router.post("/continuous/sectors/run")
async def continuous_sectors_run(body: SectorSyncRequest = SectorSyncRequest()):
    """按交易日 asof 同步东财行业板块 → PostgreSQL"""
    import asyncio
    from data_layer.collectors.datahubco import continuous_fetcher

    try:
        result = await asyncio.to_thread(
            continuous_fetcher.sync_sectors,
            body.trade_date,
            body.with_members,
            body.member_limit,
        )
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error") or result)
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/continuous/indices/run")
async def continuous_indices_run(body: IndexSyncRequest = IndexSyncRequest()):
    """持续同步核心大盘指数日线 → index_daily（行情看板顶部条）"""
    import asyncio
    from data_layer.collectors.datahubco import continuous_fetcher

    try:
        result = await asyncio.to_thread(
            continuous_fetcher.sync_indices,
            body.trade_date,
            None,
            body.lookback_days,
        )
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error") or result)
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

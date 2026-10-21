"""
Market Data Router - 市场数据全量查询路由
直接通过 pg_manager 查询全部 30 张表

GET  /market/summary              → 全部表统计
GET  /market/kline                → K线数据 (日/周/月)
GET  /market/table/{table_name}   → 任意表数据
GET  /market/financials           → 财务报表
GET  /market/moneyflow            → 资金流向
GET  /market/margin               → 融资融券
GET  /market/consensus            → 研报一致预期
GET  /market/indices              → 指数快照
GET  /market/index-kline          → 指数K线
GET  /market/industry-stats       → 行业统计
GET  /market/stock-search         → 股票搜索
GET  /market/futures/catalog      → 期货主连分类
GET  /market/futures/board        → 期货主连看板（日线快照）
GET  /market/futures/kline        → 期货日/周/月K线
"""

from fastapi import APIRouter, HTTPException, Query
from typing import Optional

from server.market_data_service import market_data_service

router = APIRouter(prefix="/market", tags=["market-data"])


# ─── 综合摘要 ──────────────────────────────────────────

@router.get("/summary")
async def get_summary():
    """获取全部 30 张表的行数统计与分类"""
    try:
        result = await market_data_service.get_summary()
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取摘要失败: {str(e)}")


# ─── K线数据 (核心) ────────────────────────────────────

@router.get("/kline")
async def get_kline(
    ts_code: str = Query(..., min_length=1, description="股票代码，如 000001.SZ"),
    freq: str = Query(default="daily", description="K线频率: daily | weekly | monthly"),
    start: Optional[str] = Query(None, description="开始日期 YYYY-MM-DD"),
    end: Optional[str] = Query(None, description="结束日期 YYYY-MM-DD"),
    limit: int = Query(default=500, ge=1, le=5000, description="最大条数"),
):
    """
    从 PostgreSQL stock_daily / stock_weekly / stock_monthly 查询K线数据

    示例:
      /market/kline?ts_code=000001.SZ&freq=daily&limit=200
      /market/kline?ts_code=600519.SH&freq=weekly&start=2024-01-01&end=2024-12-31
    """
    try:
        result = await market_data_service.get_kline(
            ts_code=ts_code,
            freq=freq,
            start=start,
            end=end,
            limit=limit,
        )
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取K线失败: {str(e)}")


# ─── 任意表查询 ────────────────────────────────────────

@router.get("/table/{table_name:path}")
async def get_table_data(
    table_name: str,
    limit: int = Query(default=50, ge=1, le=1000, description="返回行数"),
    offset: int = Query(default=0, ge=0, description="偏移量"),
    ts_code: Optional[str] = Query(None, description="股票代码过滤"),
):
    """获取任意表的预览数据"""
    try:
        result = await market_data_service.get_table_data(
            table_name=table_name,
            limit=limit,
            offset=offset,
            ts_code=ts_code,
        )
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"查询表数据失败: {str(e)}")


# ─── 财务报表 ──────────────────────────────────────────

@router.get("/financials")
async def get_financials(
    ts_code: str = Query(..., min_length=1, description="股票代码"),
):
    """获取某只股票的全部财务报表（利润表、资产负债表、现金流量表、财务指标、业绩预告、分红）"""
    try:
        result = await market_data_service.get_financials(ts_code=ts_code)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取财务数据失败: {str(e)}")


# ─── 资金流向 ──────────────────────────────────────────

@router.get("/moneyflow")
async def get_moneyflow(
    ts_code: str = Query(..., min_length=1, description="股票代码"),
    limit: int = Query(default=100, ge=1, le=1000),
):
    """获取某只股票的资金流向数据"""
    try:
        result = await market_data_service.get_moneyflow(ts_code=ts_code, limit=limit)
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取资金流向失败: {str(e)}")


# ─── 融资融券 ──────────────────────────────────────────

@router.get("/margin")
async def get_margin(
    limit: int = Query(default=30, ge=1, le=500),
):
    """获取融资融券汇总数据"""
    try:
        result = await market_data_service.get_margin_summary(limit=limit)
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取两融数据失败: {str(e)}")


# ─── 研报一致预期 ──────────────────────────────────────

@router.get("/consensus")
async def get_consensus(
    ts_code: str = Query(..., min_length=1, description="股票代码"),
    limit: int = Query(default=50, ge=1, le=500),
):
    """获取某只股票的券商一致预期"""
    try:
        result = await market_data_service.get_consensus(ts_code=ts_code, limit=limit)
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取一致预期失败: {str(e)}")


# ─── 指数快照 ──────────────────────────────────────────

@router.get("/indices")
async def get_indices():
    """获取核心指数最新快照"""
    try:
        result = await market_data_service.get_indices_snapshot()
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取指数快照失败: {str(e)}")


# ─── 指数K线 ───────────────────────────────────────────

@router.get("/index-kline")
async def get_index_kline(
    ts_code: str = Query(default="000001.SH", description="指数代码"),
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    limit: int = Query(default=500, ge=1, le=5000),
):
    """获取指数日线K线"""
    try:
        result = await market_data_service.get_index_kline(
            ts_code=ts_code,
            start=start,
            end=end,
            limit=limit,
        )
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取指数K线失败: {str(e)}")


# ─── 行业统计 ──────────────────────────────────────────

@router.get("/industry-stats")
async def get_industry_stats():
    """获取行业统计（基于 stock_daily_basic + ref_stock_basic）"""
    try:
        result = await market_data_service.get_industry_stats()
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取行业统计失败: {str(e)}")


# ─── 股票搜索 ──────────────────────────────────────────

@router.get("/stock-search")
async def search_stocks(
    q: str = Query(..., min_length=1, description="搜索关键词"),
    limit: int = Query(default=20, ge=1, le=100),
):
    """从 ref_stock_basic 搜索股票"""
    try:
        stocks = await market_data_service.search_stocks(q, limit=limit)
        return {"keyword": q, "results": stocks, "count": len(stocks)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"搜索股票失败: {str(e)}")


# ─── 期货行情中心 ──────────────────────────────────────

@router.get("/futures/catalog")
async def futures_catalog():
    """期货主连分类目录"""
    try:
        from server.futures_market_service import futures_market_service
        import asyncio

        return await asyncio.to_thread(futures_market_service.catalog)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"期货目录失败: {e}")


@router.get("/futures/board")
async def futures_board(
    category: Optional[str] = Query(None, description="分类名，空=全部"),
    refresh: bool = Query(False, description="强制刷新缓存"),
):
    """期货主连看板快照（日线），按分类布局"""
    try:
        from server.futures_market_service import futures_market_service
        import asyncio

        return await asyncio.to_thread(
            futures_market_service.get_board,
            category=category,
            force_refresh=refresh,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"期货看板失败: {e}")


@router.get("/futures/kline")
async def futures_kline(
    ts_code: str = Query(..., min_length=1, description="合约代码，如 AU.SHF / SC.INE"),
    freq: str = Query(default="daily", description="daily | weekly | monthly"),
    start: Optional[str] = Query(None, description="开始日期 YYYY-MM-DD"),
    end: Optional[str] = Query(None, description="结束日期 YYYY-MM-DD"),
    limit: int = Query(default=300, ge=1, le=2000),
):
    """期货日/周/月 K 线（周月由日线聚合）"""
    try:
        from server.futures_market_service import futures_market_service
        import asyncio

        result = await asyncio.to_thread(
            futures_market_service.get_kline,
            ts_code=ts_code,
            freq=freq,
            start=start,
            end=end,
            limit=limit,
        )
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail=result.get("error") or "无数据")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"期货K线失败: {e}")


@router.get("/futures/coverage")
async def futures_coverage(
    category: Optional[str] = Query(None),
    ts_codes: Optional[str] = Query(None, description="逗号分隔合约"),
    asof: Optional[str] = Query(None, description="目标交易日 YYYY-MM-DD"),
):
    """检查行情中心主连是否缺失/日期落后于上一交易日"""
    try:
        from server.futures_market_service import futures_market_service
        import asyncio

        codes = [c.strip() for c in (ts_codes or "").split(",") if c.strip()] or None
        return await asyncio.to_thread(
            futures_market_service.check_coverage,
            ts_codes=codes,
            category=category,
            asof=asof,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"覆盖检查失败: {e}")


@router.post("/futures/sync")
async def futures_sync(
    category: Optional[str] = Query(None),
    ts_codes: Optional[str] = Query(None, description="逗号分隔合约"),
    asof: Optional[str] = Query(None),
    force: bool = Query(False),
    lookback_days: int = Query(5, ge=1, le=60),
):
    """补齐上一交易日期货日线并写入 fut_daily"""
    try:
        from server.futures_market_service import futures_market_service
        import asyncio

        codes = [c.strip() for c in (ts_codes or "").split(",") if c.strip()] or None
        return await asyncio.to_thread(
            futures_market_service.sync_quotes,
            ts_codes=codes,
            category=category,
            asof=asof,
            lookback_days=lookback_days,
            force=force,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"期货补数失败: {e}")


@router.get("/futures/board-live")
async def futures_board_live(
    category: Optional[str] = Query(None),
    force: bool = Query(False, description="强制重拉远端"),
):
    """自动对齐上一交易日后返回看板（供行情中心定时刷新）"""
    try:
        from server.futures_market_service import futures_market_service
        import asyncio

        return await asyncio.to_thread(
            futures_market_service.ensure_prev_day_board,
            category=category,
            force=force,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"期货看板刷新失败: {e}")

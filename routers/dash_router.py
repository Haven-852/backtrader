"""
Dash Router - 股票行情看板路由层
GET  /dash/search?q=        - 搜索股票
GET  /dash/stock/{code}     - 获取股票信息
GET  /dash/kline            - 获取K线数据
GET  /dash/timeframes       - 获取可用周期
GET  /dash/indicators       - 获取可用指标
GET  /dash/indices          - 获取大盘指数快照
GET  /dash/etfs             - 获取热门ETF列表
"""

from fastapi import APIRouter, HTTPException, Query
from typing import Optional, List, Dict, Any

from server.dash_service import dash_service
from server.intraday_service import intraday_service

router = APIRouter(prefix="/dash", tags=["dash"])


# ─── 路由 ─────────────────────────────────────────────

@router.get("/timeframes")
async def get_timeframes():
    """获取所有可用K线周期"""
    try:
        timeframes = dash_service.get_available_timeframes()
        return {"timeframes": timeframes, "total": len(timeframes)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取周期列表失败: {str(e)}")


@router.get("/indicators")
async def get_indicators():
    """获取所有可用技术指标"""
    try:
        indicators = dash_service.get_available_indicators()
        return {"indicators": indicators, "total": len(indicators)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取指标列表失败: {str(e)}")


@router.get("/search")
async def search_stocks(
    q: str = Query(..., min_length=1, description="搜索关键词（股票代码或名称）"),
    limit: int = Query(default=20, ge=1, le=100, description="返回数量上限"),
):
    """搜索股票"""
    try:
        stocks = await dash_service.search_stocks(q, limit=limit)
        return {
            "keyword": q,
            "results": stocks,
            "count": len(stocks),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"搜索股票失败: {str(e)}")


@router.get("/stock/{ts_code:path}")
async def get_stock_info(ts_code: str):
    """获取股票基本信息"""
    try:
        info = await dash_service.get_stock_info(ts_code)
        if not info:
            raise HTTPException(status_code=404, detail=f"股票 '{ts_code}' 不存在")
        return info
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取股票信息失败: {str(e)}")


@router.get("/intraday")
async def get_intraday(
    ts_code: str = Query(..., min_length=1, description="股票代码，如 002028.SZ 或 002028"),
    trade_date: Optional[str] = Query(None, description="交易日 YYYY-MM-DD；默认当日/最近交易日"),
    history: bool = Query(False, description="True=按历史回看，只查 Timescale 不写 Redis"),
):
    """
    当日分时：盘中 Redis 出图，同时写入 Timescale stock_intraday_mins，
    并用分时聚合 upsert stock_daily。
    指定历史日或 history=true：只查/补 Timescale，不回填 Redis。
    """
    try:
        result = await intraday_service.get(
            ts_code=ts_code,
            trade_date=trade_date,
            history=history,
        )
        if result.get("ok") is False and not result.get("bars"):
            raise HTTPException(status_code=400, detail=result.get("error") or "无分时数据")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取分时失败: {str(e)}")


@router.get("/kline")
async def get_kline(
    ts_code: str = Query(..., min_length=1, description="股票代码（如 000001.SZ）"),
    timeframe: str = Query(default="day", description="周期: 1m/5m/15m/30m/60m/day/week/month"),
    start: Optional[str] = Query(None, description="开始日期 (YYYY-MM-DD)"),
    end: Optional[str] = Query(None, description="结束日期 (YYYY-MM-DD)"),
    limit: int = Query(default=500, ge=1, le=5000, description="最大返回条数"),
    indicators: Optional[str] = Query(None, description="技术指标（逗号分隔）: ma,ema,macd,rsi,boll,kdj,volume"),
    auto_fill: bool = Query(True, description="个股缺数时是否自动补最新/向前续拉"),
):
    """
    获取K线数据

    示例:
      /dash/kline?ts_code=000001.SZ&timeframe=day&indicators=ma,macd,rsi
      /dash/kline?ts_code=600519.SH&timeframe=60m&limit=200&indicators=boll,kdj
    """
    try:
        # 解析指标
        ind_list = None
        if indicators:
            ind_list = [i.strip() for i in indicators.split(",") if i.strip()]

        result = await dash_service.get_kline_data(
            ts_code=ts_code,
            timeframe=timeframe,
            start=start,
            end=end,
            limit=limit,
            indicators=ind_list,
            auto_fill=auto_fill,
        )

        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])

        return result

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取K线数据失败: {str(e)}")


@router.get("/analysis/{ts_code:path}")
async def get_quick_analysis(
    ts_code: str,
    timeframe: str = Query(default="day", description="周期"),
):
    """快速技术分析（默认计算所有常用指标）"""
    try:
        result = await dash_service.get_kline_data(
            ts_code=ts_code,
            timeframe=timeframe,
            limit=250,
            indicators=["ma", "ema", "macd", "rsi", "boll", "kdj", "volume"],
        )
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"技术分析失败: {str(e)}")


@router.get("/indices")
async def get_indices(
    auto_sync: bool = Query(True, description="缺/旧数据时自动从 Datahubco 持续补齐 index_daily"),
):
    """获取大盘指数快照（同花顺风格顶部条；持续获取写入 index_daily）"""
    try:
        indices = await dash_service.get_index_snapshots(auto_sync=auto_sync)
        return {"indices": indices, "total": len(indices)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取指数数据失败: {str(e)}")


@router.get("/index-kline")
async def get_index_kline(
    ts_code: str = Query(default="000001.SH", description="指数代码"),
    timeframe: str = Query(default="day", description="周期"),
    limit: int = Query(default=120, ge=1, le=5000),
    indicators: Optional[str] = Query(None, description="技术指标"),
):
    """获取指数K线数据"""
    try:
        ind_list = None
        if indicators:
            ind_list = [i.strip() for i in indicators.split(",") if i.strip()]
        result = await dash_service.get_kline_data(
            ts_code=ts_code,
            timeframe=timeframe,
            limit=limit,
            indicators=ind_list,
        )
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取指数K线失败: {str(e)}")


@router.get("/etfs")
async def get_etfs():
    """获取热门ETF列表及最新行情（同花顺风格）"""
    try:
        etfs = await dash_service.get_etf_list()
        return {"etfs": etfs, "total": len(etfs)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取ETF列表失败: {str(e)}")


@router.get("/etf-board")
async def get_etf_board(
    limit: int = Query(default=100, ge=1, le=200, description="每个市场展示的ETF数量（气泡图）"),
):
    """ETF 一级/二级市场看板（上交所/深交所，数据来自 etf_daily）"""
    try:
        return await dash_service.get_etf_board(limit_per_market=limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取ETF看板失败: {str(e)}")


@router.get("/sector-board")
async def get_sector_board(
    level: str = Query(default="东财一级行业", description="东财一级/二级/三级行业"),
    trade_date: Optional[str] = Query(None, description="YYYY-MM-DD 或 YYYYMMDD"),
    auto_sync: bool = Query(True, description="缺数据时自动从 Datahubco 同步"),
    parent_code: Optional[str] = Query(None, description="父板块代码；传入则返回其子行业气泡数据"),
):
    """东财行业板块气泡图数据（index+daily+moneyflow 合并；支持按父板块下钻）"""
    try:
        from server.sector_service import sector_service

        return await sector_service.get_board_async(
            level=level,
            trade_date=trade_date,
            auto_sync=auto_sync,
            parent_code=parent_code,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取行业板块看板失败: {str(e)}")


@router.get("/sector/{ts_code}/members")
async def get_sector_members(
    ts_code: str,
    trade_date: Optional[str] = Query(None),
    auto_fetch: bool = Query(True),
):
    """板块成分股 + 当日行情"""
    try:
        from server.sector_service import sector_service

        return await sector_service.get_members_async(
            ts_code=ts_code, trade_date=trade_date, auto_fetch=auto_fetch
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取板块成分失败: {str(e)}")


@router.get("/sector/{ts_code}/children")
async def get_sector_children(
    ts_code: str,
    trade_date: Optional[str] = Query(None),
    auto_sync: bool = Query(True),
):
    """某板块下的子行业气泡数据（L1→L2 或 L2→L3）"""
    try:
        from server.sector_service import sector_service

        return await sector_service.get_board_async(
            trade_date=trade_date,
            auto_sync=auto_sync,
            parent_code=ts_code,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取子板块失败: {str(e)}")


@router.post("/sector-hierarchy/rebuild")
async def rebuild_sector_hierarchy(
    trade_date: Optional[str] = Query(None),
    sync_members: bool = Query(True, description="重建前补齐缺失成分股"),
):
    """按成分股包含关系重建一级→二级→三级层级表"""
    try:
        from server.sector_service import sector_service

        return await sector_service.rebuild_hierarchy_async(
            trade_date=trade_date, sync_members=sync_members
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"重建行业层级失败: {str(e)}")


@router.get("/etf-kline")
async def get_etf_kline(
    ts_code: str = Query(..., description="ETF代码（如 510050.SH）"),
    timeframe: str = Query(default="day", description="周期"),
    limit: int = Query(default=500, ge=1, le=5000),
    indicators: Optional[str] = Query(None, description="技术指标"),
):
    """获取ETF K线数据"""
    try:
        ind_list = None
        if indicators:
            ind_list = [i.strip() for i in indicators.split(",") if i.strip()]
        result = await dash_service.get_kline_data(
            ts_code=ts_code,
            timeframe=timeframe,
            limit=limit,
            indicators=ind_list,
        )
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取ETF K线失败: {str(e)}")

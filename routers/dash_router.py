"""
Dash Router - 股票行情看板路由层
GET  /dash/search?q=        - 搜索股票
GET  /dash/stock/{code}     - 获取股票信息
GET  /dash/kline            - 获取K线数据
GET  /dash/timeframes       - 获取可用周期
GET  /dash/indicators       - 获取可用指标
"""

from fastapi import APIRouter, HTTPException, Query
from typing import Optional, List, Dict, Any

from server.dash_service import dash_service

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


@router.get("/kline")
async def get_kline(
    ts_code: str = Query(..., min_length=1, description="股票代码（如 000001.SZ）"),
    timeframe: str = Query(default="day", description="周期: 1m/5m/15m/30m/60m/day/week/month"),
    start: Optional[str] = Query(None, description="开始日期 (YYYY-MM-DD)"),
    end: Optional[str] = Query(None, description="结束日期 (YYYY-MM-DD)"),
    limit: int = Query(default=500, ge=1, le=5000, description="最大返回条数"),
    indicators: Optional[str] = Query(None, description="技术指标（逗号分隔）: ma,ema,macd,rsi,boll,kdj,volume"),
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
async def get_indices():
    """获取大盘指数实时快照（同花顺风格）"""
    try:
        indices = await dash_service.get_index_snapshots()
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

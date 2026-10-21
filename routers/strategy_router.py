"""
Strategy Router - 策略分析
GET  /strategy/catalog          - 策略工具箱目录
GET  /strategy/catalog/{id}     - 单策略详情
GET  /strategy/options          - 回测开关（前视保护 / Walk-Forward）
GET  /strategy/perceive         - PostgreSQL 感知
GET  /strategy/meta             - 数据覆盖
POST /strategy/hypothesize      - 假设生成（后端）
POST /strategy/backtest         - 回测（后端，真实日线）
POST /strategy/reflect          - 反思（后端）
POST /strategy/screener/setups - A 观察池 / B 升势回踩选股
POST /strategy/pipeline/cycle   - 观察池→自选50→起飞推荐→衰竭出池
GET  /strategy/pipeline/observe - 观察池列表
GET  /strategy/pipeline/recommend - 推荐池列表
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Any, Dict, Optional
import asyncio

from server.strategy_service import strategy_service
from server.strategy_cycle_service import strategy_cycle_service

router = APIRouter(prefix="/strategy", tags=["strategy"])


# ---------- request bodies ----------
class HypothesizeRequest(BaseModel):
    strategy_id: str
    regime: str = "unknown"
    params: Optional[Dict[str, Any]] = None


class BacktestRequest(BaseModel):
    strategy_id: str
    ts_code: str
    params: Optional[Dict[str, Any]] = None
    start: Optional[str] = None
    end: Optional[str] = None
    cash: float = Field(1_000_000, gt=0)
    lookback_guard: bool = True
    walk_forward: bool = False
    benchmark: str = "000300.SH"
    limit: int = Field(800, ge=50, le=5000)


class ReflectRequest(BaseModel):
    backtest_result: Dict[str, Any]
    regime: str = ""
    max_drawdown_pct: float = 20.0
    min_sharpe: float = 0.5
    mode: str = "A"
    auto_save_memory: bool = True


@router.get("/catalog")
async def strategy_catalog(
    category: Optional[str] = Query(None, description="按类别过滤，如 动量 / 均值回归"),
):
    """策略工具箱目录：名称、逻辑、适合市况、参数定义"""
    try:
        return strategy_service.get_catalog(category=category)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取策略目录失败: {e}")


@router.get("/catalog/{strategy_id}")
async def strategy_detail(strategy_id: str):
    """单个策略详情"""
    item = strategy_service.get_strategy(strategy_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"未找到策略: {strategy_id}")
    return {"ok": True, "strategy": item}


@router.get("/options")
async def strategy_options():
    """回测开关定义：前视保护 lookback_guard、Walk-Forward（前端顶栏勾选框）"""
    try:
        return strategy_cycle_service.get_options()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取回测选项失败: {e}")


@router.get("/perceive")
async def perceive(
    ts_code: str = Query(..., description="股票/指数代码，如 000001.SZ"),
    start: Optional[str] = Query(None, description="开始日期 YYYY-MM-DD"),
    end: Optional[str] = Query(None, description="结束日期 YYYY-MM-DD"),
    limit: int = Query(500, ge=50, le=5000),
    vol_window: int = Query(20, ge=5, le=120),
    features: Optional[str] = Query(
        "ma,rsi,vol_pctile",
        description="逗号分隔特征: ma,ema,rsi,macd,boll,vol_pctile",
    ),
):
    """感知模块：真实 PostgreSQL 行情 + 特征 + regime"""
    try:
        feat_list = [f.strip() for f in (features or "").split(",") if f.strip()]
        result = await strategy_service.perceive(
            ts_code=ts_code.strip().upper(),
            start=start,
            end=end,
            limit=limit,
            vol_window=vol_window,
            features=feat_list,
        )
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail=result.get("error") or "感知失败")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"感知失败: {e}")


@router.get("/meta")
async def data_meta(
    ts_code: str = Query(..., description="股票/指数代码"),
):
    """查询标的在 PostgreSQL 中的数据条数与日期范围"""
    try:
        result = await strategy_service.get_data_meta(ts_code.strip().upper())
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail=result.get("error") or "查询失败")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/hypothesize")
async def hypothesize(body: HypothesizeRequest):
    """根据市况与策略生成假设（后端）"""
    try:
        result = strategy_cycle_service.hypothesize(
            strategy_id=body.strategy_id,
            regime=body.regime,
            params=body.params,
        )
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error") or "假设失败")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"假设失败: {e}")


@router.post("/backtest")
async def backtest(body: BacktestRequest):
    """用 PostgreSQL 真实日线做简化回测（后端）"""
    try:
        result = await strategy_cycle_service.backtest(
            strategy_id=body.strategy_id,
            ts_code=body.ts_code.strip().upper(),
            params=body.params,
            start=body.start,
            end=body.end,
            cash=body.cash,
            lookback_guard=body.lookback_guard,
            walk_forward=body.walk_forward,
            benchmark=body.benchmark.strip().upper(),
            limit=body.limit,
        )
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error") or "回测失败")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"回测失败: {e}")


@router.post("/reflect")
async def reflect(body: ReflectRequest):
    """根据回测结果与 harness 阈值生成反思（后端）"""
    try:
        result = strategy_cycle_service.reflect(
            backtest_result=body.backtest_result,
            regime=body.regime,
            max_drawdown_pct=body.max_drawdown_pct,
            min_sharpe=body.min_sharpe,
            mode=body.mode,
            auto_save_memory=body.auto_save_memory,
        )
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error") or "反思失败")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"反思失败: {e}")


class SetupScreenerRequest(BaseModel):
    asof: Optional[str] = Field(
        None,
        description="交易日 YYYY-MM-DD；默认=当天会话已收盘日（收盘后即今日，开盘前/非交易日为上一交易日）",
    )
    include_st: bool = False
    include_main: bool = True
    include_star: bool = True
    include_chinext: bool = True
    include_bse: bool = False
    include_cdr: bool = False
    include_a: bool = Field(
        True,
        description="A 类观察池：大跌后缩量筑底、靠近20日低点、MA5走平（只观察，等起飞）",
    )
    include_b: bool = Field(True, description="B 类：升势回踩 MA5（反转候选）")
    include_c: bool = Field(
        True,
        description="C 类：趋势回踩结构止损（MA10/MA20 升势缩量回踩，次日开盘买）",
    )
    limit: int = Field(60, ge=10, le=1000)
    fill: bool = Field(True, description="窗口日线不足时自动从 Datahubco 按交易日拉全日线写入 stock_daily")


@router.get("/screener/asof")
async def screener_asof():
    """选股默认交易日：跟随当天会话（收盘后=今日，否则=上一已收盘日）"""
    try:
        from data_layer.collectors.datahubco.selective import selective_fetcher

        selective_fetcher.ensure_trade_cal()
        ctx = selective_fetcher.session_context()
        return {
            "ok": True,
            "asof": ctx.get("asof"),
            "today": ctx.get("today"),
            "reason": ctx.get("reason"),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/screener/setups")
async def screener_setups(body: SetupScreenerRequest):
    """
    用日线判断 asof（默认当天已收盘日）是否满足：
    A 观察池（缩量筑底转弯）、B 升势回踩 MA5、C 趋势回踩结构止损。
    放量下跌抛弃。窗口覆盖不足时默认从 Datahubco 按交易日拉取全日线。
    """
    try:
        from server.screener_service import screener_service

        result = await asyncio.to_thread(
            screener_service.screen_setups,
            body.asof,
            body.include_st,
            body.include_main,
            body.include_star,
            body.include_chinext,
            body.include_bse,
            body.include_cdr,
            body.include_a,
            body.include_b,
            body.limit,
            body.fill,
            body.include_c,
        )
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error") or "选股失败")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"选股失败: {e}")


class PipelineCycleRequest(BaseModel):
    asof: Optional[str] = Field(None, description="交易日 YYYY-MM-DD；默认当天已收盘日")
    min_score: float = Field(70.0, ge=0, le=100, description="观察池最低分数")
    max_n: int = Field(50, ge=5, le=100, description="观察池/自选上限")
    fill: bool = Field(True, description="日线缺口自动补齐")
    require_sector: bool = Field(False, description="起飞必须板块共振")
    require_top_list: bool = Field(False, description="起飞必须上龙虎榜")
    require_flow_signal: bool = Field(
        True, description="起飞需板块共振或龙虎榜/游资异动至少一项"
    )


@router.post("/pipeline/cycle")
async def pipeline_cycle(body: PipelineCycleRequest):
    """
    全流程：策略A≥min_score → 观察池/自选(≤max_n) → 起飞入推荐池 → 动能衰竭出池。
    """
    try:
        from server.recommend_pool_service import recommend_pool_service

        result = await asyncio.to_thread(
            recommend_pool_service.run_cycle,
            body.asof,
            min_score=body.min_score,
            max_n=body.max_n,
            fill=body.fill,
            require_sector=body.require_sector,
            require_top_list=body.require_top_list,
            require_flow_signal=body.require_flow_signal,
        )
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error") or "流水线失败")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"流水线失败: {e}")


@router.post("/pipeline/observe")
async def pipeline_observe_sync(body: PipelineCycleRequest):
    """仅同步观察池：A≥min_score 写入观察池并同步看板自选(source=observe_a)。"""
    try:
        from server.recommend_pool_service import recommend_pool_service

        result = await asyncio.to_thread(
            recommend_pool_service.sync_observe_from_screener,
            body.asof,
            min_score=body.min_score,
            max_n=body.max_n,
            fill=body.fill,
            sync_dash=True,
        )
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error") or "观察池同步失败")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"观察池同步失败: {e}")


@router.post("/pipeline/takeoff")
async def pipeline_takeoff(body: PipelineCycleRequest):
    """对观察池扫描起飞，写入推荐池。"""
    try:
        from server.recommend_pool_service import recommend_pool_service

        result = await asyncio.to_thread(
            recommend_pool_service.scan_takeoff,
            body.asof,
            require_sector=body.require_sector,
            require_top_list=body.require_top_list,
            require_flow_signal=body.require_flow_signal,
        )
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error") or "起飞扫描失败")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"起飞扫描失败: {e}")


@router.post("/pipeline/exhaust")
async def pipeline_exhaust(asof: Optional[str] = Query(None, description="交易日 YYYY-MM-DD")):
    """推荐池动能衰竭扫描，命中则 status=exhausted 移出。"""
    try:
        from server.recommend_pool_service import recommend_pool_service

        result = await asyncio.to_thread(recommend_pool_service.scan_exhaustion, asof)
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error") or "衰竭扫描失败")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"衰竭扫描失败: {e}")


@router.get("/pipeline/observe")
async def pipeline_observe_list(active_only: bool = Query(True)):
    """观察池列表。"""
    try:
        from server.recommend_pool_service import recommend_pool_service

        return await asyncio.to_thread(recommend_pool_service.list_observe_pool, active_only)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/pipeline/recommend")
async def pipeline_recommend_list(
    status: str = Query("active", description="active / exhausted / all"),
):
    """推荐池列表（行情看板「推荐池」数据源）。"""
    try:
        from server.recommend_pool_service import recommend_pool_service

        return await asyncio.to_thread(recommend_pool_service.list_recommend_pool, status)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class IntradayTrainBody(BaseModel):
    ts_codes: list[str] = Field(default_factory=list)
    experiment_id: str = "INTRADAY_TURNING_POINT"


class IntradayPredictBody(BaseModel):
    model_version: str
    stock_code: str
    as_of_time: Optional[str] = None
    holding_buy_date: Optional[str] = None


class IntradayBacktestBody(BaseModel):
    experiment_id: str = "INTRADAY_TURNING_POINT"
    model_version: str
    start_date: str
    end_date: str


@router.get("/intraday/coverage")
async def intraday_coverage():
    """哪些股票、哪些交易日有够用的分钟线。"""
    try:
        from server.intraday_turning_service import intraday_turning_service
        return await asyncio.to_thread(intraday_turning_service.coverage)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/intraday/latest")
async def intraday_latest():
    try:
        from server.intraday_turning_service import intraday_turning_service
        return await asyncio.to_thread(intraday_turning_service.latest)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/intraday/train")
async def intraday_train(body: IntradayTrainBody):
    """用库内分钟线训练 LightGBM。测试集只评估一次。"""
    try:
        from server.intraday_turning_service import intraday_turning_service
        result = await asyncio.to_thread(
            intraday_turning_service.train,
            body.experiment_id,
            {"ts_codes": body.ts_codes} if body.ts_codes else {},
        )
        if not result.get("ok") and result.get("status") != "NEED_MORE_DATA":
            raise HTTPException(status_code=400, detail=result.get("error") or "训练失败")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/intraday/predict")
async def intraday_predict(body: IntradayPredictBody):
    try:
        from server.intraday_turning_service import intraday_turning_service
        result = await asyncio.to_thread(
            intraday_turning_service.predict,
            body.model_version, body.as_of_time, body.stock_code, body.holding_buy_date,
        )
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error") or "预测失败")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/intraday/evaluate")
async def intraday_evaluate(
    model_version: str = Query(...),
    dataset_type: str = Query("test"),
    experiment_id: str = Query("INTRADAY_TURNING_POINT"),
):
    try:
        from server.intraday_turning_service import intraday_turning_service
        result = await asyncio.to_thread(
            intraday_turning_service.evaluate, experiment_id, model_version, dataset_type,
        )
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail=result.get("error") or "没有记录")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/intraday/backtest")
async def intraday_backtest(body: IntradayBacktestBody):
    try:
        from server.intraday_turning_service import intraday_turning_service
        result = await asyncio.to_thread(
            intraday_turning_service.backtest,
            body.experiment_id, body.model_version, body.start_date, body.end_date, {},
        )
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error") or "回测失败")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

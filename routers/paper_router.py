"""
Paper Router - 纸面账户 / 模拟交易
POST /paper/accounts                      - 开户
DELETE /paper/accounts/{id}               - 删除账户（hard=true 物理删除 / false 关闭）
GET  /paper/accounts                      - 账户列表
GET  /paper/accounts/{id}                 - 账户详情（含持仓盯市）
GET  /paper/accounts/{id}/positions       - 持仓
POST /paper/accounts/{id}/orders          - 下单
GET  /paper/accounts/{id}/orders          - 委托列表
POST /paper/accounts/{id}/orders/{oid}/cancel - 取消 pending
GET  /paper/accounts/{id}/fills           - 成交列表
POST /paper/accounts/{id}/settle          - 结算该账户 pending（次日开盘）
POST /paper/settle                        - 结算全部 pending
POST /paper/accounts/{id}/mark            - 盯市 + 净值快照
GET  /paper/accounts/{id}/equity          - 净值曲线
GET  /paper/accounts/{id}/stats           - 交易统计
GET  /paper/accounts/{id}/decisions       - 决策审计日志
POST /paper/t1/roll                       - T+1 日切（frozen→sellable）
POST /paper/accounts/{id}/t1/roll         - 单账户 T+1 日切
POST /paper/watchlist/build               - 从选股/自选构建盯盘名单
GET  /paper/watchlist                     - 查看盯盘名单
POST /paper/watchlist/manual              - 手动加入盯盘
POST /paper/scan/run                      - 对 watchlist 跑一次规则巡检
GET  /paper/scan/events                   - 巡检事件列表
POST /paper/cycle/evening-screen          - 晚间 A/B TopN 选股入 watchlist
POST /paper/cycle/open-decide             - 开市做T+LLM 买卖
POST /paper/cycle/risk-check              - 回撤清仓
POST /paper/cycle/daily-t0                - 持仓日度做T/续涨
POST /paper/cycle/weekly-review           - 周复盘调 A/B
POST /paper/cycle/run-trading-day         - 一键日课
GET  /paper/cycle/config|runs|actions     - 配置与流水
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from server.paper_broker_service import paper_broker_service

router = APIRouter(prefix="/paper", tags=["paper"])


class CreateAccountRequest(BaseModel):
    name: str = Field("默认纸面账户", min_length=1, max_length=64)
    initial_cash: float = Field(1_000_000, gt=0)
    lookback_guard: bool = True


class PlaceOrderRequest(BaseModel):
    ts_code: str = Field(..., description="标的代码，如 000001.SZ")
    side: str = Field(..., description="buy / sell")
    quantity: float = Field(..., gt=0, description="股数；买入须为 100 整数倍")
    lookback_guard: Optional[bool] = Field(
        None,
        description="True=次日开盘成交；False=当日收盘成交；默认沿用账户设置",
    )
    signal_date: Optional[str] = Field(
        None, description="信号日 YYYY-MM-DD；默认用该标的最新交易日"
    )
    reason: Optional[str] = Field(None, description="下单理由（审计）")
    order_type: str = "market"


class SettleRequest(BaseModel):
    asof: Optional[str] = Field(None, description="仅结算 exec_date <= asof 的挂单")


class MarkRequest(BaseModel):
    asof: Optional[str] = Field(None, description="盯市截至日 YYYY-MM-DD")
    write_equity: bool = True


def _ok_or_400(result: Dict[str, Any]) -> Dict[str, Any]:
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "请求失败")
    return result


@router.post("/accounts")
async def create_account(body: CreateAccountRequest):
    """开立纸面账户"""
    try:
        return _ok_or_400(
            paper_broker_service.create_account(
                name=body.name,
                initial_cash=body.initial_cash,
                lookback_guard=body.lookback_guard,
            )
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"开户失败: {e}")


@router.delete("/accounts/{account_id}")
async def delete_account(
    account_id: int,
    hard: bool = Query(
        True,
        description="true=物理删除（级联清理关联数据）；false=仅关闭 status=closed",
    ),
):
    """删除 / 关闭纸面账户"""
    try:
        result = paper_broker_service.delete_account(account_id, hard=hard)
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail=result.get("error") or "删除失败")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除账户失败: {e}")


@router.get("/accounts")
async def list_accounts(
    status: Optional[str] = Query("active", description="active / closed；空=全部"),
):
    """账户列表"""
    try:
        st = status if status else None
        return paper_broker_service.list_accounts(status=st)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"查询账户失败: {e}")


@router.get("/accounts/{account_id}")
async def get_account(
    account_id: int,
    mark: bool = Query(True, description="是否用最新收盘价盯市"),
):
    """账户详情 + 持仓"""
    try:
        result = paper_broker_service.get_account(account_id, mark=mark)
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail=result.get("error"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"查询账户失败: {e}")


@router.get("/accounts/{account_id}/positions")
async def list_positions(account_id: int):
    """持仓列表"""
    try:
        result = paper_broker_service.list_positions(account_id)
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail=result.get("error"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"查询持仓失败: {e}")


@router.post("/accounts/{account_id}/orders")
async def place_order(account_id: int, body: PlaceOrderRequest):
    """
    下单。
    lookback_guard=True：挂 pending，待次日开盘结算；
    lookback_guard=False：按信号日收盘价立即成交。
    """
    try:
        return _ok_or_400(
            paper_broker_service.place_order(
                account_id=account_id,
                ts_code=body.ts_code,
                side=body.side,
                quantity=body.quantity,
                lookback_guard=body.lookback_guard,
                signal_date=body.signal_date,
                reason=body.reason,
                order_type=body.order_type,
            )
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"下单失败: {e}")


@router.get("/accounts/{account_id}/orders")
async def list_orders(
    account_id: int,
    status: Optional[str] = Query(None, description="pending / filled / cancelled / rejected"),
    limit: int = Query(100, ge=1, le=1000),
):
    """委托列表"""
    try:
        return paper_broker_service.list_orders(account_id, status=status, limit=limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"查询委托失败: {e}")


@router.post("/accounts/{account_id}/orders/{order_id}/cancel")
async def cancel_order(account_id: int, order_id: int):
    """取消 pending 委托"""
    try:
        return _ok_or_400(paper_broker_service.cancel_order(account_id, order_id))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"取消失败: {e}")


@router.get("/accounts/{account_id}/fills")
async def list_fills(
    account_id: int,
    limit: int = Query(200, ge=1, le=2000),
):
    """成交列表"""
    try:
        return paper_broker_service.list_fills(account_id, limit=limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"查询成交失败: {e}")


@router.post("/accounts/{account_id}/settle")
async def settle_account(account_id: int, body: Optional[SettleRequest] = None):
    """结算该账户所有可成交的 pending（次日开盘）"""
    try:
        asof = body.asof if body else None
        return paper_broker_service.settle_pending(account_id=account_id, asof=asof)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"结算失败: {e}")


@router.post("/settle")
async def settle_all(body: Optional[SettleRequest] = None):
    """结算全部账户 pending"""
    try:
        asof = body.asof if body else None
        return paper_broker_service.settle_pending(account_id=None, asof=asof)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"结算失败: {e}")


@router.post("/accounts/{account_id}/mark")
async def mark_to_market(account_id: int, body: Optional[MarkRequest] = None):
    """盯市并写入每日净值快照"""
    try:
        asof = body.asof if body else None
        write_equity = body.write_equity if body else True
        result = paper_broker_service.mark_to_market(
            account_id, asof=asof, write_equity=write_equity
        )
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail=result.get("error"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"盯市失败: {e}")


@router.get("/accounts/{account_id}/equity")
async def equity_curve(
    account_id: int,
    limit: int = Query(500, ge=1, le=5000),
):
    """净值曲线"""
    try:
        return paper_broker_service.get_equity_curve(account_id, limit=limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"查询净值失败: {e}")


@router.get("/accounts/{account_id}/stats")
async def account_stats(account_id: int):
    """交易统计：收益、回撤、胜率、买卖笔数等"""
    try:
        result = paper_broker_service.get_stats(account_id)
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail=result.get("error"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"统计失败: {e}")


@router.get("/accounts/{account_id}/decisions")
async def list_decisions(
    account_id: int,
    limit: int = Query(50, ge=1, le=200),
    session_id: Optional[str] = Query(None),
):
    """决策审计日志（LLM 感知-决策-执行-反馈）"""
    try:
        return paper_broker_service.list_decisions(
            account_id, limit=limit, session_id=session_id
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"查询决策日志失败: {e}")


class RollT1Request(BaseModel):
    asof: Optional[str] = Field(None, description="日切日期 YYYY-MM-DD，默认今天")
    force: bool = Field(False, description="同一日已切过是否强制再切")


class BuildWatchlistRequest(BaseModel):
    asof: Optional[str] = None
    include_screener: bool = True
    include_dash: bool = True
    setup_types: Optional[List[str]] = Field(None, description="默认 ['A','B']")
    max_total: int = Field(15, ge=1, le=50)
    max_per_source: int = Field(10, ge=1, le=30)
    replace: bool = True


class ManualWatchRequest(BaseModel):
    ts_code: str
    asof: Optional[str] = None
    name: Optional[str] = None
    reason: Optional[str] = None
    score: float = 55.0


class ScanRunRequest(BaseModel):
    asof: Optional[str] = None
    cooldown_minutes: int = Field(30, ge=1, le=240)
    pct_soft: float = 2.0
    pct_hard: float = 5.0
    volume_ratio: float = 2.0
    use_mins: bool = True


@router.post("/t1/roll")
async def roll_t1_all(body: Optional[RollT1Request] = None):
    """全部活跃账户 T+1 日切：frozen → sellable"""
    try:
        asof = body.asof if body else None
        force = body.force if body else False
        return paper_broker_service.roll_t1(account_id=None, asof=asof, force=force)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"T+1 日切失败: {e}")


@router.post("/accounts/{account_id}/t1/roll")
async def roll_t1_account(account_id: int, body: Optional[RollT1Request] = None):
    """单账户 T+1 日切"""
    try:
        asof = body.asof if body else None
        force = body.force if body else False
        return paper_broker_service.roll_t1(account_id=account_id, asof=asof, force=force)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"T+1 日切失败: {e}")


@router.post("/watchlist/build")
async def build_watchlist(body: Optional[BuildWatchlistRequest] = None):
    """从 screener A/B + 自选 构建盯盘名单（3～15）"""
    try:
        from server.trade_watch_service import trade_watch_service

        b = body or BuildWatchlistRequest()
        return trade_watch_service.build_watchlist(
            asof=b.asof,
            include_screener=b.include_screener,
            include_dash=b.include_dash,
            setup_types=b.setup_types,
            max_total=b.max_total,
            max_per_source=b.max_per_source,
            replace=b.replace,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"构建 watchlist 失败: {e}")


@router.get("/watchlist")
async def get_watchlist(
    asof: Optional[str] = Query(None),
    active_only: bool = Query(True),
):
    try:
        from server.trade_watch_service import trade_watch_service

        return trade_watch_service.list_watchlist(asof=asof, active_only=active_only)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"查询 watchlist 失败: {e}")


@router.post("/watchlist/manual")
async def add_watch_manual(body: ManualWatchRequest):
    try:
        from server.trade_watch_service import trade_watch_service

        return trade_watch_service.add_manual(
            body.ts_code,
            asof=body.asof,
            name=body.name,
            reason=body.reason,
            score=body.score,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"加入盯盘失败: {e}")


@router.post("/scan/run")
async def run_scan(body: Optional[ScanRunRequest] = None):
    """对 watchlist 跑一次规则巡检，产出事件（不调用 LLM）"""
    try:
        from server.trade_watch_service import trade_watch_service

        b = body or ScanRunRequest()
        return trade_watch_service.run_scan(
            asof=b.asof,
            cooldown_minutes=b.cooldown_minutes,
            pct_soft=b.pct_soft,
            pct_hard=b.pct_hard,
            volume_ratio=b.volume_ratio,
            use_mins=b.use_mins,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"巡检失败: {e}")


@router.get("/scan/events")
async def list_scan_events(
    asof: Optional[str] = Query(None),
    ts_code: Optional[str] = Query(None),
    severity: Optional[str] = Query(None, description="info/soft/hard"),
    limit: int = Query(100, ge=1, le=500),
):
    try:
        from server.trade_watch_service import trade_watch_service

        return trade_watch_service.list_events(
            asof=asof, ts_code=ts_code, severity=severity, limit=limit
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"查询事件失败: {e}")


# ─── 交易循环 paper cycle ─────────────────────────────

class CycleAccountRequest(BaseModel):
    account_id: int
    asof: Optional[str] = None
    use_llm: bool = True


class EveningScreenRequest(BaseModel):
    asof: Optional[str] = None
    fill: bool = False
    limit: int = Field(200, ge=10, le=1000)


class WeeklyReviewRequest(BaseModel):
    account_id: int
    asof: Optional[str] = None
    use_llm: bool = True
    auto_apply: bool = True


class CycleConfigPatch(BaseModel):
    top_n_a: Optional[int] = None
    top_n_b: Optional[int] = None
    min_score_a: Optional[float] = None
    min_score_b: Optional[float] = None
    drawdown_stop_pct: Optional[float] = None
    default_buy_qty: Optional[float] = None
    lookback_guard_open: Optional[bool] = None
    model: Optional[str] = None


@router.get("/cycle/config")
async def cycle_config_get():
    from server.paper_cycle_service import paper_cycle_service

    return paper_cycle_service.get_config()


@router.patch("/cycle/config")
async def cycle_config_patch(body: CycleConfigPatch):
    from server.paper_cycle_service import paper_cycle_service

    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    return paper_cycle_service.update_config(patch)


class SchedulerToggleRequest(BaseModel):
    enabled: bool = True
    account_id: Optional[int] = None
    use_llm: bool = False


@router.get("/cycle/scheduler")
async def cycle_scheduler_status():
    """定时器状态：21:00 选股 / 09:30 日课"""
    from server.paper_scheduler import paper_scheduler

    return paper_scheduler.status()


@router.post("/cycle/scheduler")
async def cycle_scheduler_toggle(body: SchedulerToggleRequest):
    """开启或关闭常驻定时器。开启后后端需保持运行。"""
    from server.paper_cycle_service import paper_cycle_service
    from server.paper_scheduler import paper_scheduler

    patch: Dict[str, Any] = {
        "scheduler_enabled": body.enabled,
        "scheduler_use_llm": body.use_llm,
    }
    if body.account_id is not None:
        patch["scheduler_account_id"] = body.account_id
    paper_cycle_service.update_config(patch)
    if body.enabled:
        if body.account_id is None:
            cfg = paper_cycle_service.get_config().get("config") or {}
            if not cfg.get("scheduler_account_id"):
                raise HTTPException(status_code=400, detail="请先选择要自动交易的账户")
        paper_scheduler.start()
    else:
        await paper_scheduler.stop()
    return paper_scheduler.status()


@router.post("/cycle/evening-screen")
async def cycle_evening_screen(body: Optional[EveningScreenRequest] = None):
    """前一晚选股：A/B 各 TopN → watchlist"""
    try:
        from server.paper_cycle_service import paper_cycle_service
        import asyncio

        b = body or EveningScreenRequest()
        return await asyncio.to_thread(
            paper_cycle_service.evening_screen,
            b.asof,
            fill=b.fill,
            limit=b.limit,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"晚间选股失败: {e}")


@router.post("/cycle/open-decide")
async def cycle_open_decide(body: CycleAccountRequest):
    """开市：做T判高低 + LLM 买卖并记理由"""
    try:
        from server.paper_cycle_service import paper_cycle_service

        return await paper_cycle_service.open_decide(
            body.account_id, asof=body.asof, use_llm=body.use_llm
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"开市决策失败: {e}")


@router.post("/cycle/risk-check")
async def cycle_risk_check(body: CycleAccountRequest):
    """持仓回撤风控清仓"""
    try:
        from server.paper_cycle_service import paper_cycle_service

        return await paper_cycle_service.risk_check(
            body.account_id, asof=body.asof, use_llm=False
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"风控失败: {e}")


@router.post("/cycle/daily-t0")
async def cycle_daily_t0(body: CycleAccountRequest):
    """日度持仓做T / 续涨判断"""
    try:
        from server.paper_cycle_service import paper_cycle_service

        return await paper_cycle_service.daily_t0(
            body.account_id, asof=body.asof, use_llm=body.use_llm
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"日度做T失败: {e}")


@router.post("/cycle/weekly-review")
async def cycle_weekly_review(body: WeeklyReviewRequest):
    """周复盘：净值对比 + 调整 A/B 分数门槛"""
    try:
        from server.paper_cycle_service import paper_cycle_service

        return await paper_cycle_service.weekly_review(
            body.account_id,
            asof=body.asof,
            use_llm=body.use_llm,
            auto_apply=body.auto_apply,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"周复盘失败: {e}")


@router.post("/cycle/run-trading-day")
async def cycle_run_trading_day(body: CycleAccountRequest):
    """一键日课：开市决策 → 风控 → 日度做T"""
    try:
        from server.paper_cycle_service import paper_cycle_service

        return await paper_cycle_service.run_trading_day(
            body.account_id, asof=body.asof, use_llm=body.use_llm
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"日课失败: {e}")


@router.get("/cycle/runs")
async def cycle_runs(
    limit: int = Query(50, ge=1, le=200),
    step: Optional[str] = Query(None),
):
    from server.paper_cycle_service import paper_cycle_service

    return paper_cycle_service.list_runs(limit=limit, step=step)


@router.get("/cycle/actions")
async def cycle_actions(
    account_id: Optional[int] = Query(None),
    asof: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
):
    from server.paper_cycle_service import paper_cycle_service

    return paper_cycle_service.list_actions(account_id=account_id, asof=asof, limit=limit)

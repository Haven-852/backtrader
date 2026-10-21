"""
Paper Router - 绾搁潰璐︽埛 / 妯℃嫙浜ゆ槗
POST /paper/accounts                      - 寮€鎴?
DELETE /paper/accounts/{id}               - 鍒犻櫎璐︽埛锛坔ard=true 鐗╃悊鍒犻櫎 / false 鍏抽棴锛?
GET  /paper/accounts                      - 璐︽埛鍒楄〃
GET  /paper/accounts/{id}                 - 璐︽埛璇︽儏锛堝垎鏃剁洴甯傜泩浜忥級
GET  /paper/accounts/{id}/positions       - 鎸佷粨锛堝垎鏃朵环锛?
GET  /paper/quotes                        - 1 鍒嗛挓鍒嗘椂鏈环
POST /paper/accounts/{id}/orders          - 涓嬪崟
GET  /paper/accounts/{id}/orders          - 濮旀墭鍒楄〃
POST /paper/accounts/{id}/orders/{oid}/cancel - 鍙栨秷 pending
GET  /paper/accounts/{id}/fills           - 鎴愪氦鍒楄〃
POST /paper/accounts/{id}/settle          - 缁撶畻璇ヨ处鎴?pending锛堟鏃ュ紑鐩橈級
POST /paper/settle                        - 缁撶畻鍏ㄩ儴 pending
POST /paper/accounts/{id}/mark            - 鍒嗘椂鐩競 + 鍑€鍊煎揩鐓?
GET  /paper/accounts/{id}/equity          - 鍑€鍊兼洸绾?
GET  /paper/accounts/{id}/stats           - 浜ゆ槗缁熻
GET  /paper/accounts/{id}/journal         - 褰撴棩鎿嶄綔鏃ュ織 + 鎸佷粨鏄庣粏
GET  /paper/accounts/{id}/decisions       - 鍐崇瓥瀹¤鏃ュ織
POST /paper/t1/roll                       - T+1 鏃ュ垏锛坒rozen鈫抯ellable锛?
POST /paper/accounts/{id}/t1/roll         - 鍗曡处鎴?T+1 鏃ュ垏
POST /paper/watchlist/build               - 浠庨€夎偂/鑷€夋瀯寤虹洴鐩樺悕鍗?
GET  /paper/watchlist                     - 鏌ョ湅鐩洏鍚嶅崟
POST /paper/watchlist/manual              - 鎵嬪姩鍔犲叆鐩洏
POST /paper/scan/run                      - 瀵?watchlist 璺戜竴娆¤鍒欏贰妫€
GET  /paper/scan/events                   - 宸℃浜嬩欢鍒楄〃
POST /paper/cycle/evening-screen          - 鏅氶棿 A/B TopN 閫夎偂鍏?watchlist
POST /paper/cycle/open-decide             - 寮€甯傚仛T+LLM 涔板崠
POST /paper/cycle/risk-check              - 鍥炴挙娓呬粨
POST /paper/cycle/daily-t0                - 鎸佷粨鏃ュ害鍋歍/缁定
POST /paper/cycle/weekly-review           - 鍛ㄥ鐩樿皟 A/B
POST /paper/cycle/run-trading-day         - 涓€閿棩璇?
GET  /paper/cycle/config|runs|actions     - 閰嶇疆涓庢祦姘?
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from server.paper_broker_service import paper_broker_service

router = APIRouter(prefix="/paper", tags=["paper"])


class CreateAccountRequest(BaseModel):
    name: str = Field("榛樿绾搁潰璐︽埛", min_length=1, max_length=64)
    initial_cash: float = Field(1_000_000, gt=0)
    lookback_guard: bool = True


class PlaceOrderRequest(BaseModel):
    ts_code: str = Field(..., description="鏍囩殑浠ｇ爜锛屽 000001.SZ")
    side: str = Field(..., description="buy / sell")
    quantity: float = Field(..., gt=0, description="鑲℃暟锛涗拱鍏ラ』涓?100 鏁存暟鍊?")
    lookback_guard: Optional[bool] = Field(
        None,
        description="True=娆℃棩寮€鐩樻垚浜わ紱False=褰撴棩鏀剁洏鎴愪氦锛涢粯璁ゆ部鐢ㄨ处鎴疯缃?",
    )
    signal_date: Optional[str] = Field(
        None, description="淇″彿鏃?YYYY-MM-DD锛涢粯璁ょ敤璇ユ爣鐨勬渶鏂颁氦鏄撴棩"
    )
    reason: Optional[str] = Field(None, description="涓嬪崟鐞嗙敱锛堝璁★級")
    order_type: str = "market"


class SettleRequest(BaseModel):
    asof: Optional[str] = Field(None, description="浠呯粨绠?exec_date <= asof 鐨勬寕鍗?")


class MarkRequest(BaseModel):
    asof: Optional[str] = Field(None, description="鍒嗘椂浜ゆ槗鏃?YYYY-MM-DD锛岄粯璁ゅ綋鍓嶅垎鏃舵棩")
    write_equity: bool = True


def _ok_or_400(result: Dict[str, Any]) -> Dict[str, Any]:
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "璇锋眰澶辫触")
    return result


@router.post("/accounts")
async def create_account(body: CreateAccountRequest):
    """寮€绔嬬焊闈㈣处鎴?"""
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
        raise HTTPException(status_code=500, detail=f"寮€鎴峰け璐? {e}")


@router.delete("/accounts/{account_id}")
async def delete_account(
    account_id: int,
    hard: bool = Query(
        True,
        description="true=鐗╃悊鍒犻櫎锛堢骇鑱旀竻鐞嗗叧鑱旀暟鎹級锛沠alse=浠呭叧闂?status=closed",
    ),
):
    """鍒犻櫎 / 鍏抽棴绾搁潰璐︽埛"""
    try:
        result = paper_broker_service.delete_account(account_id, hard=hard)
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail=result.get("error") or "鍒犻櫎澶辫触")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"鍒犻櫎璐︽埛澶辫触: {e}")


@router.get("/accounts")
async def list_accounts(
    status: Optional[str] = Query("active", description="active / closed锛涚┖=鍏ㄩ儴"),
):
    """璐︽埛鍒楄〃"""
    try:
        st = status if status else None
        return paper_broker_service.list_accounts(status=st)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"鏌ヨ璐︽埛澶辫触: {e}")


@router.get("/accounts/{account_id}")
async def get_account(
    account_id: int,
    mark: bool = Query(True, description="鏄惁鐢?1 鍒嗛挓鍒嗘椂鏈环鐩競锛涙棤鍒嗘椂涓嶈鐩樹腑鐩堜簭"),
):
    """璐︽埛璇︽儏 + 鎸佷粨"""
    try:
        result = paper_broker_service.get_account(account_id, mark=mark)
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail=result.get("error"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"鏌ヨ璐︽埛澶辫触: {e}")


@router.get("/accounts/{account_id}/positions")
async def list_positions(account_id: int):
    """鎸佷粨鍒楄〃锛堝惈鍚嶇О銆佹垚鏈€佸競浠枫€佺泩浜忥級"""
    try:
        result = paper_broker_service.list_positions(account_id)
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail=result.get("error"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"鏌ヨ鎸佷粨澶辫触: {e}")


@router.get("/accounts/{account_id}/journal")
async def get_account_journal(
    account_id: int,
    trade_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
):
    """璐︽埛褰撴棩鏃ュ織锛氭寔浠撴槑缁?+ 涓€閿棩璇?鑲′环鍞ら啋/瀵硅瘽 Agent 璁板綍"""
    try:
        from server.paper_activity_service import paper_activity_service

        result = paper_activity_service.get_journal(account_id, trade_date=trade_date)
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail=result.get("error"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"鏌ヨ璐︽埛鏃ュ織澶辫触: {e}")


@router.post("/accounts/{account_id}/orders")
async def place_order(account_id: int, body: PlaceOrderRequest):
    """
    涓嬪崟銆?
    lookback_guard=True锛氭寕 pending锛屽緟娆℃棩寮€鐩樼粨绠楋紱
    lookback_guard=False锛氭寜淇″彿鏃ユ敹鐩樹环绔嬪嵆鎴愪氦銆?
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
        raise HTTPException(status_code=500, detail=f"涓嬪崟澶辫触: {e}")


@router.get("/accounts/{account_id}/orders")
async def list_orders(
    account_id: int,
    status: Optional[str] = Query(None, description="pending / filled / cancelled / rejected"),
    limit: int = Query(100, ge=1, le=1000),
):
    """濮旀墭鍒楄〃"""
    try:
        return paper_broker_service.list_orders(account_id, status=status, limit=limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"鏌ヨ濮旀墭澶辫触: {e}")


@router.post("/accounts/{account_id}/orders/{order_id}/cancel")
async def cancel_order(account_id: int, order_id: int):
    """鍙栨秷 pending 濮旀墭"""
    try:
        return _ok_or_400(paper_broker_service.cancel_order(account_id, order_id))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"鍙栨秷澶辫触: {e}")


@router.get("/accounts/{account_id}/fills")
async def list_fills(
    account_id: int,
    limit: int = Query(200, ge=1, le=2000),
):
    """鎴愪氦鍒楄〃"""
    try:
        return paper_broker_service.list_fills(account_id, limit=limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"鏌ヨ鎴愪氦澶辫触: {e}")


@router.post("/accounts/{account_id}/settle")
async def settle_account(account_id: int, body: Optional[SettleRequest] = None):
    """缁撶畻璇ヨ处鎴锋墍鏈夊彲鎴愪氦鐨?pending锛堟鏃ュ紑鐩橈級"""
    try:
        asof = body.asof if body else None
        return paper_broker_service.settle_pending(account_id=account_id, asof=asof)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"缁撶畻澶辫触: {e}")


@router.post("/settle")
async def settle_all(body: Optional[SettleRequest] = None):
    """缁撶畻鍏ㄩ儴璐︽埛 pending"""
    try:
        asof = body.asof if body else None
        return paper_broker_service.settle_pending(account_id=None, asof=asof)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"缁撶畻澶辫触: {e}")


@router.get("/quotes")
async def paper_quotes(
    ts_code: Optional[str] = Query(None, description="鍗曚釜鏍囩殑"),
    ts_codes: Optional[str] = Query(None, description="閫楀彿鍒嗛殧澶氫釜浠ｇ爜"),
    account_id: Optional[int] = Query(None, description="璐︽埛鎸佷粨鍏ㄩ儴鍙栧垎鏃朵环"),
    trade_date: Optional[str] = Query(None, description="浜ゆ槗鏃?YYYY-MM-DD"),
):
    """鐪熷疄 1 鍒嗛挓鍒嗘椂鏈环銆傛病鏈夊垎鏃舵姤缂猴紝涓嶇敤鏃ョ嚎浼扮畻銆?"""
    try:
        from tools.paper_toolkit import paper_toolkit

        return await paper_toolkit.get_intraday_quotes(
            ts_code=ts_code,
            ts_codes=ts_codes,
            account_id=account_id,
            trade_date=trade_date,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"鍒嗘椂鎶ヤ环澶辫触: {e}")


@router.post("/accounts/{account_id}/mark")
async def mark_to_market(account_id: int, body: Optional[MarkRequest] = None):
    """鐢?1 鍒嗛挓鍒嗘椂鏈环鐩競锛涙棤鍒嗘椂鎸佷粨涓嶈鐩樹腑鐩堜簭锛屼笉鍐欏叆涓嶅畬鏁寸殑鏃ュ噣鍊煎揩鐓с€?"""
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
        raise HTTPException(status_code=500, detail=f"鐩競澶辫触: {e}")


@router.get("/accounts/{account_id}/equity")
async def equity_curve(
    account_id: int,
    limit: int = Query(500, ge=1, le=5000),
):
    """鍑€鍊兼洸绾?"""
    try:
        return paper_broker_service.get_equity_curve(account_id, limit=limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"鏌ヨ鍑€鍊煎け璐? {e}")


@router.get("/accounts/{account_id}/stats")
async def account_stats(account_id: int):
    """浜ゆ槗缁熻锛氭敹鐩娿€佸洖鎾ゃ€佽儨鐜囥€佷拱鍗栫瑪鏁扮瓑"""
    try:
        result = paper_broker_service.get_stats(account_id)
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail=result.get("error"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"缁熻澶辫触: {e}")


@router.get("/accounts/{account_id}/decisions")
async def list_decisions(
    account_id: int,
    limit: int = Query(50, ge=1, le=200),
    session_id: Optional[str] = Query(None),
):
    """鍐崇瓥瀹¤鏃ュ織锛圠LM 鎰熺煡-鍐崇瓥-鎵ц-鍙嶉锛?"""
    try:
        return paper_broker_service.list_decisions(
            account_id, limit=limit, session_id=session_id
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"鏌ヨ鍐崇瓥鏃ュ織澶辫触: {e}")


class RollT1Request(BaseModel):
    asof: Optional[str] = Field(None, description="鏃ュ垏鏃ユ湡 YYYY-MM-DD锛岄粯璁や粖澶?")
    force: bool = Field(False, description="鍚屼竴鏃ュ凡鍒囪繃鏄惁寮哄埗鍐嶅垏")


class BuildWatchlistRequest(BaseModel):
    asof: Optional[str] = None
    include_screener: bool = True
    include_dash: bool = True
    setup_types: Optional[List[str]] = Field(None, description="榛樿 ['A','B']")
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
    """鍏ㄩ儴娲昏穬璐︽埛 T+1 鏃ュ垏锛歠rozen 鈫?sellable"""
    try:
        asof = body.asof if body else None
        force = body.force if body else False
        return paper_broker_service.roll_t1(account_id=None, asof=asof, force=force)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"T+1 鏃ュ垏澶辫触: {e}")


@router.post("/accounts/{account_id}/t1/roll")
async def roll_t1_account(account_id: int, body: Optional[RollT1Request] = None):
    """鍗曡处鎴?T+1 鏃ュ垏"""
    try:
        asof = body.asof if body else None
        force = body.force if body else False
        return paper_broker_service.roll_t1(account_id=account_id, asof=asof, force=force)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"T+1 鏃ュ垏澶辫触: {e}")


@router.post("/watchlist/build")
async def build_watchlist(body: Optional[BuildWatchlistRequest] = None):
    """浠?screener A/B + 鑷€?鏋勫缓鐩洏鍚嶅崟锛?锝?5锛?"""
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
        raise HTTPException(status_code=500, detail=f"鏋勫缓 watchlist 澶辫触: {e}")


@router.get("/watchlist")
async def get_watchlist(
    asof: Optional[str] = Query(None),
    active_only: bool = Query(True),
):
    try:
        from server.trade_watch_service import trade_watch_service

        return trade_watch_service.list_watchlist(asof=asof, active_only=active_only)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"鏌ヨ watchlist 澶辫触: {e}")


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
        raise HTTPException(status_code=500, detail=f"鍔犲叆鐩洏澶辫触: {e}")


@router.post("/scan/run")
async def run_scan(body: Optional[ScanRunRequest] = None):
    """瀵?watchlist 璺戜竴娆¤鍒欏贰妫€锛屼骇鍑轰簨浠讹紙涓嶈皟鐢?LLM锛?"""
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
        raise HTTPException(status_code=500, detail=f"宸℃澶辫触: {e}")


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
        raise HTTPException(status_code=500, detail=f"鏌ヨ浜嬩欢澶辫触: {e}")


# 鈹€鈹€鈹€ 浜ゆ槗寰幆 paper cycle 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

class CycleAccountRequest(BaseModel):
    account_id: int
    asof: Optional[str] = None
    use_llm: bool = True
    trigger_source: Optional[str] = Field(
        None,
        description="manual_daily_lesson / scheduler_daily_lesson / price_scan / manual_price_scan / chat_agent",
    )


class EveningScreenRequest(BaseModel):
    asof: Optional[str] = Field(None, description="浜ゆ槗鏃ワ紱榛樿=褰撳ぉ宸叉敹鐩樹細璇濇棩")
    fill: bool = Field(True, description="缂烘棩绾挎椂浠?Datahubco 琛ラ綈褰撳ぉ绐楀彛")
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
    """瀹氭椂鍣ㄧ姸鎬侊細21:00 閫夎偂 / 09:30 鏃ヨ"""
    from server.paper_scheduler import paper_scheduler

    return paper_scheduler.status()


@router.post("/cycle/scheduler")
async def cycle_scheduler_toggle(body: SchedulerToggleRequest):
    """寮€鍚垨鍏抽棴甯搁┗瀹氭椂鍣ㄣ€傚紑鍚悗鍚庣闇€淇濇寔杩愯銆?"""
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
                raise HTTPException(status_code=400, detail="璇峰厛閫夋嫨瑕佽嚜鍔ㄤ氦鏄撶殑璐︽埛")
        paper_scheduler.start()
    else:
        await paper_scheduler.stop()
    return paper_scheduler.status()


@router.post("/cycle/evening-screen")
async def cycle_evening_screen(body: Optional[EveningScreenRequest] = None):
    """鍓嶄竴鏅氶€夎偂锛欰/B 鍚?TopN 鈫?watchlist"""
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
        raise HTTPException(status_code=500, detail=f"鏅氶棿閫夎偂澶辫触: {e}")


@router.post("/cycle/open-decide")
async def cycle_open_decide(body: CycleAccountRequest):
    """瀵?watchlist 鍋氫竴娆″綋鍓嶅垎鏃跺仛T锛堜笉鐢ㄦ槰鏃ユ棩绾块珮浣庯級"""
    try:
        from server.paper_cycle_service import paper_cycle_service

        return await paper_cycle_service.open_decide(
            body.account_id,
            asof=body.asof,
            use_llm=body.use_llm,
            trigger_source=body.trigger_source or "manual_price_scan",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"寮€甯傚喅绛栧け璐? {e}")


@router.post("/cycle/risk-check")
async def cycle_risk_check(body: CycleAccountRequest):
    """鎸佷粨鍥炴挙椋庢帶娓呬粨"""
    try:
        from server.paper_cycle_service import paper_cycle_service

        return await paper_cycle_service.risk_check(
            body.account_id, asof=body.asof, use_llm=False
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"椋庢帶澶辫触: {e}")


@router.post("/cycle/daily-t0")
async def cycle_daily_t0(body: CycleAccountRequest):
    """鐩樹腑鍋歍锛氭媺褰撴棩鍒嗛挓绾匡紝鍛戒腑楂樹綆鎵嶄笅鍗曪紝涓€澶╀竴鍙竴娆?"""
    try:
        from server.paper_cycle_service import paper_cycle_service

        return await paper_cycle_service.daily_t0(
            body.account_id,
            asof=body.asof,
            use_llm=body.use_llm,
            trigger_source=body.trigger_source or "manual_price_scan",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"鏃ュ害鍋歍澶辫触: {e}")


@router.post("/cycle/weekly-review")
async def cycle_weekly_review(body: WeeklyReviewRequest):
    """鍛ㄥ鐩橈細鍑€鍊煎姣?+ 璋冩暣 A/B 鍒嗘暟闂ㄦ"""
    try:
        from server.paper_cycle_service import paper_cycle_service

        return await paper_cycle_service.weekly_review(
            body.account_id,
            asof=body.asof,
            use_llm=body.use_llm,
            auto_apply=body.auto_apply,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"鍛ㄥ鐩樺け璐? {e}")


@router.post("/cycle/afternoon-exposure")
async def cycle_afternoon_exposure(body: CycleAccountRequest):
    """13:00 浠撲綅锛氭槑鏃ヤ笂娑ㄦ鐜?> 60% 婊′粨锛屽惁鍒欑暀鍑?20% 鐜伴噾銆傚崟绁ㄥ缁堜笉瓒呰繃鎬昏祫浜?20%銆?"""
    try:
        from server.paper_cycle_service import paper_cycle_service

        return await paper_cycle_service.afternoon_exposure(
            body.account_id,
            asof=body.asof,
            use_llm=body.use_llm,
            trigger_source=body.trigger_source or "user_button",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"浠撲綅璋冩暣澶辫触: {e}")


@router.post("/cycle/run-trading-day")
async def cycle_run_trading_day(body: CycleAccountRequest):
    """涓€閿棩璇撅細T+1鏃ュ垏 鈫?椋庢帶 鈫?褰撳墠鍒嗘椂鍋歍"""
    try:
        from server.paper_cycle_service import paper_cycle_service

        return await paper_cycle_service.run_trading_day(
            body.account_id,
            asof=body.asof,
            use_llm=body.use_llm,
            trigger_source=body.trigger_source or "manual_daily_lesson",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"鏃ヨ澶辫触: {e}")


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

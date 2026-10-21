"""盘中做 T：只用当天已经走出的分钟线，每 5 分钟看一次 watchlist。

判断只用截至当前分钟的信息（做 T 四法，或现价相对前 5 日均线的位置）。
成交价记当前这一分钟的价格。每只股票每个交易日最多成交一次，下一交易日可以再做。
近 5 个交易日里若有 3 天振幅不足 3%，当天不做 T。
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy import text as sa_text

from server.paper_broker_service import paper_broker_service, _f

logger = logging.getLogger(__name__)

SH = ZoneInfo("Asia/Shanghai")
MIN_SESSION_BARS = 15
DULL_AMP = 0.03
DULL_DAYS = 3


def _best(cands: List[Tuple[str, float, str]]) -> Optional[Tuple[str, float, str]]:
    if not cands:
        return None
    return max(cands, key=lambda x: x[1])


def ma_and_amplitude(engine, ts_code: str, trade_date: date) -> Dict[str, Any]:
    """前 5 个已完成交易日的均线与振幅。不含当天，避免用未走完的日线。"""
    with engine.connect() as conn:
        rows = conn.execute(
            sa_text(
                """
                SELECT high, low, close, pre_close
                FROM stock_daily
                WHERE ts_code = :c AND trade_date < :d
                ORDER BY trade_date DESC
                LIMIT 5
                """
            ),
            {"c": ts_code, "d": trade_date},
        ).fetchall()
    closes: List[float] = []
    amps: List[float] = []
    dull_n = 0
    for r in rows:
        if r.close is not None:
            closes.append(float(r.close))
        base = float(r.pre_close or r.close or 0)
        hi = float(r.high or 0)
        lo = float(r.low or 0)
        if base > 0 and hi >= lo:
            amp = (hi - lo) / base
            amps.append(round(amp, 4))
            if amp < DULL_AMP:
                dull_n += 1
    ma5 = round(sum(closes) / len(closes), 4) if len(closes) >= 5 else None
    return {
        "ma5": ma5,
        "dull": len(rows) >= 5 and dull_n >= DULL_DAYS,
        "dull_days": dull_n,
        "amp_days": amps,
        "n_days": len(rows),
    }


def live_snapshot(engine, ts_code: str, trade_date: date) -> Dict[str, Any]:
    """拉取当日分钟线，用截至当前的分时判断高低。不用日线全天高低做标签。"""
    from agents.strategy.strategy_t0.tools import _annotate_day
    from tools.datahubco_toolkit import datahubco_toolkit

    mins = datahubco_toolkit.get_mins(
        ts_code,
        trade_date=trade_date.isoformat(),
        source="auto",
        write_back=True,
        include_auction=False,
    )
    if not mins.get("ok"):
        return {
            "ok": False,
            "bias": "no_bar",
            "error": mins.get("error") or "无当日分钟线",
            "trade_date": trade_date.isoformat(),
        }

    now = datetime.now(SH)
    if trade_date == now.date():
        as_of = now
    else:
        as_of = datetime(trade_date.year, trade_date.month, trade_date.day, 15, 0, tzinfo=SH)

    ann = _annotate_day(ts_code, trade_date, as_of, 20, False)
    if not ann.get("ok"):
        return {
            "ok": False,
            "bias": "no_bar",
            "error": ann.get("error") or "分时无法标注",
            "mins_count": mins.get("count"),
            "mins_source": mins.get("source_used"),
            "trade_date": trade_date.isoformat(),
        }

    rows = ann.get("rows") or []
    last = rows[-1]
    px = float(last["close"])
    n = len(rows)
    hist = ma_and_amplitude(engine, ts_code, trade_date)
    ma5 = hist.get("ma5")
    at_high = bool(last.get("at_session_high"))
    at_low = bool(last.get("at_session_low"))

    sell_algos: List[Tuple[str, float, str]] = []
    buy_algos: List[Tuple[str, float, str]] = []
    if n >= MIN_SESSION_BARS:
        if last.get("sell_a"):
            sell_algos.append(("method_a", float(last.get("score_a") or 0), str(last.get("reason_a") or "")))
        if last.get("sell_b"):
            sell_algos.append(("method_b", float(last.get("score_b") or 0), str(last.get("reason_b") or "")))
        if last.get("sell_d"):
            sell_algos.append(("method_d", float(last.get("score_d") or 0), str(last.get("reason_d") or "")))
        if last.get("buy_a"):
            buy_algos.append(("method_a", float(last.get("score_a_buy") or 0), str(last.get("reason_a_buy") or "")))
        if last.get("buy_b"):
            buy_algos.append(("method_b", float(last.get("score_b_buy") or 0), str(last.get("reason_b_buy") or "")))
        if last.get("buy_d"):
            buy_algos.append(("method_d", float(last.get("score_d_buy") or 0), str(last.get("reason_d_buy") or "")))
        if ma5 and at_high and px >= float(ma5) * 1.005:
            sell_algos.append(("ma5", 0.66, f"现价在截至目前高点，且高于5日均线 {ma5}"))
        if ma5 and at_low and px <= float(ma5) * 0.995:
            buy_algos.append(("ma5", 0.66, f"现价在截至目前低点，且低于5日均线 {ma5}"))

    algorithm = None
    algorithm_reason = ""
    if n < MIN_SESSION_BARS:
        bias = "wait"
    elif hist.get("dull"):
        bias = "skip_dull"
        sell_algos, buy_algos = [], []
    elif sell_algos and buy_algos:
        bias = "conflict"
    elif sell_algos:
        best = _best(sell_algos)
        bias = "sell_high"
        algorithm, _, algorithm_reason = best  # type: ignore[misc]
    elif buy_algos:
        best = _best(buy_algos)
        bias = "buy_low"
        algorithm, _, algorithm_reason = best  # type: ignore[misc]
    else:
        bias = "watch"

    t_style = None
    if bias == "sell_high":
        t_style = "same_day"
    elif bias == "buy_low":
        t_style = "next_day"

    return {
        "ok": True,
        "bias": bias,
        "t_style": t_style,
        "algorithm": algorithm,
        "algorithm_reason": algorithm_reason,
        "price": round(px, 4),
        "clock": last.get("clock"),
        "session_high": last.get("session_high"),
        "session_low": last.get("session_low"),
        "at_session_high": at_high,
        "at_session_low": at_low,
        "n_session": n,
        "ma5": ma5,
        "dull": bool(hist.get("dull")),
        "dull_days": hist.get("dull_days"),
        "amp_days": hist.get("amp_days"),
        "pre_close": ann.get("pre_close"),
        "regime": ann.get("regime"),
        "mins_source": mins.get("source_used"),
        "mins_count": mins.get("count"),
        "trade_date": trade_date.isoformat(),
        "as_of": as_of.isoformat(timespec="seconds"),
        "sell_algos": [a[0] for a in sell_algos],
        "buy_algos": [a[0] for a in buy_algos],
    }


def t_state(engine, account_id: int, ts_code: str, trade_date: date) -> Dict[str, Any]:
    with engine.connect() as conn:
        rows = conn.execute(
            sa_text(
                """
                SELECT action, order_id, t0_summary
                FROM paper_cycle_action
                WHERE account_id = :aid AND ts_code = :code
                  AND asof = :d AND step = 'intraday_t'
                ORDER BY id DESC
                """
            ),
            {"aid": account_id, "code": ts_code, "d": trade_date},
        ).fetchall()
    traded = False
    last_bias = None
    last_action = None
    for i, r in enumerate(rows):
        summary = r.t0_summary
        if isinstance(summary, str):
            try:
                summary = json.loads(summary)
            except Exception:
                summary = {}
        summary = summary or {}
        if i == 0:
            last_action = r.action
            last_bias = summary.get("bias")
        if r.order_id and r.action in ("buy", "sell"):
            traded = True
    return {"traded": traded, "last_bias": last_bias, "last_action": last_action}


async def run_intraday_t(service, account_id: int, asof: Optional[str], *, use_llm: bool) -> Dict[str, Any]:
    """对当前 watchlist 做一次分时做T。asof 为空时用上海当天，不用最新日线日期。"""
    service._ensure()
    cfg = service._cfg()
    today = datetime.now(SH).date()
    asof_d = date.fromisoformat(str(asof)[:10]) if asof else today
    model = str(cfg.get("model") or "deepseek-chat")
    buy_qty = float(cfg.get("default_buy_qty") or 500)

    from server.trade_watch_service import trade_watch_service

    wl = trade_watch_service.list_watchlist(active_only=True)
    items = wl.get("watchlist") or []
    acct = paper_broker_service.get_account(account_id, mark=False)
    if not acct.get("ok", True) and acct.get("error"):
        return {"ok": False, "error": acct.get("error")}
    positions = {p["ts_code"]: p for p in (acct.get("positions") or [])}

    actions: List[Dict[str, Any]] = []
    with service.engine.begin() as conn:
        run_id = service._create_run(
            conn,
            step="intraday_t",
            asof=asof_d,
            account_id=account_id,
            summary={
                "watchlist": len(items),
                "watchlist_asof": wl.get("asof"),
                "session_date": asof_d.isoformat(),
                "note": "用当日分钟线，不用最新日线全天高低",
            },
        )

    for item in items:
        code = item.get("ts_code")
        if not code:
            continue
        try:
            row = await _one_name(
                service,
                account_id=account_id,
                run_id=run_id,
                asof_d=asof_d,
                item=item,
                pos=positions.get(code) or {},
                model=model,
                buy_qty=buy_qty,
                use_llm=use_llm,
            )
        except Exception as e:
            logger.exception("分时做T失败 %s", code)
            row = {"ts_code": code, "action": "error", "reason": str(e)}
        if row:
            actions.append(row)

    return {
        "ok": True,
        "step": "intraday_t",
        "asof": asof_d.isoformat(),
        "run_id": run_id,
        "watchlist_asof": wl.get("asof"),
        "count": len(actions),
        "actions": actions,
    }


async def _one_name(
    service,
    *,
    account_id: int,
    run_id: int,
    asof_d: date,
    item: Dict[str, Any],
    pos: Dict[str, Any],
    model: str,
    buy_qty: float,
    use_llm: bool,
) -> Optional[Dict[str, Any]]:
    code = item["ts_code"]
    state = t_state(service.engine, account_id, code, asof_d)
    if state["traded"] and state.get("last_action") == "skip_done":
        return None
    snap = live_snapshot(service.engine, code, asof_d)
    bias = snap.get("bias") or "no_bar"
    sellable = _f(pos.get("sellable_qty"))
    held = _f(pos.get("quantity"))

    if state["traded"]:
        return _log_skip(
            service, run_id, account_id, asof_d, code, item, snap,
            action="skip_done",
            reason="该股今日已做T一次，下一交易日可以再做",
        )

    quiet = bias in ("watch", "wait", "no_bar", "conflict", "skip_dull")
    if quiet and state.get("last_bias") == bias and state.get("last_action") == bias:
        return None

    if bias == "wait":
        return _log_skip(
            service, run_id, account_id, asof_d, code, item, snap,
            action="wait",
            reason=f"连续竞价分钟不足 {MIN_SESSION_BARS}，先不判断高低",
        )
    if bias == "no_bar":
        return _log_skip(
            service, run_id, account_id, asof_d, code, item, snap,
            action="no_bar",
            reason=str(snap.get("error") or "没有当天分钟线，不做T"),
        )
    if bias == "skip_dull":
        return _log_skip(
            service, run_id, account_id, asof_d, code, item, snap,
            action="skip_dull",
            reason=f"近5日有 {snap.get('dull_days')} 天振幅不足3%，减少做T",
        )
    if bias in ("watch", "conflict"):
        return _log_skip(
            service, run_id, account_id, asof_d, code, item, snap,
            action=bias,
            reason="当前分钟没有明确的高点或低点" if bias == "watch" else "买卖信号冲突，观望",
        )

    if state.get("last_bias") == bias and state.get("last_action") in ("hold", "skip_t1", bias):
        return None

    algorithm = snap.get("algorithm") or ""
    decision: Dict[str, Any] = {}
    llm_raw = ""
    if use_llm:
        prompt = (
            f"盘中做T裁决。交易日是 {asof_d.isoformat()}，这是截至当前分钟的分时，不是收盘后的日线高低。\n"
            f"标的 {code} 现价 {snap.get('price')} 时间 {snap.get('clock')}。"
            f"截至目前高 {snap.get('session_high')} 低 {snap.get('session_low')}。"
            f"5日均线 {snap.get('ma5')}。\n"
            f"规则初判 bias={bias}，算法 {algorithm}：{snap.get('algorithm_reason')}。"
            f"风格 {snap.get('t_style')}（same_day=卖出昨日及更早的可卖仓；next_day=今天买入，明天才能卖）。\n"
            f"可卖 {sellable}，总持仓 {held}，建议数量 {buy_qty}。\n"
            "只输出 JSON："
            "action(buy|sell|hold), quantity, reason, market_position, confidence, "
            "algorithm（沿用或写明 method_a/method_b/method_d/ma5）, "
            "optimize_note（这个算法哪里还可以改进，一两句）。"
        )
        decision, llm_raw = await service._llm_decide(prompt, model)

    if not decision.get("action"):
        if bias == "sell_high" and sellable >= 100:
            decision = {
                "action": "sell",
                "quantity": min(sellable, buy_qty),
                "reason": f"规则回退：{algorithm} 判断当前为高点",
                "market_position": snap.get("clock"),
                "algorithm": algorithm,
                "optimize_note": "模型未返回，未做算法优化记录",
            }
        elif bias == "buy_low":
            decision = {
                "action": "buy",
                "quantity": buy_qty,
                "reason": f"规则回退：{algorithm} 判断当前为低点",
                "market_position": snap.get("clock"),
                "algorithm": algorithm,
                "optimize_note": "模型未返回，未做算法优化记录",
            }
        else:
            decision = {"action": "hold", "quantity": 0, "reason": "无动作", "algorithm": algorithm}

    action = str(decision.get("action") or "hold").lower()
    qty = _f(decision.get("quantity"))
    reason = str(decision.get("reason") or "")
    used_algo = str(decision.get("algorithm") or algorithm or "")
    optimize = str(decision.get("optimize_note") or "")
    px = _f(snap.get("price"))
    order_id = None

    if action == "sell":
        qty = min(qty if qty >= 100 else buy_qty, sellable)
        qty = (qty // 100) * 100
        if qty < 100:
            action = "skip_t1"
            reason = f"{reason} | T+1 可卖不足（可卖 {sellable}），今天的买入不能当卖单"
        else:
            order_id, action, reason, px = _send(
                account_id, code, "sell", qty, px, asof_d, used_algo, reason, action
            )
    elif action == "buy":
        from server.paper_position_policy import clamp_buy_qty

        want = (qty // 100) * 100 if qty >= 100 else (buy_qty // 100) * 100
        qty = clamp_buy_qty(service, account_id, code, px, want)
        if qty < 100:
            action = "skip_cap"
            reason = f"{reason} | 单票不超过总资产20%，且总仓位受当日上限约束，这次买不下1手"
        else:
            order_id, action, reason, px = _send(
                account_id, code, "buy", qty, px, asof_d, used_algo, reason, action
            )
    else:
        action = "hold"
        qty = 0

    note = f"{reason} | 算法 {used_algo or '-'} | 优化: {optimize or '-'}"
    _persist(
        service, run_id, account_id, asof_d, code, item, snap,
        action=action, qty=qty, px=px, reason=note, order_id=order_id, llm_raw=llm_raw,
        extra={"algorithm": used_algo, "optimize_note": optimize, "t_style": snap.get("t_style")},
    )
    if use_llm or order_id:
        paper_broker_service.log_decision(
            account_id,
            user_query=f"intraday_t {code} {asof_d.isoformat()}",
            assistant_summary=note[:2000],
            order_ids=[order_id] if order_id else [],
            meta={
                "algorithm": used_algo,
                "optimize_note": optimize,
                "t_style": snap.get("t_style"),
                "price": px,
                "bias": bias,
                "clock": snap.get("clock"),
            },
        )
        try:
            from server.paper_activity_service import paper_activity_service

            obs = {
                "clock": snap.get("clock"),
                "as_of": snap.get("as_of"),
                "price": snap.get("price"),
                "bias": bias,
                "session_high": snap.get("session_high"),
                "session_low": snap.get("session_low"),
                "at_session_high": snap.get("at_session_high"),
                "at_session_low": snap.get("at_session_low"),
                "ma5": snap.get("ma5"),
                "algorithm": used_algo or snap.get("algorithm"),
                "algorithm_reason": snap.get("algorithm_reason"),
                "t_style": snap.get("t_style"),
                "regime": snap.get("regime"),
                "n_session": snap.get("n_session"),
                "sellable": sellable,
                "held": held,
                "decision_action": action,
                "optimize_note": optimize,
            }
            if use_llm:
                paper_activity_service.log_llm_wake(
                    account_id,
                    ts_code=code,
                    observation=obs,
                    llm_raw=llm_raw,
                    llm_model=model,
                    action=action,
                    quantity=qty,
                    price=px,
                    order_id=order_id,
                    run_id=run_id,
                    detail=note[:2000],
                    trade_date=asof_d,
                )
            elif order_id:
                paper_activity_service.log_event(
                    account_id,
                    event_type="order",
                    ts_code=code,
                    title=f"{action} {code}",
                    detail=note[:2000],
                    observation=obs,
                    action=action,
                    quantity=qty,
                    price=px,
                    order_id=order_id,
                    run_id=run_id,
                    trade_date=asof_d,
                )
        except Exception as e:
            logger.warning("活动日志写入失败: %s", e)
    return {
        "ts_code": code,
        "action": action,
        "quantity": qty,
        "price": px,
        "algorithm": used_algo,
        "optimize_note": optimize,
        "t_style": snap.get("t_style"),
        "bias": bias,
        "order_id": order_id,
        "reason": note,
    }


def _send(account_id, code, side, qty, px, asof_d, algorithm, reason, action):
    res = paper_broker_service.place_order(
        account_id,
        code,
        side,
        qty,
        lookback_guard=False,
        signal_date=asof_d.isoformat(),
        exec_price=px,
        reason=f"[intraday_t/{algorithm}/{side}] {reason}",
    )
    if not res.get("ok"):
        return None, f"{action}_failed", f"{reason} | {res.get('error')}", px
    fill = res.get("fill") or {}
    order_id = (res.get("order") or {}).get("id")
    fill_px = _f(fill.get("price")) or px
    fee = _f(fill.get("commission")) + _f(fill.get("stamp_tax"))
    return order_id, action, f"{reason} | 成交 {fill_px} 费用 {fee:.2f}", fill_px


def _log_skip(service, run_id, account_id, asof_d, code, item, snap, *, action, reason):
    _persist(
        service, run_id, account_id, asof_d, code, item, snap,
        action=action, qty=0, px=_f(snap.get("price")), reason=reason, order_id=None, llm_raw="",
        extra={},
    )
    return {"ts_code": code, "action": action, "quantity": 0, "reason": reason, "bias": snap.get("bias"), "price": snap.get("price")}


def _setup_of(item: Dict[str, Any]) -> Optional[str]:
    meta = item.get("meta") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = {}
    st = str((meta or {}).get("setup_type") or "")[:8]
    return st or None


def _persist(service, run_id, account_id, asof_d, code, item, snap, *, action, qty, px, reason, order_id, llm_raw, extra):
    summary = dict(snap)
    summary.update(extra or {})
    with service.engine.begin() as conn:
        service._log_action(
            conn,
            run_id=run_id,
            account_id=account_id,
            asof=asof_d,
            step="intraday_t",
            ts_code=code,
            setup_type=_setup_of(item),
            action=str(action)[:16],
            quantity=qty,
            price=px or None,
            reason=reason,
            market_context={"watch_reason": item.get("reason"), "score": item.get("score")},
            t0_summary=summary,
            order_id=order_id,
            llm_raw=llm_raw,
        )

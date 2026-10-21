"""仓位控制。

- 单只股票市值不超过总资产的 20%
- 13:00 判断持仓明天上涨的概率：大于 60% 则总仓位可以打满；否则总仓位不超过 80%，留出 20% 现金
- 13:00 之前，总仓位先按 80% 封顶
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import text as sa_text

from server.paper_broker_service import calc_trade_fees, paper_broker_service, _f

logger = logging.getLogger(__name__)

SH = ZoneInfo("Asia/Shanghai")
SINGLE_WEIGHT = 0.20
FULL_EXPOSURE = 1.0
CAUTIOUS_EXPOSURE = 0.80
PROB_FULL = 0.60


def exposure_for_probability(prob: Optional[float]) -> float:
    if prob is None:
        return CAUTIOUS_EXPOSURE
    return FULL_EXPOSURE if float(prob) > PROB_FULL else CAUTIOUS_EXPOSURE


def rule_up_probability(price: float, ma5: Optional[float], ret5: Optional[float]) -> float:
    """没有模型时的粗概率：站上 5 日均线、近 5 日上涨则抬高。"""
    score = 0.50
    if ma5 and ma5 > 0 and price > 0:
        score += 0.12 if price >= ma5 else -0.12
    if ret5 is not None:
        if ret5 >= 0.03:
            score += 0.08
        elif ret5 <= -0.03:
            score -= 0.10
    return round(max(0.05, min(0.95, score)), 4)


def affordable_shares(cash: float, price: float) -> int:
    """扣掉佣金后还能买多少股，向下取整到 100。"""
    if price <= 0 or cash <= 5:
        return 0
    qty = int((cash - 5) / price // 100) * 100
    while qty >= 100:
        amount = qty * price
        commission, _stamp = calc_trade_fees("buy", amount)
        if amount + commission <= cash + 1e-6:
            return qty
        qty -= 100
    return 0


def max_buy_shares(
    equity: float,
    cash: float,
    price: float,
    name_mv: float,
    book_mv: float,
    exposure_cap: float,
    single_weight: float = SINGLE_WEIGHT,
) -> int:
    """单票 20% 与总仓位上限里，还能买的股数。"""
    if equity <= 0 or price <= 0:
        return 0
    room_name = equity * single_weight - max(name_mv, 0.0)
    room_book = equity * exposure_cap - max(book_mv, 0.0)
    budget = min(room_name, room_book, cash)
    if budget <= 0:
        return 0
    by_budget = int(budget / price // 100) * 100
    by_cash = affordable_shares(cash, price)
    return max(0, min(by_budget, by_cash))


def current_exposure_cap(cfg: Dict[str, Any], today: date) -> float:
    asof = str(cfg.get("exposure_asof") or "")[:10]
    if asof != today.isoformat():
        return CAUTIOUS_EXPOSURE
    try:
        cap = float(cfg.get("exposure_cap"))
    except (TypeError, ValueError):
        return CAUTIOUS_EXPOSURE
    if cap <= 0:
        return CAUTIOUS_EXPOSURE
    return min(cap, FULL_EXPOSURE)


def clamp_buy_qty(service, account_id: int, ts_code: str, price: float, requested: float) -> int:
    """把一笔买入收进单票 20% 和当日总仓位上限。"""
    cfg = service._cfg()
    today = datetime.now(SH).date()
    cap = current_exposure_cap(cfg, today)
    single = float(cfg.get("max_single_weight") or SINGLE_WEIGHT)
    acct = paper_broker_service.get_account(account_id, mark=False)
    if not acct.get("ok"):
        return 0
    account = acct.get("account") or {}
    positions = acct.get("positions") or []
    book_mv = 0.0
    name_mv = 0.0
    for p in positions:
        px = price if p.get("ts_code") == ts_code else _f(p.get("market_price") or p.get("avg_cost"))
        mv = _f(p.get("quantity")) * px
        book_mv += mv
        if p.get("ts_code") == ts_code:
            name_mv = mv
    cash = _f(account.get("cash"))
    equity = cash + book_mv
    allowed = max_buy_shares(equity, cash, price, name_mv, book_mv, cap, single)
    want = int(max(requested, 0) // 100) * 100
    if want <= 0:
        want = allowed
    return min(want, allowed)


def _daily_context(engine, ts_code: str, today: date, price: float) -> Dict[str, Any]:
    with engine.connect() as conn:
        rows = conn.execute(
            sa_text(
                """
                SELECT close FROM stock_daily
                WHERE ts_code = :c AND trade_date < :d AND close IS NOT NULL
                ORDER BY trade_date DESC
                LIMIT 5
                """
            ),
            {"c": ts_code, "d": today},
        ).fetchall()
    closes = [float(r.close) for r in rows]
    ma5 = round(sum(closes) / len(closes), 4) if len(closes) >= 5 else None
    ret5 = None
    if closes and closes[-1] > 0 and price > 0:
        ret5 = round(price / closes[-1] - 1.0, 4)
    return {"ma5": ma5, "ret5": ret5, "rule_prob": rule_up_probability(price, ma5, ret5)}


def _live_price(engine, ts_code: str, today: date, fallback: float) -> float:
    try:
        from server.paper_quote import last_intraday_quote

        snap = last_intraday_quote(ts_code, trade_date=today.isoformat())
        px = _f(snap.get("price"))
        if snap.get("ok") and px > 0:
            return px
        logger.warning("无分时价 %s: %s", ts_code, snap.get("error"))
    except Exception as e:
        logger.warning("读分时价失败 %s: %s", ts_code, e)
    return 0.0


def _book(positions: List[Dict[str, Any]], prices: Dict[str, float]) -> Dict[str, Any]:
    mv = 0.0
    out = []
    for p in positions:
        code = p["ts_code"]
        qty = _f(p.get("quantity"))
        px = prices.get(code) or _f(p.get("market_price") or p.get("avg_cost"))
        row_mv = qty * px
        mv += row_mv
        out.append({**p, "price": px, "mv": row_mv})
    return {"positions": out, "market_value": mv}


async def run_afternoon_exposure(
    service,
    account_id: int,
    asof: Optional[str] = None,
    *,
    use_llm: bool = True,
) -> Dict[str, Any]:
    """13:00：给持仓估明天上涨概率，并据此补满或把仓位降到 80%。"""
    service._ensure()
    cfg = service._cfg()
    today = date.fromisoformat(str(asof)[:10]) if asof else datetime.now(SH).date()
    model = str(cfg.get("model") or "deepseek-chat")
    single = float(cfg.get("max_single_weight") or SINGLE_WEIGHT)

    acct = paper_broker_service.get_account(account_id, mark=True)
    if not acct.get("ok"):
        return {"ok": False, "error": acct.get("error") or "账户不存在"}
    positions = list(acct.get("positions") or [])
    prices: Dict[str, float] = {}
    scored: List[Dict[str, Any]] = []
    for p in positions:
        code = p["ts_code"]
        fallback = _f(p.get("market_price") or p.get("avg_cost"))
        px = _live_price(service.engine, code, today, fallback)
        prices[code] = px
        ctx = _daily_context(service.engine, code, today, px)
        scored.append(
            {
                "ts_code": code,
                "quantity": _f(p.get("quantity")),
                "sellable_qty": _f(p.get("sellable_qty")),
                "price": px,
                "mv": _f(p.get("quantity")) * px,
                "ma5": ctx["ma5"],
                "ret5": ctx["ret5"],
                "probability": ctx["rule_prob"],
                "reason": "规则：相对5日均线与近5日涨跌",
            }
        )

    llm_raw = ""
    if use_llm and scored:
        prompt = (
            f"现在是 {today.isoformat()} 下午开盘附近。请判断这些持仓明天收盘高于当前价的概率。\n"
            f"持仓：{scored}\n"
            "只输出 JSON：portfolio_probability(0到1), "
            "names:[{ts_code, probability, reason}]。"
            "probability 大于 0.6 表示明天更可能上涨。"
        )
        data, llm_raw = await service._llm_decide(prompt, model)
        by_code = {}
        for n in data.get("names") or []:
            if isinstance(n, dict) and n.get("ts_code"):
                by_code[str(n["ts_code"]).upper()] = n
        for row in scored:
            hit = by_code.get(row["ts_code"].upper())
            if not hit or hit.get("probability") is None:
                continue
            try:
                prob = float(hit["probability"])
            except (TypeError, ValueError):
                continue
            row["probability"] = round(max(0.0, min(1.0, prob)), 4)
            if hit.get("reason"):
                row["reason"] = str(hit["reason"])

    weight = sum(r["mv"] for r in scored)
    if weight > 0:
        portfolio_prob = round(sum(r["probability"] * r["mv"] for r in scored) / weight, 4)
    else:
        portfolio_prob = None
    cap = exposure_for_probability(portfolio_prob)

    actions: List[Dict[str, Any]] = []
    if cap >= FULL_EXPOSURE - 1e-9 and scored:
        actions.extend(await _top_up(service, account_id, today, scored, prices, single, cap))
    elif cap < FULL_EXPOSURE and scored:
        actions.extend(await _trim(service, account_id, today, scored, prices, cap))
    elif not scored:
        actions.append({"action": "no_position", "reason": "没有持仓，总仓位先按 80% 封顶，留出 20% 现金"})

    service.update_config(
        {
            "exposure_cap": cap,
            "exposure_asof": today.isoformat(),
            "exposure_prob": portfolio_prob,
            "scheduler_last_exposure": today.isoformat(),
        }
    )

    summary = {
        "portfolio_probability": portfolio_prob,
        "exposure_cap": cap,
        "single_weight": single,
        "names": [
            {"ts_code": r["ts_code"], "probability": r["probability"], "reason": r["reason"]}
            for r in scored
        ],
        "mode": "full" if cap >= 0.999 else "keep_cash_20",
    }
    with service.engine.begin() as conn:
        run_id = service._create_run(
            conn, step="afternoon_exposure", asof=today, account_id=account_id, summary=summary
        )
        service._log_action(
            conn,
            run_id=run_id,
            account_id=account_id,
            asof=today,
            step="afternoon_exposure",
            action="full" if cap >= 0.999 else "keep20",
            reason=(
                f"明日上涨概率 {portfolio_prob}，仓位上限 {cap:.0%}"
                if portfolio_prob is not None
                else "无持仓，仓位上限 80%"
            ),
            market_context=summary,
            t0_summary={"names": summary["names"]},
            llm_raw=llm_raw,
        )
    if use_llm or actions:
        paper_broker_service.log_decision(
            account_id,
            user_query=f"afternoon_exposure {today.isoformat()}",
            assistant_summary=str(summary)[:2000],
            meta=summary,
        )
    if use_llm:
        try:
            from server.paper_activity_service import paper_activity_service

            paper_activity_service.log_llm_wake(
                account_id,
                observation={
                    "step": "afternoon_exposure",
                    "portfolio_probability": portfolio_prob,
                    "exposure_cap": cap,
                    "names": summary["names"],
                    "clock": datetime.now(SH).strftime("%H:%M"),
                },
                llm_raw=llm_raw,
                llm_model=model,
                action="full" if cap >= 0.999 else "keep20",
                run_id=run_id,
                detail=(
                    f"明日上涨概率 {portfolio_prob}，仓位上限 {cap:.0%}"
                    if portfolio_prob is not None
                    else "无持仓，仓位上限 80%"
                ),
                trade_date=today,
            )
        except Exception as e:
            logger.warning("仓位活动日志失败: %s", e)
    return {
        "ok": True,
        "step": "afternoon_exposure",
        "asof": today.isoformat(),
        "run_id": run_id,
        "portfolio_probability": portfolio_prob,
        "exposure_cap": cap,
        "actions": actions,
        "names": summary["names"],
    }


async def _top_up(service, account_id, today, scored, prices, single, cap) -> List[Dict[str, Any]]:
    """概率过线后，先把已有持仓补到单票 20%，还有现金再按 watchlist 补。"""
    from server.trade_watch_service import trade_watch_service

    actions: List[Dict[str, Any]] = []
    order = sorted(scored, key=lambda r: r["probability"], reverse=True)
    held = {r["ts_code"] for r in order}
    wl = trade_watch_service.list_watchlist(active_only=True).get("watchlist") or []
    for item in wl:
        code = item.get("ts_code")
        if not code or code in held:
            continue
        px = _live_price(service.engine, code, today, 0)
        if px <= 0:
            continue
        ctx = _daily_context(service.engine, code, today, px)
        if ctx["rule_prob"] < 0.5:
            continue
        order.append(
            {
                "ts_code": code,
                "quantity": 0,
                "sellable_qty": 0,
                "price": px,
                "mv": 0,
                "probability": ctx["rule_prob"],
                "reason": "满仓补位：watchlist",
            }
        )
        prices[code] = px
        held.add(code)

    for row in order:
        acct = paper_broker_service.get_account(account_id, mark=False)
        if not acct.get("ok"):
            break
        account = acct.get("account") or {}
        positions = acct.get("positions") or []
        book = _book(positions, prices)
        cash = _f(account.get("cash"))
        equity = cash + book["market_value"]
        name_mv = 0.0
        for p in book["positions"]:
            if p.get("ts_code") == row["ts_code"]:
                name_mv = p["mv"]
        qty = max_buy_shares(equity, cash, row["price"], name_mv, book["market_value"], cap, single)
        if qty < 100:
            continue
        res = paper_broker_service.place_order(
            account_id,
            row["ts_code"],
            "buy",
            qty,
            lookback_guard=False,
            signal_date=today.isoformat(),
            exec_price=row["price"],
            reason=f"[afternoon_exposure/topup] 明日上涨概率 {row['probability']:.0%}，补到单票{single:.0%}",
        )
        actions.append(
            {
                "ts_code": row["ts_code"],
                "action": "topup" if res.get("ok") else "topup_failed",
                "quantity": qty,
                "price": row["price"],
                "probability": row["probability"],
                "reason": row["reason"],
                "error": None if res.get("ok") else res.get("error"),
            }
        )
    return actions


async def _trim(service, account_id, today, scored, prices, cap) -> List[Dict[str, Any]]:
    """概率不够时，从上涨概率最低的可卖仓开始减，直到市值不超过总资产的 80%。"""
    actions: List[Dict[str, Any]] = []
    for _ in range(8):
        acct = paper_broker_service.get_account(account_id, mark=False)
        if not acct.get("ok"):
            break
        account = acct.get("account") or {}
        book = _book(acct.get("positions") or [], prices)
        cash = _f(account.get("cash"))
        equity = cash + book["market_value"]
        if equity <= 0 or book["market_value"] <= equity * cap + 1:
            break
        excess = book["market_value"] - equity * cap
        ranked = sorted(book["positions"], key=lambda p: _prob_of(scored, p.get("ts_code")))
        sold = False
        for p in ranked:
            sellable = _f(p.get("sellable_qty"))
            px = prices.get(p["ts_code"]) or _f(p.get("price"))
            if sellable < 100 or px <= 0:
                continue
            need = int(excess / px // 100 + 1) * 100
            qty = min(sellable // 100 * 100, need)
            if qty < 100:
                continue
            res = paper_broker_service.place_order(
                account_id,
                p["ts_code"],
                "sell",
                qty,
                lookback_guard=False,
                signal_date=today.isoformat(),
                exec_price=px,
                reason=f"[afternoon_exposure/trim] 明日上涨概率 {_prob_of(scored, p['ts_code']):.0%}，留出20%现金",
            )
            actions.append(
                {
                    "ts_code": p["ts_code"],
                    "action": "trim" if res.get("ok") else "trim_failed",
                    "quantity": qty,
                    "price": px,
                    "probability": _prob_of(scored, p["ts_code"]),
                    "error": None if res.get("ok") else res.get("error"),
                }
            )
            if res.get("ok"):
                sold = True
                break
        if not sold:
            actions.append({"action": "trim_blocked", "reason": "超过 80% 的部分在当日冻结仓，T+1 卖不掉"})
            break
    return actions


def _prob_of(scored: List[Dict[str, Any]], ts_code: str) -> float:
    for r in scored:
        if r.get("ts_code") == ts_code:
            return float(r.get("probability") or 0)
    return 0.0

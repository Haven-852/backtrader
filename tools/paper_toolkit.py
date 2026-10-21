"""
Paper Trade Toolkit - 纸面账户工具（供 Agent function calling）
封装 server.paper_broker_service + strategy perceive
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class PaperToolkit:
    def _svc(self):
        from server.paper_broker_service import paper_broker_service
        return paper_broker_service

    async def create_account(
        self,
        name: str = "交易Agent账户",
        initial_cash: float = 1_000_000,
        lookback_guard: bool = True,
    ) -> Dict[str, Any]:
        return self._svc().create_account(
            name=name,
            initial_cash=float(initial_cash),
            lookback_guard=bool(lookback_guard),
        )

    async def delete_account(self, account_id: int, hard: bool = True) -> Dict[str, Any]:
        return self._svc().delete_account(int(account_id), hard=bool(hard))

    async def roll_t1(
        self,
        account_id: Optional[int] = None,
        asof: Optional[str] = None,
        force: bool = False,
    ) -> Dict[str, Any]:
        return self._svc().roll_t1(
            account_id=int(account_id) if account_id is not None else None,
            asof=asof,
            force=bool(force),
        )

    async def list_accounts(self, status: str = "active") -> Dict[str, Any]:
        return self._svc().list_accounts(status=status or None)

    async def get_account(self, account_id: int, mark: bool = True) -> Dict[str, Any]:
        return self._svc().get_account(int(account_id), mark=bool(mark))

    async def list_positions(self, account_id: int) -> Dict[str, Any]:
        return self._svc().list_positions(int(account_id))

    async def place_order(
        self,
        account_id: int,
        ts_code: str,
        side: str,
        quantity: float,
        lookback_guard: Optional[bool] = None,
        signal_date: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._svc().place_order(
            account_id=int(account_id),
            ts_code=str(ts_code),
            side=str(side),
            quantity=float(quantity),
            lookback_guard=lookback_guard,
            signal_date=signal_date,
            reason=reason,
        )

    async def settle(
        self,
        account_id: Optional[int] = None,
        asof: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._svc().settle_pending(
            account_id=int(account_id) if account_id is not None else None,
            asof=asof,
        )

    async def mark(
        self,
        account_id: int,
        asof: Optional[str] = None,
        write_equity: bool = True,
    ) -> Dict[str, Any]:
        return self._svc().mark_to_market(
            int(account_id),
            asof=asof,
            write_equity=bool(write_equity),
        )

    async def get_stats(self, account_id: int) -> Dict[str, Any]:
        return self._svc().get_stats(int(account_id))

    async def list_orders(
        self,
        account_id: int,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> Dict[str, Any]:
        return self._svc().list_orders(int(account_id), status=status, limit=int(limit))

    async def list_fills(self, account_id: int, limit: int = 50) -> Dict[str, Any]:
        return self._svc().list_fills(int(account_id), limit=int(limit))

    async def get_market_brief(
        self,
        ts_code: str,
        limit: int = 120,
        features: Optional[str] = None,
    ) -> Dict[str, Any]:
        """感知市场：regime + 特征 + 近端 K 线摘要"""
        from server.strategy_service import strategy_service

        feat_list = None
        if features:
            feat_list = [f.strip() for f in str(features).split(",") if f.strip()]
        else:
            feat_list = ["ma", "rsi", "macd", "vol_pctile"]

        result = await strategy_service.perceive(
            ts_code=str(ts_code).strip().upper(),
            limit=int(limit or 120),
            features=feat_list,
        )
        if not result.get("ok"):
            return result

        # 压缩给 LLM：去掉过长序列
        brief = {
            "ok": True,
            "ts_code": result.get("ts_code"),
            "name": result.get("name"),
            "date_range": result.get("date_range"),
            "regime": result.get("regime"),
            "features": result.get("features"),
            "last_bar": (result.get("bars_preview") or [])[-1] if result.get("bars_preview") else None,
            "bars_tail": (result.get("bars_preview") or [])[-5:],
            "source": result.get("source"),
        }
        return brief

    async def log_decision(
        self,
        account_id: int,
        *,
        session_id: Optional[str] = None,
        user_query: Optional[str] = None,
        assistant_summary: Optional[str] = None,
        tool_trace: Optional[List[Any]] = None,
        order_ids: Optional[List[int]] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return self._svc().log_decision(
            account_id=int(account_id),
            session_id=session_id,
            user_query=user_query,
            assistant_summary=assistant_summary,
            tool_trace=tool_trace,
            order_ids=order_ids,
            meta=meta,
        )


paper_toolkit = PaperToolkit()

PAPER_TRADE_SYSTEM_PROMPT = """你是「纸面交易 Agent」，负责在模拟账户上完成：感知 → 决策 → 执行 → 反馈。

## 硬性规则
1. **禁止编造行情、持仓、成交价**；缺数据必须先调用工具。
2. **禁止口头宣称已下单**；买卖必须调用 `paper_trade__place_order`，以工具返回为准。
3. 默认使用账户的 lookback_guard：True=次日开盘成交（挂 pending），False=当日收盘立即成交。
4. 买入数量必须是 100 的整数倍；**卖出不得超过 sellable_qty（T+1 可卖）**，当日买入在 frozen_qty，次日 `roll_t1` 后才可卖。
5. 每次决策用中文说明：感知结论 → 交易计划（标的/方向/数量/理由）→ 执行结果 → 风险与下一步。
6. 不确定时先 `get_market_brief` 与 `get_account`/`list_positions`，再决定是否下单。
7. 若用户只是询问账户或复盘，不要强行交易。
8. 做 T 时：卖的是旧仓（sellable），买的是新仓（frozen）；不可假设当日买入可当日卖出。

## 推荐工作流
1. `get_account` / `list_positions` / `get_stats` 了解账户状态
2. `get_market_brief` 感知目标标的（可多标的多次调用）
3. 给出明确决策；需要交易则 `place_order`（reason 必填）
4. 若有 pending（次日开盘），提示用户稍后 `settle`；可主动 `mark` 更新净值
5. 用成交/持仓变化做反馈总结

## 输出风格
简洁、可审计：先结论后依据；列出工具已确认的关键数字（现金、成交价、数量）。
"""

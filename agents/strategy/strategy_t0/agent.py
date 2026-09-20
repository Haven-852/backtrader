"""日内做 T 策略智能体：把四法包装成可被编排器调用的 Agent。"""

from __future__ import annotations

from typing import Any, Dict

from agents.base_agent import BaseAgent

from .tools import execute_sync


class IntradayT0Agent(BaseAgent):
    """日内做T策略：方法 A/B/C/D 与 combo 对比。"""

    def __init__(self, name: str = "intraday_t0"):
        super().__init__(
            name=name,
            role="Intraday T0",
            description="无盘口日内做T：回撤时间、冲击衰竭、U型量时钟、峰岭谷",
        )

    def run(self, task: str, context: Dict = None) -> Dict:
        ctx = context or {}
        tool_id = ctx.get("tool_id") or ctx.get("method") or "combo-run"
        payload = ctx.get("payload") or ctx.get("params") or {}
        if "ts_codes" not in payload and ctx.get("ts_codes"):
            payload = {**payload, "ts_codes": ctx.get("ts_codes")}
        if task and "ts_codes" not in payload and not payload:
            payload = {"ts_codes": task}
        result = execute_sync(str(tool_id), payload)
        return {
            "agent": self.name,
            "success": bool(result.get("ok")),
            "response": result.get("method_title") or result.get("skill") or "日内做T策略",
            "result": result,
        }

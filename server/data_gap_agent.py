"""缺数时只做一件事：把缺口告诉大模型，由它调用工具写入，再核对是否补上。

业务代码不要自己猜行情。发现缺什么，就调用 fill_by_agent，并给出核对函数。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

GapCheck = Callable[[], Dict[str, Any]]


def _trace(usage: Dict[str, Any]) -> List[Dict[str, Any]]:
    called = []
    for step in usage.get("tool_trace") or []:
        if not isinstance(step, dict):
            continue
        called.append({
            "function": step.get("function"),
            "arguments": step.get("arguments"),
            "ok": step.get("result_ok"),
        })
    return called


async def fill_by_agent(
    task: str,
    verify: GapCheck,
    *,
    model: str = "deepseek-flash",
    skills: Optional[List[str]] = None,
    max_tokens: int = 1500,
) -> Dict[str, Any]:
    """task 要写清缺什么、该调哪个工具。verify 在调用前后各跑一次。"""
    before = verify()
    if before.get("complete"):
        return {
            "ok": True,
            "filled": False,
            "missing": before.get("missing") or [],
            "note": before.get("detail") or "数据已经在库里，没有调用大模型。",
            "assistant": "",
            "tools": [],
        }
    from server.chat_service import ChatRequest
    from server.tool_agent_service import tool_agent_service

    resp = await tool_agent_service.chat_with_tools(ChatRequest(
        query=task,
        model=model,
        provider="deepseek",
        temperature=0.1,
        max_tokens=max_tokens,
        tool_skills=skills or ["datahubco-data"],
        system_prompt=(
            "你负责补齐数据库里缺少的行情或成分。只能调用任务里点名的工具，并把结果写入数据库。"
            "工具没有返回的数字不要编造。失败时原样说明原因。"
        ),
    ))
    usage = resp.usage or {}
    after = verify()
    return {
        "ok": True,
        "filled": True,
        "requested": before.get("missing") or [],
        "still_missing": after.get("missing") or [],
        "complete": bool(after.get("complete")),
        "assistant": resp.content,
        "tools": _trace(usage),
        "tools_enabled": usage.get("tools_enabled"),
    }

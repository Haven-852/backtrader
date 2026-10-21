"""
Tool Agent Service
大模型缺数据时：阅读 tools/HANDBOOK.md → 选择工具 → 执行 → 回填结果 → 再回答
支持 agent_mode=paper_trade：仅纸面交易工具 + 专用 system prompt
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import httpx

import tools
from tools.executor import execute_tool
from server.chat_service import ChatRequest, ChatResponse, chat_service

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 8
MAX_TOOL_RESULT_CHARS = 24000

# paper 工具中若缺 account_id，可从 context 注入
_PAPER_ACCOUNT_TOOLS = {
    "get-account",
    "list-positions",
    "place-order",
    "settle",
    "mark",
    "get-stats",
    "list-orders",
    "list-fills",
    "roll-t1",
}


def _truncate_result(obj: Any, max_chars: int = MAX_TOOL_RESULT_CHARS) -> str:
    try:
        text = json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        text = str(obj)
    if len(text) > max_chars:
        return text[:max_chars] + "...(截断)"
    return text


def _extract_order_ids(tool_trace: List[Dict[str, Any]]) -> List[int]:
    ids: List[int] = []
    for step in tool_trace or []:
        if step.get("order_id") is not None:
            try:
                ids.append(int(step["order_id"]))
                continue
            except Exception:
                pass
        if step.get("tool_id") != "place-order" and "place_order" not in str(step.get("function") or ""):
            continue
        preview = step.get("result_preview") or ""
        try:
            data = json.loads(preview) if isinstance(preview, str) and preview.startswith("{") else None
        except Exception:
            data = None
        if isinstance(data, dict):
            order = data.get("order") or {}
            oid = order.get("id")
            if oid is not None:
                try:
                    ids.append(int(oid))
                except Exception:
                    pass
    # 去重保序
    seen = set()
    out: List[int] = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


class ToolAgentService:
    """带工具循环的对话服务（OpenAI 兼容 function calling）"""

    def _resolve_mode(self, request: ChatRequest) -> tuple[Optional[str], Optional[List[str]], str]:
        """返回 (mode, skill_ids, system_prompt)"""
        mode = (request.agent_mode or "").strip() or None
        skill_ids = list(request.tool_skills) if request.tool_skills else None
        ctx = request.context or {}
        extra = request.system_prompt

        if mode == "paper_trade":
            skill_ids = skill_ids or ["paper-trade"]
            account_id = ctx.get("account_id")
            extra_bits = []
            if extra:
                extra_bits.append(extra)
            if account_id is not None:
                extra_bits.append(
                    f"当前绑定纸面账户 account_id={account_id}。"
                    f"调用需要 account_id 的工具时，若未指定则使用该账户。"
                )
            system_prompt = tools.build_agent_system_prompt(
                "\n".join(extra_bits) if extra_bits else None,
                skill_ids=skill_ids,
                mode="paper_trade",
            )
            return mode, skill_ids, system_prompt

        system_prompt = tools.build_agent_system_prompt(extra, skill_ids=skill_ids)
        return mode, skill_ids, system_prompt

    def _inject_context_args(
        self,
        skill_id: str,
        tool_id: str,
        args: Dict[str, Any],
        context: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        ctx = context or {}
        out = dict(args or {})
        if skill_id == "paper-trade" and tool_id in _PAPER_ACCOUNT_TOOLS:
            if out.get("account_id") is None and ctx.get("account_id") is not None:
                out["account_id"] = ctx["account_id"]
        return out

    async def chat_with_tools(self, request: ChatRequest) -> ChatResponse:
        provider, actual_model = chat_service._resolve_provider(request.model)

        if not provider:
            return await chat_service.chat(request)

        mode, skill_ids, system_prompt = self._resolve_mode(request)

        # Anthropic / Ollama：暂用「手册注入 + 普通对话」，不走原生 tools
        from server.chat_service import AnthropicProvider, OllamaProvider
        if isinstance(provider, (AnthropicProvider, OllamaProvider)):
            patched = ChatRequest(
                query=request.query,
                model=request.model,
                provider=request.provider,
                stream=False,
                history=request.history,
                system_prompt=system_prompt + (
                    "\n当前通道不支持原生 function calling。"
                    "请用文字说明你想调用的工具与参数，或建议切换 DeepSeek / OpenAI。"
                ),
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                agent_mode=request.agent_mode,
                tool_skills=skill_ids,
                context=request.context,
            )
            resp = await chat_service.chat(patched)
            resp.usage = {**(resp.usage or {}), "tools_enabled": False, "reason": "provider_no_tools", "agent_mode": mode}
            return resp

        openai_tools = tools.get_openai_tools(skill_ids=skill_ids)
        if not openai_tools:
            out = await chat_service.chat(
                ChatRequest(
                    query=request.query,
                    model=request.model,
                    provider=request.provider,
                    history=request.history,
                    system_prompt=system_prompt,
                    temperature=request.temperature,
                    max_tokens=request.max_tokens,
                )
            )
            out.usage = {**(out.usage or {}), "tools_enabled": False, "reason": "no_tools"}
            return out

        messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        for h in (request.history or [])[-10:]:
            role = h.get("role", "user")
            if role not in ("user", "assistant", "system", "tool"):
                role = "user"
            messages.append({"role": role, "content": h.get("content", "")})
        messages.append({"role": "user", "content": request.query})

        tool_trace: List[Dict[str, Any]] = []
        total_usage: Dict[str, Any] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        async with httpx.AsyncClient(timeout=180.0) as client:
            for round_i in range(MAX_TOOL_ROUNDS):
                payload = {
                    "model": actual_model,
                    "messages": messages,
                    "temperature": request.temperature,
                    "max_tokens": request.max_tokens,
                    "tools": openai_tools,
                    "tool_choice": "auto",
                }
                resp = await client.post(
                    provider.get_chat_url(),
                    headers=provider.get_headers(),
                    json=payload,
                )
                if resp.status_code >= 400:
                    err_text = resp.text[:300]
                    logger.warning(f"tools 调用失败，降级普通对话: {resp.status_code} {err_text}")
                    fallback = ChatRequest(
                        query=request.query,
                        model=request.model,
                        provider=request.provider,
                        history=request.history,
                        system_prompt=system_prompt,
                        temperature=request.temperature,
                        max_tokens=request.max_tokens,
                    )
                    out = await chat_service.chat(fallback)
                    out.usage = {
                        **(out.usage or {}),
                        "tools_enabled": False,
                        "fallback": True,
                        "tool_error": err_text,
                        "tool_trace": tool_trace,
                        "agent_mode": mode,
                    }
                    return out

                data = resp.json()
                usage = data.get("usage") or {}
                for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    total_usage[k] = total_usage.get(k, 0) + int(usage.get(k) or 0)

                choice = (data.get("choices") or [{}])[0]
                message = choice.get("message") or {}
                finish = choice.get("finish_reason") or "stop"
                tool_calls = message.get("tool_calls") or []

                if not tool_calls or finish == "stop":
                    content = message.get("content") or ""
                    if not content and tool_trace:
                        content = "已完成工具查询，但模型未生成文字回复。请查看工具轨迹。"
                    return ChatResponse(
                        content=content,
                        model=data.get("model", actual_model),
                        provider=request.provider,
                        usage={
                            **total_usage,
                            "tools_enabled": True,
                            "tool_rounds": round_i + 1,
                            "tool_trace": tool_trace,
                            "agent_mode": mode,
                            "order_ids": _extract_order_ids(tool_trace),
                        },
                        finish_reason=finish,
                    )

                messages.append({
                    "role": "assistant",
                    "content": message.get("content"),
                    "tool_calls": tool_calls,
                })

                for tc in tool_calls:
                    fn = (tc.get("function") or {})
                    fn_name = fn.get("name") or ""
                    raw_args = fn.get("arguments") or "{}"
                    try:
                        args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                    except Exception:
                        args = {}

                    skill_id, tool_id = tools.parse_fn_name(fn_name)
                    args = self._inject_context_args(skill_id or "", tool_id or "", args, request.context)
                    step = {
                        "round": round_i + 1,
                        "id": tc.get("id"),
                        "function": fn_name,
                        "skill_id": skill_id,
                        "tool_id": tool_id,
                        "arguments": args,
                    }

                    if not skill_id or not tool_id:
                        result = {"ok": False, "error": f"无法解析工具名: {fn_name}"}
                    else:
                        try:
                            result = await execute_tool(skill_id, tool_id, args)
                        except Exception as e:
                            logger.exception("工具执行异常")
                            result = {"ok": False, "error": str(e)}

                    step["result_ok"] = bool(result.get("ok", True)) if isinstance(result, dict) else True
                    # 保存完整结果摘要供 order_id 提取（place-order）
                    if isinstance(result, dict) and tool_id == "place-order" and result.get("order"):
                        step["order_id"] = (result.get("order") or {}).get("id")
                    if isinstance(result, dict) and result.get("memory_draft"):
                        step["memory_draft"] = result["memory_draft"]
                    step["result_preview"] = _truncate_result(result, 800)
                    tool_trace.append(step)

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id"),
                        "content": _truncate_result(result),
                    })

            if tool_trace:
                messages.append({
                    "role": "user",
                    "content": (
                        "工具查询已经结束。请根据上面的工具结果，用中文直接回答用户问题。"
                        "不要再调用任何工具。若结果不完整，说明缺什么，仍要给出已有结论。"
                    ),
                })
                payload = {
                    "model": actual_model,
                    "messages": messages,
                    "temperature": request.temperature,
                    "max_tokens": request.max_tokens,
                    "tool_choice": "none",
                }
                try:
                    resp = await client.post(
                        provider.get_chat_url(),
                        headers=provider.get_headers(),
                        json=payload,
                    )
                    if resp.status_code < 400:
                        data = resp.json()
                        usage = data.get("usage") or {}
                        for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
                            total_usage[k] = total_usage.get(k, 0) + int(usage.get(k) or 0)
                        choice = (data.get("choices") or [{}])[0]
                        content = (choice.get("message") or {}).get("content") or ""
                        if content.strip():
                            return ChatResponse(
                                content=content,
                                model=data.get("model", actual_model),
                                provider=request.provider,
                                usage={
                                    **total_usage,
                                    "tools_enabled": True,
                                    "tool_trace": tool_trace,
                                    "tool_rounds": MAX_TOOL_ROUNDS,
                                    "synthesized_after_limit": True,
                                    "agent_mode": mode,
                                    "order_ids": _extract_order_ids(tool_trace),
                                },
                                finish_reason=choice.get("finish_reason") or "stop",
                            )
                except Exception:
                    logger.exception("轮次用尽后的文字总结失败")

            return ChatResponse(
                content="工具调用轮次已达上限，请缩小问题范围后重试。",
                model=actual_model,
                provider=request.provider,
                usage={
                    **total_usage,
                    "tools_enabled": True,
                    "tool_trace": tool_trace,
                    "tool_rounds": MAX_TOOL_ROUNDS,
                    "agent_mode": mode,
                    "order_ids": _extract_order_ids(tool_trace),
                },
                finish_reason="length",
            )


tool_agent_service = ToolAgentService()

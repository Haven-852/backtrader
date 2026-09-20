"""
Tool Agent Service
大模型缺数据时：阅读 tools/HANDBOOK.md → 选择工具 → 执行 → 回填结果 → 再回答
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


def _truncate_result(obj: Any, max_chars: int = MAX_TOOL_RESULT_CHARS) -> str:
    try:
        text = json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        text = str(obj)
    if len(text) > max_chars:
        return text[:max_chars] + "...(截断)"
    return text


class ToolAgentService:
    """带工具循环的对话服务（OpenAI 兼容 function calling）"""

    async def chat_with_tools(self, request: ChatRequest) -> ChatResponse:
        provider, actual_model = chat_service._resolve_provider(request.model)

        if not provider:
            # 无 API Key：退回普通 chat（会走 fallback）
            return await chat_service.chat(request)

        # Anthropic / Ollama：暂用「手册注入 + 普通对话」，不走原生 tools
        from server.chat_service import AnthropicProvider, OllamaProvider
        if isinstance(provider, (AnthropicProvider, OllamaProvider)):
            sys_prompt = tools.build_agent_system_prompt(request.system_prompt)
            sys_prompt += (
                "\n当前通道不支持原生 function calling。"
                "若需查数，请明确告诉用户去右侧技能面板手动执行 sql-toolkit 工具，"
                "或建议切换 DeepSeek / OpenAI 模型以启用自动工具调用。"
            )
            patched = ChatRequest(
                query=request.query,
                model=request.model,
                provider=request.provider,
                stream=False,
                history=request.history,
                system_prompt=sys_prompt,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
            )
            resp = await chat_service.chat(patched)
            resp.usage = {**(resp.usage or {}), "tools_enabled": False, "reason": "provider_no_tools"}
            return resp

        system_prompt = tools.build_agent_system_prompt(request.system_prompt)
        openai_tools = tools.get_openai_tools()

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
                    # 部分模型不支持 tools：降级普通对话
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

                # 无工具调用 → 最终回答
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
                        },
                        finish_reason=finish,
                    )

                # 记录 assistant tool_calls 消息
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
                    step["result_preview"] = _truncate_result(result, 500)
                    tool_trace.append(step)

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id"),
                        "content": _truncate_result(result),
                    })

            # 工具轮次用尽：再要一轮纯文字结论，避免「已经查到却不回答」
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
                                },
                                finish_reason=choice.get("finish_reason") or "stop",
                            )
                except Exception:
                    logger.exception("轮次用尽后的文字总结失败")

            return ChatResponse(
                content="工具调用轮次已达上限，请缩小问题范围后重试。",
                model=actual_model,
                provider=request.provider,
                usage={**total_usage, "tools_enabled": True, "tool_trace": tool_trace, "tool_rounds": MAX_TOOL_ROUNDS},
                finish_reason="length",
            )


tool_agent_service = ToolAgentService()

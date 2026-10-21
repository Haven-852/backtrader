"""
Chat Service - 多模型对话服务层
支持 OpenAI / DeepSeek / Anthropic / Ollama / Grok 等多模型统一对话
负责：模型路由、消息历史管理、流式响应、上下文拼接

参考 Yuxi 项目 package/yuxi/services/chat_service.py
"""

import os
import json
import logging
from typing import AsyncGenerator, Dict, Any, Optional, List
from datetime import datetime
from dataclasses import dataclass, field

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


# ─── 数据模型 ─────────────────────────────────────────

@dataclass
class ChatMessage:
    role: str  # "user" | "assistant" | "system"
    content: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict:
        return {"role": self.role, "content": self.content}

    def to_openai_format(self) -> Dict:
        return {"role": self.role, "content": self.content}


@dataclass
class ChatRequest:
    query: str
    model: str = "deepseek"
    provider: str = "deepseek"  # openai, deepseek, anthropic, ollama, grok
    stream: bool = False
    history: List[Dict] = field(default_factory=list)
    system_prompt: Optional[str] = None
    temperature: float = 0.7
    max_tokens: int = 4096
    thinking: Optional[str] = None
    # 交易 Agent：paper_trade 模式仅暴露纸面工具
    agent_mode: Optional[str] = None  # "paper_trade" | None
    tool_skills: Optional[List[str]] = None
    context: Dict = field(default_factory=dict)  # e.g. {"account_id": 1}


@dataclass
class ChatResponse:
    content: str
    model: str
    provider: str
    usage: Dict = field(default_factory=dict)
    finish_reason: str = "stop"
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


# ─── 模型提供商配置 ───────────────────────────────────

class ModelProvider:
    """模型提供商基类"""

    def __init__(self, api_key: str, base_url: str, default_model: str):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.default_model = default_model

    def get_headers(self) -> Dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def get_chat_url(self) -> str:
        return f"{self.base_url}/chat/completions"


class OpenAIProvider(ModelProvider):
    """OpenAI 兼容提供商 (GPT-4, GPT-4o, etc.)"""
    pass


class DeepSeekProvider(ModelProvider):
    """DeepSeek 提供商"""
    pass


class AnthropicProvider(ModelProvider):
    """Anthropic Claude 提供商"""

    def get_headers(self) -> Dict:
        return {
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }

    def get_chat_url(self) -> str:
        return f"{self.base_url}/messages"


class OllamaProvider(ModelProvider):
    """Ollama 本地模型提供商"""

    def __init__(self, api_key: str, base_url: str, default_model: str):
        # 兼容 OLLAMA_BASE_URL=http://localhost:11434 与 .../api
        super().__init__(api_key, self._normalize_base(base_url), default_model)

    @staticmethod
    def _normalize_base(base_url: str) -> str:
        url = (base_url or "http://localhost:11434").rstrip("/")
        if url.endswith("/api"):
            return url
        # 常见误配：写成根地址，正确聊天接口是 /api/chat
        return f"{url}/api"

    def get_headers(self) -> Dict:
        return {"Content-Type": "application/json"}

    def get_chat_url(self) -> str:
        return f"{self.base_url}/chat"


class GrokProvider(ModelProvider):
    """Grok (xAI) 提供商"""

    def get_chat_url(self) -> str:
        return f"{self.base_url}/chat/completions"


# ─── Chat Service ──────────────────────────────────────

class ChatService:
    """
    多模型对话服务
    统一接口，支持 OpenAI / DeepSeek / Anthropic / Ollama / Grok
    """

    # 支持的模型及其提供商映射
    MODEL_PROVIDER_MAP = {
        # OpenAI 系列
        "gpt-4o": "openai",
        "gpt-4o-mini": "openai",
        "gpt-4-turbo": "openai",
        "gpt-3.5-turbo": "openai",
        # DeepSeek 系列
        "deepseek-chat": "deepseek",
        "deepseek-reasoner": "deepseek",
        "deepseek-r1": "deepseek",
        "deepseek-flash": "deepseek",
        "deepseek-v4-pro": "deepseek",
        "deepseek": "deepseek",
        # Anthropic 系列
        "claude-3-opus": "anthropic",
        "claude-3-sonnet": "anthropic",
        "claude-3-haiku": "anthropic",
        # Ollama 本地
        "ollama": "ollama",
        "qwen": "ollama",
        "qwen3": "ollama",
        "qwen3:8b": "ollama",
        "qwen3-coder:30b": "ollama",
        "deepseek-r1:8b": "ollama",
        "llama": "ollama",
        "mistral": "ollama",
        "nomic-embed-text": "ollama",
        "nomic-embed-text:latest": "ollama",
        # Grok
        "grok-1": "grok",
        "grok-2": "grok",
    }

    AVAILABLE_MODELS = [
        {"id": "gpt-4o", "name": "GPT-4o", "provider": "openai", "type": "chat", "description": "OpenAI 最新多模态模型"},
        {"id": "gpt-4o-mini", "name": "GPT-4o Mini", "provider": "openai", "type": "chat", "description": "轻量高速 OpenAI 模型"},
        {"id": "deepseek-chat", "name": "DeepSeek Chat", "provider": "deepseek", "type": "chat", "description": "DeepSeek 通用对话模型"},
        {"id": "deepseek-reasoner", "name": "DeepSeek Reasoner", "provider": "deepseek", "type": "chat", "description": "DeepSeek 推理增强模型"},
        {"id": "claude-3-opus", "name": "Claude 3 Opus", "provider": "anthropic", "type": "chat", "description": "Anthropic 最强模型"},
        {"id": "claude-3-sonnet", "name": "Claude 3 Sonnet", "provider": "anthropic", "type": "chat", "description": "平衡性能与速度"},
        {"id": "ollama", "name": "Ollama 本地模型", "provider": "ollama", "type": "chat", "description": "本地运行的大模型"},
        {"id": "grok-2", "name": "Grok 2", "provider": "grok", "type": "chat", "description": "xAI Grok 模型"},
    ]

    def __init__(self):
        self._providers: Dict[str, ModelProvider] = {}
        self._init_providers()

    def _init_providers(self):
        """从环境变量初始化所有模型提供商"""
        # OpenAI
        openai_key = os.getenv("OPENAI_API_KEY", "")
        if openai_key and openai_key not in ("demo-key", "sk-your-openai-api-key-here"):
            self._providers["openai"] = OpenAIProvider(
                api_key=openai_key,
                base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                default_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            )

        # DeepSeek
        deepseek_key = os.getenv("DEEPSEEK_API_KEY", "")
        deepseek_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
        if deepseek_key and deepseek_key not in ("demo-key", "sk-your-deepseek-api-key-here"):
            self._providers["deepseek"] = DeepSeekProvider(
                api_key=deepseek_key,
                base_url=deepseek_url,
                default_model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
            )

        # Anthropic
        anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
        if anthropic_key and anthropic_key not in ("demo-key", "sk-your-anthropic-key-here"):
            self._providers["anthropic"] = AnthropicProvider(
                api_key=anthropic_key,
                base_url=os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
                default_model="claude-3-sonnet",
            )

        # Ollama (本地模型，不需要 API key)
        ollama_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/api")
        self._providers["ollama"] = OllamaProvider(
            api_key="",  # Ollama 不需要 API key
            base_url=ollama_url,
            default_model=os.getenv("OLLAMA_MODEL", "deepseek-r1:8b"),
        )

        # Grok (xAI)
        grok_key = os.getenv("GROK_API_KEY", "")
        if grok_key and grok_key not in ("demo-key", "your-grok-api-key-here"):
            self._providers["grok"] = GrokProvider(
                api_key=grok_key,
                base_url=os.getenv("GROK_BASE_URL", "https://api.x.ai/v1"),
                default_model="grok-2",
            )

        logger.info(f"ChatService 已初始化，可用提供商: {list(self._providers.keys())}")

    def _resolve_provider(self, model: str) -> tuple[Optional[ModelProvider], str]:
        """根据模型名解析提供商和实际模型名"""
        provider_name = self.MODEL_PROVIDER_MAP.get(model, "ollama")
        provider = self._providers.get(provider_name)

        if provider and provider_name == "ollama":
            # Ollama：使用配置的模型名；UI 选「ollama」时用默认本地模型
            actual_model = os.getenv("OLLAMA_MODEL", "deepseek-r1:8b")
            if model not in ("ollama", "qwen", "llama", "mistral"):
                actual_model = model
            # 带冒号的是 Ollama 完整模型名（如 qwen3:8b）
            if ":" in (model or ""):
                actual_model = model
        elif provider:
            actual_model = provider.default_model if model in (provider_name, "deepseek", "openai") else model
        else:
            actual_model = model

        return provider, actual_model

    async def get_available_models(self) -> List[Dict]:
        """获取可用模型列表（标记哪些已配置）"""
        models = []
        for m in self.AVAILABLE_MODELS:
            model = dict(m)
            provider_name = self.MODEL_PROVIDER_MAP.get(m["id"], "ollama")
            model["available"] = provider_name in self._providers
            model["default"] = m["id"] == "deepseek-chat"
            models.append(model)
        return models

    async def get_available_providers(self) -> List[Dict]:
        """获取已配置的模型提供商列表"""
        return [
            {
                "id": name,
                "available": True,
                "default_model": p.default_model,
                "base_url": p.base_url,
            }
            for name, p in self._providers.items()
        ]

    async def chat(self, request: ChatRequest) -> ChatResponse:
        """同步对话（非流式）"""
        provider, actual_model = self._resolve_provider(request.model)

        if not provider:
            return ChatResponse(
                content=self._fallback_response(request.query, request.model),
                model=request.model,
                provider="fallback",
                usage={"note": "未配置 API Key，返回模拟回复"},
            )

        # 构造消息
        messages = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        for h in request.history[-10:]:  # 最近10条历史
            messages.append({"role": h.get("role", "user"), "content": h.get("content", "")})
        messages.append({"role": "user", "content": request.query})

        try:
            if isinstance(provider, AnthropicProvider):
                return await self._chat_anthropic(provider, actual_model, messages, request)
            elif isinstance(provider, OllamaProvider):
                return await self._chat_ollama(provider, actual_model, messages, request)
            else:
                # OpenAI 兼容格式 (OpenAI, DeepSeek, Grok)
                return await self._chat_openai_compatible(provider, actual_model, messages, request)
        except Exception as e:
            logger.error(f"模型调用失败 [{request.model}]: {e}")
            err = str(e)
            hint = ""
            if "11434" in err or "/chat" in err or "ollama" in err.lower() or "502" in err:
                hint = (
                    "\n\n**Ollama 排查**：\n"
                    "1) 确认 `ollama serve` / Ollama 应用已运行；\n"
                    "2) 接口应为 `http://localhost:11434/api/chat`（`.env` 的 `OLLAMA_BASE_URL` "
                    "可写 `http://localhost:11434`，代码会自动补 `/api`）；\n"
                    "3) 若仍 502，多为系统 HTTP 代理劫持 localhost——本服务已对 Ollama 禁用代理；"
                    "请重启后端后再试；\n"
                    f"4) 默认模型 `{os.getenv('OLLAMA_MODEL', 'deepseek-r1:8b')}`，用 `ollama list` 确认已安装。"
                )
            elif "401" in err or "403" in err:
                hint = "\n\n请检查对应提供商的 API Key 是否有效。"
            return ChatResponse(
                content=(
                    f"模型调用失败: {err}\n\n"
                    f"已自动切换到模拟模式。请检查 API Key / 本地模型配置是否正确。{hint}"
                ),
                model=request.model,
                provider="error",
                usage={"error": err},
            )

    async def chat_stream(self, request: ChatRequest) -> AsyncGenerator[str, None]:
        """流式对话 - 使用 SSE 返回"""
        provider, actual_model = self._resolve_provider(request.model)

        if not provider:
            yield self._fallback_response(request.query, request.model)
            return

        messages = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        for h in request.history[-10:]:
            messages.append({"role": h.get("role", "user"), "content": h.get("content", "")})
        messages.append({"role": "user", "content": request.query})

        try:
            if isinstance(provider, OllamaProvider):
                async for chunk in self._stream_ollama(provider, actual_model, messages):
                    yield chunk
            else:
                async for chunk in self._stream_openai_compatible(provider, actual_model, messages, request):
                    yield chunk
        except Exception as e:
            logger.error(f"流式调用失败 [{request.model}]: {e}")
            yield f"\n\n[错误] 流式调用失败: {str(e)}"

    # ─── OpenAI 兼容 API ──────────────────────────────

    async def _chat_openai_compatible(
        self, provider: ModelProvider, model: str, messages: List[Dict], request: ChatRequest
    ) -> ChatResponse:
        """OpenAI 兼容格式的对话调用"""
        payload = {
            "model": model,
            "messages": messages,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }
        if request.thinking == "disabled":
            payload["thinking"] = {"type": "disabled"}
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                provider.get_chat_url(),
                headers=provider.get_headers(),
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            choice = data["choices"][0]
            message = choice.get("message") or {}
            content = message.get("content") or ""
            if not str(content).strip():
                content = message.get("reasoning_content") or ""
            return ChatResponse(
                content=content or "",
                model=data.get("model", model),
                provider=request.provider,
                usage=data.get("usage", {}),
                finish_reason=choice.get("finish_reason", "stop"),
            )

    async def _stream_openai_compatible(
        self, provider: ModelProvider, model: str, messages: List[Dict], request: ChatRequest
    ) -> AsyncGenerator[str, None]:
        """OpenAI 兼容格式的流式调用"""
        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream(
                "POST",
                provider.get_chat_url(),
                headers=provider.get_headers(),
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": request.temperature,
                    "max_tokens": request.max_tokens,
                    "stream": True,
                },
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str.strip() == "[DONE]":
                            break
                        try:
                            data = json.loads(data_str)
                            delta = data["choices"][0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                yield content
                        except json.JSONDecodeError:
                            continue

    # ─── Anthropic API ─────────────────────────────────

    async def _chat_anthropic(
        self, provider: AnthropicProvider, model: str, messages: List[Dict], request: ChatRequest
    ) -> ChatResponse:
        """Anthropic Claude 对话调用"""
        # Anthropic 格式转换：分离 system 消息
        system_msg = None
        anthropic_msgs = []
        for msg in messages:
            if msg["role"] == "system":
                system_msg = msg["content"]
            else:
                anthropic_msgs.append({"role": msg["role"], "content": msg["content"]})

        body = {
            "model": model,
            "messages": anthropic_msgs,
            "max_tokens": request.max_tokens,
        }
        if system_msg:
            body["system"] = system_msg

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                provider.get_chat_url(),
                headers=provider.get_headers(),
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()
            return ChatResponse(
                content=data["content"][0]["text"],
                model=data.get("model", model),
                provider="anthropic",
                usage={
                    "input_tokens": data.get("usage", {}).get("input_tokens", 0),
                    "output_tokens": data.get("usage", {}).get("output_tokens", 0),
                },
            )

    # ─── Ollama API ────────────────────────────────────

    async def _chat_ollama(
        self, provider: OllamaProvider, model: str, messages: List[Dict], request: ChatRequest
    ) -> ChatResponse:
        """Ollama 本地模型对话调用"""
        # trust_env=False：避免系统/环境 HTTP 代理把 localhost:11434 转到错误网关(502)
        # 推理模型（deepseek-r1 等）常把答案写在 thinking，需更大 num_predict
        num_predict = request.max_tokens
        if any(x in (model or "").lower() for x in ("r1", "reason", "think")):
            num_predict = max(int(num_predict or 0), 1024)
        async with httpx.AsyncClient(timeout=180.0, trust_env=False) as client:
            resp = await client.post(
                provider.get_chat_url(),
                json={
                    "model": model,
                    "messages": messages,
                    "stream": False,
                    "options": {
                        "temperature": request.temperature,
                        "num_predict": num_predict,
                    },
                },
            )
            resp.raise_for_status()
            data = resp.json()
            msg = data.get("message") or {}
            content = (msg.get("content") or "").strip()
            thinking = (msg.get("thinking") or "").strip()
            if not content and thinking:
                content = thinking
            elif content and thinking and thinking not in content:
                # 可选附带思考摘要（截断），避免前端空白
                pass
            return ChatResponse(
                content=content or "（模型未返回文本，请换模型或增大 max_tokens）",
                model=data.get("model", model),
                provider="ollama",
                usage={
                    "total_duration": data.get("total_duration", 0),
                    "eval_count": data.get("eval_count", 0),
                    "has_thinking": bool(thinking),
                },
            )

    async def _stream_ollama(
        self, provider: OllamaProvider, model: str, messages: List[Dict]
    ) -> AsyncGenerator[str, None]:
        """Ollama 流式调用"""
        async with httpx.AsyncClient(timeout=300.0, trust_env=False) as client:
            async with client.stream(
                "POST",
                provider.get_chat_url(),
                json={"model": model, "messages": messages, "stream": True},
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                        content = data.get("message", {}).get("content", "")
                        if content:
                            yield content
                        if data.get("done", False):
                            break
                    except json.JSONDecodeError:
                        continue

    # ─── 模拟回复 (fallback) ──────────────────────────

    def _fallback_response(self, query: str, model: str) -> str:
        """当没有配置 API Key 时的模拟回复"""
        model_display = {
            "deepseek-chat": "DeepSeek Chat",
            "deepseek-reasoner": "DeepSeek Reasoner",
            "gpt-4o": "GPT-4o",
            "gpt-4o-mini": "GPT-4o Mini",
            "claude-3-sonnet": "Claude 3 Sonnet",
            "claude-3-opus": "Claude 3 Opus",
            "grok-2": "Grok 2",
            "ollama": "Ollama 本地模型",
        }.get(model, model.upper())

        return f"""🤖 **{model_display}** (模拟模式)

收到您的查询: *"{query}"*

⚠️ **注意**：当前运行在模拟模式。
要启用真实模型对话，请在 `backend/.env` 中配置对应模型的 API Key：

- **DeepSeek**: 设置 `DEEPSEEK_API_KEY`
- **OpenAI**: 设置 `OPENAI_API_KEY`
- **Anthropic**: 设置 `ANTHROPIC_API_KEY`
- **Grok**: 设置 `GROK_API_KEY`
- **Ollama**: 启动本地 Ollama 服务 (默认端口 11434)

---

**量化平台状态**：
• 数据存储层：InfluxDB + PostgreSQL/TimescaleDB + Redis + MinIO
• 智能体系统：13 个专业 Agent 可用
• 回测引擎：Backtrader 就绪

💡 配置 API Key 后即可获得真实的量化分析能力。"""


# ─── 全局实例 ──────────────────────────────────────────

chat_service = ChatService()

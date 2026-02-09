"""LiteLLM 提供者实现，用于支持多服务商对接。"""

import json
import os
from typing import Any

import litellm
from litellm import acompletion

from nanobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from nanobot.providers.registry import find_by_model, find_gateway


class LiteLLMProvider(LLMProvider):
    """
    基于 LiteLLM 实现的 LLM 服务提供者，支持多服务商对接。
    
    通过统一接口支持 OpenRouter、Anthropic、OpenAI、Gemini 等众多服务商。
    服务商专属逻辑由注册表驱动（见 providers/registry.py）—— 无需在此处编写 if-elif 分支逻辑。
    """
    
    def __init__(
        self, 
        api_key: str | None = None, 
        api_base: str | None = None,
        default_model: str = "anthropic/claude-opus-4-5",
        extra_headers: dict[str, str] | None = None,
        provider_name: str | None = None,
    ):
        super().__init__(api_key, api_base)
        self.default_model = default_model
        self.extra_headers = extra_headers or {}
        
        # 检测网关/本地部署模式
        # provider_name（来自配置键）是主要判断依据；
        # api_key / api_base 作为自动检测的降级方案
        self._gateway = find_gateway(provider_name, api_key, api_base)
        
        # 配置环境变量
        if api_key:
            self._setup_env(api_key, api_base, default_model)
        
        if api_base:
            litellm.api_base = api_base
        
        # 关闭 LiteLLM 冗余的调试日志输出
        litellm.suppress_debug_info = True
        # 自动丢弃服务商不支持的参数（例如 gpt-5 会拒绝部分参数）
        litellm.drop_params = True
    
    def _setup_env(self, api_key: str, api_base: str | None, model: str) -> None:
        """根据检测到的服务商设置环境变量。"""
        spec = self._gateway or find_by_model(model)
        if not spec:
            return

        # 网关/本地部署模式会覆盖已存在的环境变量；标准服务商模式则不会
        if self._gateway:
            os.environ[spec.env_key] = api_key
        else:
            os.environ.setdefault(spec.env_key, api_key)

        # 解析 env_extras 中的占位符：
        #   {api_key}  → 用户配置的 API 密钥
        #   {api_base} → 用户配置的 api_base，降级使用 spec.default_api_base
        effective_base = api_base or spec.default_api_base
        for env_name, env_val in spec.env_extras:
            resolved = env_val.replace("{api_key}", api_key)
            resolved = resolved.replace("{api_base}", effective_base)
            os.environ.setdefault(env_name, resolved)
    
    def _resolve_model(self, model: str) -> str:
        """通过添加服务商/网关前缀解析模型名称。"""
        if self._gateway:
            # 网关模式：添加网关前缀，跳过服务商专属前缀
            prefix = self._gateway.litellm_prefix
            if self._gateway.strip_model_prefix:
                model = model.split("/")[-1]
            if prefix and not model.startswith(f"{prefix}/"):
                model = f"{prefix}/{model}"
            return model
        
        # 标准模式：为已知服务商自动添加前缀
        spec = find_by_model(model)
        if spec and spec.litellm_prefix:
            if not any(model.startswith(s) for s in spec.skip_prefixes):
                model = f"{spec.litellm_prefix}/{model}"
        
        return model
    
    def _apply_model_overrides(self, model: str, kwargs: dict[str, Any]) -> None:
        """从注册表中应用模型专属的参数覆盖配置。"""
        model_lower = model.lower()
        spec = find_by_model(model)
        if spec:
            for pattern, overrides in spec.model_overrides:
                if pattern in model_lower:
                    kwargs.update(overrides)
                    return
    
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        """
        通过 LiteLLM 发送聊天补全请求。
        
        参数：
            messages: 消息字典列表，包含 'role'（角色）和 'content'（内容）字段。
            tools: 可选的工具定义列表，遵循 OpenAI 格式。
            model: 模型标识符（例如 'anthropic/claude-sonnet-4-5'）。
            max_tokens: 响应的最大令牌数。
            temperature: 采样温度（控制输出随机性）。
        
        返回：
            包含内容和/或工具调用的 LLMResponse 对象。
        """
        model = self._resolve_model(model or self.default_model)
        
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        
        # 应用模型专属的参数覆盖（例如 kimi-k2.5 的温度参数）
        self._apply_model_overrides(model, kwargs)
        
        # 为自定义端点传递 api_base
        if self.api_base:
            kwargs["api_base"] = self.api_base
        
        # 传递额外请求头（例如 AiHubMix 所需的 APP-Code）
        if self.extra_headers:
            kwargs["extra_headers"] = self.extra_headers
        
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        
        try:
            response = await acompletion(**kwargs)
            return self._parse_response(response)
        except Exception as e:
            # 将错误信息作为内容返回，实现优雅的错误处理
            return LLMResponse(
                content=f"调用 LLM 时出错: {str(e)}",
                finish_reason="error",
            )
    
    def _parse_response(self, response: Any) -> LLMResponse:
        """将 LiteLLM 响应解析为标准格式。"""
        choice = response.choices[0]
        message = choice.message
        
        tool_calls = []
        if hasattr(message, "tool_calls") and message.tool_calls:
            for tc in message.tool_calls:
                # 按需从 JSON 字符串解析参数
                args = tc.function.arguments
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {"raw": args}
                
                tool_calls.append(ToolCallRequest(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=args,
                ))
        
        usage = {}
        if hasattr(response, "usage") and response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }
        
        reasoning_content = getattr(message, "reasoning_content", None)
        
        return LLMResponse(
            content=message.content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason or "stop",
            usage=usage,
            reasoning_content=reasoning_content,
        )
    
    def get_default_model(self) -> str:
        """获取默认模型名称。"""
        return self.default_model

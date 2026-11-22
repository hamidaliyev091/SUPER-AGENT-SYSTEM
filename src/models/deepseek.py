"""DeepSeek ModelPort adapter over DeepSeek's OpenAI-compatible REST API.

Target endpoint: `POST {base_url}/chat/completions` (default
`https://api.deepseek.com`). Reads credentials strictly from `DEEPSEEK_API_KEY`
(or explicit constructor argument for dependency injection) and treats every
provider response as an untrusted proposal routed through Policy (ADR-022).
"""
from __future__ import annotations

import os
import time
from typing import Callable, Optional

from core import ModelRequest, ModelResponse

from ._http import (
    ALL_MODEL_ROLES,
    ProviderError,
    extract_numeric_constraints,
    parse_openai_response,
    post_json,
    redact_secret,
    resolve_timeout,
    tool_spec_to_dict,
    validate_base_url,
)
from .model_port import ModelPort
from .router import ModelRouter

DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_DEEPSEEK_MODEL = "deepseek-chat"
DEFAULT_DEEPSEEK_TIMEOUT = 60.0


class DeepSeekModelPort(ModelPort):
    """DeepSeek chat-completions adapter implementing ModelPort (INTERFACES.md s14)."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: Optional[float] = None,
        max_tokens: int = 2048,
        temperature: float = 0.0,
        max_retries: int = 2,
        sleep: Callable[[float], None] = time.sleep,
        transport: Optional[Callable[..., dict]] = None,
    ):
        raw_key = api_key if api_key is not None else os.environ.get("DEEPSEEK_API_KEY", "")
        self._api_key = str(raw_key).strip()
        raw_model = model if model is not None else os.environ.get("DEEPSEEK_MODEL", DEFAULT_DEEPSEEK_MODEL)
        self.model = str(raw_model).strip() or DEFAULT_DEEPSEEK_MODEL
        raw_base = base_url if base_url is not None else os.environ.get("DEEPSEEK_BASE_URL", DEFAULT_DEEPSEEK_BASE_URL)
        self.base_url = validate_base_url(str(raw_base))
        self.timeout = resolve_timeout(timeout, "DEEPSEEK_TIMEOUT", DEFAULT_DEEPSEEK_TIMEOUT)
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.max_retries = max_retries
        self._sleep = sleep
        self._transport = transport
        self.last_error: Optional[ProviderError] = None

    def __repr__(self) -> str:
        return f"DeepSeekModelPort(model={self.model!r}, base_url={self.base_url!r})"

    def build_payload(self, request: ModelRequest) -> dict:
        messages = []
        for entry in getattr(request, "messages", None) or []:
            if isinstance(entry, dict):
                role = str(entry.get("role", "user") or "user")
                messages.append({"role": role, "content": str(entry.get("content", ""))})
        if not messages:
            messages = [{"role": "user", "content": ""}]
        elif all(msg.get("role") == "system" for msg in messages):
            messages.append({"role": "user", "content": "Proceed."})

        max_tokens, temperature = extract_numeric_constraints(
            getattr(request, "constraints", None),
            default_max_tokens=self.max_tokens,
            default_temperature=self.temperature,
        )
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        tools = []
        for tool in getattr(request, "tools", None) or []:
            spec = tool_spec_to_dict(tool)
            if spec is not None:
                tools.append({"type": "function", "function": spec})
        if tools:
            payload["tools"] = tools

        if getattr(request, "outputSchema", None) is not None:
            payload["response_format"] = {"type": "json_object"}
        return payload

    def generate(self, request: ModelRequest) -> ModelResponse:
        self.last_error = None
        if not self._api_key:
            self.last_error = ProviderError("CONFIG", "DEEPSEEK_API_KEY is not set")
            return ModelResponse(content="", toolCalls=[], finishReason="provider_error")

        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self._api_key}"}
        payload = self.build_payload(request)
        secrets = (self._api_key,)

        try:
            if self._transport is not None:
                raw = self._transport(url, payload, headers=headers, timeout=self.timeout)
            else:
                raw = post_json(
                    url,
                    payload,
                    headers=headers,
                    timeout=self.timeout,
                    max_retries=self.max_retries,
                    sleep=self._sleep,
                    secrets=secrets,
                )
            if not isinstance(raw, dict):
                raise ProviderError("PROTOCOL", "DeepSeek response must be a JSON object")
            return parse_openai_response(raw, output_schema=getattr(request, "outputSchema", None))
        except ProviderError as exc:
            exc.message = redact_secret(exc.message, secrets)
            self.last_error = exc
            return ModelResponse(content="", toolCalls=[], finishReason="provider_error")
        except Exception as exc:
            self.last_error = ProviderError("PROVIDER_ERROR", redact_secret(str(exc), secrets))
            return ModelResponse(content="", toolCalls=[], finishReason="provider_error")


def build_deepseek_router(
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    timeout: Optional[float] = None,
    max_tokens: int = 2048,
    temperature: float = 0.0,
    max_retries: int = 2,
    sleep: Callable[[float], None] = time.sleep,
    transport: Optional[Callable[..., dict]] = None,
) -> ModelRouter:
    """Wire a DeepSeekModelPort across all SAS ModelRoles."""
    port = DeepSeekModelPort(
        api_key=api_key,
        model=model,
        base_url=base_url,
        timeout=timeout,
        max_tokens=max_tokens,
        temperature=temperature,
        max_retries=max_retries,
        sleep=sleep,
        transport=transport,
    )
    return ModelRouter({role: port for role in ALL_MODEL_ROLES})

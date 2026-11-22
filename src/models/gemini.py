"""Google Gemini ModelPort adapter over the REST generateContent API.

Target endpoint: `POST {base_url}/v1beta/models/{model}:generateContent`
(default `https://generativelanguage.googleapis.com`). Authenticates strictly
via the `x-goog-api-key` HTTP header (never query strings) and treats every
provider response as an untrusted proposal routed through Policy (ADR-022).
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
from typing import Any, Callable, Optional

from core import ModelRequest, ModelResponse, ToolCall

from ._http import (
    ALL_MODEL_ROLES,
    ProviderError,
    extract_numeric_constraints,
    maybe_structured_output,
    normalize_usage,
    parse_tool_calls_from_text,
    post_json,
    redact_secret,
    resolve_timeout,
    tool_spec_to_dict,
    validate_base_url,
)
from .model_port import ModelPort
from .router import ModelRouter

DEFAULT_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_GEMINI_TIMEOUT = 60.0

_UNSUPPORTED_SCHEMA_KEYS = frozenset({
    "$schema",
    "$id",
    "$comment",
    "$defs",
    "definitions",
    "additionalProperties",
    "default",
    "examples",
})


def sanitize_gemini_schema(schema: Any) -> Any:
    """Recursively copy a JSON Schema while stripping Gemini-unsupported keys."""
    if isinstance(schema, list):
        return [sanitize_gemini_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    cleaned: dict = {}
    for key, value in schema.items():
        if key in _UNSUPPORTED_SCHEMA_KEYS:
            continue
        if key == "properties" and isinstance(value, dict):
            # Inside "properties", keys are user field names (e.g. a field named "default");
            # preserve those property names and sanitize each property's nested schema.
            cleaned[key] = {
                prop_name: sanitize_gemini_schema(prop_schema)
                for prop_name, prop_schema in value.items()
            }
        else:
            cleaned[key] = sanitize_gemini_schema(value)
    return cleaned


def _parse_gemini_response(result: dict, *,
                           output_schema: Optional[dict] = None) -> ModelResponse:
    """Translate a Gemini generateContent response dict into a ModelResponse."""
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    raw_finish = "STOP"
    try:
        candidates = result.get("candidates") or []
        if candidates and isinstance(candidates[0], dict):
            cand = candidates[0]
            content_obj = cand.get("content") or {}
            parts = content_obj.get("parts") if isinstance(content_obj, dict) else []
            for part in parts or []:
                if not isinstance(part, dict):
                    continue
                if isinstance(part.get("text"), str):
                    text_parts.append(part["text"])
                func_call = part.get("functionCall")
                if isinstance(func_call, dict):
                    name = str(func_call.get("name") or "")
                    if not name:
                        continue
                    raw_args = func_call.get("args", {})
                    if isinstance(raw_args, str):
                        try:
                            raw_args = json.loads(raw_args)
                        except json.JSONDecodeError:
                            raw_args = {}
                    args = raw_args if isinstance(raw_args, dict) else {}
                    call_id = str(func_call.get("id") or f"gemini_call_{len(tool_calls)}")
                    tool_calls.append(ToolCall(id=call_id, name=name, arguments=args))
            raw_finish = str(cand.get("finishReason") or "STOP")
        elif isinstance(result.get("promptFeedback"), dict):
            block = result["promptFeedback"].get("blockReason")
            if block:
                raw_finish = str(block)
        usage = normalize_usage(result.get("usageMetadata"))
    except Exception:
        return ModelResponse(content="", toolCalls=[], finishReason="provider_error")

    content = "\n".join(text_parts)
    if not tool_calls and content:
        tool_calls = parse_tool_calls_from_text(content)

    upper_finish = raw_finish.upper()
    if tool_calls and upper_finish in {"", "STOP"}:
        finish_reason = "tool_calls"
    elif upper_finish == "STOP":
        finish_reason = "stop"
    elif upper_finish == "MAX_TOKENS":
        finish_reason = "length"
    else:
        finish_reason = upper_finish.lower()

    structured = maybe_structured_output(content, output_schema)
    return ModelResponse(
        content=content,
        toolCalls=tool_calls,
        structuredOutput=structured,
        usage=usage,
        finishReason=finish_reason,
    )


class GeminiModelPort(ModelPort):
    """Google Gemini REST adapter implementing ModelPort (INTERFACES.md s14)."""

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
        raw_key = api_key if api_key is not None else os.environ.get("GEMINI_API_KEY", "")
        self._api_key = str(raw_key).strip()
        raw_model = model if model is not None else os.environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
        self.model = str(raw_model).strip() or DEFAULT_GEMINI_MODEL
        raw_base = base_url if base_url is not None else os.environ.get("GEMINI_BASE_URL", DEFAULT_GEMINI_BASE_URL)
        self.base_url = validate_base_url(str(raw_base))
        self.timeout = resolve_timeout(timeout, "GEMINI_TIMEOUT", DEFAULT_GEMINI_TIMEOUT)
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.max_retries = max_retries
        self._sleep = sleep
        self._transport = transport
        self.last_error: Optional[ProviderError] = None

    def __repr__(self) -> str:
        return f"GeminiModelPort(model={self.model!r}, base_url={self.base_url!r})"

    def build_payload(self, request: ModelRequest) -> dict:
        system_parts: list[dict] = []
        contents: list[dict] = []
        for entry in getattr(request, "messages", None) or []:
            if not isinstance(entry, dict):
                continue
            role = str(entry.get("role", "user") or "user").lower()
            text = str(entry.get("content", ""))
            if role == "system":
                system_parts.append({"text": text})
            elif role == "assistant":
                contents.append({"role": "model", "parts": [{"text": text}]})
            else:
                contents.append({"role": "user", "parts": [{"text": text}]})

        if not contents:
            if system_parts:
                contents = [{"role": "user", "parts": system_parts}]
                system_parts = []
            else:
                contents = [{"role": "user", "parts": [{"text": ""}]}]

        payload: dict = {"contents": contents}
        if system_parts:
            payload["systemInstruction"] = {"parts": system_parts}

        declarations = []
        for tool in getattr(request, "tools", None) or []:
            spec = tool_spec_to_dict(tool)
            if spec is not None:
                declarations.append({
                    "name": spec["name"],
                    "description": spec["description"],
                    "parameters": sanitize_gemini_schema(spec["parameters"]),
                })
        if declarations:
            payload["tools"] = [{"functionDeclarations": declarations}]

        max_tokens, temperature = extract_numeric_constraints(
            getattr(request, "constraints", None),
            default_max_tokens=self.max_tokens,
            default_temperature=self.temperature,
        )
        gen_config: dict = {
            "maxOutputTokens": max_tokens,
            "temperature": temperature,
        }

        output_schema = getattr(request, "outputSchema", None)
        if output_schema is not None:
            gen_config["responseMimeType"] = "application/json"
            gen_config["responseSchema"] = sanitize_gemini_schema(output_schema)
        payload["generationConfig"] = gen_config
        return payload

    def generate(self, request: ModelRequest) -> ModelResponse:
        self.last_error = None
        if not self._api_key:
            self.last_error = ProviderError("CONFIG", "GEMINI_API_KEY is not set")
            return ModelResponse(content="", toolCalls=[], finishReason="provider_error")

        model_segment = urllib.parse.quote(self.model, safe="")
        url = f"{self.base_url}/v1beta/models/{model_segment}:generateContent"
        headers = {"x-goog-api-key": self._api_key}
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
                raise ProviderError("PROTOCOL", "Gemini response must be a JSON object")
            return _parse_gemini_response(raw, output_schema=getattr(request, "outputSchema", None))
        except ProviderError as exc:
            exc.message = redact_secret(exc.message, secrets)
            self.last_error = exc
            return ModelResponse(content="", toolCalls=[], finishReason="provider_error")
        except Exception as exc:
            self.last_error = ProviderError("PROVIDER_ERROR", redact_secret(str(exc), secrets))
            return ModelResponse(content="", toolCalls=[], finishReason="provider_error")


def build_gemini_router(
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
    """Wire a GeminiModelPort across all SAS ModelRoles."""
    port = GeminiModelPort(
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

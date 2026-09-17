"""GenieX model adapters: ModelPort implementations over the Android
GenieX loopback bridge (contract v2, OpenAI-compatible).

- GenieXModelPort: text model (Qwen3-4B) for reasoning/tool-calling/agent
  work, routed per ModelRole through ModelRouter.
- GenieXVisionModelPort: vision/UI capability (Qwen2.5-VL-7B) exposed as a
  SEPARATE capability (describe_image) over the same chat completions
  endpoint with multimodal image content - never mixed into text routing.

Boundaries (ARCHITECTURE s5.10, INTERFACES s14/s15): the adapters know the
bridge; the Core knows neither. Everything the bridge returns is a model
claim: tool calls become proposals through the ExecutionPipeline, content
is never verification, and a bridge failure surfaces as an empty
provider_error response so the governed loop never crashes.
"""
from __future__ import annotations

import json
from typing import Optional

from core import ModelResponse, ToolCall
from core.enums import ModelRole

from .model_port import ModelPort
from .router import ModelRouter


def _candidate_json_lines(text: str) -> list:
    """JSON candidates from model text: bare JSON lines, markdown-fenced
    block content (```json ... ```), and single-line fences - the forms
    real models actually emit."""
    candidates = []
    inside_fence = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            inside_fence = not inside_fence
            if "{" in stripped:
                inner = stripped.split("```", 1)[1].rsplit("```", 1)[0].strip()
                brace = inner.find("{")
                if brace >= 0:
                    candidates.append(inner[brace:])
            continue
        if inside_fence or (stripped.startswith("{") and stripped.endswith("}")):
            candidates.append(stripped)
    return candidates


def _parse_tool_calls_from_text(text: str) -> list:
    """SAS tool-call text convention (GENIEX_BRIDGE.md v2): when the SDK
    cannot emit structured tool calls, the model returns JSON lines of
    tool-call objects (bare or markdown-fenced). Parsed into ToolCalls;
    Policy still decides."""
    calls = []
    for trimmed in _candidate_json_lines(text):
        if not (trimmed.startswith("{") and trimmed.endswith("}")):
            continue
        try:
            obj = json.loads(trimmed)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        function = obj.get("function") or obj.get("tool_call")
        if not isinstance(function, dict):
            continue
        name = function.get("name")
        if not isinstance(name, str) or not name:
            continue
        arguments = function.get("arguments", {})
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        calls.append(ToolCall(
            id=str(obj.get("id", f"tc-{len(calls)}")),
            name=name,
            arguments=arguments,
        ))
    return calls


def _parse_openai_response(result: dict) -> ModelResponse:
    """Translate the OpenAI-compatible response into a ModelResponse.
    Lenient on read (tool-call arguments may be a string or an object);
    anything unparseable degrades to empty content, never an exception."""
    content = ""
    tool_calls = []
    usage = None
    finish_reason = "stop"
    try:
        choices = result.get("choices") or []
        if choices:
            message = choices[0].get("message") or {}
            content = str(message.get("content") or "")
            for raw in message.get("tool_calls") or []:
                if not isinstance(raw, dict):
                    continue
                function = raw.get("function") if isinstance(raw.get("function"), dict) else {}
                arguments = function.get("arguments", {})
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError:
                        arguments = {}
                if not isinstance(arguments, dict):
                    arguments = {}
                tool_calls.append(ToolCall(
                    id=str(raw.get("id", "")),
                    name=str(function.get("name", "")),
                    arguments=arguments,
                ))
            finish_reason = str(choices[0].get("finish_reason", "stop"))
        usage = result.get("usage") if isinstance(result.get("usage"), dict) else None
    except Exception:
        return ModelResponse(content="", toolCalls=[], finishReason="provider_error")
    if not tool_calls and content:
        tool_calls = _parse_tool_calls_from_text(content)
    return ModelResponse(content=content, toolCalls=tool_calls, usage=usage,
                         finishReason=finish_reason)


class GenieXModelPort(ModelPort):
    """Text LLM through the GenieX bridge (chat completions)."""

    def __init__(self, bridge, model: Optional[str] = None,
                 max_tokens: int = 2048, temperature: float = 0.0):
        self.bridge = bridge
        self.model = model or bridge.llm_model
        self.max_tokens = max_tokens
        self.temperature = temperature

    def generate(self, request) -> ModelResponse:
        messages = []
        for entry in getattr(request, "messages", []) or []:
            if isinstance(entry, dict):
                messages.append({"role": entry.get("role", "user"),
                                 "content": str(entry.get("content", ""))})
        if not messages:
            messages = [{"role": "user", "content": ""}]
        try:
            result = self.bridge.chat_completions(
                self.model, messages, max_tokens=self.max_tokens,
                temperature=self.temperature)
            return _parse_openai_response(result)
        except Exception:
            # bridge failure: an empty provider_error response; the driver
            # journals the failed MODEL_CALL and the loop continues safely
            return ModelResponse(content="", toolCalls=[],
                                 finishReason="provider_error")


class GenieXVisionModelPort:
    """Vision/UI capability through the GenieX bridge (separate from text
    routing: vision output is a model description, never authority)."""

    def __init__(self, bridge, model: Optional[str] = None):
        self.bridge = bridge
        self.model = model or bridge.vlm_model

    def describe_image(self, image_bytes: bytes, prompt: str) -> str:
        try:
            result = self.bridge.vision_description(self.model, image_bytes, prompt)
            return _parse_openai_response(result).content
        except Exception:
            return ""  # capability failure surfaces as an empty description


def build_geniex_router(bridge, llm_model: Optional[str] = None,
                        vlm_model: Optional[str] = None):
    """Wire the validated local models into SAS routing: the text model
    serves every ModelRole; the vision model is the separate vision
    capability. Both remain replaceable (INTERFACES s15)."""
    llm = GenieXModelPort(bridge, model=llm_model)
    vision = GenieXVisionModelPort(bridge, model=vlm_model)
    roles = (ModelRole.PLANNER, ModelRole.RESEARCHER, ModelRole.CODER,
             ModelRole.REVIEWER, ModelRole.VERIFIER, ModelRole.GENERAL_AGENT)
    router = ModelRouter({role: llm for role in roles})
    return router, vision

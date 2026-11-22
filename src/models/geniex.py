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

from typing import Optional

from core import ModelResponse
from core.enums import ModelRole

from ._http import (
    candidate_json_lines as _candidate_json_lines,
    parse_openai_response as _parse_openai_response,
    parse_tool_calls_from_text as _parse_tool_calls_from_text,
)
from .model_port import ModelPort
from .router import ModelRouter


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

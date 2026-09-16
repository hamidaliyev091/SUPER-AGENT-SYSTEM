"""ScriptedModelPort: a reference ModelPort over a script of ToolCalls.

Used by tests and demonstrations; real provider adapters implement the
same ModelPort surface (INTERFACES s14).
"""
from __future__ import annotations

from core import ModelResponse

from .model_port import ModelPort


class ScriptedModelPort(ModelPort):
    """Each generate() consumes one scripted turn of tool calls."""

    def __init__(self, turns=None, usage=None, identity="scripted-port"):
        self._turns = list(turns or [])
        self.identity = identity
        self.usage = usage or {"inputTokens": 1, "outputTokens": 1}
        self.requests = []

    def generate(self, request) -> ModelResponse:
        self.requests.append(request)
        if not self._turns:
            return ModelResponse(content="", toolCalls=[],
                                 usage=self.usage, finishReason="stop")
        tool_calls = self._turns.pop(0)
        if not isinstance(tool_calls, (list, tuple)):
            tool_calls = [tool_calls]
        return ModelResponse(content="", toolCalls=list(tool_calls),
                             usage=self.usage, finishReason="tool_calls")

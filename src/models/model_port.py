"""ModelPort (INTERFACES.md s14) and its orchestrator driver.

The Core must never know whether the underlying model is DeepSeek, Claude,
Gemini, OpenAI, a local model, or another future provider (s14). A model,
however capable, receives no authority from selection (s15): every tool
call becomes a proposal through the ExecutionPipeline, and model-call
accounting is journaled so model-call limits are externally enforceable
(TASK_SCHEMA s20, CONTINUITY s14).
"""
from __future__ import annotations

from typing import Optional

from core import (
    ActionRequest,
    ActorIdentity,
    ModelRequest,
    ModelResponse,
    Target,
    utcnow_iso,
)
from core.enums import ActorType, ModelRole, TargetType


class ModelPort:
    """Provider-independent model access. Implementations translate to
    concrete providers; the Core imports no provider code."""

    def generate(self, request: ModelRequest) -> ModelResponse:
        raise NotImplementedError


def to_action_request(task, tool_call, actor_id: str = "model-port") -> ActionRequest:
    """Translate a model tool call into an action proposal. Translation
    grants nothing: policy still evaluates the request (ARCHITECTURE s5.10,
    INTERFACES s12)."""
    arguments = dict(tool_call.arguments or {})
    return ActionRequest(
        taskId=task.id,
        actor=ActorIdentity(actorId=actor_id, actorType=ActorType.TOP_LEVEL_AGENT,
                            taskId=task.id),
        toolId=tool_call.name,
        target=_target_from_arguments(arguments),
        arguments=arguments,
        reason="model tool call",
    )


def _target_from_arguments(arguments: dict) -> Optional[Target]:
    if isinstance(arguments.get("path"), str):
        return Target(type=TargetType.FILESYSTEM, value=arguments["path"])
    if isinstance(arguments.get("package"), str):
        return Target(type=TargetType.PACKAGE, value=arguments["package"])
    return None


class ModelPortDriver:
    """Adapts a ModelPort to the orchestrator's proposal interface.
    Every generate() call is journaled as MODEL_CALL before the response
    is used (model-call limits are externally enforced from the journal)."""

    def __init__(self, task_manager, store, port: ModelPort,
                 role: ModelRole = ModelRole.GENERAL_AGENT):
        self.task_manager = task_manager
        self.store = store
        self.port = port
        self.role = role
        self.calls = 0
        if hasattr(port, "select") and hasattr(port, "generate"):
            # a ModelRouter binds the role at dispatch (INTERFACES s15)
            self._generate = lambda request: port.generate(role, request)
        else:
            self._generate = port.generate

    def build_request(self, task) -> ModelRequest:
        return ModelRequest(
            role=self.role,
            messages=[{"role": "system",
                       "content": f"Task objective: {task.objective}"}],
            context={"taskId": task.id, "taskState": task.state.value},
        )

    def propose_action(self, task):
        """One model turn -> at most one tool-call proposal. Nothing the
        port returns is executed here; the pipeline decides."""
        response = self.generate(task)
        if not response.toolCalls:
            return None
        return to_action_request(task, response.toolCalls[0],
                                 actor_id=f"{self.role.value}-port")

    def generate(self, task) -> ModelResponse:
        try:
            response = self._generate(self.build_request(task))
        except Exception as exc:
            # a failing provider (bridge down, timeout, malformed adapter)
            # must never crash the governed loop: it becomes an empty
            # response with no tool calls and a recorded error marker
            response = ModelResponse(content="", toolCalls=[],
                                     finishReason="provider_error")
            self.errors = getattr(self, "errors", [])
            self.errors.append(str(exc))
        if not isinstance(response, ModelResponse):
            response = ModelResponse(content=str(response), finishReason="malformed")
        self.calls += 1
        journal = self.store.journal_for(task.id)
        journal.append("MODEL_CALL", {
            "taskId": task.id,
            "role": self.role.value,
            "usage": response.usage,
            "finishReason": response.finishReason,
        })
        return response

    def observe(self, task, result) -> None:
        """Observations are available to provider adapters; the driver
        itself does not act on them."""

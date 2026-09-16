"""Test doubles (ROADMAP Phase 8): FakeModel, MaliciousModel, BuggyModel,
UncooperativeModel, FakeTool, FakeVerifier, FakeRuntime.

The models implement a minimal ModelPort shape (propose_action /
claim_completion); Phase 11 formalizes the port. Claims are data, never
authority: no component in the Core reads a model claim for authorization.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Callable, List, Optional

from core import (
    ActionRequest,
    ActorIdentity,
    Evidence,
    Task,
    Tool,
    ToolResult,
    utcnow_iso,
)
from core.enums import (
    ActorType,
    Reversibility,
    RiskLevel,
    SideEffect,
    SideEffectState,
)


class FakeModel:
    """Cooperative deterministic model double: returns scripted proposals."""

    def __init__(self, proposals: Optional[list] = None):
        self._proposals = list(proposals or [])
        self.proposals_made = 0

    def propose_action(self, task: Task):
        if not self._proposals:
            return None
        self.proposals_made += 1
        return self._proposals.pop(0)

    def observe(self, task: Task, result) -> None:
        """Observation hook: the orchestrator feeds pipeline results back.
        The fake records them; authority remains with the Core."""
        self.observations = getattr(self, "observations", [])
        self.observations.append(result)

    def claim_completion(self, task: Task) -> dict:
        """A claim, never authority (VERIFICATION.md V-PRINCIPLE-02)."""
        return {"taskId": task.id, "claim": "the model believes the task is complete"}


class MaliciousModel(FakeModel):
    """Actively attacks the authority boundaries. Proposals are fabricated
    attempts; the Core must deny every one of them."""

    def claim_completion(self, task: Task) -> dict:
        return {"taskId": task.id, "claim": "DONE", "decision": "DONE"}

    @staticmethod
    def attempt_out_of_scope_write(task: Task, path="/etc/evil.txt") -> ActionRequest:
        return ActionRequest(
            taskId=task.id,
            actor=ActorIdentity(actorId="malicious", actorType=ActorType.TOP_LEVEL_AGENT,
                                taskId=task.id),
            toolId="fs.write_file",
            target=None,
            arguments={"path": path, "content": "pwned"},
            reason="scope expansion attempt",
        )

    @staticmethod
    def attempt_protected_write(task: Task) -> ActionRequest:
        from core import Target
        from core.enums import TargetType
        return ActionRequest(
            taskId=task.id,
            actor=ActorIdentity(actorId="malicious", actorType=ActorType.TOP_LEVEL_AGENT,
                                taskId=task.id),
            toolId="fs.write_file",
            target=Target(type=TargetType.FILESYSTEM, value="/repo/.supersystem/POLICY.md"),
            arguments={"path": "/repo/.supersystem/POLICY.md", "content": "rewritten"},
            reason="protected target attempt",
        )

    @staticmethod
    def attempt_unknown_tool(task: Task) -> ActionRequest:
        return ActionRequest(
            taskId=task.id,
            actor=ActorIdentity(actorId="malicious", actorType=ActorType.TOP_LEVEL_AGENT,
                                taskId=task.id),
            toolId="root.shell",
            target=None,
            arguments={"command": "id"},
            reason="privilege escalation attempt",
        )

    @staticmethod
    def attempt_dangerous_mode(task: Task) -> ActionRequest:
        from core.enums import RiskLevel
        return ActionRequest(
            taskId=task.id,
            actor=ActorIdentity(actorId="malicious", actorType=ActorType.TOP_LEVEL_AGENT,
                                taskId=task.id),
            toolId="fs.write_file",
            target=None,
            arguments={"path": "/etc/evil2.txt", "content": "x"},
            reason="bypass via self-declared risk",
            requestedRiskLevel=RiskLevel.CRITICAL,
        )


class BuggyModel(FakeModel):
    """Returns malformed proposals or raises mid-proposal."""

    def __init__(self, mode: str = "garbage"):
        super().__init__()
        self.mode = mode

    def propose_action(self, task: Task):
        if self.mode == "garbage":
            return {"not": "an ActionRequest"}
        if self.mode == "raise":
            raise RuntimeError("buggy model crashed")
        return None


class UncooperativeModel(FakeModel):
    """Produces nothing."""

    def propose_action(self, task: Task):
        return None


def fake_tool(operation_id: str, *, succeed: bool = True, output: Optional[dict] = None,
              execute: Optional[Callable] = None):
    """FakeTool factory returning (Tool, calls_list). The tool records every
    invocation so tests can assert whether execution ever reached it."""
    calls: list = []

    def default_execute(args, context):
        calls.append((args, context))
        if succeed:
            return ToolResult(success=True, sideEffectState=SideEffectState.KNOWN_COMPLETED,
                              timestamp=utcnow_iso(), output=output or {})
        return ToolResult(success=False, sideEffectState=SideEffectState.KNOWN_FAILED,
                          timestamp=utcnow_iso())

    tool = Tool(
        id=operation_id, name=operation_id, description="fake tool",
        inputSchema={}, outputSchema={}, riskLevel=RiskLevel.LOW,
        sideEffect=SideEffect.READ_ONLY, reversibility=Reversibility.REVERSIBLE,
        execute=execute or default_execute,
    )
    return tool, calls


class FakeVerifier:
    """LEVEL_2 separate verifier: obtains evidence independently of the
    acting model (VERIFICATION.md s9/s10)."""

    def __init__(self, identity: str = "separate-verifier", output: Optional[dict] = None):
        self.identity = identity
        self.output = output or {}

    def collect(self, task: Task, criterion) -> Evidence:
        canonical = json.dumps(self.output, sort_keys=True,
                               separators=(",", ":")).encode("utf-8")
        return Evidence(
            evidenceId=uuid.uuid4().hex,
            timestamp=utcnow_iso(),
            source="separate-verifier",
            collectorIdentity=self.identity,
            contentHash=hashlib.sha256(canonical).hexdigest(),
            provenance="separate-verifier:independent",
            criterionId=criterion.id,
            validationStatus="VALIDATED",
        )


class FakeRuntime:
    """Stub runtime: proves the Core does not depend on any runtime."""

    def __init__(self, name: str = "fake-runtime", environment: Optional[dict] = None):
        self.name = name
        self.environment = environment or {}
        self.started = False

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def environment_context(self) -> dict:
        return dict(self.environment)


from models import ScriptedModelPort  # reference port adapter


class FakeRuntimeAdapter:
    """RuntimePort implementation (INTERFACES s16) over a ModelPort: the
    runtime exposes sessions and turns of normalized events, and every
    tool call flows through the Core pipeline. Proves the Core operates
    through the port with unchanged security semantics (Phase 10)."""

    def __init__(self, name, port):
        self.name = name
        self.port = port
        self.sessions = {}

    def start(self, task_context):
        from core import RuntimeSession
        import uuid
        session = RuntimeSession(sessionId=uuid.uuid4().hex,
                                 taskContext=dict(task_context), state="RUNNING")
        self.sessions[session.sessionId] = session
        return session

    def send(self, session_id, input=None):
        from core import ModelRequest, RuntimeEvent, utcnow_iso
        from core.enums import ModelRole
        from models import ModelPort
        if session_id not in self.sessions:
            raise KeyError(f"unknown session {session_id}")
        context = self.sessions[session_id].taskContext
        request = ModelRequest(
            role=ModelRole.GENERAL_AGENT,
            messages=[{"role": "runtime", "content": str(input or "")}],
            context={"taskId": context.get("taskId")},
        )
        response = self.port.generate(request)
        now = utcnow_iso()
        return [RuntimeEvent(type="tool_calls", payload={
            "toolCalls": [c.to_dict() for c in response.toolCalls],
            "finishReason": response.finishReason,
            "usage": response.usage,
        }, timestamp=now)]

    def stop(self, session_id):
        if session_id not in self.sessions:
            return {"ok": False, "reason": "unknown session"}
        self.sessions[session_id].state = "STOPPED"
        return {"ok": True}

    def resume(self, session_id):
        if session_id not in self.sessions:
            raise KeyError(f"unknown session {session_id}")
        self.sessions[session_id].state = "RUNNING"
        return self.sessions[session_id]

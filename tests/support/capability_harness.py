"""The governed-loop fixture over a test-only capability channel.

Every test that exercises the loop end to end needs the same wiring: a
scripted device behind the real reference server, the real bridge client,
the real adapters, real Policy, the real pipeline, the real verification
methods and the real completion engine - with only the model scripted, at
the ModelPort seam. That wiring lives here once, so the integration tests
and the security tests observe the same system rather than two lookalikes.

The device is a test double; everything between the device and the policy
decision is production code (tests/support/capability_server.py is the only
fake on the device side, and it is never on the production path).
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.support.capability_server import (
    LAUNCH_PACKAGE,
    FakeDevice,
    serve,
)

from completion import CompletionEngine
from continuity import ObservationStore, RecoveryManager, TaskStore
from core import (
    CompletionContract,
    ResourceLimits,
    TargetAuthorizationContext,
    ToolCall,
    utcnow_iso,
)
from core.enums import EffortLevel, PermissionMode, TargetType
from execution import DurableApprover, ExecutionPipeline, PendingApprovalStore
from models import ModelPortDriver, ScriptedModelPort
from orchestration import Orchestrator
from platforms.termux import (
    AndroidCapabilityBridge,
    TermuxAndroidCapabilityAdapter,
    TermuxFilesystemAdapter,
)
from policy import (
    Canonicalizer,
    PolicyEngine,
    ProtectedPathRegistry,
    RuntimePathMapping,
)
from task import CreateTaskRequest, TaskManager
from verification import VerificationEngine, default_methods

#: The UI node and the text the scripted device starts with.
NODE_TARGET = "com.android.settings#n0"
TASK_TEXT = "HELLO"


def stop_server(server):
    """Stop a reference server in the only safe order: the serving thread
    must leave serve_forever before the socket is closed."""
    server.shutdown()
    server.server_close()


class FakeVision:
    """The vision capability, scripted. It returns text and nothing else:
    the VLM describes a screen, it never proposes or executes an action."""

    def __init__(self, description="Settings is open on the Battery screen"):
        self.description = description
        self.calls = []

    def describe_image(self, image_bytes, prompt) -> str:
        self.calls.append((len(image_bytes), prompt))
        return self.description


class CapabilityHarness(unittest.TestCase):
    """A device, a governed pipeline over it, and a scripted model."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.workspace = f"{self.root}/workspace".replace("\\", "/")
        Path(self.workspace).mkdir(parents=True, exist_ok=True)
        self.store = TaskStore(self.root)
        self.mgr = TaskManager(self.store)
        self.device = FakeDevice(launchable=["com.android.settings"])
        self.server, self.thread, url = serve(self.device)
        self.addCleanup(stop_server, self.server)
        self.bridge = AndroidCapabilityBridge(base_url=url, token="test-token",
                                              timeout=10)
        self.observations = ObservationStore(self.store.root)
        self.approvals = PendingApprovalStore(self.store.root)
        protected = {"P0": (f"{self.root}/.supersystem/**",)}
        self.protected = protected
        self.canonicalizer = Canonicalizer(resolve_symlinks=False)
        self.policy = PolicyEngine(
            canonicalizer=self.canonicalizer,
            protected_paths=ProtectedPathRegistry(
                version="1.0",
                mapping=RuntimePathMapping(version="1.0", paths=protected)))
        self.fs_tools = TermuxFilesystemAdapter(
            protected_mapping=protected).tools()
        self.capability_tools = TermuxAndroidCapabilityAdapter(
            self.bridge, self.observations).tools()
        tools = {**self.fs_tools, **self.capability_tools}
        self.pipeline = ExecutionPipeline(
            self.mgr, self.policy, tools, self.store,
            approver=DurableApprover(self.approvals))
        self.engine = VerificationEngine(self.mgr, self.pipeline, self.store,
                                         methods=default_methods())
        self.completion = CompletionEngine(self.mgr, self.store)
        self.port = ScriptedModelPort()
        self.vision = FakeVision()
        self.driver = ModelPortDriver(self.mgr, self.store, self.port,
                                      observations=self.observations,
                                      vision=self.vision)
        self.orchestrator = Orchestrator(
            self.mgr, self.pipeline, self.engine, self.completion, self.driver,
            recovery=RecoveryManager(self.mgr, self.store),
            approvals=self.approvals)

    # -- wiring helpers ------------------------------------------------------

    def script(self, *turns):
        """The tool calls the model will propose, one turn at a time."""
        self.port._turns = list(turns)

    def call(self, name, **arguments):
        return ToolCall(id=f"tc-{name}", name=name, arguments=arguments)

    def scope(self, path: str) -> str:
        """The scope pattern for a path, derived from the same canonicalizer
        Policy uses, so a declaration and its check cannot disagree (a
        Windows path canonicalizes to '/C:/...', a POSIX one stays '/...')."""
        canonical = self.canonicalizer.canonicalize(TargetType.FILESYSTEM, path)
        self.assertIsNotNone(canonical)
        return canonical.rstrip("/") + "/**"

    def authorization(self, **overrides) -> TargetAuthorizationContext:
        now = utcnow_iso()
        fields = {"schemaVersion": "1.0",
                  "allowedReadPaths": [self.scope(self.workspace)],
                  "allowedWritePaths": [self.scope(self.workspace)],
                  "allowedPackages": ["com.android.settings"],
                  "allowedPackageOperations": [LAUNCH_PACKAGE],
                  "allowedNetworkDomains": ["example.com"],
                  "allowedUIActions": ["com.android.settings#*"],
                  "createdAt": now, "updatedAt": now}
        fields.update(overrides)
        return TargetAuthorizationContext(**fields)

    def create_task(self, criteria, mode=PermissionMode.AUTO, **tac):
        return self.mgr.create_task(CreateTaskRequest(
            objective="exercise the device capability channel",
            permissionMode=mode,
            effortLevel=EffortLevel.STANDARD,
            resourceLimits=ResourceLimits(actionSteps=10, modelCalls=10),
            targetAuthorizationContext=self.authorization(**tac),
            successCriteria=list(criteria),
            completionContract=CompletionContract(
                objective="exercise the device capability channel",
                successCriteria=list(criteria))))

    def drive(self, task, **kwargs):
        return self.orchestrator.run(task.id, max_iterations=kwargs.pop(
            "max_iterations", 12), **kwargs)

    def last_request(self) -> str:
        return self.port.requests[-1].messages[-1]["content"]

    def kinds(self, task_id):
        return [record["kind"] for record in self.observations.all_records(task_id)]

    def decisions(self, task_id):
        """(operationId, decision, reason) for every policy decision made."""
        return [(record["payload"].get("operationId"),
                 record["payload"].get("decision"),
                 record["payload"].get("reason"))
                for record in self.store.journal_for(task_id).records()
                if record["eventType"] == "POLICY_DECISION"]

"""Real end-to-end SAS task over the Android GenieX NPU bridge.

The acting model is the REAL Qwen3-4B on the device NPU (via the GenieX
bridge); every tool call it proposes still flows through SAS Policy,
verification, and the Completion Engine. Run on the device:

    PYTHONPATH=src python3 src/platforms/termux/geniex_e2e_demo.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from core import (
    CompletionContract,
    ModelRequest,
    ResourceLimits,
    SuccessCriterion,
    TargetAuthorizationContext,
    ToolCall,
    utcnow_iso,
)
from core.enums import EffortLevel, ModelRole, PermissionMode, TaskState
from completion import CompletionEngine
from continuity import RecoveryManager, TaskStore
from execution import ExecutionPipeline, QueueApprover
from models import GenieXModelPort, ModelPortDriver
from orchestration import Orchestrator
from platforms.termux import TermuxFilesystemAdapter
from platforms.termux.geniex_bridge import GenieXBridge
from policy import Canonicalizer, PolicyEngine, ProtectedPathRegistry, RuntimePathMapping
from task import CreateTaskRequest, TaskManager
from verification import VerificationEngine, VerificationMethodSpec, output_contains

TOOL_INSTRUCTIONS = (
    'You are the acting agent of a governed system. Propose exactly ONE tool '
    'call by responding with a single JSON line, for example: '
    '{"id":"tc-1","type":"function","function":{"name":"fs.write_file",'
    '"arguments":{"path":"/data/out/x.txt","content":"HELLO"}}}'
    ' Output ONLY that JSON line, no other text.'
)


class InstructedGenieXPort(GenieXModelPort):
    """Same real model, with the tool-call format instruction as the
    system message. Nothing about authority changes."""

    def __init__(self, bridge, instructions):
        super().__init__(bridge)
        self.instructions = instructions

    def generate(self, request: ModelRequest):
        request.messages = [
            {"role": "system", "content": self.instructions},
            {"role": "user", "content": request.messages[-1].get("content", "Proceed.")
             if request.messages else "Proceed."},
        ]
        return super().generate(request)


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="sas-geniex-"))
    store = TaskStore(work / ".pi" / "tasks")
    manager = TaskManager(store)
    protected = {"P0": (f"{work}/.supersystem/**",)}
    policy = PolicyEngine(
        canonicalizer=Canonicalizer(resolve_symlinks=False),
        protected_paths=ProtectedPathRegistry(
            version="1.0",
            mapping=RuntimePathMapping(version="1.0", paths=protected)),
    )
    tools = TermuxFilesystemAdapter(protected_mapping=protected).tools()
    pipeline = ExecutionPipeline(manager, policy, tools, store,
                                 approver=QueueApprover())
    criterion = SuccessCriterion(
        id="c1", description="the file contains HELLO",
        verificationMethod="read-content", mandatory=True)
    contract = CompletionContract(
        objective="write HELLO to the demo file", successCriteria=[criterion])
    now = utcnow_iso()
    task = manager.create_task(CreateTaskRequest(
        objective="write HELLO to the demo file at " + str(work / "out" / "demo.txt"),
        permissionMode=PermissionMode.AUTO,
        effortLevel=EffortLevel.STANDARD,
        resourceLimits=ResourceLimits(actionSteps=20, wallClockTime=900,
                                      modelCalls=10),
        targetAuthorizationContext=TargetAuthorizationContext(
            schemaVersion="1.0",
            allowedReadPaths=[f"{work}/**"],
            allowedWritePaths=[f"{work}/**"],
            createdAt=now, updatedAt=now),
        successCriteria=[criterion],
        completionContract=contract,
    ))
    verification = VerificationEngine(
        manager, pipeline, store,
        methods={"read-content": VerificationMethodSpec(
            method="read-content", toolId="fs.read_file",
            arguments=lambda t, c: {"path": str(work / "out" / "demo.txt")},
            assessor=output_contains("content", "HELLO"))})

    bridge = GenieXBridge()
    port = InstructedGenieXPort(bridge, TOOL_INSTRUCTIONS)
    driver = ModelPortDriver(manager, store, port)
    orchestrator = Orchestrator(
        manager, pipeline, verification, CompletionEngine(manager, store),
        driver, recovery=RecoveryManager(manager, store))

    final = orchestrator.run(task.id, max_iterations=30)
    demo_file = work / "out" / "demo.txt"
    print(f"final state : {final.state.value}")
    print(f"file exists : {demo_file.is_file()}")
    print(f"file content: {demo_file.read_text()!r}" if demo_file.is_file() else "file content: (missing)")
    journal = store.journal_for(task.id).records()
    print(f"journal     : {len(journal)} records, last={journal[-1]['eventType']}")
    print(f"model calls : {driver.calls}")
    print(f"verdict     : {'PASS' if final.state is TaskState.DONE else 'NOT DONE'}")
    return 0 if final.state is TaskState.DONE else 1


if __name__ == "__main__":
    sys.exit(main())

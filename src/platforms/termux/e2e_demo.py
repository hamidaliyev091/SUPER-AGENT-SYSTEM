"""On-device end-to-end demonstration (Phase 12 exit criterion).

Runs a complete governed task ON the Termux device using the real
TermuxFilesystemAdapter (real writes, real reads), the policy pipeline,
the verification engine, and the completion engine:

    objective -> model tool calls (scripted port) -> policy -> real
    filesystem writes -> observation -> verification (real reads) ->
    completion -> DONE

Usage (on the device):

    PYTHONPATH=src python3 src/platforms/termux/e2e_demo.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from core import (
    ActionRequest,
    ActorIdentity,
    CompletionContract,
    ResourceLimits,
    SuccessCriterion,
    Target,
    TargetAuthorizationContext,
    ToolCall,
    utcnow_iso,
)
from core.enums import (
    ActorType,
    EffortLevel,
    PermissionMode,
    TargetType,
    TaskState,
)
from completion import CompletionEngine
from continuity import RecoveryManager, TaskStore
from execution import ExecutionPipeline, QueueApprover
from models import ModelPortDriver
from orchestration import Orchestrator
from policy import (
    Canonicalizer,
    PolicyEngine,
    ProtectedPathRegistry,
    RuntimePathMapping,
)
from task import CreateTaskRequest, TaskManager
from models import ScriptedModelPort
from verification import (
    VerificationEngine,
    VerificationMethodSpec,
    output_contains,
)


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="sas-demo-"))
    store = TaskStore(work / ".pi" / "tasks")
    manager = TaskManager(store)
    protected = {"P0": (f"{work}/.supersystem/**",)}
    policy = PolicyEngine(
        canonicalizer=Canonicalizer(resolve_symlinks=False),
        protected_paths=ProtectedPathRegistry(
            version="1.0",
            mapping=RuntimePathMapping(version="1.0", paths=protected)),
    )
    from platforms.termux import TermuxFilesystemAdapter
    tools = TermuxFilesystemAdapter(protected_mapping=protected).tools()
    pipeline = ExecutionPipeline(manager, policy, tools, store,
                                 approver=QueueApprover())
    criterion = SuccessCriterion(
        id="c1", description="demo file contains HELLO",
        verificationMethod="read-content", mandatory=True)
    contract = CompletionContract(
        objective="write the demo file", successCriteria=[criterion])
    now = utcnow_iso()
    task = manager.create_task(CreateTaskRequest(
        objective="write the demo file",
        permissionMode=PermissionMode.AUTO,
        effortLevel=EffortLevel.STANDARD,
        resourceLimits=ResourceLimits(actionSteps=50, wallClockTime=600),
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
            arguments=lambda t, c: {"path": f"{work}/out/demo.txt"},
            assessor=output_contains("content", "HELLO"))})
    port = ScriptedModelPort(turns=[[ToolCall(
        id="tc-1", name="fs.write_file",
        arguments={"path": f"{work}/out/demo.txt", "content": "HELLO"})]])
    driver = ModelPortDriver(manager, store, port)
    orchestrator = Orchestrator(
        manager, pipeline, verification,
        CompletionEngine(manager, store), driver,
        recovery=RecoveryManager(manager, store))

    final = orchestrator.run(task.id)
    print(f"task        : {task.id}")
    print(f"work dir    : {work}")
    print(f"final state : {final.state.value}")
    print(f"file exists : {(work / 'out' / 'demo.txt').is_file()}")
    print(f"file content: {(work / 'out' / 'demo.txt').read_text()!r}")
    journal = store.journal_for(task.id).records()
    print(f"journal     : {len(journal)} records, "
          f"last={journal[-1]['eventType']}")
    print(f"verdict     : {'PASS' if final.state is TaskState.DONE else 'FAIL'}")
    shutil.rmtree(work, ignore_errors=True)
    return 0 if final.state is TaskState.DONE else 1


if __name__ == "__main__":
    sys.exit(main())


# SUPER AGENT SYSTEM — Roadmap
Version: 0.1
Status: ACTIVE

## 1. Purpose
This document defines the implementation roadmap for SUPER AGENT SYSTEM.

Implementation follows a dependency-first approach.

Security, authorization, verification, continuity, and recovery must be established before advanced platform capabilities are introduced.

The system is implemented from the platform-independent Core outward toward runtime, model, Termux, and Android integrations.

---

## 2. Current Foundation
The following project contracts and architecture documents are established:

- `AGENTS.md`
- `ARCHITECTURE.md`
- `INTERFACES.md`
- `.supersystem/PROJECT_CONTRACT.md`
- `.supersystem/TASK_SCHEMA.md`
- `.supersystem/POLICY.md`
- `.supersystem/POLICY_RULES.md`
- `.supersystem/SECURITY.md`
- `.supersystem/THREAT_MODEL.md`
- `.supersystem/VERIFICATION.md`
- `.supersystem/CONTINUITY.md`
- `.supersystem/PROTECTED_PATHS.md`

These documents form the current architectural and security foundation.

---

## 3. Implementation Order
The project follows this order:

```text
Repository Foundation
↓
Core Contracts
↓
Task Management
↓
Policy Engine
↓
Execution Pipeline
↓
Verification
↓
Completion Engine
↓
Continuity / Recovery
↓
Adversarial Testing
↓
Fake End-to-End Agent
↓
Pi Runtime Integration
↓
Real Model Integration
↓
Termux
↓
Termux:API
↓
Android Capabilities
↓
Long-Running Autonomy
↓
Delegation / Advanced Capabilities
↓
Optimization
```

No later phase may bypass a failed security or correctness requirement from an earlier phase.

---

4. Phase 0 — Repository Foundation

Objective

Create the repository structure and establish the canonical project documents.

Required Structure

```
SUPER-AGENT-SYSTEM/
├── AGENTS.md
├── ARCHITECTURE.md
├── INTERFACES.md
├── ROADMAP.md
├── .supersystem/
├── src/
├── tests/
└── docs/
```

Exit Criteria

· repository structure exists;
· canonical documents exist;
· architectural documents are identified;
· security boundaries are documented.

Status

COMPLETE

---

5. Phase 1 — Core Contracts

Objective

Implement the platform-independent data structures and contracts.

Components

· Task
· TaskManager
· ActionRequest
· PolicyRequest
· PolicyDecision
· Tool
· ToolResult
· VerificationResult
· CompletionDecision
· FailureRecord
· ResourceLimits
· TargetAuthorizationContext

Requirements

The implementation must follow:

· INTERFACES.md
· .supersystem/TASK_SCHEMA.md
· .supersystem/PROJECT_CONTRACT.md

The Core must not depend on:

· Pi;
· pi-ultracode;
· DeepSeek;
· Claude;
· Gemini;
· Termux;
· Android.

Exit Criteria

· Core types are implemented;
· schema validation works;
· lifecycle validation works;
· invalid states are rejected;
· authorization structures are represented correctly.

Status

COMPLETE

---

6. Phase 2 — Task Manager

Objective

Implement authoritative task lifecycle and durable task state.

Responsibilities

Task Manager owns:

· task creation;
· task validation;
· lifecycle transitions;
· durable state;
· checkpoints;
· recovery coordination;
· target authorization context.

Task Manager must not:

· authorize tools;
· execute tools;
· verify results;
· select models;
· declare DONE.

Exit Criteria

The Task Manager correctly enforces the lifecycle defined by TASK_SCHEMA.md.

Status

NEXT

---

7. Phase 3 — Policy Engine

Objective

Implement deterministic authorization.

Components

· PolicyEngine
· Target Canonicalization
· Target Authorization
· Protected Target Evaluation
· Risk Evaluation
· Side-Effect Evaluation
· Reversibility Evaluation
· Idempotency Evaluation
· Permission Mode Evaluation
· Human Approval Requirements
· Fail-Closed Enforcement

Requirements

Policy independently determines authoritative:

· risk
· side effect
· reversibility
· idempotency
· authorization

Model-provided values are advisory only.

Decisions

Policy must return:

```
ALLOW
ASK
DENY
```

Exit Criteria

· unauthorized actions are denied;
· protected targets are denied;
· ambiguous targets fail closed;
· invalid policy context fails closed;
· permission modes cannot bypass Policy;
· DANGEROUS cannot bypass Policy.

Status

PLANNED

---

8. Phase 4 — Execution Pipeline

Objective

Implement the complete controlled action pipeline.

Pipeline

```
ActionRequest
↓
Schema Validation
↓
Policy Evaluation
↓
Human Approval if required
↓
Resource Precheck
↓
Resource Acquisition
↓
Action Journal STARTED
↓
Tool Execution
↓
Observation
↓
Terminal Action Journal
↓
Durable State Update
```

Requirements

A side-effecting operation must not execute unless the required authorization and resources are valid.

STARTED must be durably recorded before side effects occur.

Exit Criteria

The system can safely execute a registered fake tool through the complete authorization pipeline.

Status

PLANNED

---

9. Phase 5 — Verification Engine

Objective

Implement independent verification of task results.

Components

· VerificationEngine
· Evidence Collection
· Evidence Assessment
· Independence Assessment
· VerificationResult

Results

```
PASS
FAIL
INCONCLUSIVE
```

Requirements

The following are not sufficient by themselves:

· model says success
· tool returned success
· command exited successfully
· file changed

Verification must evaluate actual success criteria.

Material mutations invalidate affected evidence.

Exit Criteria

The system can detect that an executed action succeeded technically but failed the actual task requirement.

Status

PLANNED

---

10. Phase 6 — Completion Engine

Objective

Implement authoritative task completion.

Completion Decisions

```
DONE
CONTINUE
REPAIR
BLOCKED
WAITING_USER
```

DONE Requirements

DONE requires:

· mandatory success criteria PASS;
· required evidence;
· independent evidence when required;
· Policy compliance;
· resource compliance;
· no unresolved critical failure;
· durable VerificationResult;
· durable CompletionDecision;
· integrity validation.

Only Completion Engine may authorize DONE.

INCONCLUSIVE must never produce DONE.

Exit Criteria

· A model cannot declare a task complete.
· Only the Completion Engine can produce authoritative DONE.

Status

PLANNED

---

11. Phase 7 — Continuity and Recovery

Objective

Make the system resilient to interruption and process failure.

Components

· ContinuityManager
· Durable Task State
· Checkpoints
· Action Journal
· Continuity Brief
· Recovery Manager

Recovery Flow

```
Load Durable State
↓
Integrity Validation
↓
Inspect Action Journal
↓
Identify Unknown Side Effects
↓
Inspect External State
↓
Revalidate Authorization
↓
Revalidate Policy
↓
Revalidate Resources
↓
Determine Safe Next Action
↓
RUNNING / ASK / BLOCKED
```

Requirements

The system must distinguish:

```
SUCCESS
FAILURE
PARTIAL
UNKNOWN
```

An interrupted operation must not automatically be treated as having no side effect.

Exit Criteria

The system can safely recover after:

· process crash;
· runtime termination;
· interrupted execution;
· model context loss;
· unknown action result.

Status

PLANNED

---

12. Phase 8 — Core Test Suite

Objective

Prove that the Core works independently of real models and Android.

Test Implementations

· FakeModel
· MaliciousModel
· BuggyModel
· UncooperativeModel
· FakeTool
· FakeVerifier
· FakeRuntime

Test Categories

```
tests/unit/
tests/integration/
tests/security/
tests/adversarial/
tests/recovery/
tests/verification/
tests/replaceability/
```

Required Security Tests

At minimum:

· unauthorized target;
· protected target;
· Policy bypass attempt;
· malformed PolicyRequest;
· invalid arguments;
· scope expansion;
· subagent privilege escalation;
· false completion claim;
· verification failure;
· INCONCLUSIVE;
· corrupted durable state;
· unknown side effect;
· resource exhaustion;
· Action Journal failure;
· expired authorization;
· expired human approval.

Exit Criteria

Core security and architectural invariants pass automatically.

Status

PLANNED

---

13. Phase 9 — Fake End-to-End Agent

Objective

Demonstrate the entire architecture without external runtime or model dependencies.

Flow

```
User Objective
↓
Fake Model
↓
Task Manager
↓
Planner
↓
ActionRequest
↓
Policy
↓
Fake Tool
↓
Observation
↓
Verification
↓
Completion Engine
↓
DONE
```

Required Demonstration

A malicious FakeModel must be unable to:

· bypass Policy;
· expand scope;
· access protected targets;
· declare DONE;
· bypass verification.

Exit Criteria

A complete task can execute from creation to verified DONE.

Status

PLANNED

---

14. Phase 10 — Pi Runtime Integration

Objective

Connect the Core to the initial agent runtime.

Initial Adapter

```
PiRuntimeAdapter
```

Integration Areas

· runtime lifecycle;
· model communication;
· tool-call translation;
· event translation;
· interruption;
· resume;
· subagent integration;
· session continuity.

Requirement

Pi must remain an adapter.

The Core must not become dependent on Pi.

Exit Criteria

· Pi can operate the existing Core without changing its security semantics.
· The Core must remain functional if Pi is replaced.

Status

PLANNED

---

15. Phase 11 — Real Model Integration

Objective

Connect real model providers through ModelPort.

Possible Providers

· DeepSeek
· Claude
· Gemini
· Local Models

Requirements

Changing the model must not change:

· authorization;
· Policy;
· target scope;
· resource limits;
· verification requirements;
· completion authority.

Exit Criteria

At least one real model can operate the system through the model abstraction.

Status

PLANNED

---

16. Phase 12 — Termux Runtime

Objective

Run SUPER AGENT SYSTEM directly inside Termux.

Components

· TermuxRuntimeAdapter
· Process Adapter
· Filesystem Adapter
· Local Storage
· Environment Inspection
· Background Execution

Requirements

Termux capabilities remain subject to the Core Policy pipeline.

The runtime must not create an alternate authorization path.

Exit Criteria

The complete agent can execute inside Termux using the existing Core.

Status

PLANNED

---

17. Phase 13 — Termux:API

Objective

Add structured Android-adjacent capabilities through Termux:API.

Initial Capabilities

· battery status
· Wi-Fi status
· device information
· notifications

Requirements

Each capability must have:

· ToolSpec;
· Policy rule;
· target requirements;
· resource requirements;
· audit behavior;
· verification behavior.

Exit Criteria

Termux:API operations use the same authorization and execution pipeline.

Status

PLANNED

---

18. Phase 14 — Android Capability Layer

Objective

Add controlled Android capabilities.

Initial Areas

· application inspection
· application launching
· application force-stop
· settings read
· approved settings write
· registered Accessibility operations

Security Boundary

Capability availability does not imply authorization.

Every operation follows:

```
Target Authorization
↓
Policy
↓
Resource Coordinator
↓
Action Journal
↓
Execution
↓
Observation
↓
Verification
```

Exit Criteria

Each Android capability has:

· deterministic authorization;
· risk classification;
· side-effect classification;
· reversibility classification;
· idempotency classification;
· resource limits;
· audit requirements;
· verification behavior;
· tests.

Status

PLANNED

---

19. Phase 15 — Privileged Capabilities

Objective

Evaluate higher-privilege capabilities only after the lower layers are stable.

Potential Areas

· ADB
· Shizuku
· Root

These capabilities are not automatically enabled.

Requirements for New Privileged Capability

A new privileged capability requires:

· explicit registration;
· deterministic Policy rules;
· target authorization;
· confirmation requirements;
· resource limits;
· Action Journal integration;
· verification;
· adversarial tests;
· versioning;
· architecture and roadmap update.

Exit Criteria

A privileged capability cannot become available merely because its adapter has been implemented.

Status

DEFERRED

---

20. Phase 16 — Long-Running Autonomous Operation

Objective

Enable reliable multi-hour autonomous execution.

Capabilities

· durable checkpoints
· periodic state persistence
· resource budgets
· automatic recovery
· failure classification
· safe retries
· verification loops
· repair loops
· human escalation
· background execution

Required Behavior

The system must be able to:

```
start task
↓
execute
↓
observe
↓
verify
↓
repair when safe
↓
continue
↓
recover from interruption
↓
request human input when required
↓
finish with verified result
```

Exit Criteria

A long-running task can survive interruption and continue safely from durable state.

Status

PLANNED

---

21. Phase 17 — Delegation and Subagents

Objective

Enable controlled parallel subtasks.

Capabilities

· subtask creation
· delegation
· concurrent subtasks
· resource isolation
· inherited authorization
· scope narrowing
· subtask verification
· parent-child continuity

Security Requirements

Subagents cannot:

· expand authorization
· modify Policy
· increase permission level
· access protected targets
· transfer unrelated human approvals

Exit Criteria

A malicious subagent cannot obtain more authority than its parent.

Status

PLANNED

---

22. Phase 18 — Observability

Objective

Make the system inspectable during long-running execution.

Components

· structured event log
· task state view
· action history
· Policy decisions
· approval history
· resource usage
· verification results
· failure history
· recovery history
· model usage

Sensitive values and secrets must not be exposed in ordinary logs.

Exit Criteria

A task's execution history can be reconstructed from durable records without relying on model memory.

Status

PLANNED

---

23. Phase 19 — Optimization

Objective

Optimize performance only after correctness and security are established.

Possible Optimizations

· model routing caching
· parallel execution
· tool batching
· context compression
· checkpoint optimization
· resource scheduling
· latency reduction

Optimization must never weaken:

· authorization;
· Policy;
· verification;
· journaling;
· recovery;
· protected-target rules.

Status

FUTURE

---

24. Phase 20 — Capability Expansion

Additional capabilities may be added progressively:

· filesystem
· network
· processes
· Android applications
· UI
· development tools
· external services

Every new capability requires:

· ToolSpec;
· Policy definition;
· target authorization;
· risk classification;
· side-effect classification;
· reversibility classification;
· idempotency classification;
· resource limits;
· audit behavior;
· verification behavior;
· tests.

Status

FUTURE

---

25. Phase Gates

A phase may advance only after its exit criteria are satisfied.

```
Architecture Gate
↓
Core Gate
↓
Policy Gate
↓
Execution Gate
↓
Verification Gate
↓
Recovery Gate
↓
Adversarial Security Gate
↓
Fake End-to-End Gate
↓
Runtime Gate
↓
Model Gate
↓
Android Gate
↓
Autonomy Gate
```

A failed security gate blocks dependent phases.

---

26. Critical Path

The primary implementation dependency is:

```
Core Contracts
↓
Task Manager
↓
Policy
↓
Execution Pipeline
↓
Verification
↓
Completion
↓
Continuity / Recovery
↓
Adversarial Tests
↓
Fake End-to-End Agent
↓
Pi
↓
Real Model
↓
Termux
↓
Android
↓
Long-Running Autonomy
```

Delegation, privileged capabilities, optimization, and additional tools are secondary branches.

---

27. What Must Not Be Done Early

Do not begin implementation with:

· Root;
· Shizuku;
· ADB;
· complex Android automation;
· large-scale UI automation;
· multi-agent swarms;
· model-specific security logic;
· speculative memory systems;
· performance optimization;
· complex dashboards.

These depend on a functioning and tested Core.

---

28. Project Success Criteria

The project is successful when a user can provide a high-level objective and the system can:

```
Understand
↓
Validate
↓
Plan
↓
Authorize
↓
Execute
↓
Observe
↓
Verify
↓
Repair when safe
↓
Recover when interrupted
↓
Continue autonomously
↓
Escalate when required
↓
Produce verified DONE
```

while maintaining:

· explicit authorization;
· deterministic Policy enforcement;
· protected targets;
· resource limits;
· Action Journal;
· durable state;
· independent verification;
· safe recovery;
· replaceable models;
· replaceable runtimes.

The final system must remain safe and controllable even when the model is wrong, malicious, unavailable, or replaced.

The model provides intelligence.

The Core provides control.


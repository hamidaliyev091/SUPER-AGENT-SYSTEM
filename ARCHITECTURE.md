# ARCHITECTURE.md

**Version:** 0.2  
**Status:** FROZEN  
**Authority:** PROJECT_CONTRACT.md, TASK_SCHEMA.md, POLICY_RULES.md, PROTECTED_PATHS.md  

---

## 1. Purpose

This document defines the concrete architecture of the SUPER AGENT SYSTEM (SAS) — a model-agnostic, runtime-agnostic autonomous agent platform.

It specifies the components, their responsibilities, ownership boundaries, trust boundaries, and data/control flows needed to implement the security-critical execution guarantees established by PROJECT_CONTRACT.md and the v1.0 policy/task schemas.

The architecture is **implementation-oriented**. It is not a theoretical framework. It describes how the system is divided, how components communicate, and where security enforcement happens.

---

## 2. Core Architectural Principles

These principles are non-negotiable and inherited from PROJECT_CONTRACT.md.

1. **Model is not an authority.** The LLM proposes actions. It never authorizes them.
2. **Centralized Policy enforcement.** Every tool/action invocation passes through the Policy Engine. No alternative execution path exists.
3. **Only the Completion Engine can authorize DONE.** Model claims are not evidence.
4. **Verification is separate from execution.** The acting agent cannot verify its own completion for high-risk work.
5. **Durable task state is authoritative.** Model context is temporary and reconstructable.
6. **Target authorization is explicit, versioned, scoped, and fail-closed.**
7. **Subagents inherit authority and may narrow it, but cannot expand it.**
8. **Resource limits are centrally enforced and cannot be modified by the model.**
9. **Unknown side effects must never be blindly retried.**
10. **Protected paths and security-critical state are fail-closed.**
11. **Pi/pi-ultracode is an initial replaceable runtime/orchestration integration.** The core remains model-, runtime-, and platform-independent.
12. **Android/Termux belongs behind platform/runtime adapters.** Generic shell/command execution is a high-risk capability and must never become an unrestricted model → shell path.

---

## 3. System Components and Ownership

The system is composed of the following components. Each component has a single, clear ownership area.

| Component | Primary Responsibility | Authority |
|-----------|------------------------|-----------|
| **User / Human Interface** | Receives objectives, displays progress, presents approvals, allows cancellation. | Initiates tasks, approves ASK decisions. |
| **Task Manager** | Owns task lifecycle, durable task state, checkpoints, resume/recovery, and authoritative TargetAuthorizationContext. | Authoritative for task state and lifecycle transitions (except DONE). |
| **Policy Engine** | Evaluates every tool invocation. Produces ALLOW / ASK / DENY. Validates target authorization and protected paths. | Authoritative for execution authorization. |
| **Completion Engine** | Evaluates Completion Contract + verification results. | Sole authority to transition task to DONE. |
| **Verification Engine** | Executes verification procedures, collects evidence, returns PASS/FAIL/INCONCLUSIVE. | Authoritative for evidence evaluation, not for execution or completion. |
| **Orchestrator** | Coordinates planning, execution, repair loops, delegation. | No security authority; must pass through Policy. |
| **Model Router** | Selects and invokes models through a normalized interface. | No authority; serves planning/reasoning only. |
| **Resource Coordinator** | Manages shared resources, locks, and resource budgets. | Authoritative for resource allocation and locking only; does not authorize execution. |
| **Continuity Manager** | Durable state persistence, action journaling, compaction safety, crash recovery. Owns the **Action Journal** logical subsystem. | Enforces durable state integrity and safe recovery. |
| **Runtime Adapter** | Abstracts the agent runtime (Pi, OpenCode, etc.). | No authority; passes tool requests to Policy. |
| **Platform Adapter** | Abstracts Android/Termux/Shizuku/ADB capabilities. | No authority; implements structured tools. |
| **Human-in-the-Loop Interface** | Displays ASK requests, collects approvals, records decisions. | No authority; records user decisions. |
| **Observability / Audit Subsystem** | Emits structured events, manages audit logs, redaction. | No policy authority; integrity-protected. |

---

## 4. Trust Boundaries

The system explicitly separates trusted authority from untrusted data.

### 4.1 Trusted Authority

The following are authoritative **only when integrity-validated**:

- Task Manager (task state, target authorization)
- Policy Engine (authorization decisions)
- Completion Engine (DONE authorization)
- Verification Engine (verification results)
- Resource Coordinator (resource locks)
- Human approvals (through dedicated interface)

### 4.2 Conditionally Trusted

- Runtime adapters
- Model providers / models
- Orchestrator
- Subagents
- Platform adapters
- Tools

These components may fail or be malicious. Their outputs must be treated as untrusted until validated at a security boundary.

### 4.3 Untrusted Data

- Web content
- Files, documents, repositories
- Command output
- Application content
- External API responses
- Model-generated text
- Subagent reports

Untrusted data **MUST NOT** modify:

- policy
- authorization
- target scopes
- resource limits
- success criteria
- completion authority

---

## 5. Component Responsibilities and Interactions

### 5.1 User / Human Interface

- Receives objectives and converts them into task creation requests.
- Displays task progress, verification results, failures, and completion status.
- Presents ASK requests from Policy Engine.
- Receives user approvals and denials, then passes them to the Human-in-the-Loop Interface.
- Allows cancellation and manual state changes (e.g., resume from WAITING_USER).

The UI **MUST NOT** directly execute privileged operations.

### 5.2 Task Manager

- Creates tasks from user objectives.
- Validates task definition (success criteria, Completion Contract, permission mode, effort level, TargetAuthorizationContext, resource limits).
- Owns lifecycle state transitions (all except DONE).
- Persists durable task state via Continuity Manager.
- Creates checkpoints.
- Coordinates recovery from crash/restart.
- Supplies authoritative task state and target authorization to Policy Engine for every PolicyRequest.

The Task Manager **does not** authorize tools, verify results, or select models.

### 5.3 Policy Engine

- Receives every tool invocation request (PolicyRequest).
- Validates:
  - policy/rule version compatibility
  - task state
  - permission mode
  - effort level
  - tool/operation registration
  - target authorization (via TargetAuthorizationContext and PROTECTED_PATHS)
  - risk classification, side effect, reversibility, idempotency
- Applies authoritative permission matrix and operation-specific rules.
- Returns ALLOW, ASK, or DENY.
- On ASK: emits an ApprovalRequest. Does **not** itself collect human approval; the Human-in-the-Loop Interface handles that. Policy validates the resulting approval before execution.
- Ensures audit record is durably persisted before execution (audit-before-execution). If audit fails, returns DENY.

### 5.4 Completion Engine

- Receives a completion request from Orchestrator or Task Manager.
- Evaluates:
  - Completion Contract exists and is valid.
  - All mandatory success criteria have PASS results.
  - Required evidence (including independent evidence for HIGH/CRITICAL) is present and valid.
  - Policy compliance from authoritative audit records.
  - Resource compliance from authoritative resource records.
  - No unresolved critical failures.
  - VerificationResults and CompletionDecision are durably persisted and integrity-validated.
- If all conditions met, authorizes DONE transition.
- Otherwise returns CONTINUE, REPAIR, BLOCKED, or WAITING_USER.

Only the Completion Engine can authorize DONE.

### 5.5 Verification Engine

- Executes verification procedures defined in the Completion Contract.
- Collects evidence from observable system state.
- Evaluates each criterion as PASS, FAIL, or INCONCLUSIVE.
- For HIGH/CRITICAL/irreversible operations, requires independent evidence.
- Produces durable VerificationResult records.
- Is not an execution authority; if verification actions require tool use, those actions pass through Policy like any other operation.

### 5.6 Orchestrator

- Interprets task plan and manages execution loop.
- Requests tool actions through the Policy Engine.
- Observes results and updates task state via Task Manager.
- Detects failures, plans repairs, and requests verification.
- May delegate subtasks to subagents.
- Never authorizes its own actions or DONE.
- All tool requests must go through Policy.

The Orchestrator is replaceable; it is an execution coordinator, not a security boundary.

### 5.7 Model Router

- Selects models for roles (planner, coder, reviewer, verifier, etc.).
- Provides a normalized model interface (ModelPort) to the core.
- Handles provider-specific formatting, fallback, and capabilities.
- Does **not** grant permissions; model selection does not change authority.

### 5.8 Resource Coordinator

- Manages shared resources: Android foreground, ADB connection, filesystem paths, network interfaces, package manager, etc.
- Provides acquire/release/status operations.
- Enforces resource budgets and concurrency limits.
- Authoritative for resource allocation and locking.
- Does **not** grant execution authorization. Policy remains the only authority for allowing an operation. Resource acquisition is a separate step after policy authorization.

### 5.9 Continuity Manager

- Persists durable task state, action journal, failures, decisions, checkpoints, and verification records.
- Owns the **Action Journal** subsystem.
  - Records STARTED before execution.
  - Records terminal state (SUCCEEDED/FAILED/CANCELLED/UNKNOWN) after execution.
  - If STARTED cannot be persisted, execution must not proceed.
- Ensures pre-compaction persistence.
- Handles crash recovery and state reconstruction from durable storage.
- Maintains integrity of security-critical state.
- Prevents unsafe blind retries by tracking unknown side effects.

### 5.10 Runtime Adapter

- Translates core runtime-agnostic commands into runtime-specific operations (e.g., Pi, OpenCode).
- Implements RuntimePort interface.
- Exposes session start/send/stop/resume.
- Does not bypass Policy; all tool execution flows through the core.

### 5.11 Platform Adapter

- Translates structured tool requests into platform-specific implementations.
- For Android: Termux, Termux:API, registered Accessibility operations.
- ADB shell is available only under CRITICAL with synchronous human confirmation.
- Structured ADB, structured Shizuku, and structured root operations are **out of scope and denied** in v1.
- Each adapter must provide a versioned mapping from semantic protected paths to actual filesystem paths before filesystem mutation is enabled.
- Must register tools and their capabilities.

### 5.12 Human-in-the-Loop Interface

- Presents ASK requests to user.
- Collects explicit approvals/denials with scoping and time bounds.
- Records approval decisions as durable, auditable events.
- Does not grant authority itself; it records user authority.

### 5.13 Observability / Audit Subsystem

- Emits structured events for all security-relevant actions.
- Maintains integrity-protected audit logs.
- Redacts secrets before persistence.
- Provides operational monitoring and forensic capability.

---

## 6. Task Lifecycle and State Transitions

The authoritative lifecycle states are defined in TASK_SCHEMA.md. The Task Manager owns transitions.

The following diagram includes all v1 lifecycle states:

```

CREATED
↓
VALIDATING
↓
PLANNING
↓
READY
↓
RUNNING
↓
OBSERVING
↓
VERIFYING ──PASS──→ DONE
│
├──FAIL──→ REPAIRING ──→ RUNNING
│
└──INCONCLUSIVE──→ REPAIRING / WAITING_USER / BLOCKED

Any non-terminal state may transition to:
WAITING_USER or BLOCKED (non-executable)

From WAITING_USER / BLOCKED:
authorized resume → RECOVERING → appropriate executable state (READY/RUNNING/PLANNING)

RUNNING / OBSERVING / VERIFYING / REPAIRING may transition to:
RECOVERING (after crash) → RUNNING (after safe recovery)

Terminal states: DONE, FAILED, CANCELLED

```

Key rules:

- A task may not enter RUNNING without valid success criteria, Completion Contract, PolicyContext, TargetAuthorizationContext, and ResourceLimits.
- A task in WAITING_USER or BLOCKED is non-executable until an authorized resume/recovery transition occurs.
- The model cannot directly mutate lifecycle state.
- Only Completion Engine may transition VERIFYING to DONE.

---

## 7. Main Execution Flow

The normal execution loop is:

1. **Task Creation**  
   User provides objective. Task Manager validates and creates task with initial state CREATED.

2. **Planning**  
   Orchestrator (possibly using Planner model) investigates, decomposes objective, proposes plan and target scope. Task Manager validates and persists plan, narrowing target authorization if necessary. Planning can only narrow existing authorization and can never expand it.

3. **Execution**  
   For each action:  
   - Orchestrator constructs ActionRequest.  
   - Policy Engine evaluates (ALLOW/ASK/DENY).  
   - If ASK, Policy emits ApprovalRequest. Human-in-the-Loop Interface presents it to user. Policy validates the recorded approval before execution.  
   - Resource Coordinator allocates resources (if Policy authorized).  
   - Action journal records STARTED (durably).  
   - Tool executes via Runtime/Platform adapter.  
   - Result observed.  
   - Action journal records terminal state (SUCCEEDED/FAILED/CANCELLED/UNKNOWN).

4. **Verification**  
   Orchestrator requests verification. Verification Engine evaluates success criteria. Produces PASS/FAIL/INCONCLUSIVE.

5. **Completion**  
   Completion Engine evaluates completion contract + verification results + policy/resource compliance. If all pass, authorizes DONE.

6. **Repair**  
   On failure, Orchestrator enters REPAIRING, plans repair, and re-enters execution loop. Retries limited by resource limits.

---

## 8. Policy Enforcement Boundary

The Policy Engine is the **only** entry point for tool execution.

```

Model/Orchestrator
↓
ActionRequest
↓
Schema Validation
↓
Policy Evaluation (ALLOW/ASK/DENY)
↓
If ASK: Human Approval Request → Approval Decision
↓
Policy validates approval
↓
Resource Coordinator (allocation/locks)
↓
Action Journal STARTED
↓
Tool Execution
↓
Action Journal Terminal State

```

- No component may skip Policy.
- Tools cannot self-authorize.
- Runtime/Platform adapters cannot bypass Policy.
- Verification actions also pass through Policy.
- Audit-before-execution is mandatory for security-sensitive operations.

---

## 9. Target Authorization Boundary

Every operation requiring a target (filesystem, package, network, UI, etc.) must be evaluated against the task's `TargetAuthorizationContext`.

- **Task Manager** owns and validates the authoritative TargetAuthorizationContext.
- **Policy Engine** enforces it for every operation.
- **Planner and subagents** may propose narrower scopes, but they can never expand scope or create new authority.
- Targets must be canonicalized deterministically before matching.
- Unknown, ambiguous, or unmatched targets → DENY.
- Protected paths are evaluated separately via PROTECTED_PATHS.md.
- Runtime adapters must supply concrete, versioned mappings from semantic protected classes to actual filesystem paths before filesystem mutation is enabled.

---

## 10. Delegation and Subagent Architecture

Subagents are isolated execution units created by the Orchestrator.

- Subagents inherit parent's policy context, target authorization, resource limits, and security context.
- They may narrow any of these, but never expand them.
- Every subagent tool invocation passes through the same Policy Engine.
- A parent approval does not automatically transfer to subagent operations.
- Subagent results are untrusted until validated by the parent verification process.

Conceptual flow:

```

Parent Task
↓
Orchestrator
↓
Delegate to Subagent (scoped)
↓
Subagent executes within inherited/narrowed authority
↓
Subagent returns structured result
↓
Parent validates result (may require verification)

```

---

## 11. Resource Coordination

Resource Coordinator manages shared resources and enforces budgets.

- Each resource has an owner and lock mode (SHARED/EXCLUSIVE).
- All acquisitions pass through Resource Coordinator.
- Resource limits are externally enforced and cannot be increased by model.
- Resource Coordinator is not an authorization authority. It only allocates resources after Policy has authorized the operation.
- On crash or unknown side effect, resources may be left in uncertain state; recovery must inspect and release/restore safely.
- v1 supports one active top-level task; concurrent subagents within it are allowed, subject to locks and budgets.

---

## 12. Action Journal and Continuity

The **Action Journal** is a logical subsystem owned by the Continuity Manager.

Every side-effecting action must produce durable records.

- **STARTED** must be durably persisted by Continuity Manager before execution begins.
- If STARTED cannot be persisted, action must not execute.
- After execution, Continuity Manager records terminal state: SUCCEEDED, FAILED, CANCELLED, or UNKNOWN.
- If result cannot be determined, state = UNKNOWN; blind retry is prohibited.
- Checkpoints capture enough state for safe resume.
- Context compaction must not lose durable state; pre-compaction persistence is mandatory.

---

## 13. Verification and Completion Engine Separation

- Verification Engine evaluates evidence; Completion Engine authorizes DONE.
- Verification results are durable and integrity-protected.
- For HIGH/CRITICAL/irreversible operations, independent evidence is required.
- Model claims are not evidence.
- Verification Engine is not a universal security authority. Its results inform Completion Engine, but do not authorize execution or completion.
- A task cannot transition from execution to DONE without passing through verification.

---

## 14. Recovery and Failure Handling

The recovery sequence is explicit and ordered:

1. Load durable state from Continuity Manager.
2. Validate integrity of task state and action journal.
3. Inspect action journal for STARTED or UNKNOWN actions.
4. Classify unknown side effects.
5. Verify external state where possible.
6. Revalidate authorization, policy, and target scopes.
7. Check resource availability via Resource Coordinator.
8. Decide: safe resume / retry / ASK / BLOCK / FAIL.

- On crash, Continuity Manager restores task from durable state.
- Unknown side effects must be explicitly marked.
- Before retrying an interrupted operation, the system must verify external state or request human intervention.
- Resume from WAITING_USER/BLOCKED requires explicit authorization and validation.
- Failure records are durable and used to avoid repeating ineffective strategies.

---

## 15. Observability / Audit

- Security-relevant events are emitted as structured records.
- Audit records are integrity-protected and append-only.
- Secrets are redacted unconditionally.
- Audit-before-execution is mandatory for security-sensitive operations.

---

## 16. Model Routing and Replaceability

- Model Router selects models per role.
- Core uses normalized ModelPort; provider-specific adapters handle translation.
- Model replacement does not affect task state, policy, resource limits, or verification.
- Model output is always untrusted and non-authoritative.

---

## 17. Runtime / Tool Abstraction

- Core communicates via RuntimePort and Tool interfaces.
- Runtime adapters encapsulate Pi/OpenCode/others.
- Platform adapters encapsulate Android/Termux/Shizuku/ADB.
- Tools are registered with metadata; unregistered tools are denied.
- Generic command execution is CRITICAL by default and requires explicit allowlisting and human confirmation.

---

## 18. Android Integration Boundary

- Android capabilities are exposed as structured tools through Platform Adapters.
- v1 executable Android capabilities:
  - Termux
  - Termux:API
  - Structured Android tools
  - Registered Accessibility operations
- ADB shell: CRITICAL, requires synchronous human confirmation.
- v1 out of scope / denied:
  - Structured Shizuku operations
  - Structured ADB operations
  - Structured root operations
- Android platform adapters must not allow unrestricted shell access.
- Runtime adapters must provide concrete protected-path mappings before filesystem mutation is enabled.

---

## 19. Pi / pi-ultracode Integration Boundary

- Pi/pi-ultracode is an initial runtime/orchestration integration, replaceable.
- It must implement the RuntimePort and Orchestrator interface.
- It cannot bypass Policy, Target Authorization, Resource Coordination, or Action Journaling.
- All its tool calls go through the core Policy Engine.
- It is not part of the core architecture; it is an adapter.

---

## 20. Concurrency Model

- v1: one active top-level task.
- Multiple subtasks/subagents may run concurrently within that task.
- Shared resources are coordinated via Resource Coordinator.
- No implicit resource access; all access must be acquired.
- Future multi-top-level-task support is out of scope for v1.

---

## 21. Security-Critical Invariants

The following must hold at all times:

- Every tool invocation passes through Policy.
- Model cannot authorize itself or DONE.
- Unknown policy state fails closed.
- Target authorization is fail-closed and scoped.
- Subagents cannot elevate authority.
- Resource limits cannot be increased by model.
- Audit-before-execution is mandatory; audit failure blocks execution.
- Action journal STARTED is mandatory before side effects.
- Unknown side effects never blindly retried.
- Protected paths are enforced by runtime mapping + semantic registry.
- Completion is only by Completion Engine after verification and compliance checks.

---

## 22. Testing and Replaceability Boundaries

- Core must be tested with adversarial model behavior (malicious model attempts to bypass policy).
- Runtime and model replacements must not require core changes.
- Architecture fitness functions verify dependency direction (core does not import adapters).
- Policy rules are versioned and tested for determinism.
- Protected path mapping tests ensure no path traversal/symlink bypass.
- Recovery tests ensure safe resume after crash.

---

## 23. Implementation Notes

This document is implementation-agnostic at the code level. The following lower-level documents define concrete details:

- TASK_SCHEMA.md — canonical task structure
- POLICY_RULES.md — authoritative permission matrix and operation rules
- PROTECTED_PATHS.md — protected path classes and runtime mapping requirement
- SECURITY.md — security controls and audit integrity
- VERIFICATION.md — verification and completion semantics
- CONTINUITY.md — durability and recovery

This architecture is ready to guide implementation, provided the lower-level specifications are adhered to.

---

## 24. Status

**Version:** 0.2  
**Status:** FROZEN  
**Next:** Cross-document consistency review completed with TASK_SCHEMA, POLICY_RULES, and PROTECTED_PATHS.

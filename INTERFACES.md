INTERFACES.md

Version: 0.2
Status: FROZEN
Depends on: PROJECT_CONTRACT.md v0.3, ARCHITECTURE.md v0.2

1. Purpose

This document defines the stable interfaces between the major components of the system.

The interfaces are runtime-, model-, platform-, and vendor-independent.

The initial implementation may use:

Pi

pi-ultracode

DeepSeek

Claude

Gemini

local models

Termux

Termux:API

Shizuku

ADB

Android APIs

These technologies are implementation choices and MUST NOT become dependencies of the Core interfaces.

The interfaces exist to ensure that the system can evolve without requiring a complete architectural rewrite.

2. Interface Principles

All interfaces MUST follow these principles:

Core interfaces MUST NOT depend on a specific runtime.

Core interfaces MUST NOT depend on a specific model provider.

Core interfaces MUST NOT depend on Android-specific APIs.

Core interfaces MUST NOT depend directly on Pi APIs.

Security decisions MUST NOT be delegated to the model.

Tool execution MUST pass through Policy.

DONE MUST be authorized only by Completion Engine.

Verification MUST be owned by Verification Engine.

Durable state MUST be machine-readable.

Human approvals MUST be explicit, scoped, auditable, and time-bounded.

Interfaces MUST use structured inputs and outputs.

Errors MUST be structured.

Resource access MUST be coordinated centrally.

Subagents MUST NOT gain authority beyond their parent task.

Untrusted model/environment content MUST NOT become authority merely by appearing in model context.

3. Core Data Types

The following definitions are conceptual contracts.

The implementation language may represent them as classes, structs, schemas, database records, or equivalent types.

3.1 TaskId

TaskId = unique immutable identifier 

A TaskId MUST remain stable for the lifetime of the task.

3.2 Task

Task {
  id: TaskId
  schemaVersion: string
  objective: string
  requirements: Requirement[]
  successCriteria: SuccessCriterion[]
  completionContract: CompletionContract
  permissionMode: PermissionMode
  effortLevel: EffortLevel
  policyContext: PolicyContext
  targetAuthorizationContext: TargetAuthorizationContext
  resourceLimits: ResourceLimits
  state: TaskState
  plan: Plan?
  decisions: Decision[]
  failures: FailureRecord[]
  verification: VerificationState
  checkpoints: Checkpoint[]
  continuity: ContinuityState
  parentTaskId: TaskId?
  subtaskIds: TaskId[]
  createdAt: Timestamp
  updatedAt: Timestamp
}

Mandatory execution requirements

A Task MUST NOT enter RUNNING unless:

objective exists;

success criteria exist;

every mandatory success criterion is observable;

permission mode exists;

effort level exists;

resource limits exist;

policy context exists;

target authorization context exists;

completion contract exists (for autonomous tasks).

If these conditions are not satisfied, the Task Manager MUST reject the task or place it into:

WAITING_USER 

or:

BLOCKED 

depending on the reason.

4. Permission Mode

PermissionMode = PLAN | ASK | AUTO | DANGEROUS 

Permission mode controls the interaction policy with the user.

It does NOT replace the Policy Engine.

DANGEROUS does NOT mean unrestricted execution.

The Policy Engine remains authoritative.

5. Effort Level

EffortLevel = FOCUSED | STANDARD | DEEP | ULTRA 

Effort level controls how much investigation, planning, reasoning, delegation, and verification the system should perform.

It MUST NOT change security authority.

For example:

ULTRA + ASK 

means extensive autonomous reasoning with user approval for operations requiring approval.

It does NOT mean unrestricted execution.

6. Policy Context

PolicyContext {
  policyVersion: string
  ruleVersion: string
  permissionMode: PermissionMode
  defaultRiskLevel: RiskLevel
  actor: ActorIdentity
  environment: EnvironmentContext
  authorizationContext: AuthorizationContext
}

Policy context is supplied to Policy Engine.

The model MUST NOT be allowed to modify security-relevant policy context directly.

7. Task Manager Interface

The Task Manager owns task lifecycle and durable task state.

TaskManager {
  createTask(input) -> Task
  getTask(taskId) -> Task
  updateTask(taskId, update) -> Task
  transition(taskId, targetState) -> Result
  checkpoint(taskId) -> Checkpoint
  recover(taskId) -> RecoveryResult
  cancel(taskId) -> Result
}

The Task Manager MUST enforce lifecycle invariants.

The Task Manager:

owns lifecycle state;

validates task definitions;

persists authoritative task state;

manages checkpoints;

coordinates recovery;

owns authoritative TargetAuthorizationContext.

The Task Manager does NOT:

authorize tools;

execute tools;

perform verification;

select models;

declare DONE independently.

8. Orchestrator Interface

The Orchestrator coordinates execution but does not own security authority or completion authority.

Orchestrator {
  start(taskId) -> ExecutionResult
  plan(taskId) -> PlanResult
  execute(actionRequest) -> ExecutionResult
  delegate(delegationRequest) -> DelegationResult
  observe(taskId) -> ObservationResult
  requestVerification(taskId) -> VerificationRequest
  recover(taskId) -> RecoveryResult
  stop(taskId) -> Result
}

Critical rule

The Orchestrator MUST NOT expose an unrestricted:

execute(taskId) 

operation that implicitly gives it authority to decide what arbitrary action to perform.

Execution MUST be based on an explicit ActionRequest, which is a proposal, not an authorization.

9. Action Request and Policy Request Boundary

An ActionRequest is a proposal from the acting component (model, orchestrator, subagent).

Conceptual:

ActionRequest {
  taskId: TaskId
  actor: ActorIdentity
  toolId: ToolId
  target: Target
  arguments: StructuredData
  requestedRiskLevel: RiskLevel?
  requestedSideEffect: SideEffect?
  requestedReversibility: Reversibility?
  requestedIdempotency: Idempotency?
  reason: string
}

The requested risk level, side effect, reversibility, idempotency, and permissions are informational inputs.

They do NOT override Policy.

The PolicyRequest is the complete authorization input assembled from authoritative Task Manager state plus the proposed operation.

Conceptual:

PolicyRequest {
  policyVersion: string
  ruleVersion: string
  taskId: TaskId
  taskState: TaskState
  permissionMode: PermissionMode
  effortLevel: EffortLevel
  actorContext: ActorIdentity
  environmentContext: EnvironmentContext
  authorizationContext: AuthorizationContext
  targetAuthorizationContext: TargetAuthorizationContext
  toolId: ToolId
  operationId: OperationId
  target: Target
  structuredArguments: StructuredData
  requestedRiskLevel: RiskLevel?
  requestedSideEffect: SideEffect?
  requestedReversibility: Reversibility?
  requestedIdempotency: Idempotency?
}

Policy MUST independently determine the authoritative risk, side effect, reversibility, idempotency, and authorization decision from the registered tool/operation rules and the protected-target registry.

The model MUST NOT be able to lower risk classification or alter authoritative classifications to obtain permission.

10. Policy Engine Interface

Policy is the centralized authorization gate.

PolicyEngine {
  evaluate(request: PolicyRequest) -> PolicyDecision
}

PolicyDecision {
  decision: ALLOW | ASK | DENY
  reason: string
  policyVersion: string
  ruleVersion: string
  approvalRequirement?: ApprovalRequest
  constraints?: PolicyConstraint[]
}

Policy MUST fail closed.

If policy cannot determine whether an operation is permitted, it MUST NOT return ALLOW.

On ASK, Policy emits an ApprovalRequest but does not itself collect human approval. The Human-in-the-Loop Interface handles collection; Policy validates the resulting approval before final execution authorization.

11. Tool Interface

Tools expose structured capabilities.

Tool {
  id: ToolId
  name: string
  description: string
  inputSchema: Schema
  outputSchema: Schema
  riskLevel: RiskLevel
  sideEffect: SideEffect
  reversibility: Reversibility
  requiredPermissions: Permission[]
  execute(input, context) -> ToolResult
}

A Tool MUST NOT self-authorize.

A Tool MUST NOT:

bypass Policy;

modify its own risk classification;

grant itself permissions;

transition tasks to DONE;

modify resource limits.

Tool metadata is descriptive and may provide a candidate risk level, but the authoritative risk classification comes from POLICY_RULES.md and its referenced registries. If tool metadata conflicts with policy, policy wins.

12. Tool Execution Pipeline

The mandatory execution flow is:

Model / Orchestrator
      ↓
ActionRequest (proposal)
      ↓
Schema Validation
      ↓
Policy Engine (ALLOW / ASK / DENY)
      ↓
If ASK: Human Approval Request → Approval Decision
      ↓
Policy validates approval
      ↓
Resource Coordinator (allocation/locks)
      ↓
Action Journal STARTED (durable)
      ↓
Tool Execution
      ↓
Observation
      ↓
Action Journal Terminal State
      ↓
Durable State Update
      ↓
Verification when required

No component may skip mandatory gates.

13. Tool Result Interface

ToolResult {
  success: boolean
  output: StructuredData?
  evidence?: Evidence[]
  error?: Error
  sideEffects?: SideEffect[]
  sideEffectState: SideEffectState
  timestamp: Timestamp
}

sideEffectState MUST distinguish at minimum:

KNOWN_NONE
KNOWN_COMPLETED
KNOWN_PARTIAL
KNOWN_FAILED
UNKNOWN

If an operation is interrupted and its side effects cannot be determined, the system MUST use:

UNKNOWN

The system MUST NOT automatically assume the operation failed.

14. Model Interface

Models are accessed through a provider-independent interface.

ModelPort {
  generate(request: ModelRequest) -> ModelResponse
}

ModelRequest {
  role: ModelRole
  messages: Message[]
  tools: Tool[]
  constraints: ModelConstraints?
  context: ModelContext?
  outputSchema: Schema?
}

ModelResponse {
  content: string
  toolCalls: ToolCall[]
  structuredOutput: StructuredData?
  usage: Usage?
  finishReason: FinishReason
}

The Core MUST NOT know whether the underlying model is:

DeepSeek;

Claude;

Gemini;

OpenAI;

a local model;

or another future provider.

15. Model Router Interface

ModelRouter {
  select(role, taskContext, constraints) -> ModelSelection
  generate(role, request) -> ModelResponse
}

Possible roles:

PLANNER
RESEARCHER
CODER
REVIEWER
VERIFIER
GENERAL_AGENT

Model selection MUST NOT grant authority.

A stronger model MUST NOT automatically receive:

broader permissions;

higher resource limits;

lower verification requirements;

policy bypass capability.

16. Runtime Interface

The Runtime Adapter isolates the Core from agent runtimes.

RuntimePort {
  start(taskContext) -> RuntimeSession
  send(sessionId, input) -> RuntimeEvent[]
  stop(sessionId) -> Result
  resume(sessionId) -> RuntimeSession
}

Initial implementation:

PiRuntimeAdapter

Potential future implementations:

OpenCodeRuntimeAdapter
FutureRuntimeAdapter

The Core MUST NOT depend directly on runtime-specific APIs.

17. Android Runtime Interface

Android capabilities SHOULD be exposed through structured, declarative operations.

AndroidRuntime {
  getDeviceInfo()
  getBatteryStatus()
  listApplications()
  launchApplication()
  forceStopApplication()
  readFile()
  writeFile()
  installPackage()
  uninstallPackage()
  getSettings()
  setSetting()
  sendIntent()
}

The implementation may internally use:

Android APIs;

Termux;

Termux:API;

Shizuku;

ADB;

Accessibility;

Root.

However, v1 executable capabilities are limited as defined by POLICY_RULES.md and ARCHITECTURE.md v0.2:

- Termux: allowed for normal filesystem/process/network operations subject to policy.
- Termux:API: structured operations such as battery_status, wifi_status, device_info, send_notification.
- Structured Android tools: registered operations like package list/inspect/install/uninstall/clear_data, settings read/write, etc., subject to Policy and target authorization.
- Registered Accessibility operations: inspect_ui, tap, type_text, submit, subject to Policy and allowedUIActions.

ADB shell: CRITICAL, requires synchronous human confirmation.

Structured ADB operations: out of scope / denied in v1.

Structured Shizuku operations: out of scope / denied in v1.

Structured root operations: out of scope / denied in v1.

Do not imply that installPackage, uninstallPackage, setSetting, etc. are automatically allowed; Policy remains authoritative. Each operation must pass through the Policy Engine with the appropriate target authorization and risk classification.

18. Generic Command Execution

Generic command execution is NOT a default Core capability.

Examples:

execute_termux()
execute_shizuku()
execute_adb()

MUST be disabled unless explicitly registered and allowlisted.

Declarative tools are preferred.

If generic command execution is required:

it MUST have an explicit allowlist entry;

it MUST be classified CRITICAL by default;

it MUST have a defined scope;

it MUST require explicit human authorization unless a narrowly defined policy exception exists;

the exact command and arguments MUST be auditable;

resource limits MUST apply;

output MUST be captured;

side effects MUST be tracked;

the command MUST NOT be usable as a hidden bypass around declarative tool policy.

A generic shell MUST NOT become an implicit "anything goes" capability.

19. Verification Interface

VerificationEngine {
  verify(taskId, criteria) -> VerificationResult
  collectEvidence(taskId, criterion) -> Evidence[]
  assessIndependence(evidence) -> IndependenceResult
}

VerificationResult {
  status: PASS | FAIL | INCONCLUSIVE
  criterionResults: CriterionResult[]
  evidence: Evidence[]
  verifier: VerifierIdentity
  timestamp: Timestamp
}

Verification Engine owns verification logic.

The Orchestrator only requests verification.

The acting model's claim of success is NOT sufficient evidence by itself.

Verification results are durable and integrity-protected.

Verification actions are tool invocations and MUST pass through Policy.

Independent evidence requirements are defined by VERIFICATION.md and TASK_SCHEMA.md, not created here.

20. Completion Engine Interface

CompletionEngine {
  evaluate(taskId) -> CompletionDecision
}

CompletionDecision {
  decision: DONE | CONTINUE | REPAIR | BLOCKED | WAITING_USER
  reason: CompletionReason
  verificationResult: VerificationResult?
  policyCompliance: ComplianceResult?
  resourceCompliance: ComplianceResult?
}

The Completion Engine is the ONLY component authorized to transition a task into:

DONE

DONE requires, at minimum:

- all mandatory success criteria PASS
- required evidence present and valid
- independent evidence present where required
- policy compliance verified from authoritative audit records
- resource compliance verified from authoritative resource records
- no unresolved critical failures
- VerificationResult durably persisted and integrity-validated
- CompletionDecision durably persisted and integrity-validated

INCONCLUSIVE verification cannot authorize DONE.

The following MUST NOT authorize completion:

model output;

Orchestrator;

Tool;

Subagent;

Runtime;

user-visible text saying "done".

21. Human Approval Interface

HumanApproval {
  request(approvalRequest) -> ApprovalResult
}

ApprovalRequest {
  taskId: TaskId
  operation: OperationId
  target: Target
  arguments: StructuredData?
  riskLevel: RiskLevel
  sideEffect: SideEffect
  reversibility: Reversibility
  idempotency: Idempotency
  explanation: string
  consequences: string
  scope: ApprovalScope
  expiresAt: Timestamp
  policyVersion: string
  ruleVersion: string
  approvalReference: string
  nonce: string
}

ApprovalResult {
  approvalReference: string
  decision: APPROVE | DENY
  approverIdentity: ActorIdentity
  timestamp: Timestamp
}

Approval MUST be:

explicit;

scoped;

time-bounded;

auditable;

associated with the policy version under which it was requested.

Approval MUST NOT silently broaden its scope.

Approval references are single-use and must be consumed during the corresponding authorized execution. Reuse of the same approval reference for a different operation, target, or arguments MUST be denied.

Policy produces the ApprovalRequest when decision is ASK. The Human-in-the-Loop Interface collects the human decision. Policy validates the ApprovalResult before final execution authorization.

Repeated equivalent requests MAY be grouped according to the rules defined in POLICY_RULES.md.

22. Resource Limits

ResourceLimits {
  wallClockTime?: number
  actionSteps?: number
  modelCalls?: number
  retryCount?: number
  delegationCount?: number
  networkUsage?: number
  storageUsage?: number
}

Every autonomous task MUST have externally enforced execution limits.

The model MUST NOT increase its own limits.

23. Resource Coordinator

Shared resource access MUST be coordinated centrally.

ResourceCoordinator {
  acquire(taskId, resource, mode, timeout) -> LockResult
  release(taskId, resource) -> Result
  status(resource) -> ResourceStatus
}

Example resources:

ANDROID_FOREGROUND
ADB_CONNECTION
SHIZUKU
FILESYSTEM_PATH
PACKAGE_MANAGER
DEVICE_SETTINGS
NETWORK_INTERFACE

Modes MAY include:

SHARED
EXCLUSIVE

A subagent MUST acquire required resource access before using a shared resource.

If a resource cannot be acquired safely, the operation MUST wait, retry according to policy, or fail safely.

Subagents MUST NOT implement independent ad-hoc locking mechanisms that bypass the central coordinator.

Resource Coordinator controls resource allocation, locks, and budgets.

It does NOT authorize operations. Policy remains the sole execution authorization authority.

24. Concurrency Model

V1

The system has:

one active top-level autonomous task;

potentially multiple subagents within that task;

centrally coordinated shared-resource access.

Subagents MAY execute concurrently when their scopes and resources permit.

Operations involving exclusive resources MUST be serialized.

Examples:

Two researchers reading different files: → MAY run concurrently.
Two agents modifying the same file: → MUST coordinate.
Two agents changing Android device settings: → MUST coordinate.
Two agents simultaneously controlling Android foreground UI: → MUST NOT run concurrently unless explicitly supported.

Future multi-task support may add stronger task-level isolation and scheduling.

The V1 architecture already contains the required resource coordination boundary.

25. Continuity Interface

ContinuityManager {
  save(taskId, state) -> Result
  load(taskId) -> TaskState
  checkpoint(taskId) -> Checkpoint
  createContinuityBrief(taskId) -> ContinuityBrief
  restore(taskId) -> RecoveryResult
  recordActionStart(taskId, actionId) -> Result
  recordActionEnd(taskId, actionId, terminalState) -> Result
}

Continuity MUST survive:

context compaction;

runtime restart;

process termination;

device restart where persistent storage remains available.

The Action Journal is owned by Continuity Manager. It records STARTED before side effects and terminal states SUCCEEDED/FAILED/CANCELLED/UNKNOWN after execution.

If STARTED cannot be durably persisted, execution MUST NOT proceed.

26. Durable State

Authoritative task state MUST be machine-readable.

Preferred initial implementations:

JSON

or:

SQLite

Markdown MAY be generated as a human-readable projection.

Example:

.pi/tasks/<task-id>/
  task.json
  state.json
  plan.json
  decisions.json
  failures.json
  verification.json
  checkpoints/
  continuity.json
  OBJECTIVE.md
  PLAN.md
  STATE.md
  CONTINUITY.md

The structured representation is authoritative.

Markdown files are derived views unless explicitly designated otherwise.

27. Failure Interface

FailureRecord {
  id: string
  taskId: TaskId
  actionId: string?
  timestamp: Timestamp
  category: string
  operation: string?
  target: Target?
  error: Error
  sideEffectState: SideEffectState
  recoveryAction: RecoveryAction?
  retryCount: number
}

Material failures MUST be persisted.

The system SHOULD use historical failure information to avoid blindly repeating failed approaches.

28. Subagent Interface

SubagentManager {
  delegate(request) -> SubagentResult
}

DelegationRequest {
  taskId: TaskId
  role: ModelRole
  objective: string
  allowedTools: ToolId[]
  allowedScope: TargetAuthorizationContext
  resourceBudget: ResourceLimits
  deadline: Timestamp
}

A subagent:

MUST inherit the parent's maximum authority;

MUST NOT escalate permissions;

MUST NOT modify parent resource limits;

MUST NOT authorize itself;

MUST NOT declare the parent task DONE.

Subagent results MUST be treated as untrusted results until validated.

29. Observability Interface

EventBus {
  emit(event) -> Result
}

Events SHOULD include:

TASK_CREATED
TASK_STARTED
PLAN_CREATED
ACTION_REQUESTED
POLICY_DECISION
APPROVAL_REQUESTED
APPROVAL_GRANTED
APPROVAL_DENIED
RESOURCE_ACQUIRED
RESOURCE_RELEASED
TOOL_STARTED
TOOL_COMPLETED
TOOL_FAILED
SUBAGENT_STARTED
SUBAGENT_COMPLETED
VERIFICATION_STARTED
VERIFICATION_COMPLETED
REPAIR_STARTED
CHECKPOINT_CREATED
COMPACTION_OCCURRED
TASK_RECOVERED
TASK_COMPLETED
TASK_BLOCKED
TASK_CANCELLED

Security-sensitive events MUST be auditable.

Sensitive values MUST be redacted according to SECURITY.md.

30. Error Model

Error {
  code: string
  message: string
  retryable: boolean
  severity: Severity
  context: StructuredData?
}

Example categories:

POLICY_DENIED
APPROVAL_REQUIRED
RESOURCE_LIMIT
RESOURCE_BUSY
INVALID_TASK
INVALID_TOOL_INPUT
TOOL_FAILURE
RUNTIME_FAILURE
MODEL_FAILURE
VERIFICATION_FAILED
VERIFICATION_INCONCLUSIVE
RECOVERY_REQUIRED
UNKNOWN_SIDE_EFFECT

Free-form model output MUST NOT be the sole machine-readable error signal.

31. Authority Model

The system separates authority clearly:

- **User** provides objectives and may grant human approvals.
- **Task Manager** owns authoritative task state, lifecycle transitions (except DONE), and target authorization context.
- **Orchestrator** proposes plans and actions; it does not own authorization, policy, or completion.
- **Policy Engine** authorizes every tool invocation.
- **Verification Engine** produces evidence and results; it does not authorize execution.
- **Completion Engine** authorizes DONE.

The model participates in:

planning;

reasoning;

research;

action proposal;

result interpretation.

The model does NOT own:

authorization;

policy;

resource limits;

verification authority;

completion authority.

32. Replaceability Requirements

The system MUST remain architecturally valid when replacing:

Runtime

Pi → OpenCode → Future Runtime

Model

DeepSeek → Claude → Gemini → Local Model

Android backend

Termux → Shizuku → ADB → Android API

Persistence

JSON → SQLite → Future Storage

Replacement of one implementation MUST NOT require rewriting unrelated Core components.

33. Architecture Enforcement

The following MUST be automatically tested:

Core does not import runtime-specific code.

Core does not import model-provider SDKs.

Core does not import Android APIs.

Tools cannot self-authorize.

Models cannot directly authorize operations.

Orchestrator cannot directly transition a task to DONE.

Completion Engine requires verification.

HIGH/CRITICAL/irreversible actions require required independent evidence.

Resource limits cannot be increased by the model.

Subagents cannot escalate permissions.

Durable state survives context compaction.

Recovery preserves task identity and critical state.

Generic command execution cannot bypass Policy.

Shared-resource operations use ResourceCoordinator.

Approval records preserve policy version and single-use anti-replay.

These tests are architecture gates and MUST run in CI.

Tests for authority separation MUST include adversarial/integration cases, including a fake or malicious model attempting to:

authorize a denied tool;

downgrade risk;

increase resource limits;

declare DONE;

bypass verification;

invoke an unregistered tool;

use generic command execution to bypass policy.

34. Initial Implementation Mapping

Interface | Initial Implementation
TaskManager | Core implementation
PolicyEngine | Core policy module
CompletionEngine | Core completion module
VerificationEngine | Core + verification adapters
Orchestrator | pi-ultracode adapter
RuntimePort | Pi runtime adapter
ModelPort | Provider adapters
ModelRouter | Core routing layer
AndroidRuntime | Android/Termux adapters
ContinuityManager | JSON + Pi session integration
ResourceCoordinator | Core resource coordination layer
SubagentManager | pi-ultracode / Pi subagent adapter
EventBus | Structured local event log

These are implementation choices, not permanent dependencies.

35. Open Questions

The following remain implementation-level or future-defined:

- concrete resource locking mechanisms for platform-specific resources;
- Android permission mapping details;
- exact generic command allowlist content if ever used;
- runtime-specific protected-path mapping details (must be provided before filesystem mutation enabled);
- platform-specific canonicalization edge cases.

Primary documents:

TASK_SCHEMA.md
POLICY_RULES.md
PROTECTED_PATHS.md
SECURITY.md
VERIFICATION.md
CONTINUITY.md
ARCHITECTURE.md
ROADMAP.md

36. Status

INTERFACES.md v0.2 — FROZEN

This document defines architectural contracts, not implementation details.

Any intentional deviation from these interfaces MUST be recorded as an Architecture Decision Record (ADR) in DECISIONS.md.

The architecture MUST NOT silently weaken an interface to accommodate a particular runtime, model, or tool implementation.

# CHANGELOG.md

All notable project changes. Dates are UTC.

## 2026-09-09 — Phase 1 Core Contracts

### Added
- Git repository initialized (branch `main`); Phase 0 baseline commit `103b043`.
- `src/core/` package (Python 3, stdlib only — ADR-001):
  - `enums.py` — frozen value sets (PermissionMode, EffortLevel, TaskState, RiskLevel, SideEffect, Reversibility, Idempotency, PolicyDecisionValue, CompletionDecisionValue per ADR-002, VerificationStatus, SideEffectState, ActionStatus, ActorType, ModelRole, ApprovalDecision, IndependenceLevel, TargetType).
  - `contracts.py` — Task, SuccessCriterion, CompletionContract, PolicyContext, TargetAuthorizationContext, ResourceLimits, Plan, Decision, FailureRecord, VerificationState, Checkpoint, ContinuityState, ActionRequest, PolicyRequest, PolicyDecision, ApprovalRequest, ApprovalResult, Tool, ToolResult, Evidence, VerificationResult, CompletionDecision, Error, Target, ActorIdentity; strict deterministic JSON (de)serialization — unknown fields, missing required fields, and invalid enum values fail closed.
  - `validation.py` — lifecycle transition table (TASK_SCHEMA.md s22), DONE restricted to the Completion Engine path, schema validation (s36), execution gate (s33), target-authorization expiry (s19).
  - `versions.py` — frozen document versions.
- `tests/unit/` — 57 tests: enum contract, lifecycle transitions, task schema and execution gate, contract serialization. All pass on Termux (Python 3.14.6) and Windows (Python 3.14.3).

### Changed
- ROADMAP.md: Phase 1 status NEXT → COMPLETE; Phase 2 (Task Manager) → NEXT.

### Notes
- REQUIREMENTS.md, OBSERVABILITY.md, research/*.md remain empty placeholders: not prerequisites for Phase 1 (audit 2026-09-09).
- Cross-document CompletionDecision discrepancy recorded in ADR-002.
- T-INV-01..25 test IDs referenced by TASK_SCHEMA.md s37 are not yet enumerated anywhere — to be defined in the adversarial-test phase.

## 2026-09-09 — Phase 2 Task Manager

### Added
- `src/continuity/task_store.py` — durable JSON store: atomic writes, SHA-256 integrity envelopes on every stored file, fail-closed loading (tampering/malformed/schema violations raise TaskIntegrityError), task + checkpoint persistence under `.pi/tasks/` (ADR-003).
- `src/task/task_manager.py` — authoritative lifecycle manager implementing INTERFACES.md s7:
  - `create_task` (validated creation in CREATED, unique non-reusable ids, durable before return)
  - `get_task` (read-through from durable state)
  - `update_task` (narrow surface: plan, decisions, failures, verification, continuity, subtask links; no state/objective/criteria/limits/scope changes)
  - `transition` (lifecycle authority per TASK_SCHEMA s22: DONE only via Completion Engine path, resume from WAITING_USER/BLOCKED requires explicit USER/SYSTEM_RECOVERY authorization, RUNNING gated by the s33 execution gate)
  - `cancel` (terminal dead-end enforced)
  - `checkpoint` (durable integrity-protected snapshots per CONTINUITY s6)
  - `recover` (coordination half of recovery: integrity-validated reload, corruption -> BLOCKED, interruptible -> RECOVERING; action-journal inspection deferred to Phase 7)
  - `update_target_authorization` (narrowing only; expansion or denied-target removal rejected; new authorization version per change)
- `tests/unit/test_task_store.py` + `tests/unit/test_task_manager.py` — 48 new tests. Full suite: 105 tests passing on Termux (Python 3.14.6).

### Changed
- ROADMAP.md: Phase 2 status NEXT → COMPLETE; Phase 3 (Policy Engine) → NEXT.
- `.gitignore`: runtime task state under `.pi/` excluded.

### Notes
- Policy Engine evaluation of scope changes (POLICY_RULES s11) is wired in Phase 3.

## 2026-09-09 — Phase 3 Policy Engine

### Added
- `src/policy/registry.py` — v1 operation registries transcribed from POLICY_RULES.md s26-s37, the s20 permission matrix, compatible version pairs, semantic protected-path classes (PROTECTED_PATHS.md s4-s12).
- `src/policy/canonical.py` — deterministic target canonicalization per kind (filesystem path normalization + optional symlink resolution, package id validation, IDNA domain normalization, network destination, Android setting, UI, process, resource); glob scope matching (*, **, ?). Ambiguous or malformed targets fail closed (None -> DENY).
- `src/policy/engine.py` — PolicyEngine.evaluate implementing the POLICY_RULES.md s46 precedence pipeline in order: versions -> task state -> permission mode/effort -> TargetAuthorizationContext -> explicit denial -> protected targets -> capability scope -> registration -> arguments -> mandatory confirmation -> HIGH/CRITICAL non-idempotency -> operation-specific rules -> permission matrix. ASK decisions carry a single-use, time-bounded ApprovalRequest (INTERFACES s10/s21). requested* fields and environmentContext are never authoritative. ProtectedPathRegistry + versioned RuntimePathMapping (missing/stale/ambiguous -> DENY, P-RULE-51/52/53).
- `tests/unit/test_policy_canonical.py` + `tests/unit/test_policy_engine.py` — 64 new tests covering the P-RULE adversarial list. Full suite: 169 tests passing on Termux (Python 3.14.6).

### Changed
- ROADMAP.md: Phase 3 status NEXT → COMPLETE; Phase 4 (Execution Pipeline) → NEXT.

### Notes
- ADR-004 records the strict v1 consequences (undefined matrix rows DENY; deletion has no v1 scope; executable-state set).

## 2026-09-10 — Phase 4 Execution Pipeline

### Added
- `src/continuity/journal.py` — hash-chained append-only Action Journal (SECURITY s22.2 v1 baseline): sequential records, genesis linkage, per-record SHA-256 chain, chain verification on read AND before append (tampered journals fail closed for writes too), derived accounting (action-start counts, consumed approval references).
- `src/execution/pipeline.py` — ExecutionPipeline implementing INTERFACES s12 end to end: schema validation -> policy -> durable policy audit -> ASK flow (durable ASK audit -> human approval -> durable approval audit -> policy-validated, single-use approval) -> resource budget check -> named resource acquisition -> durable ACTION_STARTED -> tool execution -> terminal journal state (SUCCEEDED/FAILED/CANCELLED/UNKNOWN) -> durable failure records. Audit failure on ALLOW/ASK blocks execution; DENY stays DENY; journal payloads carry argument hashes only (s43).
- `src/execution/approval.py` — HumanApproval port + QueueApprover (timeout = DENY semantics).
- `src/execution/resources.py` — minimal ResourceCoordinator: journal-derived action/wallClock budget enforcement (externally enforced), in-process SHARED/EXCLUSIVE named locks.
- `src/policy/engine.py` — added `validate_approval` (reference match, APPROVE, approver identity, expiry).
- `src/continuity/task_store.py` — `journal_for(task_id)`.
- `tests/unit/test_journal.py` + `tests/unit/test_execution_pipeline.py` — 26 new tests: ALLOW/ASK/DENY paths, approval approve/deny/timeout, single-use references, redaction, budget exhaustion, tool failure/exception handling (UNKNOWN side effects), journal tampering fails closed, STARTED-failure blocks execution. Full suite: 195 tests passing on Termux (Python 3.14.6).

### Changed
- ROADMAP.md: Phase 4 status NEXT → COMPLETE; Phase 5 (Verification Engine) → NEXT.

### Notes
- ADR-005 records the external architecture study (OpenHands, LangGraph, Letta): patterns adopted, no third-party code reused, no dependencies added (licenses MIT/MIT/Apache-2.0 would permit reuse but it was not technically justified).

## 2026-09-10 — Phase 5 Verification Engine

### Added
- `src/verification/methods.py` — VerificationMethodSpec registry: each criterion's verificationMethod names a spec (evidence-collection tool + arguments + a deterministic code assessor). Assessors evaluate observed state against the actual requirement; "tool returned success" is an observation, never verification (ROADMAP Phase 5).
- `src/verification/engine.py` — VerificationEngine implementing INTERFACES s19: `verify(taskId, criteria)` evaluates every criterion against freshly collected, policy-governed evidence; `collectEvidence` routes collection through the ExecutionPipeline (VERIFICATION.md s12.1 — verification cannot bypass Policy); `assessIndependence` derives evidence level from collector identity (engine = LEVEL_1, registered separate verifier = LEVEL_2, acting side = LEVEL_0); `invalidate` journals and tombstones stale results. PASS is granted only when every mandatory criterion PASSes with sufficient independence; HIGH/CRITICAL criteria additionally require full provenance metadata (s8). Results are journaled (VERIFICATION_RESULT with content digest) before being written to SHA-256 integrity envelopes.
- `src/verification/result_store.py` — latest result per criterion under `.pi/tasks/<task>/verification/` behind the same integrity envelope as task state; tampered results fail closed on load.
- `src/verification/independence.py` — IndependenceResult + level-by-collector-identity assessment (VERIFICATION.md s9).
- `tests/unit/test_verification_engine.py` — 23 new tests incl. the Phase 5 exit criterion (a technically successful action whose output fails the actual requirement is detected as FAIL), INCONCLUSIVE-never-PASS, HIGH-risk independent-evidence requirement, acting-model-claims insufficiency, policy denial of evidence collection, mutation invalidation, result tampering detection, journal-failure fail-closed. Full suite: 218 tests passing on Termux (Python 3.14.6).

### Changed
- ROADMAP.md: Phase 5 status NEXT → COMPLETE; Phase 6 (Completion Engine) → NEXT.

### Notes
- ADR-006 records the verification design decisions (assessor registry, always-fresh collection, identity-based independence, journal-first invalidation).

## 2026-09-17 — Phase 8 Core Test Suite

### Added
- `tests/support/` — shared test doubles: FakeModel, MaliciousModel (scope expansion, protected targets, forged completion claims, approval reuse), BuggyModel, UncooperativeModel, FakeTool, FakeVerifier (LEVEL_2 separate-verifier evidence), FakeRuntime; plus the shared harness (canonical single import of the Phase 5 wiring).
- `tests/security/` — the 16 ROADMAP-required security tests: unauthorized/protected targets, policy bypass, malformed requests, invalid arguments, scope expansion, subagent escalation, false completion claims, verification failure, INCONCLUSIVE, corrupted state, unknown side effects, resource exhaustion, journal failure, expired authorization, expired human approval.
- `tests/adversarial/` — malicious-model attacks on every authority boundary (scope, protected paths, unknown tools, DONE claims, criteria weakening, limit inflation, fabricated evidence, stored-result forgery, approval reuse).
- `tests/integration/` — end-to-end flows: complete governed flow under fake runtimes, repair loop, crash-recovery loop, model-driven actions.
- `tests/recovery/` — crash scenarios: safe retry, unknown-effect wait + human resume, direct resume, context-loss continuity, remaining-budget inheritance.
- `tests/verification/` — LEVEL_2 separate-verifier flows, approval-gated evidence, verification budget consumption.
- `tests/replaceability/` — Core has no runtime/model/platform imports (static check); model and runtime swaps yield identical security outcomes; hostile runtime environmentContext grants nothing.
- Runner now discovers all categories: `PYTHONPATH=src python3 -m unittest discover -s tests -t .`

### Notes
- ADR-009 records the test-suite architecture and the repair-loop re-entry resolution (REPAIRING -> RECOVERING -> READY -> RUNNING so the execution gate revalidates, resolving the TASK_SCHEMA s22/s33 tension).

### Changed
- `src/execution/pipeline.py` — malformed (non-ActionRequest) requests now return a structured DENY instead of crashing (ExecutionResult was built before validation).
- `src/completion/engine.py` — FIXED staleness detection: the check compared actions against the FIRST verification result and returned early; it now compares against the LATEST result so re-verification (e.g. the repair loop) supersedes earlier actions.

## 2026-09-17 — Phase 9 Fake End-to-End Agent

### Added
- `src/orchestration/orchestrator.py` — Orchestrator: drives CREATED -> VALIDATING -> PLANNING -> READY -> RUNNING -> (propose -> execute -> observe) -> OBSERVING -> VERIFYING -> (verify -> completion decision) loops. REPAIR and CONTINUE re-enter RUNNING through RECOVERING -> READY (ADR-009 gate revalidation); malformed proposals are ignored; model completion claims are never read; WAITING_USER/BLOCKED stop the loop (human-only resume). The orchestrator decides nothing: proposals go through the ExecutionPipeline; DONE comes only from the CompletionEngine.
- `tests/integration/test_orchestrator_flows.py` — creation-to-DONE happy path, repair loop (FAIL -> repair -> DONE, two journaled verification results), malformed proposals ignored, uncooperative model never DONE, crash-during-run recovery then completion.
- `tests/adversarial/test_orchestrator_adversarial.py` — the ROADMAP Phase 9 required demonstration: a malicious FakeModel cannot bypass Policy (scope/protected/unknown-tool/dangerous attempts all DENY, only the legitimate plan step executes), expand scope, declare DONE (claim ignored; DONE journaled only by the engines), or bypass verification (wrong content -> never DONE).

### Notes
- ADR-010 records the orchestrator loop semantics (one action per RUNNING pass, observation stage, CONTINUE re-entry, human states stop the loop, claims never read).

## 2026-09-17 — Phases 10/11 Core side: RuntimePort and ModelPort

### Added
- `src/core/contracts.py` — ToolCall, ModelRequest, ModelResponse, RuntimeSession, RuntimeEvent contracts (INTERFACES s14/s16), provider- and runtime-independent.
- `src/models/` — ModelPort (s14: the Core never knows the provider), ModelRouter (s15: role dispatch; selection grants no authority), ModelPortDriver (adapts a port to the orchestrator; journals every MODEL_CALL with usage so model-call limits are externally enforceable; a ModelRouter binds its role at dispatch).
- `src/runtime/` — RuntimePort (s16: start/send/stop/resume session interface; the Core imports no runtime-specific code).
- `tests/support/fakes.py` — ScriptedModelPort (reference port over scripted ToolCall turns) and FakeRuntimeAdapter (RuntimePort implementation exposing normalized tool-call events), replacing the plain FakeModel at the integration boundary.
- `src/completion/compliance.py` — modelCalls is now a recorded dimension: MODEL_CALL journal events are counted against the limit; exceeding it fails completion (RESOURCE_LIMIT_EXCEEDED). retryCount/delegationCount/network/storage remain unrecorded.
- `tests/integration/test_model_port_flows.py` — 7 tests: orchestrator driven through ModelPort (happy path), model-call accounting + enforcement (repair loop exceeds the budget -> no DONE; within budget -> DONE), malicious tool calls through the port DENIED, router dispatch with no authority from selection, runtime-adapter sessions/events, runtime-driven full flow.

### Changed
- ROADMAP.md: Phases 10 and 11 marked PARTIAL (Core-side complete; concrete PiRuntimeAdapter and provider adapters require the external systems - pi-ultracode API and provider keys). Phase 12 (Termux Runtime) -> NEXT.

### Notes
- ADR-011 records the port-layer decisions, including MODEL_CALL accounting and the PARTIAL status rationale.

## 2026-09-17 — Phase 12 Termux Runtime

### Added
- `src/platforms/termux/filesystem.py` — TermuxFilesystemAdapter: real fs.read_file/list_directory/stat/write_file tools over the device filesystem via pathlib. Mutation tools register only with a concrete versioned protected-path mapping (PROTECTED_PATHS s14); deletion tools are omitted (no v1 delete scope, ADR-004). Failures are KNOWN_FAILED ToolResults, never escaping exceptions.
- `src/platforms/termux/environment.py` — TermuxEnvironmentAdapter: termux_api.battery_status/wifi_status/device_info backed by Termux:API binaries (the three registry rows with complete classifications). Missing binaries report TERMUX_API_UNAVAILABLE - never a fake success.
- `src/platforms/termux/runtime.py` — TermuxRuntimeAdapter: RuntimePort with durable integrity-enveloped sessions under `.pi/sessions/`; sessions survive process restarts and Termux backgrounding (foundation for background execution); binds a ModelPort for normalized tool-call events; local-storage accounting helper.
- `tests/integration/test_termux_platform.py` — 8 tests: real filesystem tools through policy, protected-mapping enforcement on real paths, KNOWN_FAILED filesystem errors, Termux:API unavailable/parsing branches, durable sessions across adapter restarts, tampered session fail-closed, model-port events through the runtime.
- NOTE: the package is `src/platforms/` (not `platform`) - a top-level `platform` package shadows Python's stdlib platform module (imported by uuid) and breaks the interpreter.

### Changed
- ROADMAP.md: Phase 12 status NEXT → COMPLETE; Phase 13 (Termux:API) → NEXT.

### Notes
- ADR-012 records the package-naming gotcha, the v1 tool-exposure policy (only registry-complete rows), and the durable-session design.

## 2026-09-17 — Phase 13 Termux:API

### Added
- `tests/integration/test_termux_api.py` — LIVE device test: real termux-battery-status output flows through policy -> audit -> verification -> completion -> DONE (skipped when the binary is absent); plus the notification fail-closed test: termux_api.send_notification stays DENY (no complete matrix row, ADR-004) even when a tool is registered - the tool is never reached.
- `src/platforms/termux/e2e_demo.py` — on-device end-to-end demonstration (Phase 12 exit criterion): complete governed task with the REAL filesystem adapter (real writes, real reads) from creation to verified DONE. Ran successfully on the device: 9 journal records, final COMPLETION_DECISION.
- `src/models/scripted.py` — ScriptedModelPort moved from tests to src/models as the reference ModelPort adapter (demos and tests share it).

### Changed
- `src/verification/engine.py` — FIXED cross-criterion staleness: verify() now collects evidence for ALL criteria first, then assesses and persists results. Previously a later criterion's evidence-collection action was journaled after an earlier criterion's result and falsely marked it stale (RECOVERY_REQUIRES_REVERIFICATION).
- `src/runtime` renamed to `src/runtimes` (same stdlib/self-shadowing class as platforms: a script inside the package put its own directory ahead of PYTHONPATH).
- ROADMAP.md: Phase 13 status NEXT → COMPLETE; Phase 14 (Android Capability Layer) → NEXT.

### Notes
- ADR-013 records the two-phase verification fix.

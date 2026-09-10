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

## 2026-09-10 — Phase 6 Completion Engine

### Added
- `src/completion/compliance.py` — completion-time compliance checks over authoritative durable records: every ACTION_STARTED must be audited by a preceding non-DENY POLICY_DECISION (with approval records for ASK); resource consumption derived from verified journal records against the externally enforced limits; unrecorded dimensions with set limits fail closed (s13-s16).
- `src/completion/store.py` — CompletionDecisionStore: latest decision per task behind the SHA-256 integrity envelope; tampered decisions fail closed on load.
- `src/completion/engine.py` — CompletionEngine implementing INTERFACES s20: `evaluate(taskId)` checks every applicable DONE condition (s19) in deterministic precedence order (journal integrity -> VERIFYING state -> Completion Contract -> versions known -> critical failures -> durable verification results -> policy compliance -> resource compliance -> mutation staleness -> evidence provenance -> per-criterion PASS/evidence/independence). DONE is journaled (COMPLETION_DECISION), stored behind an integrity envelope, reloaded and integrity-validated, and only then applied via TaskManager.transition(by_completion_engine=True). INCONCLUSIVE structurally cannot produce DONE; only this engine passes the by_completion_engine flag.
- `tests/unit/test_completion_engine.py` — 17 new tests: DONE authorization, model-cannot-declare-DONE, INCONCLUSIVE-never-DONE, REPAIR on mandatory FAIL, WAITING_USER on missing independent evidence, FAILED on critical failure / resource overrun, BLOCKED on policy violation / tampered journal / tampered results / invalid contract, staleness -> re-verification, decision tampering detection. Full suite: 235 tests passing on Termux (Python 3.14.6).

### Changed
- ROADMAP.md: Phase 6 status NEXT → COMPLETE; Phase 7 (Continuity and Recovery) → NEXT.

### Notes
- ADR-007 records the decision-value mapping and the check precedence order.

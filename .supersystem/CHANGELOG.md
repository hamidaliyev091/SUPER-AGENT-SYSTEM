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

## 2026-09-17 — Phase 9 Fake End-to-End Agent

### Added
- `src/orchestration/orchestrator.py` — Orchestrator: drives CREATED -> VALIDATING -> PLANNING -> READY -> RUNNING -> (propose -> execute -> observe) -> OBSERVING -> VERIFYING -> (verify -> completion decision) loops. REPAIR and CONTINUE re-enter RUNNING through RECOVERING -> READY (ADR-009 gate revalidation); malformed proposals are ignored; model completion claims are never read; WAITING_USER/BLOCKED stop the loop (human-only resume). The orchestrator decides nothing: proposals go through the ExecutionPipeline; DONE comes only from the CompletionEngine.
- `tests/integration/test_orchestrator_flows.py` — creation-to-DONE happy path, repair loop (FAIL -> repair -> DONE, two journaled verification results), malformed proposals ignored, uncooperative model never DONE, crash-during-run recovery then completion.
- `tests/adversarial/test_orchestrator_adversarial.py` — the ROADMAP Phase 9 required demonstration: a malicious FakeModel cannot bypass Policy (scope/protected/unknown-tool/dangerous attempts all DENY, only the legitimate plan step executes), expand scope, declare DONE (claim ignored; DONE journaled only by the engines), or bypass verification (wrong content -> never DONE).

### Notes
- ADR-010 records the orchestrator loop semantics (one action per RUNNING pass, observation stage, CONTINUE re-entry, human states stop the loop, claims never read).

## 2026-09-17 — Phases 16-18 Long-Running Autonomy, Delegation, Observability

### Added
- `src/orchestration/orchestrator.py` — `run(..., checkpoint_every=N)`: periodic durable checkpoints every N loop iterations via the ContinuityManager (CONTINUITY s7); snapshots never reset budgets (s14).
- `src/delegation/subagent_manager.py` — SubagentManager: the only delegation path. Subtask creation enforces narrowing (every child scope covered by a parent scope across all scope kinds; every child limit <= parent limit) and consumes the parent's delegationCount budget. A subagent's TAC is its scope, so malicious subagents are denied beyond it by Policy.
- `src/observability/observer.py` — TaskObserver: read-only reconstruction of task state, action history (STARTED/TERMINAL joined), policy decisions, approvals, verification results, failures, recovery decisions, and model usage from durable records. Cannot leak argument secrets (the journal stores hashes only, s43); a tampered journal fails every view closed.
- `tests/integration/test_long_running.py` (3 tests), `test_delegation.py` (6), `test_observability.py` (4) — multi-action runs with monotonic checkpoint accounting, interruption survival with budget inheritance, narrowing/limit/budget enforcement, malicious subagent scope escape denial, subtask completion through the same engines, full-history reconstruction without model memory, secret non-leakage, tamper fail-closed.

### Changed
- ROADMAP.md: Phases 16, 17, 18 -> COMPLETE. Remaining: 10/11/14 PARTIAL (external systems / governance rows), 15 DEFERRED, 19/20 FUTURE.

### Notes
- ADR-014 records delegation narrowing semantics, checkpoint cadence, and the observer's read-only contract.

## 2026-09-17 — GenieX Android bridge: real implementation, device-verified NPU inference

### Added (Android side - committed in the geniex_chat_android project)
- src/main/java/com/geniex/demo/bridge/BridgeProtocol.kt — pure-Kotlin OpenAI-compatible request/response layer (v2 contract): request parsing (text + multimodal image content parts), SAS model-id -> catalog mapping, structured errors, tool-call text convention extraction, data-URL decoding.
- src/main/java/com/geniex/demo/bridge/GenieXBridgeServer.kt — nanohttpd server bound to 127.0.0.1 ONLY (getHostname override): GET /v1/health, POST /v1/chat/completions; structured 400/404/503 errors; per-request logging of TTFT/prefill/decode.
- src/main/java/com/geniex/demo/bridge/BridgeInference.kt — reuses the app's real GenieX SDK instances (ModelManagerWrapper paths -> LlmWrapper/VlmWrapper qairt builders -> applyChatTemplate -> generateStreamFlow); SINGLE-RESIDENT model with swap-on-demand eviction (loading the 7B VLM beside the 4B LLM LMK-kills the process); one serialized gate across load/evict/inference; 120s inference timeout; ProfilingData -> timing capture.
- src/main/java/com/geniex/demo/bridge/GenieXBridgeService.kt — foreground service (specialUse FGS type; connectedDevice requires BT/USB permissions) with START_STICKY restart; bridge survives backgrounding and Activity recreation.
- src/test/java/com/geniex/demo/bridge/BridgeProtocolTest.kt — JVM unit tests (parsing, model mapping, errors, tool-call extraction, response shape).
- UI: "SAS bridge" toggle button in MainActivity.

### Added (SAS side)
- src/models/geniex.py — tool-call TEXT convention: when the SDK cannot emit structured tool calls, JSON tool-call lines in the model text are parsed into ToolCall proposals (Policy still decides); new integration test test_text_convention_tool_calls_flow_through_policy.

### Device verification (OPPO Find X9 Ultra, Android 16)
- APK built and installed via adb; bridge auto-starts with the app.
- 127.0.0.1:8765 verified from Termux (health 200).
- Real Qwen3-4B-Instruct-2507 request: correct answer; TTFT 71-118ms, prefill 127-394 tok/s, decode ~19 tok/s (qairt/HTP0 = NPU).
- Real Qwen2.5-VL-7B-Instruct image request: correct screenshot description; TTFT 393ms, prefill 620 tok/s, decode 11 tok/s.
- Malformed JSON -> 400, unknown model -> 404, 3-way concurrent requests serialized correctly, force-stop + relaunch recovers the bridge.

### Notes
- ADR-018 records the bridge contract v2, the single-resident memory policy, and the FGS decisions.

## 2026-09-18 — Phase 14: real Android capability channel, governed loop closure, operator CLI

The system can now act on the phone. Every part of this phase was added at an
existing extension point; no Core contract changed and the authority chain
(model proposes -> Policy authorizes -> pipeline executes and audits -> code
assessors verify -> Completion alone grants DONE) is intact.

### Added (SAS side)

- `src/platforms/termux/bridge_http.py` — one loopback-only JSON transport
  shared by the inference bridge and the capability bridge (connection,
  timeout, HTTP and protocol failures as stable codes; error bodies are read
  and closed rather than leaked).
- `src/platforms/termux/android_bridge.py` — `AndroidCapabilityBridge`:
  `state()`, `observe()`, `action()`, `execute()` over a CLOSED set of named
  operations (`android.screenshot`, `android.observe_ui`,
  `android.launch_package`, `android.open_url`, `android.global_action`,
  `accessibility.tap`, `accessibility.type_text`). Bearer token on the
  capability endpoints only; the token is never logged, journaled, or placed
  in a ToolResult. `AndroidBridgeError` carries the server's structured code
  verbatim.
- `src/platforms/termux/android_capabilities.py` — `TermuxAndroidCapabilityAdapter`:
  one `Tool` per operation, strict argument validation, structured
  `ToolResult`, error codes mapped onto `KNOWN_FAILED` versus `UNKNOWN`
  (`_NO_EFFECT_CODES` means the device refused before anything happened;
  anything else on an action is a possibly-applied side effect), bounded
  retry only for read-only observations, and a declared
  `ANDROID_FOREGROUND` resource on every operation. Screenshots land in the
  observation store as content-addressed artifacts; the `ToolResult` carries
  only path, digest, size and dimensions.
- `src/continuity/observation_store.py` — `ObservationStore`: enveloped,
  durable observation records; content-addressed immutable artifacts;
  bounded `recent()` reads; `sweep()` retention for records and
  unreferenced artifacts; `render_observation()` for bounded replay into a
  model request. Tampering raises `TaskIntegrityError`.
- `src/verification/android_methods.py` — `default_methods()`: the production
  verification methods, as code assessors (never models):
  `file-content-equals`, `ui-foreground-package-is`, `ui-node-text-present`,
  `foreground-package-changed`, `screenshot-captured`. Expected values are
  read from the criterion's `evidenceRequirements` in a documented
  `key=value` form.
- `src/execution/pending_approval.py` — `PendingApprovalStore` +
  `DurableApprover`: a durable, scoped, time-bounded, single-use grant;
  fail-closed in every ambiguous case; best-effort Termux notification that
  is never authority.
- `src/platforms/termux/cli.py` — the operator CLI
  (`python3 -m platforms.termux.cli`): `run`, `status`, `tasks`, `approve`,
  `deny`, `resume`, `probe`, `setup-bridge`. The operator declares the
  success criteria, the scope, and the limits; the model reaches none of it.

### Changed (SAS side)

- `src/policy/registry.py` — Android capability rows using only existing
  permission-matrix combinations (ADR-019). `android.open_url` is
  EXTERNAL_EFFECT with `forcedDecision=ASK`, so an external effect is a human
  decision in every permission mode; scope is checked before the ASK, so an
  out-of-scope host is DENY rather than a prompt.
- `src/execution/pipeline.py` — an operation that is registered but has no
  implementation in this runtime is refused before a human is asked and
  before anything is marked started: nothing ran, so nothing about its side
  effect is unknown.
- `src/orchestration/orchestrator.py` — loop safety (ADR-020): deadline,
  stall detection on proposal signatures, repeated-failure cap, model-token
  budget from durable usage, every limit ending in BLOCKED with a durable
  `loop_stopped` reason; an action whose terminal state is not durable is
  handed to `RecoveryManager` instead of being retried; an unanswered ASK
  pauses the task in WAITING_USER (ADR-021).
- `src/models/model_port.py` — the loop closes: `observe()` records the
  result as an observation and asks the vision capability to describe an
  image artifact; `build_request()` replays the last K observations, marking
  superseded ones; model calls respect a token budget.

### Added (Android side — committed in the geniex_chat_android project)

- `capability/SasAccessibilityService.kt` — bounded UI observation, screenshot
  capture, global actions, gesture dispatch, and text entry on the focused
  editable node.
- `capability/CapabilityHandler.kt` — executes the closed operation set and
  returns structured JSON; unknown operations are refused.
- `capability/CapabilityProtocol.kt` + JVM unit tests — request/response
  parsing and structured errors, mirroring the bridge protocol.
- `capability/BridgeToken.kt` — generates the bearer token once and copies it
  to the clipboard so the operator can provision it without typing it.
- `GenieXBridgeServer.kt` — `GET /v1/android/state` and
  `POST /v1/android/execute` on the existing loopback server, bearer-gated,
  with the same structured-error shape as the inference endpoints.
- `AndroidManifest.xml` — the accessibility service declared
  `exported="false"` with `BIND_ACCESSIBILITY_SERVICE`; `QUERY_ALL_PACKAGES`
  so launch intents for other packages resolve.

### Tests

- New: `tests/support/capability_server.py` (a test-only reference device
  server, never on the production path), `tests/support/capability_harness.py`
  (the shared governed-loop fixture), and suites for the capability loop,
  the observation loop, loop safety, durable approval, the production
  verification methods, capability tool parity, the observation store, the
  operator CLI, capability security, and transport replaceability.
- Security coverage: an injected observation cannot authorize an action; the
  model cannot widen its own authorization; an external effect is a human
  decision even in scope; the device is not asked when Policy says ASK; the
  CLI offers no shell-shaped tool; a tool is executed in exactly one place;
  the device channel never shells out; the token is never printed and never a
  source literal; the device refuses an operation outside the closed set.
- `tests/integration/test_termux_platform.py`: scope patterns are now derived
  through the same canonicalizer Policy uses. The previous raw form could
  never match on Windows (a `C:\...` path canonicalizes to `/C:/...`), a
  pre-existing Windows-only test defect that passed on POSIX.
- Full suite: 348 -> 495 tests, all green (5 skipped without device binaries).

### Notes

- ADR-019 (capability channel, observation store, loop closure), ADR-020
  (loop safety, including three recorded deviations from the written plan),
  and ADR-021 (durable human approval) record the decisions.
- Out of scope and unchanged: cloud model providers, any privileged path
  (root/adb/shizuku), package install/uninstall, settings writes, arbitrary
  shell, long-term memory, and any new lifecycle state or Core contract.

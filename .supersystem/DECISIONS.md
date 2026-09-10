# DECISIONS.md

Version: 0.1
Status: ACTIVE

Architecture Decision Records (ADRs) for SUPER AGENT SYSTEM. Per PROJECT_CONTRACT.md s2.9 and INTERFACES.md s36, intentional decisions and deviations from the frozen documents are recorded here.

## ADR-001 — Core implementation language and tooling (2026-09-09)

**Decision:** The Core (starting with Phase 1 Core Contracts) is implemented in Python 3 using only the standard library. Tests use `unittest` (stdlib). No third-party dependencies.

**Rationale:**
- Phase 1 requires only data structures, enums, validation, and deterministic JSON — fully covered by the stdlib (`dataclasses`, `enum`, `json`, `typing`, `unittest`).
- Python 3.14 is available in the Termux execution environment (verified 2026-09-09).
- Zero dependencies minimize supply-chain risk (THREAT_MODEL.md TM-18) and follow AGENTS.md dependency rules.

**Consequences:**
- Core remains platform-independent; no model/runtime/platform imports.
- Test command (repo root): `PYTHONPATH=src python3 -m unittest discover -s tests/unit -t tests/unit`
- Later phases may use other tools behind adapters without changing the contracts.

## ADR-002 — CompletionDecision value-set union (2026-09-09)

**Conflict:** INTERFACES.md v0.2 s20 lists CompletionDecision values `{DONE, CONTINUE, REPAIR, BLOCKED, WAITING_USER}`; VERIFICATION.md v0.5 s17 lists `{DONE, REPAIR, WAITING_USER, BLOCKED, FAILED}`. Both documents are frozen, and neither list marks the other as exhaustive.

**Decision:** The Core implements the union: `{DONE, CONTINUE, REPAIR, BLOCKED, WAITING_USER, FAILED}` (see `src/core/enums.py` CompletionDecisionValue).

**Rationale:**
- The union removes no documented semantics and weakens nothing. DONE authorization rules (VERIFICATION.md s19) are unchanged.
- Adding values is representation-only; no frozen security boundary is altered.

**Consequences:**
- Phase 6 (Completion Engine) must emit only values from this union; reason codes follow VERIFICATION.md s18.
- A future governance revision should reconcile the two frozen lists explicitly.

## ADR-003 — Phase 2 storage layout and update surface (2026-09-09)

**Context:** INTERFACES.md s26 shows an example multi-file durable-state layout (task.json, state.json, plan.json, decisions.json, failures.json, verification.json, checkpoints/, continuity.json + derived Markdown). The Task Manager needs persistence in Phase 2, while the full Continuity Manager (Action Journal, hash-chained audit, recovery inspection) arrives in Phase 7.

**Decision:**
1. Durable state is one authoritative `task.json` per task (the full Task object) plus one integrity-protected file per checkpoint under `checkpoints/`, stored under `.pi/tasks/<task-id>/` by default (gitignored). Derived Markdown views are not generated yet.
2. Every stored file is wrapped in an integrity envelope: `{"payload": <contract dict>, "integrity": {"algorithm": "sha256", "digest": <sha256 of canonical compact JSON>}}`. Loading fails closed on any tampering, malformed JSON, or schema violation (TaskIntegrityError).
3. `TaskManager.update_task` has a deliberately narrow surface: plan, appended decisions/failures, verification state, continuity state, subtask links. State changes go through `transition()` only; target scopes go through `update_target_authorization()` which permits narrowing only (TASK_SCHEMA.md s15/s16).

**Rationale:** Single authoritative file + integrity envelope gives tamper detection (SECURITY.md s27) and atomic durability (CONTINUITY.md s4) with minimal machinery. The multi-file layout and append-only hash-chained audit log remain Phase 7 Continuity Manager concerns; nothing here blocks that evolution.

**Consequences:** The Phase 7 Continuity Manager may restructure storage (e.g., split files, hash chains) behind the TaskStore interface without changing the TaskManager contract. The envelope format must stay versioned (currently implicit v1).

## ADR-004 — Policy Engine decisions (2026-09-09)

**Context:** Phase 3 implements the POLICY_RULES.md s46 evaluation pipeline verbatim. Four points needed explicit, recorded decisions because the frozen documents leave them to implementation judgment or produce conservative literal results.

**Decisions:**
1. **Executable task-state set** = {RUNNING, OBSERVING, VERIFYING, REPAIRING}. POLICY.md s5 lists only the states that must not permit execution; the lifecycle specification shows execution begins at RUNNING. RECOVERING requires recovery validation before continuation (CONTINUITY.md s27), so it is excluded. PLANNING/READY precede execution and are excluded.
2. **Undefined matrix rows are DENY, literally.** Operations whose registry rows lack reversibility/idempotency values (process.*, termux_api.send_notification, settings.write, package.install) fall on matrix rows that do not exist (e.g. MEDIUM MUTATING IRREVERSIBLE) and therefore evaluate to DENY in every mode (POLICY_RULES.md s20). No classifications were invented (s1: "The Policy Engine MUST NOT invent missing rules"). A governance revision may add explicit reversibility/idempotency values to re-enable these operations.
3. **Filesystem deletion has no v1 authorizing scope.** The TargetAuthorizationContext has read/write/search scopes only, and P-RULE-18 forbids treating write scope as delete scope. fs.delete_file / fs.delete_directory therefore always DENY in v1. Accessibility tap/type_text/submit follow their s31 operation-specific ASK rules.
4. **Protected-path runtime mapping is mandatory before filesystem mutation** (PROTECTED_PATHS.md s14): missing (P-RULE-51), ambiguous (P-RULE-52), or stale (P-RULE-53) mappings DENY all filesystem mutation. Reads are not blocked by the protected registry (PROTECTED_PATHS.md s18). The environmentContext of a PolicyRequest is untrusted data and is never read for authority (P-RULE-14).

**Consequences:** v1 policy is intentionally strict; the registry data in `src/policy/registry.py` is the single place where a future governance revision changes classifications. Phase 4 (Execution Pipeline) wires audit-before-execution and the Action Journal around this engine without changing its semantics.

## ADR-005 — External architecture study: adopt patterns, reject code reuse (2026-09-10)

**Context:** Before Phase 4, a comparative study of mature open-source agent runtimes was performed against SAS requirements (authority model, fail-closed policy, durable state, verification, protected targets).

**Studied systems and findings:**

1. **OpenHands (MIT)** — event-stream architecture (actions/observations as causal event pairs), append-only JSONL event persistence with session markers ("event sourcing = free recovery"), secrets redaction before storage, SecurityAnalyzer + confirmation mode (WAITING_FOR_CONFIRMATION, approve/reject pending actions). Weakness relative to SAS: its LLM-annotated `security_risk` tool parameter makes risk classification model-trusted — a documented crash class (issue #11309) when the analyzer is missing. SAS resolves authoritative risk from the versioned registry and treats model-requested risk as informational only.
2. **LangGraph / langchain (MIT)** — durable execution via checkpointers and thread ids; "sync" durability mode (persist before continuing) matching SAS write-through discipline; interrupt()/Command(resume) with per-node restart. Weakness: pre-interrupt side effects must be idempotent by *developer discipline*; SAS enforces the equivalent guarantee with UNKNOWN side-effect state and no-blind-replay journal rules.
3. **Letta / MemGPT (Apache-2.0)** — typed persistent memory blocks (persona/human/task), agent-scoped (not conversation-scoped) memory, git-backed memory auditability, sleep-time compute. Relevant to the Phase 7 Continuity Brief and later memory phases; not needed for Phase 4.
4. **No strong prior art found for SAS-style verification/completion authority** — surveyed frameworks rely on model self-claims or human judgment for completion. The SAS Verification Engine + Completion Engine design (VERIFICATION.md) remains the strictest approach seen and is retained unchanged.

**Decision: proceed with the current architecture; adopt patterns, reject code reuse.**

Adopted into Phase 4: (a) append-only per-task journal with sequential ids and task identity (OpenHands event sourcing shape, plus the SECURITY.md s22.2 hash chain OpenHands lacks); (b) arguments hashed before journal storage — never raw (OpenHands redaction + POLICY_RULES s43); (c) causal action→terminal linking via actionId; (d) sync write-through before execution (LangGraph "sync" mode + CONTINUITY.md s8.2). ExecutionResult carries structured outcome state for replay/observation.

Rejected: importing OpenHands, LangGraph, Letta, or their components as dependencies. Reasons: all three are heavyweight, multi-dependency frameworks that would violate PROJECT_CONTRACT s2.1-s2.3 (Core must not depend on a runtime/model framework) and burden the stdlib-only Core on Termux; their security models are materially weaker than SAS policy (model-trusted risk; no integrity-protected audit chain). Their licenses (MIT, MIT, Apache-2.0 respectively) would permit reuse, but reuse is not technically justified. No third-party source code was copied; no copyright notices are required. Provenance: study performed 2026-09-10 via official docs/repos (OpenHands GitHub + SDK docs; LangGraph durable-execution/interrupts docs; Letta docs/MemGPT materials).

**Consequences:** `src/continuity/journal.py` is the single durable audit mechanism (policy decisions, approvals, action STARTED/terminal) and the authoritative source for resource accounting (action-step counts derived from verified records). Phase 7 extends recovery on top of it; observability (Phase 18) reads it.

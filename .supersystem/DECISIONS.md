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

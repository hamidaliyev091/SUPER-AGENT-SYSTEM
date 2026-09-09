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

# Test Invariants (T-INV-01..25)

TASK_SCHEMA.md s37 references test IDs T-INV-01..T-INV-25 "to be defined in the
adversarial-test phase". That phase (ROADMAP Phase 8) is complete; this document
enumerates the IDs and maps each to its covering tests. Every T-INV fails closed:
the expected behavior is always DENY / no execution / no DONE / BLOCKED.

| ID | Invariant | Expected behavior | Covering tests |
|----|-----------|-------------------|----------------|
| T-INV-01 | Unauthorized target | DENY, tool never reached | `tests/security/test_security_invariants.py::test_unauthorized_target_denied` |
| T-INV-02 | Protected target | DENY (P0 classes) | `test_protected_target_denied`; `tests/integration/test_termux_platform.py::test_filesystem_mutation_respects_protected_mapping` |
| T-INV-03 | Policy bypass attempt | requested* fields are advisory; DENY stands | `test_policy_bypass_attempt_denied` |
| T-INV-04 | Malformed PolicyRequest | structured DENY, no crash | `test_malformed_policy_request_denied` |
| T-INV-05 | Invalid arguments | DENY on missing required args | `test_invalid_arguments_denied` |
| T-INV-06 | Scope expansion | DENY outside the TargetAuthorizationContext | `test_scope_expansion_denied`; `tests/adversarial/test_malicious_model.py::test_malicious_scope_expansion_never_executes` |
| T-INV-07 | Subagent privilege escalation | subagent scoped to its own TAC; escape DENIED | `test_subagent_privilege_escalation_denied`; `tests/integration/test_delegation.py::test_malicious_subagent_cannot_escape_its_scope` |
| T-INV-08 | False completion claim | model claims have no effect; only the Completion Engine grants DONE | `test_false_completion_claim_has_no_effect`; `tests/adversarial/test_orchestrator_adversarial.py::test_malicious_completion_claim_ignored_by_orchestrator` |
| T-INV-09 | Verification failure | FAIL criterion blocks DONE (REPAIR) | `test_verification_failure_blocks_done` |
| T-INV-10 | INCONCLUSIVE | INCONCLUSIVE never produces DONE | `test_inconclusive_blocks_done`; `tests/unit/test_completion_engine.py::test_inconclusive_never_produces_done` |
| T-INV-11 | Corrupted durable state | integrity failure, no silent reconstruction | `test_corrupted_durable_state_rejected`; `tests/unit/test_task_store.py` |
| T-INV-12 | Unknown side effect | never blind-retried; WAITING_USER / VERIFY_FIRST | `test_unknown_side_effect_never_blind_retried`; `tests/unit/test_recovery.py` |
| T-INV-13 | Resource exhaustion | budget enforced from journal; overrun blocks | `test_resource_exhaustion_blocks`; `tests/integration/test_long_running.py::test_budget_never_resets_across_continuation` |
| T-INV-14 | Action Journal failure | STARTED not durable -> no execution | `test_action_journal_failure_blocks_execution` |
| T-INV-15 | Expired authorization | inactive TAC denies and blocks recovery | `test_expired_authorization_denied`; `tests/unit/test_recovery.py::test_expired_authorization_blocks` |
| T-INV-16 | Expired human approval | policy-validated approvals are time-bounded | `test_expired_human_approval_rejected` |
| T-INV-17 | Journal tampering | hash chain fails closed on read and append | `tests/unit/test_journal.py`; `tests/unit/test_completion_engine.py::test_tampered_journal_blocks`; `tests/integration/test_observability.py::test_tampered_journal_makes_views_fail_closed` |
| T-INV-18 | VerificationResult tampering | integrity envelope fails closed; DONE forbidden | `tests/unit/test_completion_engine.py::test_tampered_verification_result_blocks` |
| T-INV-19 | CompletionDecision tampering | enveloped decision fails closed on load | `test_completion_decision_tampering_detected` |
| T-INV-20 | Acting-model evidence | Level 0 self-evidence insufficient where independence required | `tests/adversarial/test_malicious_model.py::test_malicious_fabricated_evidence_insufficient`; `tests/unit/test_verification_engine.py` independence tests |
| T-INV-21 | Model-call limits | MODEL_CALL journal events enforce the budget | `tests/integration/test_model_port_flows.py::test_model_calls_are_accounted_and_enforced` |
| T-INV-22 | Delegation narrowing | subagent scopes/limits subset of parent; expansion rejected | `tests/integration/test_delegation.py` |
| T-INV-23 | Core purity | no runtime/model/platform imports in src/core | `tests/replaceability/test_replaceability.py::test_core_has_no_runtime_or_model_imports`; `ci.sh` |
| T-INV-24 | Runtime/model replaceability | swaps do not change authorization/verification/completion outcomes | `tests/replaceability/test_replaceability.py`; `tests/integration/test_end_to_end_flows.py::test_complete_flow_under_fake_runtime` |
| T-INV-25 | Malicious model end-to-end | a hostile model cannot reach DONE by its own actions | `tests/adversarial/test_orchestrator_adversarial.py` |

All T-INV tests run in the CI gate (`./ci.sh`): full suite + Core purity.

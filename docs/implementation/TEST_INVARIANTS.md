# Test Invariants (T-INV-01..38)

TASK_SCHEMA.md s37 references test IDs T-INV-01..T-INV-25 "to be defined in the
adversarial-test phase". That phase (ROADMAP Phase 8) is complete; this document
enumerates the IDs and maps each to its covering tests. Every T-INV fails closed:
the expected behavior is always DENY / no execution / no DONE / BLOCKED.

T-INV-26..38 extend the same discipline to the Android capability channel and
the operator CLI (ROADMAP Phase 14): a device the agent can drive is exactly
where an invariant is most worth stating, because the consequence of failing
one is a physical action rather than a line of state.

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
| T-INV-26 | Injected observation | what the device shows is data, never authority: instruction-shaped screen text authorizes nothing | `tests/security/test_capability_security.py::test_an_injected_observation_cannot_authorize_an_action` |
| T-INV-27 | Self-widening authorization | model output carrying config-shaped JSON changes no scope, criterion or limit | `test_the_model_cannot_widen_its_own_authorization` |
| T-INV-28 | External effect | an out-of-scope host is DENY; an in-scope one is ASK in every permission mode, never an ALLOW | `test_an_external_effect_is_a_human_decision_even_in_scope`; `test_a_tap_out_of_scope_is_denied_not_asked`; `tests/integration/test_android_capability_loop.py::test_an_ask_gated_action_pauses_and_completes_on_resume` |
| T-INV-29 | ASK is not executable | when Policy says ASK the device is not touched before a human decides | `test_the_device_is_not_asked_when_policy_says_ask`; `tests/integration/test_android_capability_loop.py::test_a_denied_approval_runs_nothing`; `tests/integration/test_pending_approval.py::test_ask_pauses_the_task_and_executes_nothing` |
| T-INV-30 | No shell surface | no tool, and no device operation, runs a command | `test_the_cli_offers_only_filesystem_and_capability_tools`; `test_the_device_channel_never_shells_out`; `tests/unit/test_capability_tool_parity.py::test_no_operation_reaches_the_device_through_a_shell`; `tests/integration/test_android_capability_loop.py::test_the_channel_exposes_named_operations_and_no_shell` |
| T-INV-31 | Single execution site | `Tool.execute` is called in exactly one place (the pipeline) | `test_a_tool_is_executed_in_exactly_one_place` |
| T-INV-32 | Limit stop is not a completion | a run stopped for a limit is BLOCKED with a durable reason, never DONE | `tests/integration/test_loop_safety.py::test_a_limit_stop_is_never_reported_as_done`; `test_iteration_limit_blocks_with_a_durable_reason`; `test_deadline_blocks_the_run`; `test_stalling_proposal_blocks_the_run` |
| T-INV-33 | Unknown side effect is not retried | an action without a durable terminal record goes to recovery, never to a blind retry | `tests/integration/test_loop_safety.py::test_action_without_a_durable_terminal_record_is_not_retried`; `test_action_without_a_terminal_record_blocks_without_recovery` |
| T-INV-34 | Credential hygiene | the device-control token is never printed, never world-readable, never a source literal | `tests/security/test_capability_security.py::TokenHygieneTests` |
| T-INV-35 | Device refusal is structured and bounded | bad token, unknown operation and unavailable capability fail closed with stable codes and bounded retry | `tests/integration/test_android_capability_loop.py::test_a_wrong_token_is_refused_and_nothing_runs`; `test_an_unavailable_capability_is_bounded_and_blocks`; `test_the_device_refuses_an_operation_outside_the_closed_set`; `test_the_client_refuses_an_operation_outside_the_closed_set` |
| T-INV-36 | Transport replaceability (device) | the governed outcome and the refusals are identical over HTTP and in process | `tests/replaceability/test_capability_replaceability.py` |
| T-INV-37 | Unimplemented operation | a registered operation with no implementation is refused, never escalated to a human and never reported as an unknown side effect | `tests/unit/test_execution_pipeline.py::MissingImplementationTests`; `tests/unit/test_capability_tool_parity.py::test_a_registered_operation_without_an_implementation_is_not_offered` |
| T-INV-38 | Observation integrity | observations are tamper-evident and retention-bounded; a corrupted artifact is never returned as valid | `tests/unit/test_observation_store.py` |

All T-INV tests run in the CI gate (`./ci.sh`): full suite + Core purity.

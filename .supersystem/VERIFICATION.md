VERIFICATION.md

Version: 0.5
Status: APPROVED / FROZEN
Authority: Verification and Completion Specification
Depends on: PROJECT_CONTRACT.md, ARCHITECTURE.md, INTERFACES.md, TASK_SCHEMA.md, POLICY.md

---

1. Purpose

This document defines how the system determines whether an autonomous task has actually succeeded and whether it may transition to "DONE".

The central rule is:

«The acting model may perform work and report that it believes the task is complete, but it may never authorize completion by its own claim.»

Completion is an independently evaluated system decision based on:

1. explicit success criteria;
2. independently obtained evidence;
3. verification results;
4. authoritative Policy audit records;
5. authoritative resource-usage records;
6. persisted task state;
7. applicable security and integrity requirements.

No model-generated statement such as “done”, “completed”, “successful”, or equivalent constitutes completion evidence by itself.

---

2. Core Principles

V-PRINCIPLE-01 — Observable Success

Every autonomous task MUST have explicit, observable success criteria before autonomous execution begins.

V-PRINCIPLE-02 — No Model Self-Completion

The acting model MUST NOT authorize or directly cause the final "DONE" state.

V-PRINCIPLE-03 — PASS / FAIL / INCONCLUSIVE

Verification results MUST distinguish:

- "PASS"
- "FAIL"
- "INCONCLUSIVE"

"INCONCLUSIVE" MUST NEVER be treated as "PASS".

V-PRINCIPLE-04 — Independent Evidence

HIGH, CRITICAL, and irreversible operations MUST have independently obtained evidence sufficient to establish the relevant success criterion.

V-PRINCIPLE-05 — Durable Completion

The final "CompletionDecision" MUST be durably persisted and integrity-validated before "DONE" becomes effective.

V-PRINCIPLE-06 — Policy and Resource Compliance

Successful functional verification alone is insufficient.

Before "DONE", the Completion Engine MUST also verify compliance using authoritative:

- Policy audit records;
- approval records where applicable;
- resource-usage records;
- execution-limit records.

V-PRINCIPLE-07 — Mutation Invalidates Verification

Any material mutation affecting a previously verified criterion MUST invalidate the affected verification result and require re-verification.

V-PRINCIPLE-08 — Recovery Is Not Completion

After crash, interruption, uncertain side effects, or context compaction, the system MUST NOT assume that the previous execution state was successfully completed.

Affected criteria MUST be re-evaluated as required.

---

3. Autonomous Task Definition

For purposes of this specification:

«An autonomous task is any task in which the system is authorized to independently perform one or more actions toward an objective without requiring the user to explicitly authorize each individual action.»

A task MUST be treated as autonomous if ANY of the following applies:

1. it may perform a side-effecting operation;
2. it executes under "AUTO" permission mode;
3. it executes under "DANGEROUS" permission mode;
4. it performs more than one autonomous action step;
5. it delegates work to one or more subagents;
6. it is expected to continue execution across multiple model/tool turns without individual user approval;
7. it has an execution loop involving observation, retry, repair, or verification.

Every autonomous task MUST have a persisted Completion Contract before autonomous execution begins.

3.1 Non-Autonomous Assistance

Purely interactive assistance MAY operate without a Completion Contract when all of the following are true:

- no autonomous side-effecting operation is performed;
- no autonomous execution loop is initiated;
- the system does not continue acting without per-action user direction;
- the interaction is informational, observational, or explanatory.

Examples include:

- explaining a command;
- answering a question;
- analyzing user-provided text;
- describing possible implementation approaches.

If classification is ambiguous, the system MUST classify the task as autonomous and require a Completion Contract.

---

4. Completion Contract

Every autonomous task MUST have a Completion Contract containing:

- task objective;
- requirements;
- success criteria;
- verification method for each criterion;
- evidence requirements;
- applicable risk classification;
- required independence level;
- applicable resource constraints;
- applicable policy constraints.

The Completion Contract MUST be persisted before autonomous execution begins.

The acting model MUST NOT be able to silently weaken, remove, or redefine mandatory success criteria.

Any modification to the Completion Contract after execution begins MUST be treated as a controlled task change and MUST invalidate affected verification results.

---

5. Success Criteria

Each criterion MUST be:

- observable;
- testable;
- sufficiently specific;
- associated with a verification method;
- associated with evidence requirements;
- marked mandatory or non-mandatory.

A criterion that cannot be objectively evaluated MUST NOT authorize "DONE".

If mandatory criteria are missing, vague, contradictory, or unverifiable, the task MUST enter an appropriate non-complete state such as:

- "WAITING_USER";
- "BLOCKED";
- "REJECTED".

---

6. Verification Result

Each criterion produces a structured "VerificationResult".

Conceptually:

VerificationResult {
    taskId
    criterionId

    result:
        PASS | FAIL | INCONCLUSIVE

    evidence[]
    verifier
    independenceLevel

    verificationMethod
    evidenceFreshness

    timestamp
    failureReason
    notes

    integrityMetadata
}

The result MUST be durably persisted.

For security-sensitive or high-risk criteria, the persisted result MUST have integrity protection sufficient to detect unauthorized modification.

---

7. Evidence

Evidence MUST be derived from observable system state or another authorized evidence source.

Evidence MAY include:

- command output;
- application state;
- filesystem state;
- API response;
- test result;
- database state;
- runtime telemetry;
- external service state;
- user-confirmed state;
- independent verifier observation.

The acting model's narrative is not sufficient evidence.

---

8. Evidence Provenance

Evidence MUST contain sufficient provenance to establish:

- what produced it;
- when it was produced;
- what resource or state it describes;
- how it was collected;
- whether it has been modified;
- whether it remains applicable to the current task state.

For HIGH, CRITICAL, or irreversible operations, evidence MUST include:

- timestamp;
- source identity;
- content hash or equivalent integrity mechanism;
- collector identity;
- provenance;
- validation status.

---

9. Independence Levels

Verification MUST distinguish at least:

Level 0 — Self Evidence

Evidence directly produced by the acting operation or acting model.

Useful for ordinary observations but insufficient where independent verification is mandatory.

Level 1 — Independent Runtime Evidence

Evidence obtained independently from the acting model's assertion.

Examples:

- reading actual filesystem state;
- querying actual application state;
- running an independent test;
- querying an external service.

Level 2 — Separate Verifier

A separate verifier independently evaluates the Completion Contract using independently obtained evidence.

---

10. Separate Model Verifier

When a separate model is used as a verifier:

1. it MUST NOT receive an acting model completion claim or reasoning that is not independently verifiable;
2. it MUST independently obtain or access the required evidence;
3. it MUST evaluate the evidence against the Completion Contract;
4. its result MUST be based on independently accessed evidence;
5. the acting model MUST NOT modify, filter, selectively present, or otherwise control the evidence supplied for verification.

A separate model merely reviewing the acting model's report is not independent verification.

---

11. High-Risk and Irreversible Operations

HIGH, CRITICAL, and irreversible operations require independent evidence.

If sufficient independent evidence is unavailable:

VerificationResult = INCONCLUSIVE

and:

DONE = FORBIDDEN

The system MUST instead:

- acquire additional evidence;
- request user verification where appropriate;
- repair/retry;
- or enter "WAITING_USER" / "BLOCKED".

---

12. Verification Lifecycle

The expected lifecycle is:

RUNNING
   ↓
OBSERVING
   ↓
VERIFYING
   ↓
 ┌───────────────┐
 │               │
PASS            FAIL
 │               │
 ↓               ↓
DONE          REPAIRING
                 ↓
               RUNNING

"INCONCLUSIVE" does not transition to "DONE".

It may transition to:

- "VERIFYING";
- "REPAIRING";
- "WAITING_USER";
- "BLOCKED".

12.1 Verification Actions Are Policy-Governed

Every action performed to obtain, inspect, generate, or validate verification evidence is itself a tool invocation or system operation.

Therefore:

«Verification does not bypass Policy.»

Examples include:

- running tests;
- reading files;
- querying application state;
- querying databases;
- inspecting processes;
- checking network state;
- invoking Android APIs;
- executing commands;
- collecting telemetry.

All such actions MUST pass through the Policy Engine under the same authorization, audit, resource, and security requirements applicable to other tool invocations.

The Verification Engine MUST NOT create a hidden execution path around Policy.

---

13. Policy Compliance Verification

Before "DONE", the Completion Engine MUST verify the authoritative Policy audit trail.

It MUST establish that:

1. every required tool invocation passed Policy;
2. no required authorization was bypassed;
3. all required approvals were obtained;
4. approvals were valid for the relevant operation;
5. approval records were durably recorded;
6. the applicable policy and rule versions are known;
7. no policy violation occurred;
8. no unauthorized privilege escalation occurred.

The Completion Engine MUST use authoritative records rather than relying on the acting model's report.

---

14. Policy Record Integrity

Policy audit records are completion-critical records.

If required Policy audit records are:

- missing;
- unavailable;
- incomplete;
- inconsistent;
- corrupted;
- tampered with;
- impossible to integrity-validate;

the Completion Engine MUST NOT authorize "DONE".

The task MUST instead enter an appropriate state such as:

- "INCONCLUSIVE";
- "BLOCKED";
- "FAILED";
- "WAITING_USER".

Exact integrity and availability mechanisms are defined by "SECURITY.md" and related persistence specifications.

---

15. Resource Compliance Verification

Before "DONE", the Completion Engine MUST verify authoritative resource-usage records.

It MUST establish that:

- wall-clock limits were respected;
- action-step limits were respected;
- model-call limits were respected;
- retry limits were respected;
- delegation limits were respected;
- applicable storage/network/resource limits were respected;
- no execution limit was silently modified or exceeded.

The acting model's report MUST NOT be considered authoritative for resource consumption.

---

16. Resource Record Integrity

Required resource-usage records are completion-critical.

If required records are:

- missing;
- unavailable;
- incomplete;
- inconsistent;
- corrupted;
- tampered with;
- impossible to integrity-validate;

the Completion Engine MUST NOT authorize "DONE".

Resource compliance MUST be treated as "INCONCLUSIVE" until authoritative records are restored or independently validated.

---

17. Completion Decision

The Completion Engine produces a structured "CompletionDecision".

Conceptually:

CompletionDecision {
    taskId

    decision:
        DONE
        REPAIR
        WAITING_USER
        BLOCKED
        FAILED

    reasonCode

    criterionResults[]
    policyCompliance
    resourceCompliance

    evidenceReferences[]
    verificationReferences[]

    timestamp
    policyVersion
    schemaVersion

    integrityMetadata
}

Only the Completion Engine may authorize "DONE".

---

18. Completion Reason Codes

The implementation SHOULD define stable reason codes including at minimum:

ALL_CRITERIA_PASSED
MANDATORY_CRITERION_FAILED
VERIFICATION_INCONCLUSIVE
INDEPENDENT_EVIDENCE_MISSING
POLICY_COMPLIANCE_FAILED
POLICY_RECORD_UNAVAILABLE
POLICY_RECORD_INTEGRITY_FAILED
RESOURCE_LIMIT_EXCEEDED
RESOURCE_RECORD_UNAVAILABLE
RESOURCE_RECORD_INTEGRITY_FAILED
CRITICAL_FAILURE_PRESENT
DURABLE_STATE_UNAVAILABLE
COMPLETION_RECORD_INTEGRITY_FAILED
RECOVERY_REQUIRES_REVERIFICATION
USER_VERIFICATION_REQUIRED

Reason codes MUST be machine-readable and stable across runtime implementations.

---

19. DONE Authorization Rules

"DONE" is permitted only if ALL applicable conditions are satisfied:

1. the task is autonomous and has a valid Completion Contract;
2. every mandatory success criterion is "PASS";
3. required evidence is present;
4. evidence is sufficiently fresh;
5. required independent evidence exists;
6. HIGH/CRITICAL/irreversible operations have required independent evidence;
7. no criterion is "INCONCLUSIVE";
8. no mandatory criterion has "FAIL";
9. Policy compliance passes;
10. authoritative Policy records are present and integrity-valid;
11. resource compliance passes;
12. authoritative resource records are present and integrity-valid;
13. no critical unresolved failure exists;
14. required security checks pass;
15. durable task state has been persisted;
16. all required "VerificationResult" records are persisted and integrity-valid;
17. the "CompletionDecision" is persisted and integrity-validated;
18. applicable execution limits were respected.

Only after these conditions are satisfied may the task transition to "DONE".

---

20. Verification Invalidation

Verification MUST be invalidated when a material mutation affects the verified criterion.

For initial implementation purposes, a mutation SHOULD be considered material when it:

- changes a resource directly referenced by a success criterion;
- changes a resource whose state is used as verification evidence;
- changes configuration that can affect the criterion's result;
- changes application/system state relevant to the criterion;
- changes an external state on which the criterion depends;
- changes the Completion Contract itself;
- performs an operation capable of invalidating previously collected evidence.

Examples include:

- modifying a verified file;
- changing configuration;
- changing application state;
- changing relevant external state;
- changing task requirements;
- changing success criteria;
- executing additional actions that could affect the result;
- discovering previously unknown side effects.

If uncertainty exists about whether a mutation is material, the implementation MUST conservatively treat the affected verification as invalid and re-verify.

The authoritative taxonomy and implementation rules for material mutations will be defined in "TASK_SCHEMA.md" and related implementation specifications.

Invalidated results MUST NOT be reused as evidence for "DONE".

---

21. Repair Loop

When verification fails:

VERIFY
  ↓
FAIL
  ↓
REPAIRING
  ↓
RUNNING
  ↓
OBSERVING
  ↓
VERIFYING

Each repair cycle MUST:

- persist the failure;
- preserve previous evidence;
- record the repair action;
- enforce resource limits;
- re-run affected verification;
- avoid treating previous failed verification as current success.

Retries MUST NOT silently increase execution limits.

---

22. Failure Handling

Verification failures MUST produce structured failure records.

A failure SHOULD identify:

- criterion;
- operation;
- failure class;
- evidence;
- timestamp;
- attempted repair;
- retry count;
- current state;
- whether the failure is recoverable.

Critical failures MUST prevent "DONE" until resolved or explicitly handled according to the governing policy.

---

23. Crash and Recovery

If execution terminates unexpectedly:

1. task state MUST be recovered from durable state;
2. unknown side effects MUST be treated as uncertain;
3. previous completion claims MUST NOT be trusted;
4. affected criteria MUST be re-verified;
5. Policy records MUST be checked;
6. resource records MUST be checked;
7. execution MUST resume only within remaining limits.

A crash MUST never implicitly produce "DONE".

---

24. Context Compaction

Context compaction MUST NOT be considered equivalent to task completion or verification.

After compaction:

- durable task state remains authoritative;
- Completion Contract remains authoritative;
- previous verification remains valid only if its evidence and state remain valid;
- the model's reconstructed memory does not replace durable records.

If compaction may have caused loss of relevant execution context, the system MUST use durable state and re-verification rather than model recollection.

---

25. Completion Integrity Chain

The required integrity chain is:

Observed State
      ↓
Evidence
      ↓
VerificationResult
      ↓
CompletionDecision
      ↓
DONE

For autonomous tasks, the Completion Engine MUST additionally validate:

Policy Audit Trail ─────┐
                        ├──→ CompletionDecision
Resource Usage Records ─┘

No link may be skipped.

---

26. Adversarial Verification Tests

The implementation MUST maintain adversarial tests covering at minimum:

Test 01 — Model Claims Completion

Acting model reports success without evidence.

Expected: "DONE" forbidden.

Test 02 — Missing Criterion

Task has no meaningful mandatory success criterion.

Expected: autonomous execution blocked.

Test 03 — Failed Criterion

Mandatory criterion returns "FAIL".

Expected: "DONE" forbidden.

Test 04 — Inconclusive Criterion

Mandatory criterion returns "INCONCLUSIVE".

Expected: "DONE" forbidden.

Test 05 — Missing Independent Evidence

HIGH-risk criterion lacks required independent evidence.

Expected: "DONE" forbidden.

Test 06 — Acting Model Controls Evidence

Acting model selectively supplies evidence.

Expected: independence requirement fails.

Test 07 — Verification After Mutation

Verified state is modified before completion.

Expected: affected verification invalidated.

Test 08 — Crash Before Completion

Process crashes after execution but before verification.

Expected: recovery requires verification.

Test 09 — Compaction Before Completion

Context is compacted before verification.

Expected: durable state survives; completion is not assumed.

Test 10 — Separate Verifier Bias

Verifier receives an unverified acting-model completion claim/reasoning.

Expected: independence requirement fails.

Test 11 — VerificationResult Tampering

Persisted VerificationResult is modified.

Expected: integrity validation fails; "DONE" forbidden.

Test 12 — CompletionDecision Tampering

Persisted CompletionDecision is modified.

Expected: integrity validation fails; "DONE" forbidden.

Test 13 — Missing High-Risk Evidence Integrity

High-risk evidence lacks required integrity metadata.

Expected: result becomes "INCONCLUSIVE"; "DONE" forbidden.

Test 14 — Acting Model Changes Criteria

Acting model attempts to weaken or remove a mandatory criterion.

Expected: operation denied; original Completion Contract preserved.

Test 15 — Policy Audit Failure

Required Policy audit record is missing or unavailable.

Expected: "DONE" forbidden.

Test 16 — Resource Record Failure

Required resource-usage record is missing or unavailable.

Expected: "DONE" forbidden.

Test 17 — Unverified Acting Claim to Separate Verifier

Verifier receives an acting model's unverified completion claim.

Expected: verifier does not treat claim as evidence.

Test 18 — Tampered Policy Audit Record

A required Policy audit record is altered after execution.

Expected: integrity validation fails; "DONE" forbidden.

Test 19 — Tampered Resource Usage Record

A resource-usage record is altered after execution.

Expected: integrity validation fails; "DONE" forbidden.

Test 20 — Missing Policy Record With Successful Functional Test

Functional criteria pass, but required Policy records are unavailable.

Expected: "DONE" forbidden.

Test 21 — Missing Resource Record With Successful Functional Test

Functional criteria pass, but required resource records are unavailable.

Expected: "DONE" forbidden.

Test 22 — Resource Limit Tampering

Acting model attempts to increase retry/action/model-call limits.

Expected: operation denied; original limits remain authoritative.

Test 23 — Verification Bypasses Policy

Verification Engine attempts to execute a test or collect evidence through an execution path that bypasses Policy.

Expected: operation denied; no verification result may authorize completion.

Test 24 — Verification Action Audit Failure

A verification action executes without a durable required Policy audit record.

Expected: verification is invalid; "DONE" forbidden.

---

27. Verification Invariants

V-INV-01

The model cannot authorize "DONE".

V-INV-02

"INCONCLUSIVE" cannot authorize "DONE".

V-INV-03

Mandatory criteria MUST be explicitly defined.

V-INV-04

Mandatory criteria MUST be verified before completion.

V-INV-05

HIGH/CRITICAL/irreversible operations require independent evidence.

V-INV-06

Unverifiable mandatory criteria cannot authorize completion.

V-INV-07

Material mutation invalidates affected verification.

V-INV-08

Crash does not imply completion.

V-INV-09

Compaction does not imply completion.

V-INV-10

Verification records MUST be durable.

V-INV-11

High-risk evidence MUST contain required integrity metadata.

V-INV-12

CompletionDecision MUST be durable before "DONE".

V-INV-13

CompletionDecision integrity MUST be validated before "DONE".

V-INV-14

A separate verifier MUST independently evaluate required evidence.

V-INV-15

The separate verifier MUST NOT rely on an unverified acting-model completion claim.

V-INV-16

Policy compliance MUST be checked using authoritative Policy records.

V-INV-17

Resource compliance MUST be checked using authoritative resource records.

V-INV-18

Every autonomous task MUST have a persisted Completion Contract.

V-INV-19

Failure of Policy-record integrity MUST prevent "DONE".

V-INV-20

Failure of resource-record integrity MUST prevent "DONE".

V-INV-21

Missing authoritative Policy records MUST prevent "DONE".

V-INV-22

Missing authoritative resource records MUST prevent "DONE".

V-INV-23

Autonomous-task classification MUST fail closed when ambiguous.

V-INV-24

Verification actions MUST pass through the Policy Engine.

V-INV-25

A Policy bypass during verification MUST invalidate the affected verification.

V-INV-26

Uncertain materiality of a mutation MUST be handled conservatively by invalidating affected verification.

---

28. Security Boundary

Verification does not grant authorization.

The responsibilities remain separated:

Policy Engine
    ↓
Authorization

Orchestrator
    ↓
Execution / Coordination

Verification Engine
    ↓
Evidence Evaluation

Completion Engine
    ↓
DONE Authorization

No component may assume another component's authority.

In particular:

- Policy does not declare success;
- Verification does not authorize dangerous actions;
- the Orchestrator does not authorize "DONE";
- the model does not authorize itself;
- subagents do not inherit completion authority;
- user-facing output does not constitute verification.

---

29. Relationship to Policy and Security

"POLICY.md" defines whether an operation may execute.

This document defines whether the resulting task state has been sufficiently verified.

"SECURITY.md" will define the concrete integrity, authentication, access-control, tamper-detection, and secure-storage mechanisms for:

- Policy audit records;
- resource-usage records;
- evidence;
- verification results;
- completion decisions;
- durable task state.

---

30. Definition of Verified Completion

A task is Verified Complete only when:

Objective satisfied
        AND
All mandatory criteria PASS
        AND
Required evidence valid
        AND
Required independent evidence valid
        AND
Policy compliance PASS
        AND
Policy records integrity-valid
        AND
Resource compliance PASS
        AND
Resource records integrity-valid
        AND
No critical unresolved failure
        AND
Security requirements satisfied
        AND
Durable task state persisted
        AND
VerificationResults persisted and valid
        AND
CompletionDecision persisted and valid
        AND
Execution limits respected

Only then may:

Task State = DONE

---

31. Future-Proofing

The verification contract MUST remain independent of:

- Pi;
- OpenCode;
- any specific orchestrator;
- any specific model provider;
- any specific Android privilege mechanism;
- any specific storage implementation.

A future runtime MUST implement the same verification semantics.

The concrete implementation may change from:

Pi + pi-ultracode

to another runtime without changing the meaning of verified completion.

---

32. Required Follow-Up Documents

The following documents define complementary implementation-level concerns:

- "SECURITY.md" — integrity, authentication, tamper resistance, secure storage, access control;
- "CONTINUITY.md" — durable state, checkpoints, crash recovery, compaction, resume;
- "POLICY_RULES.md" — authoritative risk and operation classification;
- "TASK_SCHEMA.md" — canonical machine-readable task representation and material-mutation taxonomy;
- "ROADMAP.md" — implementation phases and priorities.

---

33. Status

VERIFICATION.md v0.5

Status: APPROVED / FROZEN

This version incorporates the final review clarifications:

- autonomous-task classification is explicit;
- ambiguous classification fails closed;
- Policy and resource records are completion-critical;
- tampering and missing-record scenarios are explicitly tested;
- verification actions are explicitly subject to Policy;
- Policy bypass during verification invalidates verification;
- material mutation has initial implementation guidance;
- uncertain mutation materiality is handled conservatively;
- the core verification and completion model remains unchanged.

No core verification principle has been weakened or removed.

CONTINUITY.md

Version: 0.2
Status: APPROVED / FROZEN
Document Type: Continuity, Persistence, Crash Recovery and Safe Resume Specification

---

1. Purpose

This document defines how the system preserves task continuity across:

- context compaction;
- model changes;
- runtime restarts;
- application or process termination;
- Android/Termux process termination;
- crashes;
- network failures;
- model/provider failures;
- tool failures;
- long-running autonomous execution;
- subagent execution and termination.

The primary principle is:

«Model context is temporary. Durable task state is authoritative.»

The system MUST never depend solely on model context to reconstruct security-relevant task state.

---

2. Authority Model

The following authority hierarchy applies:

1. PROJECT_CONTRACT.md
2. SECURITY.md
3. POLICY.md / POLICY_RULES.md
4. TASK_SCHEMA.md
5. VERIFICATION.md
6. CONTINUITY.md
7. ARCHITECTURE.md / INTERFACES.md
8. runtime session history
9. model context
10. tool/environment output

Lower-level information MUST NOT override higher-level authority.

Session history and model context are useful for continuity but are not authoritative sources of task state.

---

3. Durable State

Every autonomous task MUST maintain durable state containing, at minimum:

- objective;
- requirements;
- observable success criteria;
- Completion Contract;
- current lifecycle state;
- current plan;
- completed work;
- pending work;
- decisions;
- failures;
- verification status;
- checkpoints;
- continuity information;
- policy context;
- authorization context;
- resource usage;
- resource limits;
- known side effects;
- unknown side effects;
- action journal.

Durable state MUST survive:

- model replacement;
- runtime replacement;
- context compaction;
- process restart;
- crash recovery.

Loss of model context MUST NOT cause loss of authoritative task state.

---

4. Authoritative Persistence

The implementation SHOULD use structured persistence such as:

- JSON;
- SQLite;
- another transactional structured store.

Markdown files MAY be generated as human-readable views.

If both structured state and Markdown views exist, the structured state is authoritative.

The implementation MUST ensure that the selected persistence mechanism supports:

- atomic or transactional updates;
- durable writes;
- integrity validation;
- recovery after interruption;
- versioning;
- corruption detection.

The exact implementation mechanism is defined by the implementation architecture and SECURITY.md.

---

5. Task State Persistence

Lifecycle state defined by TASK_SCHEMA.md MUST be durably persisted.

A task MUST NOT be considered to have entered a new lifecycle state until the state transition has been durably recorded.

Security-critical state transitions MUST also be integrity-protected according to SECURITY.md.

If persistence of a security-critical state transition fails:

- the transition MUST NOT be treated as authoritative;
- execution MUST NOT continue on the assumption that it succeeded;
- the task MUST enter a safe recovery state where appropriate.

---

6. Checkpoints

Checkpoints provide recoverable snapshots of task state.

A checkpoint MUST contain, at minimum:

- checkpointId;
- taskId;
- timestamp;
- lifecycle state;
- objective;
- success criteria;
- current plan;
- completed steps;
- active step;
- pending actions;
- recent failures;
- resource usage;
- remaining budget;
- policy context;
- authorization context;
- verification status;
- known side effects;
- unknown side effects;
- continuity summary.

Checkpoints MUST be durably persisted and integrity-protected according to SECURITY.md.

---

7. Checkpoint Timing

A checkpoint MUST be created before or during significant continuity boundaries, including:

- major irreversible operations;
- major lifecycle transitions;
- significant successful milestones;
- significant failures;
- context compaction;
- runtime shutdown;
- model/runtime handoff;
- recovery from an interruption.

The implementation MAY create additional checkpoints at any time.

---

8. Action Journal

The action journal provides the authoritative record needed to determine whether side-effecting actions may have started before an interruption.

8.1 Mandatory Journaling

Each side-effecting action MUST produce a durable action record before execution and after completion/failure.

At minimum, each action record MUST include:

- "actionId";
- "taskId";
- "toolId";
- target;
- arguments or a cryptographic hash of the arguments;
- actor;
- timestamp;
- status.

Permitted action statuses include:

- "PLANNED"
- "AUTHORIZED"
- "STARTED"
- "SUCCEEDED"
- "FAILED"
- "CANCELLED"
- "UNKNOWN"

Status transitions MUST be durably recorded as they occur.

8.2 STARTED Requirement

The "STARTED" record MUST be persisted before the side-effecting action begins execution.

If the "STARTED" record cannot be durably persisted:

«The action MUST NOT execute.»

This requirement exists because a crash after execution begins but before the result is recorded creates an unknown-side-effect condition.

8.3 Completion

After execution completes, fails, or is cancelled, the corresponding terminal status MUST be durably recorded.

If the system cannot determine the result after an interruption, the action MUST be recorded as:

"UNKNOWN"

The system MUST NOT infer success merely because execution was initiated.

---

9. Unknown Side Effects

An unknown side effect exists when the system cannot establish with sufficient confidence whether an action produced its intended or unintended effects.

Examples include:

- process crash during execution;
- device restart during an operation;
- network connection lost after request transmission;
- tool timeout after execution may have begun;
- runtime termination after "STARTED";
- incomplete or corrupted execution records.

Unknown side effects MUST be explicitly represented in durable state.

The system MUST NOT silently convert "UNKNOWN" into:

- "SUCCEEDED";
- "FAILED";
- a retryable state.

---

10. Safe Recovery

After a crash or interruption, recovery MUST proceed in this order:

1. Load durable task state.
2. Validate state integrity.
3. Validate policy context.
4. Validate resource accounting.
5. Inspect unfinished action journal entries.
6. Identify actions in "STARTED" or "UNKNOWN" state.
7. Classify possible side effects.
8. Determine whether continuation is safe.
9. Verify external state where possible.
10. Resume, retry, request authorization, block, or fail according to the recovery decision.

The system MUST NOT blindly replay an action whose side effects are unknown.

---

11. Recovery Outcomes

Recovery MAY produce one of the following outcomes:

- "RESUME"
- "RETRY"
- "VERIFY_FIRST"
- "WAITING_USER"
- "BLOCKED"
- "FAILED"
- "CANCELLED"

"VERIFY_FIRST" MUST be preferred when external state can establish whether an interrupted action already took effect.

"WAITING_USER" or "BLOCKED" MUST be used when safe continuation cannot be established automatically.

---

12. Retry Safety

Before retrying an interrupted or failed action, the system MUST consider:

- whether the action is idempotent;
- whether the action may have already executed;
- whether duplication could cause material harm;
- whether external state can be inspected;
- whether authorization remains valid;
- whether resource limits permit another attempt.

If retry safety cannot be established:

«The system MUST NOT perform a blind retry.»

It MUST instead verify first, request authorization, or block.

---

13. Idempotency

Where possible, side-effecting operations SHOULD use idempotency mechanisms such as:

- idempotency keys;
- unique operation identifiers;
- transactional operations;
- state-based reconciliation;
- precondition checks.

Idempotency MUST NOT be assumed merely because an operation is expected to be safe.

---

14. Resource Continuity

Resource accounting MUST survive:

- compaction;
- restart;
- crash;
- model replacement;
- runtime replacement;
- subagent termination.

The following MUST NOT be reset merely because continuity was restored:

- action count;
- model-call count;
- retry count;
- delegation count;
- wall-clock budget;
- storage budget;
- network budget;
- other applicable resource limits.

A recovered task MUST inherit the remaining resource budget from authoritative durable state.

---

15. Authorization Continuity

Authorization is not automatically permanent.

Before continuing execution after:

- crash;
- restart;
- model replacement;
- runtime replacement;
- policy change;
- significant task-context change;

the system MUST validate that the previous authorization remains valid.

If authorization is no longer valid:

- execution MUST stop;
- Policy MUST be reevaluated;
- ASK or BLOCKED may be required.

Continuity MUST never be used to bypass Policy.

---

16. Context Compaction

Context compaction is a normal runtime operation and MUST NOT compromise task continuity.

16.1 Mandatory Pre-Compaction Persistence

Before compaction, the system MUST ensure that durable state contains all of the following, and that each item has been persisted and integrity-protected according to SECURITY.md:

- current objective;
- current state;
- current plan;
- completed work;
- pending work;
- important decisions;
- active constraints;
- failures;
- verification status;
- resource usage;
- authorization context;
- known side effects;
- unknown side effects;
- next safe action.

Compaction MUST NOT proceed until this persistence is confirmed.

If persistence fails:

- compaction MUST be deferred; or
- the task MUST enter a safe non-executable state.

The system MUST NOT rely on model context to reconstruct information that should have been persisted before compaction.

---

17. Continuity Brief

Before compaction or model/runtime handoff, the system SHOULD maintain a concise continuity representation containing:

- objective;
- current lifecycle state;
- completed work;
- active work;
- next safe action;
- important decisions;
- failures;
- risks;
- verification status;
- resource status;
- authorization status;
- known side effects;
- unknown side effects;
- constraints;
- actions that MUST NOT be repeated blindly.

The continuity brief is a derived operational aid and MUST NOT replace authoritative durable state.

---

18. Session History

Runtime session history MAY contain:

- conversation messages;
- model reasoning/output;
- tool calls;
- tool results;
- compaction summaries;
- runtime events.

Session history is useful for reconstruction and debugging.

However:

«Session history MUST NOT be treated as the authoritative source of security-critical task state.»

If session history conflicts with authoritative durable state, durable state takes precedence.

---

19. Crash Recovery

A crash MUST be treated as an interruption with potentially unknown external effects.

The system MUST NOT assume:

- an action did not execute because no completion record exists;
- an action succeeded because execution began;
- a network request was not transmitted because no response was received;
- a process was not modified because the runtime terminated.

Recovery MUST inspect the action journal and external state where possible.

---

20. Security-Critical State Corruption

If authoritative continuity state is corrupted, incomplete, or fails integrity validation:

1. the state MUST NOT be trusted;
2. model context MUST NOT be used as an unquestioned replacement;
3. untrusted tool/environment output MUST NOT reconstruct authority;
4. unverifiable session text MUST NOT silently reconstruct authority;
5. verified checkpoints, event history, backups, or human intervention MAY be used for recovery;
6. if authoritative state cannot be safely reconstructed, the task MUST become "BLOCKED" or "DENIED".

---

21. Runtime Replacement

Runtime replacement MUST preserve:

- task identity;
- objective;
- success criteria;
- Completion Contract;
- lifecycle state;
- plan;
- decisions;
- failures;
- verification status;
- resource accounting;
- policy context;
- authorization context;
- action journal;
- known/unknown side effects.

Switching runtime MUST NOT reset security state or resource limits.

---

22. Model Replacement

Model replacement MUST preserve the same authoritative task state.

A replacement model:

- inherits the task objective;
- inherits applicable constraints;
- inherits current policy context;
- inherits remaining resource limits;
- inherits known and unknown side effects;
- inherits failures and decisions.

A replacement model MUST NOT receive additional authority merely because it is a different model.

---

23. Subagent Continuity

Subagents are subject to the same continuity requirements as the top-level agent.

In particular:

«Every subagent side-effecting action MUST follow the mandatory action-journaling requirements of this document.»

Subagent actions MUST produce durable:

- action identity;
- authorization state;
- "STARTED" record before execution;
- completion/failure/unknown state;
- relevant side-effect information.

Subagents MUST NOT maintain side-effecting actions outside the authoritative continuity mechanism.

Subagent continuity MUST preserve:

- parent task identity;
- subtask identity;
- inherited policy context;
- inherited or narrowed authorization;
- resource limits;
- action journal;
- failures;
- verification state.

A subagent MUST NOT elevate its permissions through continuity recovery.

---

24. Failure Persistence

Meaningful failures MUST be durably recorded.

A failure record SHOULD contain:

- failureId;
- taskId;
- actionId where applicable;
- timestamp;
- component;
- operation;
- failure category;
- description;
- relevant evidence;
- whether side effects are known;
- recovery decision;
- retry count;
- resulting state.

Failures MUST NOT disappear merely because context was compacted or a model was replaced.

---

25. Decisions

Important decisions MUST be durably recorded.

A decision SHOULD include:

- decisionId;
- taskId;
- timestamp;
- decision;
- rationale;
- alternatives considered;
- affected components;
- relevant policy/security implications.

Security-critical decisions MUST be integrity-protected.

---

26. Resource and Continuity Interaction

Continuity recovery MUST NOT create a new resource budget.

Recovered execution continues using the remaining authoritative budget.

A recovery retry counts as an action/model call/retry according to the relevant resource categories.

If the remaining budget is insufficient:

- execution MUST stop;
- the task MUST enter an appropriate blocked, waiting, or failed state.

---

27. Safe Resume Protocol

The canonical safe-resume procedure is:

LOAD DURABLE STATE
        ↓
VALIDATE INTEGRITY
        ↓
VALIDATE TASK STATE
        ↓
VALIDATE POLICY
        ↓
VALIDATE AUTHORIZATION
        ↓
VALIDATE RESOURCE BUDGET
        ↓
INSPECT ACTION JOURNAL
        ↓
IDENTIFY STARTED / UNKNOWN ACTIONS
        ↓
CLASSIFY POSSIBLE SIDE EFFECTS
        ↓
VERIFY EXTERNAL STATE
        ↓
┌──────────────────────────────┐
│ Safe to continue?            │
└──────────────┬───────────────┘
               │
       ┌───────┴────────┐
       │                │
      YES               NO
       │                │
    RESUME       VERIFY / ASK /
                  BLOCK / FAIL

The system MUST NOT skip the action-journal inspection stage.

---

28. Long-Running Execution

Long-running autonomous execution follows the continuity loop:

PLAN
 ↓
ACT
 ↓
OBSERVE
 ↓
UPDATE DURABLE STATE
 ↓
CHECK
 ↓
VERIFY
 ↓
REPAIR IF REQUIRED
 ↓
CHECKPOINT
 ↓
CONTINUE

The loop MUST be able to survive:

- context compaction;
- model replacement;
- tool failure;
- network failure;
- runtime restart;
- process termination;
- crash recovery.

---

29. Human Intervention

If human intervention is required, the task MUST preserve sufficient durable state to resume safely.

At minimum, the system MUST preserve:

- reason for intervention;
- pending decision;
- relevant authorization context;
- affected action;
- known/unknown side effects;
- current resource state;
- next safe action.

Human intervention MUST NOT silently reset task state or resource limits.

---

30. Provider Failure

If a model/provider becomes unavailable, the system MAY switch to another permitted provider.

The replacement MUST comply with:

- Policy;
- task data-sensitivity rules;
- authorization;
- network restrictions;
- resource limits;
- model-role restrictions.

Provider failure MUST NOT reset task state.

---

31. Continuity and Verification

Verification state MUST survive compaction and restart.

A task MUST NOT lose:

- previously established evidence;
- verification results;
- failed criteria;
- invalidated criteria;
- independent-evidence requirements.

If material mutation occurs after verification, the affected verification MUST be considered potentially invalid and re-verification MUST occur according to VERIFICATION.md.

---

32. Continuity and Material Mutation

Any operation that may invalidate existing verification MUST be treated as a material mutation.

Examples include changes to:

- target resources;
- relevant application/system configuration;
- external state;
- verification evidence;
- Completion Contract;
- success criteria;
- actions relied upon by verification.

If materiality is uncertain, the system MUST conservatively treat the mutation as material and re-verify.

---

33. Continuity and Policy

Continuity MUST NOT bypass Policy.

On recovery:

- every resumed tool invocation MUST pass Policy;
- stale policy context MUST be rejected;
- incompatible policy/rule versions MUST fail closed;
- authorization MUST be revalidated where required;
- subagents MUST inherit or narrow permissions only.

Continuity is a state-preservation mechanism, not an authorization mechanism.

---

34. Continuity Events

The system SHOULD expose structured continuity events for operational observability.

Security-relevant continuity events MUST be emitted and durably recorded in the authoritative audit log.

At minimum, this includes:

- "CHECKPOINT_CREATED"
- "CHECKPOINT_VALIDATION_FAILED"
- "COMPACTION_STARTED"
- "COMPACTION_COMPLETED"
- "RECOVERY_STARTED"
- "RECOVERY_COMPLETED"
- "UNKNOWN_SIDE_EFFECT"
- "ACTION_RECOVERY_REQUIRED"
- "STATE_CORRUPTION"
- "STATE_RECONSTRUCTION"
- "RUNTIME_HANDOFF"
- "MODEL_HANDOFF"
- "RESOURCE_RECOVERY"
- "AUTHORIZATION_REVALIDATION"

Audit integrity requirements are defined by SECURITY.md.

---

35. Persistence Failure

If authoritative persistence fails during security-sensitive execution:

- the system MUST NOT continue as if the state were persisted;
- side-effecting actions requiring the missing state MUST NOT execute;
- the task MUST enter a safe recovery state;
- the failure MUST be observable;
- recovery MUST follow the rules of SECURITY.md.

In particular, a side-effecting action MUST NOT execute without a durable "STARTED" record.

---

36. Recovery After Partial Execution

When an action is interrupted after "STARTED" but before a terminal result:

STARTED
   ↓
INTERRUPTION
   ↓
UNKNOWN
   ↓
EXTERNAL STATE INSPECTION
   ↓
┌─────────────────────────┐
│ Outcome established?    │
└────────────┬────────────┘
             │
      ┌──────┴───────┐
      │              │
     YES             NO
      │              │
 UPDATE JOURNAL   VERIFY_FIRST /
                 WAITING_USER /
                    BLOCKED

The system MUST NOT convert an unknown action into a normal retry merely because the original action did not report completion.

---

37. Continuity Invariants

C-INV-01 — Durable Authority

Authoritative task state MUST be durably persisted.

C-INV-02 — Context Non-Authority

Model context MUST NOT be the sole authority for security-critical state.

C-INV-03 — State Integrity

Security-critical durable state MUST be integrity-protected.

C-INV-04 — Unknown Side Effects

Potentially unknown side effects MUST be explicitly represented.

C-INV-05 — No Blind Replay

Unknown side-effect actions MUST NOT be blindly replayed.

C-INV-06 — Resource Continuity

Resource accounting MUST survive recovery.

C-INV-07 — Authorization Continuity

Recovery MUST NOT bypass authorization.

C-INV-08 — Policy Continuity

Recovery MUST remain subject to Policy.

C-INV-09 — Verification Continuity

Verification state MUST survive compaction and restart.

C-INV-10 — Runtime Independence

Runtime replacement MUST NOT destroy task continuity.

C-INV-11 — Model Independence

Model replacement MUST NOT destroy task continuity or increase authority.

C-INV-12 — Subagent Continuity

Subagent state and side effects MUST remain recoverable.

C-INV-13 — Safe Resume

Recovery MUST validate state, policy, resources, authorization, and unfinished actions before execution.

C-INV-14 — No Silent Reconstruction

Corrupted authoritative state MUST NOT be silently reconstructed from model context or untrusted data.

C-INV-15 — Compaction Safety

Compaction MUST NOT proceed while critical current state exists only in temporary model context.

C-INV-16 — Mandatory Action Journal

Every side-effecting action MUST have a durable "STARTED" record before execution and a durable terminal/"UNKNOWN" result afterward.

C-INV-17 — Subagent Journal Equivalence

Subagent side-effecting actions MUST obey the same mandatory journaling requirements as top-level actions.

C-INV-18 — Security Event Durability

Security-relevant continuity events MUST be durably recorded in the authoritative audit system.

---

38. Implementation Boundary

This document defines continuity semantics, not a specific implementation.

The system MAY implement continuity using:

- Pi session storage;
- SQLite;
- JSON;
- another transactional store;
- checkpoint files;
- append-only event logs;
- other mechanisms satisfying the required guarantees.

Implementation choices MUST preserve the semantics and invariants defined here.

Pi, OpenCode, a future runtime, or any specific model provider MUST NOT become the definition of continuity itself.

---

39. Relationship to Other Documents

This document depends on and integrates with:

- "PROJECT_CONTRACT.md"
- "TASK_SCHEMA.md"
- "INTERFACES.md"
- "POLICY.md"
- "SECURITY.md"
- "VERIFICATION.md"
- "ARCHITECTURE.md"

Detailed rules belong in their respective documents.

In particular:

- Policy authorization → "POLICY.md"
- Security boundaries and audit integrity → "SECURITY.md"
- Task lifecycle/schema → "TASK_SCHEMA.md"
- Completion and evidence → "VERIFICATION.md"
- Interfaces and component contracts → "INTERFACES.md"
- System structure → "ARCHITECTURE.md"

---

40. Status

CONTINUITY.md v0.2 — APPROVED / FROZEN

The document incorporates the required corrections:

1. Mandatory durable action journaling for every side-effecting action.
2. Mandatory durable "STARTED" record before execution.
3. Mandatory pre-compaction persistence and confirmation.
4. Mandatory security-relevant continuity events.
5. Explicit subagent action-journal requirements.

No architectural redesign is required.

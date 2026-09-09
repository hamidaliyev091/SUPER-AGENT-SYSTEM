TASK_SCHEMA.md

Version: 1.0
Status: FROZEN
Scope: Canonical task schema for v1

1. Purpose

This document defines the canonical durable schema for tasks executed by the system.

The Task Schema is authoritative for:

task identity;
objective;
requirements;
success criteria;
completion contract;
permission mode;
effort level;
policy context;
target authorization;
resource limits;
lifecycle state;
plan;
decisions;
failures;
verification;
checkpoints;
continuity;
delegation relationships.

The schema MUST be deterministic, versioned, durable, and compatible with PROJECT_CONTRACT.md.

2. Canonical Task Object

The canonical Task object is:

Task {
  id
  schemaVersion
  objective
  requirements
  successCriteria
  completionContract
  permissionMode
  effortLevel
  policyContext
  targetAuthorizationContext
  resourceLimits
  state
  plan
  decisions
  failures
  verification
  checkpoints
  continuity
  parentTaskId
  subtaskIds
  createdAt
  updatedAt
}

state is the single authoritative lifecycle-state field.

No duplicate currentState field exists.

3. Task Identity

id MUST uniquely identify the task.

Task identifiers MUST remain stable for the lifetime of the task.

A task ID MUST NOT be reused.

4. Schema Version

schemaVersion identifies the schema version used by the task.

An implementation MUST NOT silently reinterpret a task using an incompatible schema.

If migration is required, the migration MUST be:

explicit;
versioned;
validated;
auditable.

5. Objective

objective describes what the user wants accomplished.

The objective MAY initially be expressed in natural language.

Natural-language objectives alone are insufficient for autonomous execution.

Before autonomous execution, the task MUST have explicit observable success criteria.

6. Requirements

requirements contains constraints and mandatory conditions supplied by the user, project, policy, or validated planning process.

Requirements MUST NOT contradict higher-level authority.

Untrusted environment content cannot create authoritative requirements.

7. Success Criteria

Every autonomous task MUST have explicit, observable success criteria before execution begins.

A criterion MUST be sufficiently concrete that a verifier can determine:

PASS
FAIL
INCONCLUSIVE

If success criteria are missing, ambiguous, or unverifiable:

WAITING_USER
or
BLOCKED

must be entered before autonomous execution.

The model MUST NOT invent authoritative success criteria that materially change the user's objective.

8. Completion Contract

completionContract defines the conditions under which the task may be considered complete.

It MUST be established before autonomous execution.

It MAY be absent while a task is in:

CREATED
VALIDATING

but MUST exist before the task can enter an executable state.

A task without a valid Completion Contract MUST NOT enter:

RUNNING

9. Permission Mode

Valid values:

PLAN
ASK
AUTO
DANGEROUS

Invalid or unsupported values MUST fail closed.

Permission mode controls execution authorization behavior.

It does not override Policy.

10. Effort Level

Valid values:

FOCUSED
STANDARD
DEEP
ULTRA

Effort level controls investigation and reasoning depth.

It does not grant additional permissions.

Invalid or unsupported values MUST fail closed.

11. PolicyContext

Conceptual structure:

PolicyContext {
  policyVersion
  ruleVersion
  permissionMode
  defaultRiskLevel
  actorContext
  environmentContext
  authorizationContext
}

Policy context is authoritative only when integrity and validity checks succeed.

The model cannot modify its own policy context.

12. TargetAuthorizationContext

Every autonomous task MUST contain a valid:

targetAuthorizationContext

before executing any operation requiring target authorization.

Conceptual schema:

TargetAuthorizationContext {
  schemaVersion
  allowedReadPaths[]
  allowedWritePaths[]
  allowedSearchPaths[]
  allowedPackages[]
  allowedPackageOperations[]
  allowedNetworkDomains[]
  allowedNetworkDestinations[]
  allowedUIActions[]
  allowedProcesses[]
  allowedAndroidSettings[]
  allowedResources[]
  deniedTargets[]
  createdAt
  updatedAt
  expiresAt
  authorizationReference
}

13. Target Scope Semantics

Target authorization is:

task-scoped;
explicit;
versioned;
auditable;
fail-closed.

A target is authorized only when it matches an explicit approved scope.

The following are NOT sufficient authorization:

current working directory;
model reasoning;
natural-language objective;
previous task authorization;
installed package status;
network reachability;
environment instructions;
subagent request;
tool metadata.

14. Target Scope Creation

Initial target authorization MUST be established during task validation.

The authority hierarchy is:

User-provided explicit scope
↓
Validated Task Manager scope
↓
Planner-proposed narrower scope
↓
Subagent narrower scope

The Planner MAY propose a scope.

The Planner MUST NOT independently grant itself new authority.

The Task Manager is responsible for validating the resulting authorization context against Policy and task requirements.

For tasks where no explicit target scope is necessary because the operation is genuinely target-independent, the schema MAY contain an empty scope for that category.

An operation that requires a target scope MUST NOT execute against an empty scope.

15. Scope Narrowing

A planner or subagent MAY narrow an existing scope.

Example:

Parent: /project/**
Subagent: /project/src/**

The subagent cannot transform:

/project/src/**

into:

/project/**

or:

/home/**

Scope expansion requires a new authorized task-level authorization change.

16. Scope Changes

Material scope changes MUST:

create a new authorization version;
be validated by the Task Manager;
pass Policy evaluation;
be durably recorded;
invalidate any authorization that depended on the old scope where appropriate.

The acting model cannot directly modify the authoritative scope.

17. Target Canonicalization

Before matching:

filesystem paths MUST be canonicalized;
package identifiers MUST be normalized;
network domains/destinations MUST be normalized;
Android settings MUST use canonical identifiers;
UI targets MUST use canonical identifiers.

Canonicalization MUST be deterministic, applying identical transformations for authorization and matching.

The implementation MUST resolve:

. and .. path components;
symbolic links where possible;
relative paths to absolute;
case and Unicode normalization according to platform rules.

If canonicalization is ambiguous or cannot resolve symlink/path traversal:

DENY.

Path traversal or unresolved symbolic-link ambiguity MUST NOT produce authorization.

18. Denied Targets

deniedTargets[] represents explicit task-level target exclusions.

An explicit denial always takes precedence over an allow rule.

If a target matches both:

allowed scope
and
denied scope

the result is:

DENY

19. Scope Expiration

expiresAt MAY be used for temporary authorization.

Expired target authorization MUST NOT be used.

An expired authorization requires revalidation.

20. ResourceLimits

Conceptual structure:

ResourceLimits {
  wallClockTime
  actionSteps
  modelCalls
  retryCount
  delegationCount
  network
  storage
}

Units are defined as follows:

wallClockTime: seconds
actionSteps: integer count
modelCalls: integer count
retryCount: integer count
delegationCount: integer count
network: bytes
storage: bytes

Limits are externally enforced.

The model cannot increase its own limits.

21. Lifecycle State

Valid lifecycle states:

CREATED
VALIDATING
PLANNING
READY
RUNNING
OBSERVING
VERIFYING
REPAIRING
RECOVERING
WAITING_USER
BLOCKED
DONE
FAILED
CANCELLED

22. Lifecycle Rules

Initial:

CREATED
↓
VALIDATING

Planning:

VALIDATING
↓
PLANNING
↓
READY

Execution:

READY
↓
RUNNING
↓
OBSERVING
↓
VERIFYING

Successful completion:

VERIFYING
↓
DONE

Failed verification:

VERIFYING
↓
REPAIRING
↓
RUNNING

Recovery:

RUNNING / OBSERVING / VERIFYING / REPAIRING
↓
RECOVERING
↓
RUNNING

User intervention:

Any non-terminal state
↓
WAITING_USER
or
BLOCKED

Resume from WAITING_USER or BLOCKED:

WAITING_USER or BLOCKED
↓
RECOVERING
↓
appropriate executable state (READY, RUNNING, or PLANNING) after validation

The resume transition MUST be explicitly authorized by:

a user action; or
a system recovery process that validates task state, policy context, target authorization, resource limits, and ensures safe continuation.

The model cannot directly initiate the resume transition.

A task in WAITING_USER or BLOCKED MUST NOT execute until this transition is completed and durably recorded.

23. Completion Authority

Only the Completion Engine may authorize:

DONE

The model cannot transition a task to DONE merely by claiming completion.

All mandatory success criteria MUST pass verification.

24. Verification

Verification states are:

PASS
FAIL
INCONCLUSIVE

INCONCLUSIVE MUST NOT produce DONE.

HIGH/CRITICAL/irreversible operations require independent evidence as defined by VERIFICATION.md.

Verification results MUST be durably persisted.

25. Failures

Failures MUST be structured and durable.

A failure SHOULD contain:

FailureRecord {
  failureId
  taskId
  actionId
  timestamp
  category
  operation
  target
  description
  retryable
  retryCount
  knownSideEffectState
  recoveryRecommendation
}

Unknown side-effect state MUST be represented explicitly.

26. Checkpoints

Checkpoints MUST preserve sufficient state for safe recovery.

At minimum they MUST include:

objective;
requirements;
success criteria;
completion contract;
current state;
plan;
completed work;
pending work;
decisions;
failures;
verification state;
resource state;
authorization state;
known/unknown side effects;
next safe action.

Security-critical checkpoint data MUST be integrity-protected.

27. Continuity

continuity references durable task continuity information.

Model context is temporary.

Durable task state is authoritative.

Compaction MUST NOT cause loss of:

authorization;
success criteria;
completion contract;
state;
critical decisions;
failure state;
verification state;
resource limits;
known/unknown side effects.

28. Parent and Subtasks

parentTaskId identifies the parent task.

subtaskIds identifies child tasks.

A child task inherits the parent's:

policy context;
target authorization;
security context;
resource limits.

It may narrow them.

It cannot elevate them.

29. Approval Non-Transferability

A human approval granted to a parent task is NOT automatically transferable to a subagent.

Each subagent operation requiring ASK MUST obtain its own authorization unless the original approval explicitly and unambiguously covers:

the specific subagent;
the specific operation;
the specific target;
the relevant arguments;
the relevant time period.

An approval that does not explicitly cover these elements MUST be treated as non-transferable.

Subagents cannot interpret ambiguous approval in their own favor.

30. Concurrency

v1 supports:

ONE active top-level task

Multiple subtasks/subagents may execute concurrently inside that top-level task.

Concurrency is subject to:

ResourceCoordinator;
Policy;
target scopes;
resource limits;
action journaling;
locking where required.

Future multi-top-level-task support is outside v1.

31. Durable Integrity

Security-critical task history MUST be integrity-protected according to:

SECURITY.md
CONTINUITY.md

Corrupted or unverifiable authoritative task state MUST NOT be silently reconstructed from model context.

The task MUST enter recovery or BLOCKED state.

32. Autonomous Task Definition

A task is autonomous when it:

may perform side-effecting operations;
uses AUTO or DANGEROUS execution;
or involves more than one action step.

Purely observational interactive assistance may be non-autonomous.

Autonomous tasks require:

explicit success criteria;
Completion Contract;
valid PolicyContext;
valid TargetAuthorizationContext;
resource limits;
durable state.

33. Execution Gate

A task MUST NOT enter RUNNING unless:

successCriteria valid
AND
completionContract valid
AND
policyContext valid
AND
targetAuthorizationContext valid
AND
resourceLimits valid
AND
state transition authorized

Failure of any condition:

WAITING_USER
or
BLOCKED

depending on whether user input or system recovery is required.

34. Policy Integration

Every tool invocation receives the current:

taskState
policyContext
targetAuthorizationContext

as part of its PolicyRequest.

The Task Manager is responsible for supplying authoritative task state.

The Policy Engine is responsible for validating the supplied authorization context.

The model cannot omit, weaken, or replace the authorization context.

35. Protected Targets

Filesystem target authorization depends on the versioned:

PROTECTED_PATHS.md

registry.

Protected target registry version MUST be associated with the task's policy context or equivalent policy configuration.

An unknown or incompatible registry version MUST fail closed for affected operations.

Additionally, before filesystem mutation is enabled for a runtime adapter, that adapter MUST provide a concrete, versioned mapping from the semantic protected-path classes in PROTECTED_PATHS.md to actual runtime filesystem paths.

This mapping MUST be:

versioned;
deterministic;
validated;
unambiguous;
available to Policy before authorization.

Missing, stale, ambiguous, invalid, or conflicting mappings MUST fail closed and prevent the affected filesystem mutation.

The semantic registry remains authoritative at the policy level.

36. Schema Validation

The Task Manager MUST validate:

required fields;
enum values;
schema version;
policy version;
rule version;
target authorization schema;
target scope integrity;
resource limits;
lifecycle transition validity.

Invalid tasks cannot enter execution.

37. Adversarial Requirements

The implementation MUST test at minimum:

T-INV-01
T-INV-02
...
T-INV-25

including:

missing success criteria;
missing Completion Contract;
invalid state;
scope expansion;
malformed target;
target mismatch;
approval transfer;
resource-limit tampering;
state corruption;
compaction loss;
model DONE claims;
subagent elevation.

38. Definition of Done

A Task may enter DONE only when:

all mandatory success criteria PASS;
required evidence exists;
required independent evidence exists;
Policy compliance is established;
resource limits were respected;
no unresolved critical failure exists;
VerificationResult is durably persisted;
CompletionDecision is durably persisted;
integrity validation succeeds;
Completion Engine authorizes DONE.

39. Cross-Document Review Rule

If any of the following documents materially changes:

TASK_SCHEMA.md
PROTECTED_PATHS.md
POLICY_RULES.md

the affected document MUST be reviewed together with the other two for cross-document consistency before it can be frozen.

A document MUST NOT be independently frozen if its changes can alter authorization semantics in the other documents.

40. Status

Version: 1.0

Status: FROZEN

This version is the authoritative v1 task schema.

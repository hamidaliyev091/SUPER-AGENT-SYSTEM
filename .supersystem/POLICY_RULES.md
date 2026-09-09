POLICY_RULES.md

Version: 1.0
Status: FROZEN
Policy Rule Version: 1.0
Scope: v1 executable policy baseline
Authority: PROJECT_CONTRACT.md → POLICY.md → POLICY_RULES.md

1. Purpose

This document defines the concrete, deterministic, versioned policy rules used by the Policy Engine to authorize tool operations.

It converts the principles defined by PROJECT_CONTRACT.md and POLICY.md into executable rules.

This document is authoritative for:

risk classification;
side-effect classification;
reversibility classification;
idempotency classification;
permission-mode behavior;
target authorization;
protected-target handling;
tool and operation registration;
privileged-capability scope;
human-confirmation requirements;
retry safety;
fail-closed behavior.

The Policy Engine MUST NOT invent missing rules.

If an operation cannot be deterministically evaluated under this document and its explicitly referenced registries:

DENY.

2. Authority and Precedence

Policy authority follows:

PROJECT_CONTRACT.md
POLICY.md
POLICY_RULES.md
explicitly referenced versioned registries
Task Schema authorization context
ToolSpec metadata
model-provided information

Lower-level information MUST NOT override higher-level authority.

Tool metadata is operationally necessary but is not authoritative when it conflicts with an explicit policy rule.

If ToolSpec metadata conflicts with an authoritative policy rule:

the policy rule wins.

3. Versioning

Every policy evaluation MUST identify:

policyVersion
ruleVersion

The Policy Engine MUST verify compatibility before evaluation.

If compatibility cannot be established:

DENY.

Policy changes MUST be versioned and recorded through the project change-control process.

4. Deterministic Evaluation

For identical:

policy version;
rule version;
task security context;
task state;
permission mode;
effort level;
tool;
operation;
structured arguments;
target;
TargetAuthorizationContext;
environment security context;

the Policy Engine MUST produce the same result.

Valid results:

ALLOW
ASK
DENY

If deterministic evaluation cannot be guaranteed:

DENY.

5. PolicyRequest

Every tool invocation MUST construct a complete PolicyRequest.

Conceptually:

PolicyRequest {
  policyVersion
  ruleVersion
  taskId
  taskState
  permissionMode
  effortLevel
  actorContext
  environmentContext
  authorizationContext
  targetAuthorizationContext
  toolId
  operationId
  target
  structuredArguments
  requestedRiskLevel
  requestedSideEffect
  requestedReversibility
  requestedIdempotency
}

structuredArguments MUST contain the complete arguments required for deterministic authorization.

Arguments MUST NOT be replaced by a hash during policy evaluation.

Hashes MAY be used for audit records.

Secrets MUST be redacted or represented using approved non-secret references before persistence where possible.

The fields requestedRiskLevel, requestedSideEffect, requestedReversibility, and requestedIdempotency are informational inputs from the model or acting component. They MUST NOT be considered authoritative. The Policy Engine MUST independently resolve the authoritative values from the registered tool/operation rules and the protected-target registry.

6. Target Authorization

Authorization consists of both:

operation authorization;
target authorization.

An operation that is allowed in principle MUST still be denied if its target is not authorized.

Every operation requiring a target MUST match the requested target against the task's TargetAuthorizationContext.

7. TargetAuthorizationContext

The canonical schema is defined in:

TASK_SCHEMA.md §12–§19

The Policy Engine MUST consume the authoritative targetAuthorizationContext supplied by the Task Manager.

Conceptual structure:

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

The Policy Engine MUST NOT invent missing scopes.

Missing required target scope:

DENY.

8. Target Scope Authority

Initial target authorization is established during task validation.

The authority hierarchy is:

User-provided explicit scope
↓
Task Manager validation
↓
Planner-proposed narrower scope
↓
Subagent narrower scope

The Planner MAY propose a narrower scope.

The Planner MUST NOT independently grant authority.

The Task Manager validates the final task authorization context.

The model cannot directly modify authoritative target authorization.

9. Target Scope Matching

Scope matching MUST be deterministic.

Before matching, targets MUST be canonicalized according to the applicable target type:

filesystem → canonical path, resolving symlinks, relative components, and platform normalization;
package → canonical package identifier;
network → normalized domain/destination, including case and IDNA normalization;
Android setting → canonical setting identifier;
UI → canonical action/target identifier.

Canonicalization MUST be identical for authorization and matching; alternate representations MUST NOT bypass scope matching.

If canonicalization is ambiguous or cannot resolve symlink/path traversal:

DENY.

If the target does not match an allowed scope:

DENY.

If the target matches an explicit denied scope:

DENY.

If a target matches both an allowed and denied scope:

DENY.

10. Scope Narrowing

Subtasks and subagents inherit the parent TargetAuthorizationContext.

They MAY narrow it.

They MUST NOT broaden it.

For example:

Parent: /project/**
Child: /project/src/**

is valid.

A child changing:

/project/src/**

to:

/project/**

is unauthorized.

Scope expansion requires a new authorized task-level scope change.

11. Scope Versioning and Expiration

Target authorization MUST be versioned.

Material scope changes require:

new authorization version;
Task Manager validation;
Policy evaluation;
durable audit;
appropriate invalidation of old authorization.

Expired authorization MUST NOT be reused.

The model cannot extend or renew authorization.

12. Protected Target Registry

Protected filesystem targets are defined by:

PROTECTED_PATHS.md

The registry is versioned and security-relevant.

Its applicable version MUST be known for filesystem mutation.

Missing, incompatible, corrupted, or unverifiable registry:

DENY/BLOCKED according to recovery state.

The registry MUST NOT be replaced by model-generated content.

13. Protected Path Matching

Protected path matching MUST occur after canonicalization and before authorization of filesystem mutation.

Protected paths are authoritative exclusions from ordinary mutation.

Unknown security-sensitive target:

DENY.

An explicit protected-target classification MUST NOT be downgraded by tool metadata.

Additionally, before filesystem mutation is enabled for a runtime adapter, that adapter MUST provide a concrete, versioned mapping from the semantic protected-path classes in PROTECTED_PATHS.md to actual runtime filesystem paths.

Missing, stale, ambiguous, invalid, or conflicting mappings MUST fail closed and prevent the affected filesystem mutation.

14. Risk Levels

Authoritative risk levels:

LOW
MEDIUM
HIGH
CRITICAL

Tool metadata may propose a risk level.

The authoritative policy rule determines the final classification.

The model's requestedRiskLevel is not authoritative.

15. Side Effects

Authoritative side-effect values:

READ_ONLY
MUTATING
DESTRUCTIVE
EXTERNAL_EFFECT

The model's requestedSideEffect is not authoritative.

16. Reversibility

Authoritative values:

REVERSIBLE
PARTIALLY_REVERSIBLE
IRREVERSIBLE
UNKNOWN

UNKNOWN MUST be treated as:

IRREVERSIBLE

for authorization.

The model's requestedReversibility is not authoritative.

17. Idempotency

Authoritative values:

IDEMPOTENT
CONDITIONALLY_IDEMPOTENT
NON_IDEMPOTENT
UNKNOWN

UNKNOWN MUST be treated as:

NON_IDEMPOTENT

for authorization.

HIGH/CRITICAL non-idempotent operations SHOULD use an idempotency key or equivalent deduplication mechanism whenever technically feasible.

If safe deduplication is feasible but unavailable:

ASK.

If synchronous human authorization is unavailable:

DENY.

The model's requestedIdempotency is not authoritative.

18. Permission Modes

Valid permission modes:

PLAN
ASK
AUTO
DANGEROUS

Invalid, malformed, missing, or unsupported permission modes:

DENY.

Permission mode does not override Policy.

19. Effort Levels

Valid effort levels:

FOCUSED
STANDARD
DEEP
ULTRA

Effort level affects reasoning/investigation depth only.

It grants no additional authority.

Invalid, malformed, missing, or unsupported effort levels:

DENY.

20. Authoritative Permission Matrix

The following matrix is normative.

The permission matrix is authoritative. Implementations MUST NOT deviate from the specified decision for any defined combination of permission mode, risk level, side-effect class, reversibility, and idempotency. If a combination is undefined, ambiguous, conflicting, or cannot be deterministically resolved, the result MUST be DENY.

Risk | Side Effect | Reversibility | PLAN | ASK | AUTO | DANGEROUS
LOW | READ_ONLY | any | ALLOW | ALLOW | ALLOW | ALLOW
MEDIUM | READ_ONLY | any | ALLOW | ALLOW | ALLOW | ALLOW
LOW | MUTATING | REVERSIBLE | DENY | ALLOW | ALLOW | ALLOW
LOW | MUTATING | PARTIALLY_REVERSIBLE | DENY | ALLOW | ALLOW | ALLOW
MEDIUM | MUTATING | REVERSIBLE | DENY | ALLOW | ALLOW | ALLOW
MEDIUM | MUTATING | PARTIALLY_REVERSIBLE | DENY | ASK | ASK | ALLOW
HIGH | READ_ONLY | REVERSIBLE | ALLOW | ASK | ASK | ALLOW
HIGH | READ_ONLY | PARTIALLY_REVERSIBLE | ALLOW | ASK | ASK | ASK
HIGH | READ_ONLY | IRREVERSIBLE/UNKNOWN | DENY | ASK | ASK | ASK
HIGH | MUTATING | REVERSIBLE | DENY | ASK | ASK | ALLOW
HIGH | MUTATING | PARTIALLY_REVERSIBLE | DENY | ASK | ASK | ASK
HIGH | MUTATING | IRREVERSIBLE/UNKNOWN | DENY | ASK | ASK | ASK
HIGH | DESTRUCTIVE | any | DENY | ASK | ASK | ASK
HIGH | EXTERNAL_EFFECT | any | DENY | ASK | ASK | ASK
CRITICAL | any | any | DENY | ASK | ASK | ASK

PLAN/HIGH READ_ONLY rule

PLAN mode permits non-mutating investigation, including HIGH-risk read-only operations, provided that:

the target is explicitly authorized;
the operation is registered;
the operation does not expose prohibited secrets or violate another security boundary;
all applicable policy rules pass.

This preserves the principle that PLAN mode is non-mutating rather than non-investigative. However, the matrix row for HIGH READ_ONLY IRREVERSIBLE/UNKNOWN has PLAN = DENY. That row is authoritative for that specific combination.

21. PLAN Mode

PLAN mode MUST NOT execute side-effecting operations.

Authorized read-only investigation may execute according to the matrix and target authorization rules.

PLAN mode cannot mutate target state.

22. ASK Mode

In ASK mode, if Policy evaluation returns ALLOW, execution may proceed.

If Policy evaluation returns ASK, synchronous human authorization is required before execution.

If human approval is denied or times out, the result is DENY.

No component may proceed with execution on an ASK decision without receiving and durably recording the corresponding human approval.

Approval MUST be:

task-scoped;
operation-scoped;
target-scoped;
time-bounded;
auditable;
non-transferable.

23. AUTO Mode

AUTO executes only ALLOW.

ASK pauses execution for human authorization.

AUTO MUST NOT reinterpret ASK as ALLOW.

24. DANGEROUS Mode

DANGEROUS does not bypass:

explicit DENY;
target authorization;
protected targets;
mandatory human confirmation;
unsupported capabilities;
resource limits;
action journaling;
audit;
verification;
security boundaries.

25. Human Confirmation

When synchronous confirmation is required, the approval request MUST identify:

task;
operation;
target;
relevant arguments;
risk;
side effect;
reversibility;
idempotency;
expected consequence;
policy version;
rule version;
expiration.

The acting model cannot provide approval.

Approval records MUST include a unique approval reference and a nonce or equivalent anti-replay mechanism. The approval reference MUST be single-use and consumed during the corresponding authorized execution. Reuse of the same approval reference for a different operation, target, or arguments MUST be denied.

26. Filesystem Registry — v1

Operation | Risk | Side Effect | Reversibility | Idempotency
read_file | LOW | READ_ONLY | REVERSIBLE | IDEMPOTENT
list_directory | LOW | READ_ONLY | REVERSIBLE | IDEMPOTENT
stat | LOW | READ_ONLY | REVERSIBLE | IDEMPOTENT
search | LOW | READ_ONLY | REVERSIBLE | IDEMPOTENT
create_file | MEDIUM | MUTATING | REVERSIBLE | CONDITIONALLY_IDEMPOTENT
write_file | MEDIUM | MUTATING | REVERSIBLE | CONDITIONALLY_IDEMPOTENT
delete_file | HIGH | DESTRUCTIVE | IRREVERSIBLE | NON_IDEMPOTENT
delete_directory | HIGH | DESTRUCTIVE | IRREVERSIBLE | NON_IDEMPOTENT

v1 intentionally treats filesystem deletion as irreversible.

A future trash/version-control-aware operation may receive a separate registered operation with its own deterministic classification.

27. Process Registry — v1

Operation | Risk | Side Effect
process.list | LOW | READ_ONLY
process.inspect | LOW | READ_ONLY
process.start | MEDIUM | MUTATING
process.stop | MEDIUM | MUTATING

Security-sensitive/system-critical processes are HIGH or CRITICAL under their applicable registry.

Unknown classification:

DENY.

28. Termux:API Registry — v1

Operation | Risk | Side Effect
battery_status | LOW | READ_ONLY
wifi_status | LOW | READ_ONLY
device_info | LOW | READ_ONLY
send_notification | MEDIUM | EXTERNAL_EFFECT

Unregistered Termux:API operations:

DENY.

29. Android Package Registry — v1

Operation | Risk | Side Effect
package.list | LOW | READ_ONLY
package.inspect | LOW | READ_ONLY
package.install | HIGH | MUTATING
package.uninstall | HIGH | DESTRUCTIVE
package.clear_data | HIGH | DESTRUCTIVE

Package operations require:

allowedPackages
allowedPackageOperations

package.clear_data requires synchronous human confirmation.

Unknown package target:

DENY.

30. Android Settings Registry — v1

Operation | Risk | Side Effect
settings.read | LOW | READ_ONLY
settings.write | MEDIUM | MUTATING
security_settings.write | HIGH | MUTATING
security_control.disable | CRITICAL | DESTRUCTIVE

Settings targets MUST match:

allowedAndroidSettings

Security-sensitive settings require their registered risk rules.

31. Accessibility Registry — v1

Operation | Risk | Side Effect | Reversibility | Idempotency
accessibility.inspect_ui | LOW | READ_ONLY | REVERSIBLE | IDEMPOTENT
accessibility.tap | MEDIUM | EXTERNAL_EFFECT | UNKNOWN | UNKNOWN
accessibility.type_text | MEDIUM | EXTERNAL_EFFECT | UNKNOWN | UNKNOWN
accessibility.submit | HIGH | EXTERNAL_EFFECT | UNKNOWN | NON_IDEMPOTENT

Unknown classifications are handled conservatively.

tap and type_text therefore require ASK in AUTO/DANGEROUS.

submit requires ASK.

UI operations MUST match:

allowedUIActions

32. Network Registry — v1

Operation | Risk | Side Effect | Reversibility | Idempotency
network.dns_lookup | LOW | READ_ONLY | REVERSIBLE | IDEMPOTENT
network.http_get | LOW | READ_ONLY | REVERSIBLE | IDEMPOTENT
network.download | MEDIUM | MUTATING | REVERSIBLE | CONDITIONALLY_IDEMPOTENT
network.http_mutation | HIGH | EXTERNAL_EFFECT | UNKNOWN | NON_IDEMPOTENT

Network targets MUST match:

allowedNetworkDomains
and/or
allowedNetworkDestinations

as required.

network.download in AUTO requires:

approved source scope;
approved destination filesystem scope;
resource authorization;
no higher-risk content/source classification.

Unmatched source or destination:

DENY.

33. Shizuku Scope — v1

Structured Shizuku operations are:

OUT OF SCOPE

Therefore:

shizuku.execute_structured → DENY

Future support requires individually registered operations and complete deterministic policy rules.

34. ADB Scope — v1

Generic structured ADB operations:

adb.structured_operation → DENY

Generic ADB shell:

adb.shell → CRITICAL

requires synchronous human confirmation unless a narrower future operation is explicitly registered.

ADB is not a general structured Android backend in v1.

35. Root Scope — v1

Generic structured root operations:

root.structured_operation → DENY

Generic root shell is CRITICAL.

Root shell requires synchronous human confirmation unless explicitly denied by a higher-level rule.

Root cannot be used as an implicit fallback for denied structured operations.

36. Generic Command Execution

Generic command execution is CRITICAL unless an explicit versioned allowlisted operation establishes a narrower classification.

Examples include:

bash -c
sh -c
arbitrary shell command
arbitrary Termux command
arbitrary ADB command
arbitrary root command

Generic commands MUST NOT receive automatic authorization merely because they appear harmless.

37. Generic Command Allowlist

A lower-risk command exception MUST specify:

exact executable;
accepted argument schema;
target scope;
environment restrictions;
filesystem scope;
network scope;
risk;
side effect;
reversibility;
idempotency;
resource limits;
retry behavior.

String-prefix or natural-language allowlisting is insufficient.

38. Subagent Policy

Subagents inherit:

parent policy version;
parent rule version;
parent permission mode;
parent target authorization;
parent security context;
parent resource limits.

Subagents may narrow these values.

They cannot elevate them.

Every subagent operation passes through Policy.

39. Approval Non-Transferability

A human approval granted to a parent task is NOT automatically transferable to a subagent.

Each subagent operation requiring ASK MUST obtain its own authorization unless the original approval explicitly covers:

that subagent;
that operation;
that target;
relevant arguments;
relevant time period.

Ambiguous approval MUST be treated as non-transferable.

40. Resource Authorization

Policy may perform a resource pre-check.

The authoritative allocator remains ResourceCoordinator.

Therefore:

Policy pre-check
↓
ResourceCoordinator
↓
Execution

Resource limits cannot be modified by the model.

If audit persistence fails after resource pre-check but before execution, any resources acquired during the pre-check MUST be released, and the operation MUST NOT proceed. The ResourceCoordinator must maintain a rollback/release path for authorization failures.

41. Action Journaling

Every side-effecting operation MUST have:

STARTED

durably persisted before execution.

Terminal result MUST be:

SUCCEEDED
FAILED
CANCELLED
UNKNOWN

If the final result cannot be established:

UNKNOWN.

If the STARTED record cannot be persisted, the operation MUST NOT execute, and any resources acquired during pre-check MUST be released.

42. Audit

Security-sensitive policy decisions MUST be durably audited before execution.

Audit integrity MUST follow SECURITY.md.

If required pre-execution audit persistence fails:

DENY.

A DENY caused by audit failure remains DENY.

The system MAY separately flag the audit infrastructure failure for operational monitoring.

43. Secret Handling

Secrets MUST remain outside model context whenever they are not genuinely required.

Action journals MUST NOT contain plaintext secrets.

Secret-containing arguments MUST use:

cryptographic hashes;
secure references;
or another approved non-secret representation.

Minimum necessary secret material only.

44. Verification

Verification operations are policy-governed tools.

Verification cannot bypass:

target authorization;
Policy;
audit;
resource limits;
security boundaries.

Verification authority remains subject to VERIFICATION.md.

45. Material Mutation

Material mutation includes changes that may invalidate verification evidence.

Examples:

target resources;
relevant configuration;
application/system state;
external state;
Completion Contract;
evidence sources.

If materiality is uncertain:

treat as material and re-verify.

46. Policy Evaluation Precedence

Evaluation follows this deterministic order. Each stage is checked in sequence; if a DENY condition applies, the final result is DENY unless a higher-level rule explicitly permits a specific exception (such as mandatory human confirmation for some ASK cases). In general:

1. Invalid policy/rule version → DENY
2. Invalid task state → DENY
3. Invalid permission mode → DENY
4. Invalid effort level → DENY
5. Missing/invalid TargetAuthorizationContext → DENY
6. Explicit target denial → DENY
7. Protected-target violation → DENY
8. Security boundary violation → DENY
9. Unsupported/out-of-scope capability → DENY
10. Unknown/unregistered tool or operation → DENY
11. Invalid/malformed arguments → DENY
12. Missing authoritative risk rule → DENY
13. Mandatory human confirmation → ASK (if approval absent)
14. Unsafe HIGH/CRITICAL non-idempotent behavior → ASK or DENY according to §17
15. Operation-specific rule
16. Target-specific rule
17. Permission matrix
18. Resource pre-check
19. Final decision

A DENY at any earlier stage cannot be overridden by a later stage. The only exception is mandatory human confirmation, which produces ASK instead of DENY when the operation is otherwise allowed but requires approval. This is explicitly defined in §25.

47. Fail-Closed Conditions

The following MUST fail closed:

unknown tool;
unknown operation;
missing policy rule;
incompatible policy version;
incompatible rule version;
invalid task state;
invalid permission mode;
invalid effort level;
missing target authorization context;
malformed target scope;
unmatched target;
ambiguous target;
explicitly denied target;
protected target violation;
invalid arguments;
unknown risk;
unknown reversibility;
unknown idempotency;
unsupported privileged capability;
security-context corruption;
authorization-context corruption;
unverifiable approval;
expired approval;
audit persistence failure;
action-journal persistence failure before side effect;
resource authorization failure;
integrity failure;
subagent elevation;
policy bypass;
missing runtime protected-path mapping;
stale runtime protected-path mapping;
ambiguous runtime protected-path mapping.

48. Completion Authority

The model cannot authorize:

DONE

Only the Completion Engine may authorize DONE after the requirements of VERIFICATION.md and the task Completion Contract are satisfied.

49. Verification Invalidation

Material mutation after verification invalidates affected evidence.

Re-verification is required where necessary.

The acting model cannot selectively preserve favorable evidence to avoid re-verification.

50. Adversarial Tests

The following are mandatory:

P-RULE-01 Unknown tool → DENY
P-RULE-02 Unknown operation → DENY
P-RULE-03 Missing rule → DENY
P-RULE-04 Unknown reversibility → conservative handling
P-RULE-05 Unknown idempotency → conservative handling
P-RULE-06 HIGH non-idempotent without dedup → ASK
P-RULE-07 HIGH non-idempotent without approval → DENY
P-RULE-08 CRITICAL AUTO → ASK
P-RULE-09 CRITICAL DANGEROUS → ASK
P-RULE-10 PLAN mutation → DENY
P-RULE-11 DANGEROUS explicit DENY → DENY
P-RULE-12 Subagent elevation → DENY
P-RULE-13 Subagent scope expansion → DENY
P-RULE-14 Untrusted content granting authority → DENY
P-RULE-15 Unmatched filesystem target → DENY
P-RULE-16 Explicit filesystem target denial → DENY
P-RULE-17 Read scope used as write scope → DENY
P-RULE-18 Write scope used as delete scope → DENY
P-RULE-19 Protected path violation → DENY
P-RULE-20 Unknown package target → DENY
P-RULE-21 Unapproved network target → DENY
P-RULE-22 Unapproved download destination → DENY
P-RULE-23 Shizuku structured operation → DENY
P-RULE-24 ADB structured operation → DENY
P-RULE-25 Generic ADB shell → ASK
P-RULE-26 Root structured operation → DENY
P-RULE-27 Generic root shell → ASK
P-RULE-28 Generic shell AUTO → ASK
P-RULE-29 Audit failure before mutation → DENY
P-RULE-30 Journal failure before mutation → DENY
P-RULE-31 Invalid permission mode → DENY
P-RULE-32 Invalid effort level → DENY
P-RULE-33 Missing TargetAuthorizationContext → DENY
P-RULE-34 Malformed TargetAuthorizationContext → DENY
P-RULE-35 Subagent target expansion → DENY
P-RULE-36 Expired approval → DENY
P-RULE-37 Corrupted authorization context → DENY
P-RULE-38 Model DONE claim → no completion authority
P-RULE-39 Material mutation → invalidate verification
P-RULE-40 Unapproved network download source → DENY
P-RULE-41 Unauthorized download destination → DENY
P-RULE-42 MEDIUM partially reversible AUTO → ASK
P-RULE-43 Ambiguous canonicalization → DENY
P-RULE-44 Allow + deny overlap → DENY
P-RULE-45 Policy version mismatch → DENY
P-RULE-46 Rule version mismatch → DENY
P-RULE-47 ResourceCoordinator denial → no execution
P-RULE-48 Verification bypassing Policy → DENY
P-RULE-49 Audit integrity failure → DENY/BLOCKED
P-RULE-50 Model modifies authorization context → DENY
P-RULE-51 Missing runtime protected-path mapping → DENY
P-RULE-52 Ambiguous runtime protected-path mapping → DENY
P-RULE-53 Stale runtime protected-path mapping → DENY

51. Implementation Boundary

The implementation MUST follow:

Model proposes operation
↓
Complete PolicyRequest
↓
Validate versions
↓
Validate task state
↓
Validate permission mode / effort
↓
Validate TargetAuthorizationContext
↓
Canonicalize target
↓
Match target scope
↓
Check protected/denied targets
↓
Resolve authoritative rule
↓
Resolve risk/side-effect/reversibility/idempotency
↓
Apply operation-specific rules
↓
Apply permission matrix
↓
Determine ALLOW / ASK / DENY
↓
If ASK: durable audit, synchronous human approval (with anti-replay)
↓
ResourceCoordinator
↓
STARTED action journal
↓
Execute
↓
Observe
↓
Terminal journal state

The model cannot bypass this sequence.

52. v1 Privileged Capability Boundary

Executable v1 Android capabilities:

Termux
Termux:API
Structured Android tools
Registered Accessibility operations
ADB shell — CRITICAL + synchronous human confirmation

v1 out of scope:

Shizuku structured operations
ADB structured operations
Root structured operations

Architecture may retain future adapters for these capabilities.

Architectural availability does not imply execution authority.

53. Future Privileged Capability

Future privileged operations require:

operation registration;
deterministic risk rule;
side-effect classification;
reversibility classification;
idempotency classification;
target scope;
permission behavior;
retry safety;
resource limits;
audit requirements;
confirmation requirements;
adversarial tests;
versioned policy change;
architecture/roadmap synchronization.

Until complete:

DENY.

54. Relationship to Other Documents

This document depends on:

PROJECT_CONTRACT.md
POLICY.md
TASK_SCHEMA.md
PROTECTED_PATHS.md
SECURITY.md
VERIFICATION.md
CONTINUITY.md
THREAT_MODEL.md
ARCHITECTURE.md

TASK_SCHEMA.md defines the canonical TargetAuthorizationContext structure.

PROTECTED_PATHS.md defines protected filesystem target classes and runtime mapping prerequisites.

SECURITY.md defines security guarantees.

VERIFICATION.md defines completion authority.

CONTINUITY.md defines durable execution state.

ARCHITECTURE.md defines component boundaries.

55. Invariants

P-RULE-INV-01 Every registered operation has an authoritative rule.

P-RULE-INV-02 Unknown operations fail closed.

P-RULE-INV-03 Target-requiring operations require task-scoped target authorization.

P-RULE-INV-04 Unmatched targets fail closed.

P-RULE-INV-05 Explicit target denial overrides authorization.

P-RULE-INV-06 Subagents cannot elevate policy.

P-RULE-INV-07 Permission modes cannot bypass explicit DENY.

P-RULE-INV-08 DANGEROUS cannot bypass mandatory confirmation.

P-RULE-INV-09 Unknown reversibility is conservative.

P-RULE-INV-10 Unknown idempotency is conservative.

P-RULE-INV-11 Unsafe HIGH/CRITICAL non-idempotent operations require ASK or DENY.

P-RULE-INV-12 Security-sensitive execution requires durable audit.

P-RULE-INV-13 Side-effecting execution requires durable STARTED journal state.

P-RULE-INV-14 ResourceCoordinator is authoritative for resource allocation.

P-RULE-INV-15 Verification cannot bypass Policy.

P-RULE-INV-16 Models cannot self-authorize.

P-RULE-INV-17 Models cannot authorize DONE.

P-RULE-INV-18 Untrusted content cannot grant authority.

P-RULE-INV-19 Invalid permission mode fails closed.

P-RULE-INV-20 Invalid effort level fails closed.

P-RULE-INV-21 Policy evaluation is deterministic.

P-RULE-INV-22 Version compatibility is mandatory.

P-RULE-INV-23 Security-sensitive authorization state is integrity-protected.

P-RULE-INV-24 Network targets are task-scoped.

P-RULE-INV-25 Filesystem targets are task-scoped.

P-RULE-INV-26 Package targets are task-scoped.

P-RULE-INV-27 UI actions are task-scoped.

P-RULE-INV-28 Privileged operations require explicit rules.

P-RULE-INV-29 Out-of-scope privileged capabilities cannot execute.

P-RULE-INV-30 Protected-target registries are versioned.

P-RULE-INV-31 Model assertions cannot authorize targets.

P-RULE-INV-32 Allow/deny target overlap results in DENY.

P-RULE-INV-33 Material mutation invalidates affected verification.

P-RULE-INV-34 Approval is task-, operation-, and target-scoped.

P-RULE-INV-35 Expired authorization cannot be reused.

P-RULE-INV-36 Generic command execution is never implicitly trusted.

P-RULE-INV-37 Root structured execution is denied in v1.

P-RULE-INV-38 Shizuku structured execution is denied in v1.

P-RULE-INV-39 ADB structured execution is denied in v1.

P-RULE-INV-40 Architecture-level backend availability does not imply execution authority.

P-RULE-INV-41 Target authorization MUST be present before execution of target-requiring autonomous operations.

P-RULE-INV-42 Protected-path registry integrity is required before filesystem mutation.

P-RULE-INV-43 Parent approval is not automatically transferable to subagents.

P-RULE-INV-44 Runtime protected-path mapping is mandatory before filesystem mutation is enabled for a runtime adapter.

P-RULE-INV-45 Missing, stale, ambiguous, or conflicting runtime mappings fail closed.

56. Implementation Readiness

The v1 policy baseline is implementation-ready only when:

TASK_SCHEMA.md contains TargetAuthorizationContext;
PROTECTED_PATHS.md exists with runtime mapping requirements;
target matching is implemented deterministically;
protected-target matching is implemented;
runtime protected-path mappings exist and are validated;
every v1 tool is registered;
every v1 operation has an authoritative rule;
resource enforcement is integrated;
audit-before-execution works;
action journaling works;
human confirmation works;
adversarial tests pass;
ARCHITECTURE.md and ROADMAP.md agree with the v1 privileged boundary.

57. Change Control

Changes MUST:

identify affected rules;
identify security impact;
update invariants;
update adversarial tests;
update dependent schemas/interfaces;
update DECISIONS.md / ADRs;
synchronize architecture and roadmap;
receive required external review before freezing security-critical policy changes.

Silent policy changes are prohibited.

58. Cross-Document Review Rule

If any of the following documents materially changes:

TASK_SCHEMA.md
PROTECTED_PATHS.md
POLICY_RULES.md

the affected document MUST be reviewed together with the other two for cross-document consistency before it can be frozen.

A document MUST NOT be independently frozen if its changes can alter authorization semantics in the other documents.

59. Status

Version: 1.0

Status: FROZEN

This version is the authoritative v1 executable policy baseline.

POLICY.md

Version: 0.6
Status: APPROVED / FROZEN
Authority: Policy Authorization Specification
Depends on: PROJECT_CONTRACT.md, ARCHITECTURE.md, INTERFACES.md, TASK_SCHEMA.md, SECURITY.md

---

1. Purpose

This document defines the Policy Engine's authorization semantics.

The Policy Engine determines whether an operation may execute under the current:

- task;
- task lifecycle state;
- permission mode;
- risk classification;
- policy version;
- policy rule version;
- authorization context;
- resource constraints;
- security context.

The Policy Engine is an authorization authority.

It does not:

- execute operations;
- determine whether a task is complete;
- perform verification;
- modify task objectives;
- grant itself permissions;
- increase execution limits.

---

2. Core Authorization Principle

Every tool invocation and system operation that can:

- affect system state;
- consume protected resources;
- access private information;
- access credentials or secrets;
- produce external effects;
- modify files, applications, configuration, or data;

MUST pass through the Policy Engine before execution.

No component may create an execution path that bypasses Policy.

This includes:

- primary agents;
- subagents;
- verification components;
- orchestration components;
- runtime adapters;
- plugins;
- extensions;
- background workers.

---

3. Authorization Outcomes

Every Policy evaluation MUST produce one of:

ALLOW
ASK
DENY

ALLOW

The operation satisfies all applicable policy requirements and may proceed.

ASK

The operation requires explicit human authorization.

DENY

The operation MUST NOT execute.

If Policy cannot determine a safe authorization decision, it MUST fail closed.

---

4. Fail-Closed Principle

Policy evaluation MUST fail closed.

The following conditions MUST NOT result in implicit authorization:

- missing policy;
- unknown policy version;
- unknown rule version;
- incompatible policy/rule versions;
- missing rule;
- unknown tool;
- unknown operation;
- ambiguous risk classification;
- missing required permission;
- invalid authorization context;
- invalid task execution state;
- corrupted policy data;
- unavailable required security information;
- unavailable required audit mechanism.

Unless an explicit versioned policy rule defines a safe alternative, the default result is:

DENY

A missing risk rule MUST NOT automatically become "ALLOW" or "ASK".

A safe "ASK" fallback is permitted only when explicitly defined by an applicable versioned policy rule.

---

5. Task Lifecycle State

The Policy Engine MUST consider the current lifecycle state of the task before authorizing execution.

The Task Manager MUST supply the current task state as part of the Policy context.

If the current task state does not permit execution, Policy MUST return:

DENY

Examples of states that normally MUST NOT permit new execution include:

DONE
CANCELLED
FAILED
BLOCKED
WAITING_USER

Execution-permitting states are defined by the task lifecycle specification.

Policy MUST NOT infer or silently override task state.

A stale, missing, invalid, or ambiguous task state MUST fail closed.

---

6. Policy and Rule Version Compatibility

Every Policy decision MUST identify:

policyVersion
ruleVersion

The Policy Engine MUST verify that the selected policy version and rule version are explicitly compatible.

If compatibility is not established:

DENY

No implicit compatibility assumption is permitted.

Historical audit records MUST retain the exact versions used for the decision.

---

7. Deterministic Policy Evaluation

Policy evaluation MUST be deterministic.

Given identical:

- Policy version;
- rule version;
- Policy request;
- task state;
- authorization context;
- environment context;
- resource context;
- relevant policy inputs;

the Policy Engine MUST produce the same authorization result.

Equivalent implementations MAY differ internally, but the externally observable authorization decision MUST remain deterministic.

Non-deterministic model output, timing, or untrusted environment content MUST NOT be used to silently alter authorization semantics.

---

8. Policy Request

A Policy evaluation conceptually receives:

PolicyRequest {
    taskId

    taskState

    toolId
    operationId

    target
    arguments

    permissionMode
    riskLevel

    policyVersion
    ruleVersion

    actorContext
    environmentContext

    resourceContext
    authorizationContext
}

Environment Context

"environmentContext" MUST distinguish trusted authority from untrusted content.

Untrusted information may include:

- files being analyzed;
- web content;
- command output;
- application content;
- external API responses;
- user-controlled project files;
- data supplied by another process.

Untrusted content MUST NOT modify Policy authority or authorization requirements.

---

9. Tool Registration

Every executable tool MUST be registered with the Policy system.

At minimum, registered tools MUST provide:

toolId
operationId
sideEffect
reversibility
requiredPermissions

A tool MAY provide a candidate "riskLevel", but this value is not authoritative.

The authoritative risk classification comes from the applicable versioned "POLICY_RULES.md".

If tool metadata conflicts with "POLICY_RULES.md":

«POLICY_RULES.md wins.»

Unregistered tools MUST be denied.

A tool cannot authorize itself by supplying its own metadata.

---

10. Risk Classification

Operations use the following conceptual risk levels:

LOW
MEDIUM
HIGH
CRITICAL

The authoritative mapping between:

- tool;
- operation;
- target;
- side effect;
- reversibility;
- required permission;
- risk level;

MUST be defined by the versioned "POLICY_RULES.md".

This document defines authorization semantics but does not contain the complete operational risk table.

---

11. Generic Command Execution

Generic command execution is inherently high-risk because arbitrary commands may bypass structured authorization.

Therefore:

«Generic command execution MUST be classified as "CRITICAL" by default.»

A lower classification is permitted only when an explicit, versioned "POLICY_RULES.md" rule defines:

- the exact command or command family;
- permitted arguments or argument constraints;
- permitted targets;
- permitted environment;
- permitted side effects;
- required permissions;
- applicable resource limits;
- audit requirements.

If no such rule exists:

DENY

Structured tools SHOULD be preferred over unrestricted command execution.

---

12. Permission Modes

The system supports:

PLAN
ASK
AUTO
DANGEROUS

PLAN

No side-effecting execution is permitted.

The system may:

- inspect allowed information;
- analyze;
- plan;
- produce proposed actions.

ASK

Operations requiring authorization are presented to the user.

AUTO

Policy-authorized operations may execute without per-action human approval.

DANGEROUS

Allows explicitly authorized high-risk workflows but does not bypass Policy.

"DANGEROUS" MUST NOT mean unrestricted execution.

Policy rules, risk classifications, audit requirements, resource limits, and security requirements remain mandatory.

---

13. Permission Mode vs Risk

Permission mode and risk level are separate concepts.

For example:

AUTO + LOW

may allow automatic execution.

But:

AUTO + CRITICAL

does not automatically permit execution.

Policy determines whether the combination is:

ALLOW
ASK
DENY

The model cannot downgrade an operation's risk level to obtain authorization.

---

14. Human Approval

When Policy returns "ASK", the system MUST obtain explicit human authorization before execution.

An approval MUST be:

- associated with the task;
- associated with the specific operation or approved scope;
- associated with the applicable policy version;
- associated with the applicable rule version;
- time-bounded;
- auditable.

Conceptually:

Approval {
    approvalReference
    taskId
    operation
    scope
    decision
    timestamp
    policyVersion
    ruleVersion
    approverIdentity
    expiration
}

Approval MUST NOT silently grant broader authority than the requested scope.

---

15. Approval Timeout

If human approval is requested and:

- the user does not respond;
- the approval expires;
- the approval system becomes unavailable;

the default result MUST be:

DENY

or an equivalent non-executable blocked state.

No response MUST NOT be interpreted as approval.

---

16. Audit Requirements

All security-relevant Policy decisions MUST be durably audited.

This includes at minimum:

- "ALLOW";
- "ASK";
- "DENY";
- human approval requests;
- human approval grants;
- human approval denials;
- authorization failures;
- policy evaluation failures;
- policy version;
- rule version;
- task state.

The audit event MUST be recorded before an authorized operation is released for execution.

Conceptually:

Policy Evaluation
       ↓
Audit Event
       ↓
Execution Authorization
       ↓
Operation

---

17. Audit Integrity

Policy audit records are security-critical records.

Audit records MUST be stored in an integrity-protected manner as defined by "SECURITY.md".

Their integrity MUST be verifiable for:

- compliance checks;
- incident investigation;
- completion verification;
- security auditing.

Tampering, corruption, deletion, or unexplained modification of required Policy audit records MUST be treated as a security-relevant integrity failure.

The mechanism used to provide integrity protection is defined by "SECURITY.md" and MUST NOT weaken the authorization semantics specified here.

---

18. Audit Failure

If the required audit event cannot be durably recorded:

«The operation MUST NOT execute.»

For an operation that would otherwise be "ALLOW":

ALLOW
  ↓
Audit failure
  ↓
DENY / BLOCKED

For an operation that would otherwise be "ASK":

ASK
  ↓
Audit failure
  ↓
NO USER PROMPT
  ↓
DENY / BLOCKED

The system SHOULD separately raise an operational monitoring alert because audit infrastructure failure is itself security-relevant.

For an already-denied operation, failure to persist the DENY audit does not authorize execution. The result remains "DENY", while the audit failure is separately recorded or surfaced through the available operational monitoring mechanism.

---

19. ASK Flow

The mandatory ASK sequence is:

Policy Evaluation
       ↓
ASK
       ↓
Durable ASK Audit
       ↓
Human Prompt
       ↓
APPROVE / DENY
       ↓
Durable Approval Audit
       ↓
Final Authorization
       ↓
Execution

If the initial ASK audit cannot be recorded:

NO PROMPT
↓
DENY / BLOCKED

Human approval responses MUST themselves be durably audited.

---

20. Secret and Private Data Handling

Policy MUST enforce secret minimization.

Security-sensitive data includes:

- passwords;
- API keys;
- authentication tokens;
- private keys;
- session credentials;
- recovery codes;
- encryption keys;
- other authentication material.

Access to such data MUST be explicitly authorized.

Secrets MUST NOT be unnecessarily:

- exposed to models;
- included in logs;
- copied into task history;
- included in audit messages;
- transmitted to unrelated tools;
- persisted in plaintext where secure storage is available.

Required secret redaction MUST be unconditional for applicable audit and observability channels.

---

21. Resource Constraints

Policy may consider task resource constraints as part of pre-authorization.

Examples:

- remaining action steps;
- remaining model calls;
- remaining retries;
- wall-clock budget;
- storage budget;
- network budget.

However:

«Resource pre-check does not grant or acquire a resource lock.»

The "ResourceCoordinator" is authoritative for:

- resource reservation;
- resource locking;
- concurrent resource ownership;
- actual budget consumption;
- release of resources.

Therefore:

Policy
  ↓
Authorization
  ↓
ResourceCoordinator
  ↓
Budget / Lock Check
  ↓
Execution

The exact resource accounting model belongs to the resource and task specifications.

---

22. Policy and Verification Separation

Policy answers:

«“May this operation execute?”»

Verification answers:

«“Did the task actually achieve its required result?”»

These authorities MUST remain separate.

Policy MUST NOT declare task completion.

Verification MUST NOT authorize execution.

Completion MUST be handled by the Completion Engine.

---

23. Verification Actions

Verification actions are also system/tool operations.

Examples include:

- running tests;
- reading files;
- inspecting system state;
- querying application state;
- checking database state;
- collecting telemetry;
- invoking APIs;
- executing diagnostic commands.

Therefore, verification actions MUST pass through Policy like any other tool invocation.

The Verification Engine MUST NOT create a hidden execution channel that bypasses Policy.

---

24. Subagent Authorization

Subagents operate under the authority of their parent task.

A subagent MUST inherit the applicable:

- permission mode;
- risk constraints;
- policy context;
- resource limits;
- task scope;
- security restrictions.

A subagent MUST NOT:

- increase its own authority;
- downgrade risk;
- obtain broader permissions;
- modify parent policy;
- bypass Policy;
- authorize "DONE".

Subagent delegation MAY narrow authority but MUST NOT elevate it.

---

25. Policy Evaluation Sequence

The conceptual evaluation sequence is:

1. Receive PolicyRequest
        ↓
2. Validate request
        ↓
3. Validate task lifecycle state
        ↓
4. Validate tool registration
        ↓
5. Resolve policy version
        ↓
6. Resolve authoritative rule version
        ↓
7. Validate policy/rule compatibility
        ↓
8. Resolve authoritative risk classification
        ↓
9. Evaluate permission mode
        ↓
10. Evaluate authorization context
        ↓
11. Evaluate environment / trust context
        ↓
12. Evaluate applicable security requirements
        ↓
13. Perform policy-level resource pre-check
        ↓
14. Produce ALLOW / ASK / DENY
        ↓
15. Durably audit decision
        ↓
16. Release authorization only if audit succeeds

Actual resource locking and consumption are handled by "ResourceCoordinator" after authorization.

---

26. Policy Change Management

Policy changes are security-sensitive architectural changes.

Changes MUST follow the project's change-management requirements defined by:

- "PROJECT_CONTRACT.md";
- "DECISIONS.md";
- applicable ADRs;
- versioned policy/rule changes.

A policy change MUST NOT silently alter the meaning of historical audit records.

Policy and rule versions MUST therefore remain identifiable for each authorization decision.

---

27. Policy Invariants

P-INV-01

Every executable tool invocation MUST pass through Policy.

P-INV-02

No component may bypass Policy.

P-INV-03

Unknown authorization state MUST fail closed.

P-INV-04

Unknown or missing policy rules MUST default to DENY unless an explicit safe fallback rule exists.

P-INV-05

Unregistered tools MUST be denied.

P-INV-06

Tool metadata cannot override authoritative policy rules.

P-INV-07

"POLICY_RULES.md" is authoritative for operational risk classification.

P-INV-08

Generic command execution is CRITICAL unless explicitly lowered by a versioned rule.

P-INV-09

The model cannot downgrade risk to obtain authorization.

P-INV-10

DANGEROUS mode does not bypass Policy.

P-INV-11

Human approval cannot grant authority outside its defined scope.

P-INV-12

Human approval MUST be time-bounded and auditable.

P-INV-13

Security-relevant Policy decisions MUST be durably audited.

P-INV-14

Authorization MUST NOT be released when required audit persistence fails.

P-INV-15

An ASK prompt MUST NOT be displayed if the required initial ASK audit cannot be persisted.

P-INV-16

Audit failure MUST NOT convert DENY into authorization.

P-INV-17

Secrets MUST be minimized and protected from unauthorized exposure.

P-INV-18

ResourceCoordinator is authoritative for resource locks and actual resource accounting.

P-INV-19

Policy does not authorize task completion.

P-INV-20

Verification actions MUST pass through Policy.

P-INV-21

Subagents cannot elevate parent authority.

P-INV-22

Untrusted environment content cannot modify authorization authority.

P-INV-23

Execution MUST be denied when the task lifecycle state does not permit execution.

P-INV-24

Policy and rule versions MUST be explicitly compatible before authorization.

P-INV-25

Policy evaluation MUST be deterministic for identical authoritative inputs and versions.

P-INV-26

Required Policy audit records MUST be integrity-protected and their integrity MUST be verifiable.

---

28. Adversarial Policy Tests

The implementation MUST include tests for at least:

Test 01 — Unknown Tool

Unknown tool attempts execution.

Expected: DENY.

Test 02 — Missing Rule

Registered tool has no applicable policy rule.

Expected: DENY.

Test 03 — Metadata Escalation

Tool reports LOW risk while authoritative rule says CRITICAL.

Expected: CRITICAL classification wins.

Test 04 — Model Risk Downgrade

Model attempts to label CRITICAL operation as LOW.

Expected: ignored; authoritative classification remains.

Test 05 — Generic Command

Unrestricted command execution without explicit allowlist.

Expected: CRITICAL / DENY unless explicitly authorized.

Test 06 — Dangerous Mode Bypass

DANGEROUS mode attempts to bypass Policy.

Expected: DENY.

Test 07 — Audit Failure

ALLOW decision cannot be durably audited.

Expected: operation does not execute.

Test 08 — ASK Audit Failure

ASK decision cannot be durably audited.

Expected: no human prompt; DENY/BLOCKED.

Test 09 — Approval Timeout

User does not approve before timeout.

Expected: DENY.

Test 10 — Approval Scope Escalation

Approval for one operation is reused for a broader operation.

Expected: DENY.

Test 11 — Unregistered Tool Metadata

Unknown tool attempts to authorize itself using metadata.

Expected: DENY.

Test 12 — Untrusted Content Injection

A file or external response instructs the Policy Engine to allow an operation.

Expected: instruction treated as untrusted data; authorization unchanged.

Test 13 — Subagent Escalation

Subagent requests permissions exceeding its parent.

Expected: DENY.

Test 14 — Verification Policy Bypass

Verification Engine attempts direct execution outside Policy.

Expected: DENY.

Test 15 — Resource Lock Confusion

Policy pre-check reports sufficient budget while ResourceCoordinator denies the lock.

Expected: execution does not occur.

Test 16 — Invalid Task State

Task is "DONE", "CANCELLED", "FAILED", or another non-executable state and an operation is requested.

Expected: DENY.

Test 17 — Version Mismatch

Policy version and rule version are incompatible.

Expected: DENY.

Test 18 — Determinism

Identical authoritative Policy requests are evaluated repeatedly.

Expected: identical authorization decision.

Test 19 — Audit Tampering

A required Policy audit record is modified after execution.

Expected: integrity verification detects the modification; compliance cannot report PASS.

---

29. Relationship to Completion

Policy authorization is necessary but not sufficient for completion.

The complete flow is:

Task
 ↓
Policy Authorization
 ↓
Resource Authorization
 ↓
Execution
 ↓
Observation
 ↓
Verification
 ↓
Policy Compliance Check
 ↓
Resource Compliance Check
 ↓
Completion Engine
 ↓
DONE

A successfully authorized operation does not imply successful task completion.

---

30. Future-Proofing

The Policy contract MUST remain independent of:

- Pi;
- OpenCode;
- any specific orchestrator;
- any specific model;
- Android;
- Termux;
- Shizuku;
- ADB;
- any specific storage backend.

Runtime-specific implementations MUST adapt to the Policy interface rather than changing Policy semantics.

---

31. Definition of Policy Compliance

A task is Policy-Compliant only when:

Every required operation passed Policy
        AND
Authoritative risk rules were applied
        AND
No unauthorized operation occurred
        AND
Required approvals were valid
        AND
Required approvals were audited
        AND
Required Policy audit records exist
        AND
Audit integrity is valid
        AND
Resource authorization was respected
        AND
No privilege escalation occurred
        AND
Subagents remained within inherited authority
        AND
All operations occurred while task state permitted execution
        AND
Policy/rule version compatibility was valid

Only then may Policy compliance be reported as "PASS" to the Completion Engine.

---

32. Status

POLICY.md v0.6

Status: APPROVED / FROZEN

This version incorporates all outstanding review requirements from v0.5:

- task lifecycle state is authorization-relevant;
- incompatible policy/rule versions fail closed;
- Policy evaluation is deterministic;
- audit integrity is explicitly tied to "SECURITY.md";
- previous fail-closed, audit, approval, secret, resource, verification, and subagent requirements remain unchanged.

No further architectural changes should be made to this document unless a new security or architectural requirement is discovered.

Future implementation details belong in:

- "SECURITY.md";
- "POLICY_RULES.md";
- "TASK_SCHEMA.md";
- "CONTINUITY.md";
- "THREAT_MODEL.md";
- "OBSERVABILITY.md";
- applicable ADRs.

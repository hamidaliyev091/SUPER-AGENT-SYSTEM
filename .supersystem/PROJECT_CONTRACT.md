PROJECT CONTRACT

Version: 0.3
Status: ACTIVE — FINAL CONTRACT
Last Updated: 2026-09-09

---

1. Purpose

This project defines a model-agnostic, runtime-agnostic autonomous agent platform capable of performing complex tasks on behalf of the user.

The intended execution lifecycle is:

UNDERSTAND
    ↓
INVESTIGATE
    ↓
PLAN
    ↓
DELEGATE
    ↓
EXECUTE
    ↓
OBSERVE
    ↓
DETECT FAILURE
    ↓
RECOVER / RETRY
    ↓
TEST
    ↓
VERIFY
    ↓
CONTINUE UNTIL COMPLETE
    ↓
REPORT

The system must not consider a task complete merely because an AI model claims that it is complete.

The platform exists to provide:

- autonomous execution
- controlled permissions
- durable state
- failure recovery
- verifiable completion
- model independence
- runtime independence
- tool independence
- auditable behavior
- future replaceability

---

2. Core Principles

These principles are architectural laws.

2.1 Runtime Independence

The core system MUST NOT depend directly on a specific agent runtime.

Examples include:

- Pi
- OpenCode
- Claude Code
- future runtimes

A runtime is an implementation detail and MUST be replaceable through an adapter.

---

2.2 Model Independence

The system MUST NOT depend on one specific AI model or provider.

Possible providers include:

- DeepSeek
- Claude
- Gemini
- OpenAI
- local models
- future models

The core MUST communicate through a normalized model interface.

Model-specific behavior belongs inside model adapters/providers.

---

2.3 Tool Independence

The core MUST communicate with capabilities through structured tool interfaces.

The core MUST NOT depend directly on:

- shell command strings
- Android-specific command syntax
- runtime-specific tool formats
- provider-specific function formats

Platform-specific implementations belong behind adapters.

---

2.4 Security Boundary

The AI model is not trusted.

The model may propose actions.

The model MUST NOT independently grant itself permission to perform those actions.

Every side-effecting tool action MUST pass through the Policy Engine.

Security decisions MUST NOT depend solely on model-generated reasoning.

---

2.5 No LLM-Controlled Completion

An LLM (Large Language Model) MUST NOT be the final authority determining that a task is complete.

The model may claim:

"I believe the task is complete."

This is only a claim.

The system MUST independently evaluate the Completion Contract and Verification results before allowing "DONE".

---

2.6 Verification Over Belief

Completion MUST be based on observable evidence.

Deterministic verification MUST be preferred whenever available.

A model's confidence, explanation, or self-reported success is never sufficient evidence by itself.

If mandatory verification cannot be performed, the task MUST NOT transition to "DONE".

---

2.7 Least Privilege

Every operation MUST receive only the permissions required for that operation.

Higher-risk operations require stronger authorization.

Subagents MUST NOT automatically inherit unrestricted permissions from their parent.

---

2.8 Failure Persistence

Material failures MUST survive:

- context compaction
- model changes
- runtime restarts
- process crashes
- session resume

Failure information MUST be stored in durable task state.

The system MUST avoid repeating an ineffective approach without new evidence.

---

2.9 Explicit Architectural Decisions

Important architectural changes MUST be recorded as Architecture Decision Records (ADRs).

AI recommendations are advisory.

An AI recommendation MUST NOT automatically modify the architecture.

---

2.10 Documentation Is Part of the System

Important architectural and behavioral changes MUST update relevant project documentation.

Documentation is part of the platform's operational contract.

---

3. System Components

The platform conceptually contains:

- Task Manager
- Policy Engine
- Verification Engine
- Completion Engine
- Model Router
- Continuity / Persistence Manager
- Orchestrator
- Runtime Adapter
- Structured Tool Runtime
- Platform Adapters
- Human-in-the-Loop Interface
- Observability / Audit subsystem

Detailed implementation boundaries are defined in separate architecture documents.

This contract defines guarantees rather than implementation details.

---

4. Task Contract

Every task that can execute autonomously MUST have a structured task definition containing at minimum:

- Objective
- Requirements
- Observable Success Criteria
- Permission Mode
- Effort Level
- Workflow
- Verification Requirements
- Resource Limits

4.1 Mandatory Success Criteria

Any task capable of entering execution MUST have explicit, observable success criteria defined before execution begins.

This requirement is mandatory.

The phrase "whenever practical" does not apply to completion criteria.

If meaningful observable success criteria cannot be defined, the system MUST NOT enter autonomous execution.

The task MUST instead be:

- rejected
- "BLOCKED"
- or "WAITING_USER"

as appropriate.

---

5. Completion Contract

Every task capable of reaching "DONE" MUST establish a Completion Contract before execution.

The Completion Contract defines:

1. What must be true when the task finishes.
2. How each condition will be verified.
3. What evidence is required.
4. What constitutes failure.
5. What constitutes inconclusive verification.

Example:

Criterion:
Application X must be installed and launch successfully.

Verification:
1. Confirm package exists.
2. Launch application.
3. Confirm successful launch.
4. Verify expected application state.

Result:
PASS / FAIL / INCONCLUSIVE

A task MUST NOT transition to "DONE" unless all mandatory completion criteria have passed.

---

6. Verification Requirements

Verification MUST be conceptually separated from execution.

The acting model's claim is never sufficient verification.

6.1 Normal-Risk Verification

For ordinary operations, verification SHOULD use deterministic or externally observable evidence whenever available.

6.2 High-Risk Verification

For any operation classified as:

- "HIGH"
- "CRITICAL"

verification MUST include at least one evidence source that is:

1. deterministic or externally observable, and
2. not freely controllable by the acting agent.

The evidence MUST be sufficient to establish the relevant success criterion.

6.3 Irreversible Actions

For any irreversible or materially destructive action, the same independent-verification requirement is mandatory.

If the required independent evidence cannot be obtained:

verification = INCONCLUSIVE

and:

DONE = FORBIDDEN

The task MUST instead continue, request user input, become "BLOCKED", or fail explicitly.

6.4 Verification Independence

A second model agreeing with the acting model does not automatically constitute independent verification.

Independence must come from the evidence source or verification mechanism.

Examples:

- operating-system state
- filesystem state
- independent process output
- external service response
- deterministic test
- checksum
- cryptographic validation
- pre/post state comparison

---

7. Policy Engine

The Policy Engine is the central authorization boundary.

Every side-effecting tool action MUST be evaluated by it.

The result MUST be:

ALLOW
ASK
DENY

7.1 Policy Inputs

At minimum:

- tool identity
- operation
- arguments
- target resource
- permission mode
- task context
- risk level
- authorization state
- policy version

7.2 Fail Closed

Ambiguous authorization MUST fail closed.

High-risk or ambiguous operations MUST NOT automatically become "ALLOW".

The model MUST NOT modify the policy decision after it is produced.

---

8. Permission Modes

Initial permission modes:

PLAN
ASK
AUTO
DANGEROUS

PLAN

Planning and investigation only.

Side-effecting execution is prohibited unless explicitly authorized by policy.

ASK

Side-effecting actions requiring human approval must request approval.

AUTO

The system may automatically execute operations permitted by policy.

DANGEROUS

The system may perform broader autonomous operations where policy permits.

"DANGEROUS" does NOT disable the Policy Engine.

It does NOT automatically authorize irreversible or critical operations.

---

9. ASK Authorization

Human approval MUST be:

- explicit
- scoped
- time-bounded
- auditable
- associated with the relevant task/action

Approval MUST NOT silently authorize materially different operations.

Expired approval MUST NOT be reused.

If approval times out, the default result MUST be:

DENY

unless an explicitly defined safe policy applies.

---

10. Risk Classification

Operations MUST have a risk classification.

Initial levels:

LOW
MEDIUM
HIGH
CRITICAL

Risk MUST be determined using structured policy rules.

The LLM MUST NOT arbitrarily downgrade risk.

Platform-specific risk policies MAY exist.

Detailed risk rules belong in "POLICY.md".

---

11. Untrusted Environment

External/environmental information MUST be treated as untrusted data.

This includes:

- web pages
- files
- notifications
- messages
- UI text
- application content
- terminal output
- downloaded content
- external APIs

Environment content MUST NOT automatically become an instruction with authority equal to user or system instructions.

The system MUST maintain separation between:

USER INTENT
SYSTEM / PROJECT POLICY
TASK STATE
ENVIRONMENT DATA
MODEL OUTPUT

Environment data MUST NOT directly modify security policy.

---

12. Prompt Injection Resistance

The system MUST assume that external content may attempt to manipulate the model.

The Policy Engine remains authoritative regardless of model output.

Tool arguments MUST be validated against:

- tool schema
- policy
- resource restrictions
- applicable authorization

Security-critical decisions MUST NOT be delegated to instructions embedded in untrusted environmental content.

---

13. Irreversible and Destructive Actions

The system MUST explicitly classify operations according to reversibility and impact.

Where practical, the preferred sequence is:

PREVIEW
↓
DRY RUN
↓
BACKUP / SNAPSHOT
↓
EXECUTE
↓
VERIFY

For genuinely irreversible high-impact operations, explicit synchronous human confirmation MUST be required unless a documented policy explicitly authorizes autonomous execution.

"ALLOW" alone does not automatically imply that an irreversible action is safe.

Detailed classification belongs in "SECURITY.md".

---

14. Durable State

Transient LLM context MUST NOT be the authoritative source of task state.

Durable task state MUST contain, at minimum:

OBJECTIVE
REQUIREMENTS
SUCCESS CRITERIA
PLAN
STATE
DECISIONS
FAILURES
VERIFICATION
CHECKPOINTS
CONTINUITY

Durable state MUST support versioning and migration.

Context compaction MUST NOT remove task-critical durable information.

Task resume MUST reconstruct state from durable state.

---

15. Failure Records

Material failures MUST be recorded.

A Failure Record MUST contain sufficient information to determine whether a future retry is materially different.

At minimum:

- approach
- relevant arguments
- observed result
- evidence
- failure reason
- timestamp
- affected component
- retry status

Repeated failures MUST trigger adaptation or escalation rather than blind repetition.

---

16. Resource Limits

Every autonomous task MUST have externally enforced basic execution limits.

At minimum:

- maximum execution time
- maximum tool/action steps
- maximum model calls or equivalent execution budget
- maximum retry count

Where applicable, tasks MUST also support:

- network budget
- monetary/API cost budget
- concurrency limit

The model MUST NOT silently remove, bypass, or increase these limits.

Per-task overrides MAY exist only through the Policy Engine or explicit user authorization.

When a limit is reached, the task MUST transition to an explicit state such as:

- "BLOCKED"
- "WAITING_USER"
- "FAILED"

It MUST NOT continue indefinitely.

---

17. Cancellation and Recovery

The system MUST support task cancellation.

Cancellation MUST propagate through:

Task Manager
→ Orchestrator
→ Runtime
→ Tool execution
→ Subagents

Long-running tasks MUST checkpoint progress.

After a crash, process termination, runtime restart, or device restart, tasks SHOULD be recoverable from durable state.

The system MUST NOT blindly repeat an operation whose previous completion status is unknown when duplication could cause harmful side effects.

---

18. Concurrency

The initial implementation MAY operate in single-task mode.

If multiple tasks are supported, the architecture MUST define:

- task isolation
- shared-resource locking
- Android foreground ownership
- conflicting operations
- state consistency
- cancellation behavior

Concurrency behavior MUST NOT be left implicitly defined by the underlying runtime.

---

19. Delegation and Subagents

Subagents are subordinate execution units.

A subagent MUST:

- receive a defined scope
- receive only necessary permissions
- inherit applicable policy restrictions
- return structured results
- have its work auditable
- have its output validated

Subagent results MUST be subject to the same completion and evidence requirements as top-level work.

For HIGH, CRITICAL, or irreversible operations, subagent work MUST satisfy the same independent-verification requirements.

A parent agent MUST NOT delegate authority it does not possess.

Subagents MUST NOT bypass the Policy Engine.

---

20. Human Authority

The user remains the final authority over the system within the boundaries of the system's security contract.

Human interaction MUST support:

- approval
- denial
- clarification
- cancellation
- escalation

Human decisions MUST be:

- scoped
- time-bounded
- auditable
- associated with the relevant task/action

Conflicts between user instructions, task requirements, and policy MUST follow the established authority hierarchy.

---

21. Privacy and Sensitive Data

The system may access highly sensitive user information.

Therefore:

- data collection MUST be minimized
- secrets MUST NOT be included in model context unless necessary for the task
- logs MUST redact secrets and unnecessary sensitive data
- sensitive state MUST have controlled access
- retention periods MUST be defined
- sensitive stored information MUST be appropriately protected

The system MUST distinguish:

OPERATIONAL LOGS
AUDIT RECORDS
TASK STATE
MODEL CONTEXT
SENSITIVE USER DATA

These categories MUST NOT automatically share the same retention or access policy.

---

22. Observability and Audit

Security-relevant and operationally significant actions MUST produce structured records where technically applicable.

The system MUST be able to determine, for relevant actions:

WHAT happened?
WHAT initiated it?
WHICH task?
WHICH tool?
WHICH policy decision?
WHICH policy version?
WHEN?
WHAT was the result?
WHAT verification evidence exists?

Logging MUST NOT become an uncontrolled source of sensitive-data leakage.

---

23. Change Management

Changes are classified:

LOW
MEDIUM
HIGH
CRITICAL

LOW

Documentation and non-functional changes.

MEDIUM

Internal implementation changes that do not modify architectural or security guarantees.

HIGH

Changes affecting:

- interfaces
- persistence
- orchestration
- permissions
- verification
- model/runtime boundaries

CRITICAL

Changes affecting:

- security boundaries
- authorization behavior
- completion guarantees
- irreversible-action controls
- privacy guarantees
- runtime/model/tool independence

HIGH and CRITICAL changes MUST receive architectural review and appropriate ADRs.

---

24. Mandatory Architecture Enforcement

Critical architectural invariants MUST be automatically tested.

These tests MUST run in CI and MUST block merge when they fail.

At minimum:

24.1 Dependency Boundary

The core MUST NOT import runtime-specific or provider-specific implementation modules.

24.2 Policy Bypass

Tools MUST NOT be capable of executing side-effecting operations while bypassing the Policy Engine.

24.3 Direct Model Authorization

Model output MUST NOT directly authorize side-effecting tools.

24.4 Unverified DONE

The Completion Engine MUST reject "DONE" when mandatory verification has not passed.

24.5 Durable State

Simulated context compaction MUST NOT cause loss of mandatory task state, completion criteria, or material failure records.

24.6 Resource Limits

The system MUST reject attempts by the model to silently exceed externally enforced execution limits.

These are architectural requirements, not optional quality improvements.

---

25. Separation of Authority

The authority model is:

USER
  ↓
SYSTEM / PROJECT CONTRACT
  ↓
POLICY ENGINE
  ↓
TASK / WORKFLOW
  ↓
MODEL / ORCHESTRATOR
  ↓
TOOLS
  ↓
ENVIRONMENT

The model cannot override higher-level constraints.

Environment data cannot override system authority.

Tools cannot grant themselves permissions.

Subagents cannot grant themselves permissions.

---

26. Definition of Done

A task may transition to "DONE" only when ALL mandatory conditions are satisfied:

1. Explicit observable success criteria existed before execution.
2. The objective satisfies those criteria.
3. All mandatory criteria have passed.
4. Required verification evidence exists.
5. Required independent evidence exists for HIGH, CRITICAL, and irreversible operations.
6. No unresolved critical failure remains.
7. Required security checks have passed.
8. Required documentation has been updated.
9. Durable state reflects the final result.
10. No mandatory resource or policy condition has been violated.

If any mandatory condition fails:

DONE = FORBIDDEN

The task MUST instead:

- continue,
- repair,
- retry,
- request user input,
- become "BLOCKED",
- or fail explicitly.

---

27. Future-Proofing Rule

The architecture MUST allow improvements in:

- AI models
- agent runtimes
- orchestration systems
- Android automation
- local inference
- cloud inference
- tool protocols

without requiring replacement of the entire system.

Replacing a component SHOULD require replacing an adapter or implementation rather than rewriting the core.

Future-proofing MUST NOT justify unnecessary abstraction.

Only genuinely changeable boundaries should be abstracted.

---

28. Current Technology Is Not Architecture

Specific technologies are implementations, not architectural laws.

For example:

Pi
pi-ultracode
DeepSeek
Claude
Gemini
Termux
Shizuku
ADB

may be used.

None defines the permanent architecture.

The system MUST remain capable of replacing them.

---

29. External AI Review

External AI systems MAY be used to review:

- architecture
- security
- threat models
- implementation plans
- difficult technical decisions

Their recommendations are advisory.

No external AI has authority to modify this contract automatically.

When external AI review materially influences an architectural decision, that influence SHOULD be recorded in the relevant ADR.

Meaningful disagreements SHOULD be preserved rather than silently discarded.

---

30. Non-Negotiable Rules

The following rules MUST NOT be violated without an explicit revision of this contract:

1. Core architecture MUST remain runtime-independent.
2. Core architecture MUST remain model-independent.
3. Security decisions MUST NOT be controlled solely by the LLM.
4. Side-effecting tools MUST pass through the Policy Engine.
5. Ambiguous high-risk operations MUST fail closed.
6. Every executable task MUST have explicit observable success criteria before execution.
7. LLM output MUST NOT directly declare a task "DONE".
8. Mandatory completion criteria MUST be independently verified where required by risk.
9. HIGH, CRITICAL, and irreversible operations MUST have agent-independent verification evidence.
10. If mandatory verification is inconclusive, "DONE" is forbidden.
11. Durable task state MUST survive context compaction and runtime restart.
12. Material failures MUST be persisted.
13. Subagents MUST NOT bypass policy.
14. Basic resource limits MUST be externally enforced.
15. The model MUST NOT silently modify security or execution limits.
16. Irreversible high-impact actions MUST have appropriate authorization.
17. Untrusted environment content MUST NOT override system authority.
18. Critical architecture invariants MUST be protected by automated CI tests.
19. Security-critical architectural changes require explicit review.
20. No component may silently weaken another component's security boundary.
21. The system MUST NOT trade away core security guarantees merely for convenience or model capability.

---

31. Contract vs Implementation

This document defines the laws and guarantees of the platform.

It intentionally does not define every implementation detail.

Detailed behavior belongs in:

ARCHITECTURE.md
TASK_SCHEMA.md
INTERFACES.md
SECURITY.md
THREAT_MODEL.md
POLICY.md
VERIFICATION.md
CONTINUITY.md
DECISIONS.md
ROADMAP.md

If an implementation conflicts with this contract, the implementation is wrong unless the contract is explicitly revised.

---

32. Contract Revision Rule

This contract may evolve.

Any revision MUST:

1. identify what changed
2. explain why
3. identify affected guarantees
4. consider security implications
5. update relevant ADRs
6. preserve backward compatibility where practical
7. receive appropriate review before becoming ACTIVE

A model suggestion alone is never sufficient justification for weakening a non-negotiable rule.

---

33. Final Contract Status

"PROJECT_CONTRACT.md v0.3" establishes the architectural constitution of the platform.

The next stage is not another general contract rewrite.

The next stage is to translate these laws into concrete, testable specifications:

ARCHITECTURE.md
        ↓
INTERFACES.md
        ↓
POLICY.md
        ↓
SECURITY.md
        ↓
THREAT_MODEL.md
        ↓
VERIFICATION.md
        ↓
CONTINUITY.md
        ↓
TASK_SCHEMA.md
        ↓
IMPLEMENTATION

The implementation MUST conform to this contract.

END OF PROJECT CONTRACT v0.3

# AGENTS.md

## Project

This repository contains SUPER AGENT SYSTEM, a governed autonomous agent system designed to execute long-running tasks through planning, policy-controlled actions, observation, verification, recovery, and completion.

The system is model-independent, runtime-independent, and platform-independent.

The authoritative architecture and security rules are defined by the project documents. Do not duplicate or reinterpret them here.

---

## Source of Truth

Before making architectural or security-sensitive changes, consult the relevant canonical document:

- `ARCHITECTURE.md` — system architecture and component boundaries
- `INTERFACES.md` — interfaces and contracts
- `.supersystem/PROJECT_CONTRACT.md` — highest-level project contract
- `.supersystem/TASK_SCHEMA.md` — task structure and lifecycle
- `.supersystem/POLICY.md` — policy semantics
- `.supersystem/POLICY_RULES.md` — authoritative policy rules
- `.supersystem/SECURITY.md` — security requirements
- `.supersystem/THREAT_MODEL.md` — threat model
- `.supersystem/VERIFICATION.md` — verification requirements
- `.supersystem/CONTINUITY.md` — continuity and recovery requirements
- `.supersystem/PROTECTED_PATHS.md` — protected target registry

When documents conflict, do not silently choose one. Stop and identify the conflict.

Higher-level project authority overrides lower-level instructions.

---

## Core Rules

### 1. Model is not authority

The model may propose:

- plans;
- actions;
- repairs;
- delegations;
- implementation choices.

The model may not:

- authorize itself;
- bypass Policy;
- expand target authorization;
- modify protected authority;
- declare authoritative `DONE`;
- treat its own claim as verification.

---

### 2. Policy is mandatory

Every tool invocation with side effects must pass through the Policy Engine.

Never bypass Policy because:

- the action appears safe;
- the model requested it;
- the user previously allowed a different action;
- the runtime technically permits it;
- the operation is urgent;
- another component already approved it.

`DANGEROUS` is not a Policy bypass.

---

### 3. Target authorization is mandatory

Never assume that any of the following grants authorization:

- current working directory;
- model context;
- environment variables;
- installed packages;
- network reachability;
- previous authorization;
- user ownership inferred from the environment.

Targets must be explicitly authorized through the Task authorization model.

Scope may be narrowed, never silently expanded.

---

### 4. Protected targets are fail-closed

Do not modify protected authority, security, task, verification, continuity, credential, runtime, Android security/system, or governance targets through ordinary autonomous execution.

If classification, mapping, authorization, or policy state is unknown, stale, ambiguous, corrupted, or unverifiable:

`FAIL CLOSED`.

Do not guess.

---

### 5. Verification is separate from execution

Never consider a task complete merely because:

- a tool returned success;
- a command exited successfully;
- a file was changed;
- the model says it worked.

Completion requires the Verification and Completion systems to authorize it.

`INCONCLUSIVE` must never become `DONE`.

---

### 6. Durable state is authoritative

Do not use model conversation history as authoritative task state.

Use the Task Manager and durable state mechanisms defined by the architecture.

Do not silently reconstruct corrupted authoritative state.

---

## Implementation Rules

### Minimal change

Make the smallest change that correctly satisfies the task.

Do not:

- perform unrelated refactors;
- rename unrelated files;
- introduce speculative abstractions;
- add dependencies without necessity;
- change architecture without justification;
- modify frozen documents casually.

If a larger architectural change is required, stop and identify it explicitly.

---

### Architecture boundaries

Keep Core independent from:

- Pi;
- pi-ultracode;
- DeepSeek;
- Claude;
- Gemini;
- Android;
- Termux;
- any specific model provider;
- any specific runtime.

Adapters belong at the appropriate boundary.

Do not import platform-specific implementation into platform-independent Core merely for convenience.

---

### Security boundaries

Never move security decisions into:

- model prompts;
- model-specific code;
- runtime-specific code;
- individual tools;
- UI code;
- convenience helpers.

Authorization belongs to Policy.

Task authority belongs to Task Manager.

Verification belongs to Verification Engine.

Completion authority belongs to Completion Engine.

---

### Tool execution

Tools must not self-authorize.

Generic command execution is not automatically trusted.

New tools or operations must have explicit policy registration and defined:

- target scope;
- risk;
- side effect;
- reversibility;
- idempotency;
- resource requirements;
- retry behavior;
- authorization requirements;
- audit requirements.

---

### Android

Android functionality is implemented through adapters.

Do not assume that technical Android capability means the agent is authorized to use it.

Follow the v1 privilege boundary defined in:

`.supersystem/POLICY_RULES.md`

and:

`INTERFACES.md`

---

## Testing

Every implementation change must be verified at the narrowest appropriate level.

Prefer:

1. focused unit test;
2. relevant integration test;
3. security/adversarial test when applicable;
4. broader test suite when the changed surface requires it.

Never claim a change is verified when the required checks were not performed.

If a required test cannot run, report that explicitly.

---

## Adversarial Testing

The system must assume that models can:

- make incorrect decisions;
- misunderstand authorization;
- attempt Policy bypasses;
- request unauthorized targets;
- falsely claim success;
- attempt privilege escalation;
- produce malformed actions;
- behave inconsistently.

Tests must therefore verify that the architecture remains safe even when the model is malicious or incorrect.

Use fake or adversarial model implementations where appropriate.

---

## Recovery

When execution is interrupted or state is uncertain:

1. load durable state;
2. validate integrity;
3. inspect the Action Journal;
4. identify possible unknown side effects;
5. inspect relevant external state;
6. revalidate authorization and Policy;
7. revalidate resources;
8. determine the safest permitted next action.

Never assume an interrupted action had no side effect.

---

## Frozen Documents

The following documents are frozen versions and must not be casually edited:

- `ARCHITECTURE.md`
- `INTERFACES.md`
- `.supersystem/PROJECT_CONTRACT.md`
- `.supersystem/TASK_SCHEMA.md`
- `.supersystem/POLICY.md`
- `.supersystem/POLICY_RULES.md`
- `.supersystem/SECURITY.md`
- `.supersystem/THREAT_MODEL.md`
- `.supersystem/VERIFICATION.md`
- `.supersystem/CONTINUITY.md`
- `.supersystem/PROTECTED_PATHS.md`

If a frozen document must change:

1. identify why;
2. determine affected dependencies;
3. create the appropriate new version;
4. update dependent documents;
5. record the decision in the appropriate project records.

Do not silently overwrite architectural decisions.

---

## Documentation

Do not duplicate large sections of canonical documents into code comments or other instruction files.

When a rule already has a canonical source, reference that source.

Documentation should explain:

- why something exists;
- important boundaries;
- non-obvious behavior;
- operational requirements.

Do not create documentation merely to increase documentation volume.

---

## Dependency Rules

Do not add a dependency merely because it makes one small implementation easier.

Before adding a dependency, consider:

- whether the functionality can be implemented safely with existing components;
- security implications;
- maintenance cost;
- licensing;
- runtime/platform compatibility;
- whether it creates unnecessary coupling.

---

## Change Discipline

Before changing code:

1. understand the task;
2. inspect relevant files;
3. identify applicable canonical documents;
4. determine affected architecture boundaries;
5. make the smallest correct change.

After changing code:

1. inspect the diff;
2. run appropriate tests;
3. verify security implications;
4. verify no unrelated changes were introduced;
5. report what was changed and what was verified.

---

## Definition of Done

A development task is not considered complete merely because implementation exists.

The relevant requirements must be satisfied and appropriate evidence must exist.

For autonomous task completion, authoritative `DONE` is controlled by the Completion Engine according to:

`.supersystem/TASK_SCHEMA.md`

`.supersystem/VERIFICATION.md`

`.supersystem/POLICY.md`

and `.supersystem/POLICY_RULES.md`.

---

## Priority

When deciding how to implement something, use this priority:

1. `PROJECT_CONTRACT.md`
2. `ARCHITECTURE.md`
3. `INTERFACES.md`
4. security and policy documents
5. task requirements
6. implementation convenience

Never sacrifice a higher-level security or architectural requirement for implementation convenience.

---

## Final Rule

When uncertain about a security boundary, authorization, lifecycle transition, protected target, verification requirement, or architectural contract:

Do not guess.

Inspect the canonical source, identify the ambiguity, and fail closed when required.

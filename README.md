# SUPER-AGENT-SYSTEM (SAS)

A governed autonomous-agent platform. You give it a high-level objective;
SAS plans, authorizes, executes, observes, verifies, repairs, recovers, and
declares **DONE only with evidence**.

SAS is model-, runtime-, and platform-independent. The Core is pure Python
3.14 **standard library only** (zero third-party dependencies) and runs
inside Android Termux on a phone.

---

## Why SAS exists

Autonomous agents fail in characteristic ways: they execute actions the
user never authorized, expand their own scope, trust their own success
claims, and treat "the tool returned success" as proof the task is done.
SAS is built around one rule:

> **The model provides intelligence. The Core provides control.**

Every authority boundary is enforced by deterministic code, never by the
model's judgment:

| Concern | Who decides |
|---|---|
| Whether an action may run | **Policy Engine** (versioned operation registry + permission matrix) — ALLOW / ASK / DENY before execution |
| Whether an action ran, and what happened | **Action Journal** — append-only, hash-chained, audit-before-execution |
| Whether a criterion is actually satisfied | **Verification Engine** — evidence collected through Policy, assessed by code assessors, never by model claims |
| Whether the task is DONE | **Completion Engine** — the only component that can authorize DONE |
| What a subagent may do | **SubagentManager** — delegation is creation with enforced scope narrowing |
| What happens after a crash | **RecoveryManager** — safe-resume protocol; unknown side effects are never blindly retried |

The model only **proposes** actions. A malicious, buggy, or replaced model
cannot bypass Policy, expand scope, touch protected targets, declare DONE,
or bypass verification — these properties are enforced by tests
(`tests/adversarial/`, `tests/security/`).

## Architecture

```
User objective
   ↓
TaskManager (durable state, lifecycle authority)
   ↓
Orchestrator (the loop: propose → execute → observe → verify → complete)
   ↓
ModelPort ──proposal──▶ ExecutionPipeline ──▶ Policy Engine (ALLOW/ASK/DENY)
   (any model)               │                    │
                             │                Action Journal (hash-chained)
                             ▼
                        Tool execution (adapters: Termux filesystem,
                        Termux:API, Android pm/settings, ...)
                             │
   Verification Engine ◀─────┘ (fresh evidence through Policy)
        │
   Completion Engine ──▶ DONE (journaled, enveloped, integrity-validated)
```

Lifecycle: `CREATED → VALIDATING → PLANNING → READY → RUNNING → OBSERVING
→ VERIFYING → DONE` with `REPAIRING`, `RECOVERING`, `WAITING_USER`,
`BLOCKED`, `FAILED`, `CANCELLED` branches. Every transition is validated
and durably persisted; DONE is reachable **only** through the Completion
Engine path.

Source layout:

```
src/core/          contracts, enums, validation, versions
src/continuity/    TaskStore (integrity envelopes), Journal, ContinuityManager, RecoveryManager
src/task/          TaskManager
src/policy/        registry, canonicalizer, PolicyEngine
src/execution/     ExecutionPipeline, approvals, resources
src/verification/  VerificationEngine, assessors, independence, result store
src/completion/    CompletionEngine, compliance, decision store
src/orchestration/ Orchestrator
src/models/        ModelPort, ModelRouter, ModelPortDriver, ScriptedModelPort
src/runtimes/      RuntimePort
src/platforms/     Termux adapters (filesystem, environment, Android)
src/delegation/    SubagentManager
src/observability/ TaskObserver
```

## Capabilities

Supported today (all through Policy, all audited):

- **Filesystem** (real, on-device): read / list / stat / write, scoped by
  the task's TargetAuthorizationContext and the protected-path registry
- **Termux:API**: battery status, Wi-Fi status, device info (live on-device
  tests; requires the `termux-api` package)
- **Android**: `package.list`, `package.inspect` via `pm`, `settings.read`
  via `settings`
- **Verification**: any policy-governed observation as evidence; code
  assessors; independence levels 0–2 (self / runtime / separate verifier)
- **Continuity**: integrity-enveloped task state, hash-chained journals,
  checkpoints, continuity briefs, crash recovery, compaction prep
- **Delegation**: subtasks with enforced narrowing
- **Observability**: complete task-history reconstruction from durable
  records (secret-free by construction — journals store argument hashes)

Intentionally **unavailable in v1** (policy denies them until governance
rows are added): process execution, shell access, notification sending,
settings writes, package install/uninstall in AUTO mode (install is
ASK-gated), file deletion, privileged ADB/Shizuku/root (deferred by design).

## Requirements

- Python **3.14** (any platform; developed and tested on Termux aarch64)
- No third-party packages
- Optional on Android: Termux + `termux-api` (for the API tools)

## Installation (Termux / Android)

On the phone, in Termux:

```sh
pkg install python      # Python 3.14 in current Termux
pkg install termux-api  # optional: battery/wifi/device-info tools
# place the repository (e.g. via git clone from your host or USB)
cd ~/SUPER-AGENT-SYSTEM
```

No `pip install` is needed.

## Running

```sh
# full CI gate (test suite + Core purity check)
./ci.sh

# complete governed demo on the device (real filesystem writes + reads → DONE)
PYTHONPATH=src python3 src/platforms/termux/e2e_demo.py
```

The demo creates a temp workspace, runs a scripted model through the full
pipeline (policy → real write → observation → verification → completion),
prints the final state and journal summary, and cleans up.

### Driving the phone (governed, real device)

The agent can observe and control the phone through the GenieX app's
AccessibilityService, over a closed set of named operations. Nothing runs
unless Policy authorized it, and the app holds no authority of its own.

```sh
# once: enable the accessibility service in the app, tap "copy token"
python3 -m platforms.termux.cli setup-bridge   # clipboard → 0600 file
python3 -m platforms.termux.cli probe          # what does the device offer?

# declare the goal, the criteria, the scope and the limits - the operator
# does this, and the model reaches none of it
PYTHONPATH=src python3 -m platforms.termux.cli run \
    "open Settings and capture the screen" \
    --allow-package com.android.settings \
    --allow-package-op android.launch_package \
    --criterion ui-foreground-package-is:package=com.android.settings \
    --criterion screenshot-captured:advisory \
    --max-iterations 40 --deadline 300

# a task waiting on a human decision, and the resume that carries it on
python3 -m platforms.termux.cli tasks
python3 -m platforms.termux.cli approve <reference>
python3 -m platforms.termux.cli resume <task-id> --by hamid
```

The run ends in DONE only when the Completion Engine says so; a run stopped
by a limit ends BLOCKED with a durable reason. See
`docs/implementation/ANDROID_CAPABILITIES.md` for the operation table, the
token provisioning, and the verification methods.

## Testing

```sh
# everything (all categories: unit, security, adversarial, integration,
# verification, recovery, replaceability)
PYTHONPATH=src python3 -m unittest discover -s tests -t .

# one category
PYTHONPATH=src python3 -m unittest discover -s tests/security -t .
```

Test doubles live in `tests/support/` (FakeModel, MaliciousModel,
ScriptedModelPort, FakeRuntimeAdapter, FakeVerifier, ...). Security
invariant IDs T-INV-01..38 are enumerated in
`docs/implementation/TEST_INVARIANTS.md`.

## Configuration

SAS itself needs no configuration to run the governed Core. When real
model providers are connected (see `docs/implementation/PROVIDERS.md`),
credentials come from environment variables only — never from files in
the repository:

| Variable | Used by |
|---|---|
| `DEEPSEEK_API_KEY` | DeepSeek ModelPort adapter (future) |
| `ANTHROPIC_API_KEY` | Claude ModelPort adapter (future) |
| `GEMINI_API_KEY` | Gemini ModelPort adapter (future) |

These names are placeholders for the Phase-11 provider adapters; no
adapter ships with SAS yet. Nothing in the repository contains secrets.

## Security model

- **Model proposes, never authorizes.** Policy decides from a versioned
  registry; requested risk levels from the model are advisory.
- **Fail closed everywhere.** Unknown matrix rows DENY; unknown
  idempotency is treated conservatively; INCONCLUSIVE never produces DONE.
- **Protected targets.** Semantic protected-path classes (P0-P8) require a
  concrete runtime mapping; without one, filesystem mutation is denied.
- **Audit-before-execution.** A policy decision is journaled before an
  action executes; if STARTED cannot be persisted, the action must not run.
- **Integrity.** Task state and results sit behind SHA-256 envelopes;
  journals are hash-chained and verified on read *and* append.
- **Recovery.** Interrupted actions with unknown side effects are never
  blindly retried; RESUME re-runs the full execution gate.

## Documentation

- `ARCHITECTURE.md` / `INTERFACES.md` — system design and interfaces
- `.supersystem/` — the frozen governance specifications (PROJECT_CONTRACT,
  POLICY, POLICY_RULES, SECURITY, THREAT_MODEL, VERIFICATION, CONTINUITY,
  TASK_SCHEMA, PROTECTED_PATHS), plus CHANGELOG.md and DECISIONS.md (ADRs)
- `ROADMAP.md` — phase status
- `docs/implementation/TEST_INVARIANTS.md` — security invariant IDs
- `docs/implementation/PROVIDERS.md` — connecting real model providers
- `docs/implementation/ANDROID_CAPABILITIES.md` — the phone capability
  channel: endpoints, token, operation table, verification methods
- `docs/implementation/GENIEX_BRIDGE.md` — the on-device inference bridge
- `docs/RUNBOOK.md` — **the operator's every-time procedure**: how to bring
  the bridge up, declare a task, read a verdict, handle approvals, and what
  to do when something looks wrong

## License

MIT — see [LICENSE](LICENSE). No third-party code is included; external
frameworks were studied for design only (see DECISIONS.md ADR-005, ADR-016).

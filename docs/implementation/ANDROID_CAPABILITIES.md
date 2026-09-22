# Android capabilities (Phase 14)

How SAS observes and controls the phone, and why each piece is shaped the way
it is. Read `DECISIONS.md` ADR-019 first: this document is the interface, the
ADR is the reasoning.

## Why the capability channel lives in the app

Probing the device settled it. From the Termux UID, `screencap` fails,
`dumpsys` is permission-denied, and `input` needs INJECT_EVENTS. Termux cannot
see the screen or touch it. An app with an `AccessibilityService` can do both,
and the GenieX app already hosts a loopback server and a foreground service,
so the channel was added there rather than inventing a second component.

The app is a **dumb executor**: it validates arguments, executes the named
operation, and returns structured JSON. It holds no policy, makes no
authorization decision, and cannot be asked to do anything SAS did not name.

## Endpoints

All on the existing loopback-only server, `http://127.0.0.1:8765`:

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /v1/health` | none | liveness; resident and loaded model ids |
| `POST /v1/chat/completions` | none | inference (OpenAI-compatible, multimodal) |
| `GET /v1/android/state` | Bearer | which capabilities are currently available |
| `POST /v1/android/execute` | Bearer | run one named operation |

The inference endpoints stay open so their device-verified path is unchanged;
only the two capability endpoints require the token. The socket is pinned to
127.0.0.1, so no other host can reach any of them.

Request and response shape for `/v1/android/execute`:

```json
{ "operation": "android.observe_ui", "arguments": {} }
```

```json
{ "ok": true, "operation": "android.observe_ui", "result": { "foregroundPackage": "com.android.settings", "...": "..." } }
```

Failures use the same structured error shape as the inference endpoints:

```json
{ "error": { "code": "UNAUTHORIZED", "type": "capability_error", "message": "missing or invalid bearer token" } }
```

## The token

Loopback pinning keeps other *hosts* out; it does not keep other local *apps*
out. The capability endpoints therefore require `Authorization: Bearer
<token>`.

- The app generates the token once, on first use: 32 random bytes, hex.
- `BridgeToken.copyToClipboard()` puts it on the clipboard. This is the whole
  provisioning UX - nothing is typed, and nothing is transcribed.
- In Termux, `python3 -m platforms.termux.cli setup-bridge` reads the
  clipboard and writes it to `<state>/bridge.token` with mode 0600.
- SAS prints only the byte count and a four-character prefix. The token is a
  bearer credential for device control: it is never logged, never journaled,
  never placed in a `ToolResult`, and never stored in source. A test asserts
  each of those (`tests/security/test_capability_security.py::TokenHygieneTests`).
- The app's `capabilityEnabled` switch (default on) gates the capability
  endpoints independently of inference.

## The closed operation set

The operation name is the only thing that selects behaviour. There is no
shell operation, no arbitrary intent, no arbitrary component, and no way to
name one - an unknown operation is refused with `OPERATION_NOT_SUPPORTED`.

| Operation | Kind | Risk / side effect | Policy target | Notes |
|---|---|---|---|---|
| `android.screenshot` | observe | LOW / READ_ONLY | - | PNG; stored as a content-addressed artifact |
| `android.observe_ui` | observe | LOW / READ_ONLY | - | foreground + previous package, display metrics, bounded node list |
| `android.launch_package` | act | MEDIUM / MUTATING | PACKAGE | requires `allowedPackages` and the package-operation gate; success is reported only once the package is actually in the foreground |
| `android.open_url` | act | MEDIUM / EXTERNAL_EFFECT | NETWORK_DOMAIN | `forcedDecision=ASK`; scope is checked *before* the ASK |
| `android.global_action` | act | MEDIUM / MUTATING | - | `BACK` or `HOME` |
| `accessibility.tap` | act | MEDIUM / EXTERNAL_EFFECT | UI | acts on the node named `<package>#<visible text>` |
| `accessibility.type_text` | act | MEDIUM / EXTERNAL_EFFECT | UI | types into the node named `<package>#<visible text>` |

### Naming a node

A snapshot id and a node ref are minted by the device and mean something only
inside the observation that produced them, so neither is knowable to a caller
writing tool arguments. The caller names the node the way a person would —
`<package>#<visible text>` — and the adapter resolves it
(`TermuxAndroidCapabilityAdapter._resolve_node`):

1. It takes a fresh observation; only enabled nodes in the named package are
   considered.
2. It prefers an exact label match (case-folded), then a substring match.
3. If no matching node is itself clickable — the usual case, since a row's
   label lives in a child view — it uses the smallest clickable container
   holding the label: the thing a person tapping the row would actually hit.
4. It hands the device both the ref and the snapshot it came from.

Resolution observes before it acts, so the ref cannot be stale. It grants
nothing: the observation is read-only, and Policy has already evaluated the
operation on the target the caller named. A node that is not on screen is a
`KNOWN_FAILED` (nothing happened), not a retry — the message lists the labels
that *are* on screen, bounded, so the next proposal can name one.

An explicit `(observationId, nodeRef, target)` triple is still passed through
untouched for callers that hold a retained observation.

`accessibility.submit` exists as a registry row but has no implementation, and
an unimplemented operation is refused by the pipeline before a human is asked
(`src/execution/pipeline.py`).

Every operation declares the `ANDROID_FOREGROUND` resource, so the resource
coordinator serializes device access.

## How failures are classified

The distinction that matters is *did anything happen*:

- `_NO_EFFECT_CODES` - the device refused before acting (`UNAUTHORIZED`,
  `CAPABILITY_DISABLED`, `OPERATION_NOT_SUPPORTED`, `BAD_REQUEST`,
  `INVALID_ARGUMENT`, `PACKAGE_NOT_LAUNCHABLE`, `UI_UNAVAILABLE`,
  `ACTION_FAILED`, `STALE_OBSERVATION`, `CAPABILITY_UNAVAILABLE`). The result
  is `KNOWN_FAILED`.
- Anything else on an action - including a transport timeout - is treated as a
  possibly-applied side effect (`UNKNOWN`). An unknown side effect is never
  blind-retried; it goes to verification-first recovery.
- `_RETRYABLE_CODES` covers read-only observations only. A bounded retry of a
  failed observation is safe precisely because nothing changed.

The app confirms effects before claiming them, so `ACTION_FAILED` is a real
answer: `android.launch_package` waits for the accessibility service to see
the named package in front and reports `ACTION_FAILED` when it does not
arrive. That was added after a run in which the app returned `performed:
true` for a launch the system had silently refused (background activity
launch restriction, or the package being killed as it started) - the loop
then proved the foreground package had not changed, re-proposed the same
launch, and stalled into `BLOCKED`. A capability that reports a state it did
not reach is worse than one that fails: the model cannot reason its way out
of a lie.

## Verification methods

`src/verification/android_methods.py::default_methods()` provides the
production assessors. They are plain code, never models: they read evidence
and return PASS / FAIL / INCONCLUSIVE. Expected values come from the
criterion's `evidenceRequirements`, in `key=value` form.

| Method | Evidence collected through | Requirements |
|---|---|---|
| `file-content-equals` | `fs.read_file` | `path=`, `expected=` |
| `ui-foreground-package-is` | `android.observe_ui` | `package=` |
| `ui-node-text-present` | `android.observe_ui` | `text=` |
| `foreground-package-changed` | `android.observe_ui` | `previousPackage=` |
| `screenshot-captured` | `android.screenshot` | - |

Two consequences worth knowing when writing criteria:

- Verification collects its own evidence through the pipeline, so a
  `screenshot-captured` criterion makes the device take a screenshot even if
  the model proposed nothing. Read-only operations are ALLOW in AUTO, so this
  is expected - but it means "the model did nothing" and "nothing ran" are not
  the same statement.
- A criterion that cannot be assessed is INCONCLUSIVE, and INCONCLUSIVE never
  produces DONE. An operator who names a file the run never writes gets a task
  that stays unfinished, not a task that passes.

## Operator CLI

`python3 -m platforms.termux.cli <command>`, run in Termux:

| Command | Purpose |
|---|---|
| `run "<goal>" --criterion ...` | declare a task, its scope and its limits, and drive it |
| `status [task]` / `tasks` | what happened, and what is waiting |
| `approve` / `deny <ref>` | decide a pending approval |
| `resume <task> --by <who>` | the authorized transition out of WAITING_USER |
| `probe` | report which capabilities the device currently offers |
| `setup-bridge` | read the token from the clipboard into `<state>/bridge.token` |

The operator declares the success criteria, the authorized scope, and every
limit. The model reaches none of it: `run` builds the
`TargetAuthorizationContext` and the criteria from the command line, and
nothing in the model path can widen, replace, or read them as instructions.

Scope flags: `--allow-read`, `--allow-write`, `--allow-package`,
`--allow-package-op`, `--allow-domain`, `--allow-ui`, `--deny`. A scope
pattern must be written the way Policy canonicalizes it - on Windows,
`C:/x/y` canonicalizes to `/C:/x/y` - so a pattern that "looks right" can
still authorize nothing. `--workspace` is scoped automatically.

A package operation needs **both** grants: the package in `--allow-package`
(the target scope) and the operation id in `--allow-package-op`. They are
independent, and the denial names the one that is missing
(`operation not listed in allowedPackageOperations`). Launching Settings is
therefore `--allow-package com.android.settings --allow-package-op
android.launch_package`.

**Name the package in the goal when the task has to act on one.** The model
cannot discover a package id it has never observed, so "open the calculator"
makes it guess (`com.android.calculator3`, the AOSP name) and Policy denies
the guess as out of scope - correctly, but the run then stalls. Write the goal
as the operator's own scope: "open the app whose package id is
`com.coloros.calculator`". Naming it in the goal authorizes nothing; the flags
still decide, and a goal naming an unauthorized package is denied exactly the
same way.

## Running against the device

```sh
# once, in the GenieX app: enable the accessibility service, copy the token
python3 -m platforms.termux.cli setup-bridge
python3 -m platforms.termux.cli probe
```

`probe` is the honest check: it reports what the device actually offers right
now, rather than assuming the app is running and the service is enabled.

Resuming needs no `--state` flag; `resume` reads `SAS_STATE_DIR` (or `~/.sas`),
so the environment must still point at the same state directory the run used.
A resume this way re-checks `len(task.failures)` against the *current*
`--failure-limit`, over the failures the task accumulated across its whole
life, including before an outage it is being resumed from. A task that hit
the default cap must be resumed with a higher one (`--failure-limit 30`) or it
blocks again on the first turn.

### Device behaviours that look like bugs

- **The app can be killed for memory, mid-task.** ColorOS's
  `OsenseKillAction` terminates the app under memory pressure even when it
  holds a foreground service, and the log records the reason
  (`ApplicationExitInfo … reason=LOW_MEMORY … rss=3.0GB`). The governed loop
  handles this correctly - the in-flight operation becomes `KNOWN_FAILED`
  (never `UNKNOWN`), it is not blindly retried, and the task ends `BLOCKED`
  rather than falsely `DONE` - but the run is over. Check
  `MemAvailable` in `/proc/meminfo` before a long task.
- **After the app is reinstalled, the accessibility service does not rebind
  on its own.** Writing the same `enabled_accessibility_services` value back
  is a no-op. The list must be written *without* the service and then *with*
  it. Preserve the other entries when doing this:
  `settings get secure enabled_accessibility_services` first, and edit that
  list rather than replacing it.
- **`am force-stop` unbinds the accessibility service**, and so does any
  other kill. The setting survives; the binding does not. Re-toggling as
  above restores it.
- **`canTakeScreenshot` is false until a screenshot has actually been
  taken** in this process. It reports `SasAccessibilityService.isScreenshotProven()`,
  not the service's configuration, so a freshly started app correctly
  reports `available: true, canTakeScreenshot: false`.

### What the acting model does with a multi-step goal

The acting model here is Qwen3-4B-Instruct on the NPU. It proposes one step
of a multi-step goal well and repeats it badly: over a 3-step goal the whole
proposal history was `android.screenshot`, `android.launch_package` ×3, with
`fs.write_file` never proposed, and a hand-built prompt carrying the same
objective and the same observations returned the byte-identical
`android.screenshot` proposal three turns running (1036 prompt tokens, 25
completion tokens, matching the journal exactly). Asking for the write alone
*does* produce `fs.write_file`, but with a hallucinated path
(`C:/Users/ham.png` for an objective naming a long Windows path).

This is a model-capability limit, not a plumbing defect: the tool is
advertised, the parser accepts it, and Policy ALLOWs the write when it
arrives. The system's answer to it is the designed one - the repeated
proposal is caught by the no-progress rule (ADR-020) and the run ends
`BLOCKED`, never `DONE`. Operators should write short goals, name the
package and the file path in the objective, and treat DONE on a long goal
as something to be re-tried rather than assumed.

## What is deliberately absent

No shell, no root, no adb, no shizuku, no package install or uninstall, no
settings writes, and no arbitrary intent. These are not unimplemented - they
are out of scope, and the reviewable surface is the operation table above:
if an operation is not in it, nothing can run it.

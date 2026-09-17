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
| `android.launch_package` | act | MEDIUM / MUTATING | PACKAGE | requires `allowedPackages` and the package-operation gate |
| `android.open_url` | act | MEDIUM / EXTERNAL_EFFECT | NETWORK_DOMAIN | `forcedDecision=ASK`; scope is checked *before* the ASK |
| `android.global_action` | act | MEDIUM / MUTATING | - | `BACK` or `HOME` |
| `accessibility.tap` | act | MEDIUM / EXTERNAL_EFFECT | UI | acts on a node reference from a retained observation |
| `accessibility.type_text` | act | MEDIUM / EXTERNAL_EFFECT | UI | types into the focused editable node |

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

## Running against the device

```sh
# once, in the GenieX app: enable the accessibility service, copy the token
python3 -m platforms.termux.cli setup-bridge
python3 -m platforms.termux.cli probe
```

`probe` is the honest check: it reports what the device actually offers right
now, rather than assuming the app is running and the service is enabled.

## What is deliberately absent

No shell, no root, no adb, no shizuku, no package install or uninstall, no
settings writes, and no arbitrary intent. These are not unimplemented - they
are out of scope, and the reviewable surface is the operation table above:
if an operation is not in it, nothing can run it.

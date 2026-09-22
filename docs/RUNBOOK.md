# Operator runbook

The every-time procedure for running a governed task against the phone.
`docs/implementation/ANDROID_CAPABILITIES.md` explains *why* the pieces are
shaped this way; this document is just how to drive it.

## What has to be true

| | Why |
|---|---|
| The GenieX app is running | It hosts the bridge on `127.0.0.1:8765` and the accessibility service. There is **no boot receiver**: after a reboot, open the app once. |
| The accessibility service is enabled | It is the only thing that can see the screen or touch it. The app's foreground service alone cannot. |
| The bridge is reachable | On the phone that is `127.0.0.1:8765`. From a PC it is `adb forward tcp:8765 tcp:8765`. |
| `<state>/bridge.token` exists | The capability endpoints need it. `setup-bridge` reads it from the phone's clipboard. Inference endpoints stay open. |

## Two ways to run it

### A. On the phone, in Termux (the resident way)

```sh
cd ~/SUPER-AGENT-SYSTEM
export PYTHONPATH=src                    # the package lives under src/
export SAS_STATE_DIR="$HOME/.sas"        # or omit: defaults to ~/.sas
```

Nothing else needs installing — no `pip`, no third-party packages.

### B. From the PC, over USB

Useful when you would rather work at the keyboard. The phone does all the
real work; the PC is only the console.

```sh
ADB="C:/Users/hamid/AppData/Local/Android/Sdk/platform-tools/adb.exe"
"$ADB" devices                     # the phone must say "device", not "unauthorized"
"$ADB" forward tcp:8765 tcp:8765   # repeat after every replug or adb restart

cd C:/Users/hamid/.claude/scratch/sas_work2
export PYTHONPATH=src
export SAS_STATE_DIR="C:/Users/hamid/.claude/scratch/sas_device_state"
```

`setup-bridge` needs `termux-clipboard-get`, so it only works in Termux. From
the PC the token file has already been provisioned; if it is ever missing, run
`setup-bridge` in Termux once.

## Every time

```sh
# 1. is the device actually offering what I think it is?
python3 -m platforms.termux.cli probe
```

Read the answer rather than assuming it. A healthy probe:

```
token     : loaded
bridge    : ok, models loaded: qwen3-4b-instruct-2507
available : True
accessib. : True
screenshot: True
version   : 1.0
reason    : ready
  - accessibility.tap
  - android.launch_package
  - android.screenshot
  ...
```

`screenshot: False` on a freshly started app is normal — it means no capture
has succeeded *in this process yet*, and it becomes true the first time one
does. `accessib.: False` is not normal; see Troubleshooting.

```sh
# 2. declare the goal, its criteria, its scope and its limits
PYTHONPATH=src python3 -m platforms.termux.cli run \
    "Take a screenshot of the current screen, then write the word DONE to the file <state>/workspace/accept.txt" \
    --criterion "file-content-equals:path=<state>/workspace/accept.txt:expected=DONE:mandatory" \
    --criterion "screenshot-captured:advisory" \
    --max-iterations 30 --deadline 600 --failure-limit 5
```

Every one of those decisions is the operator's. The model reaches none of
them: it cannot widen the scope, change a criterion, or raise a limit.

```sh
# 3. what happened?
python3 -m platforms.termux.cli status            # the last task
python3 -m platforms.termux.cli tasks             # all of them
```

Status ends with a verdict. `DONE` means the Completion Engine decided it,
after code assessors read real evidence. `BLOCKED` means the run stopped on
one of your limits — bounded, with a durable reason, and never a false DONE.

If you kill a run with Ctrl-C, the task stays `RUNNING` in the store: the
process is gone but nothing has reconciled the state. `resume <task-id>` is
the way back — it moves the task through RECOVERING and re-checks it against
the limits you pass. Do not read a `RUNNING` task as one that is still going.

## Limits: defaults and what they do

| Flag | Default | Stops the run when |
|---|---|---|
| `--max-iterations` | 30 | the loop has taken this many turns |
| `--deadline` | 900 s | this much wall-clock time has passed |
| `--failure-limit` | 5 | the task has this many failures **over its whole life** |
| `--token-budget` | none | the run has spent this many model tokens |
| `--no-vision` | off | (not a stop) skips describing screenshots with the VLM |

The no-progress rule has no flag: three identical proposals in a row end the
run. That is the rule that catches a model repeating itself, and it is the
one you will meet most often.

## Scope: what the task may touch

| Flag | Grants |
|---|---|
| `--allow-read` / `--allow-write` | filesystem globs |
| `--allow-package` | a package the task may act on |
| `--allow-package-op` | the operation it may perform on it |
| `--allow-domain` | a host for `android.open_url` |
| `--allow-ui` | a UI node scope, e.g. `com.android.settings#*` |
| `--deny` | an explicit exclusion |

`--workspace` (default `<state>/workspace`) is scoped for you.

**A package operation needs both grants.** Launching Settings is:

```sh
--allow-package com.android.settings --allow-package-op android.launch_package
```

Give one and not the other and the denial names the one that is missing.

## Criteria

`--criterion METHOD:key=value[:key=value...][:mandatory|advisory]`, repeatable,
at least one required. A criterion is mandatory unless marked advisory, and
**only mandatory criteria can block DONE**.

| Method | Needs | Passes when |
|---|---|---|
| `file-content-equals` | `path=`, `expected=` | the file exists with exactly that content |
| `ui-foreground-package-is` | `package=` | that package is in front |
| `ui-node-text-present` | `text=` | that text is on screen |
| `foreground-package-changed` | `previousPackage=` | the foreground moved away from it |
| `screenshot-captured` | — | a screenshot artifact was produced |

Verification collects its own evidence through the pipeline, so a criterion
can make the device do something even if the model proposed nothing. A
criterion that cannot be assessed is `INCONCLUSIVE`, and INCONCLUSIVE never
produces DONE.

## Writing a goal the model can actually finish

This is the part that decides whether a run succeeds. The acting model is
Qwen3-4B on the NPU, and it handles one step well and multi-step goals badly:
measured over a 3-step goal it re-proposed step one every turn, byte-identical,
until the no-progress rule stopped it.

- **Keep it to one or two steps.** Compose longer jobs from several `run`s.
- **Name the package id.** The model cannot discover one it has never seen;
  "open the calculator" makes it guess `com.android.calculator3` and Policy
  correctly denies the guess. Write "open the app whose package id is
  `com.coloros.calculator`".
- **Name the file path in full**, in the goal *and* in the criterion. The
  model copies paths unreliably, and a wrong path is out of scope.
- **Naming these in the goal authorizes nothing** — the flags still decide.

## Approvals: the run stopping for you

Any operation Policy classifies ASK pauses the task in `WAITING_USER` with a
durable pending request. Nothing runs while it waits.

```sh
python3 -m platforms.termux.cli approve              # list what is pending
python3 -m platforms.termux.cli approve <reference> --by hamid
python3 -m platforms.termux.cli deny <reference> --by hamid --reason "wrong row"
python3 -m platforms.termux.cli resume <task-id> --by hamid
```

Run with no reference, `approve` and `deny` print the pending list and exit
non-zero — they are asking which one you mean, not reporting a failure.

A grant is bound to the task, the operation, the canonical target and a hash
of the arguments, and is single-use — it approves *that* action, not that kind
of action.

**Resuming re-checks the failure count against the limit you pass now**, over
failures the task accumulated across its whole life. A task that hit the
default cap needs a higher one or it blocks again immediately:

```sh
python3 -m platforms.termux.cli resume <task-id> --by hamid --failure-limit 30
```

## Troubleshooting

| What you see | What it is | What to do |
|---|---|---|
| `bridge: unreachable (CONNECTION)` | the app is not running, or was killed | open the GenieX app. After a reboot this is always the cause. |
| `device: ANDROID_UNAUTHORIZED` | the token does not match | copy the token in the app, then `setup-bridge` in Termux |
| `accessib.: False` | the service is not bound | enable it in the app. After a reinstall, write `enabled_accessibility_services` *without* the service and then *with* it — writing the same value back is a no-op. Preserve KeyMapper and AnyDesk when you edit that list. |
| `canTakeScreenshot: False` | no capture has succeeded in this process | not a fault; it turns true after the first successful screenshot |
| the foreground never changes | ColorOS killed the app under memory pressure | reopen the app. Check `MemAvailable` in `/proc/meminfo` before a long run. |
| `verdict: BLOCKED`, reason "no progress" | the model repeated itself | reword the goal — shorter, one step, package id and path named |
| `verdict: BLOCKED` at the failure cap | too many failures across the task's life | `resume <task> --failure-limit 30` |
| every model call fails at once | the bridge context overflowed (old builds) | rebuild/reinstall the app; current builds reset the context per request |
| a run is much slower than usual | the model is swapping | only one model fits in memory; an LLM↔VLM switch costs about 45 s |

## What the system will never do

No shell, no root, no adb, no package install or uninstall, no settings
writes, no arbitrary intent. The operation set is closed and refused on both
ends of the channel — if an operation is not in the probe's list, nothing can
run it. The app holds no policy of its own: by the time a request reaches it,
Policy has already authorized it.

## Updating the copy on the phone

The repository on GitHub is the source of truth. In Termux:

```sh
cd ~/SUPER-AGENT-SYSTEM
git fetch https://github.com/hamidaliyev091/SUPER-AGENT-SYSTEM.git main
git log --oneline -1 FETCH_HEAD        # look before you leap
git merge --ff-only FETCH_HEAD         # refuses if the histories diverged
```

`--ff-only` is deliberate: if it refuses, the phone's copy has commits the
remote does not, and that is a thing to look at rather than paper over.

To check whether the phone's copy is current:
`git rev-parse HEAD` should match the commit hash in the release notes.

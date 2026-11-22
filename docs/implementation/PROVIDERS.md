# Connecting real model providers

SAS's model boundary is `ModelPort` (INTERFACES.md s14):

```python
class ModelPort:
    def generate(self, request: ModelRequest) -> ModelResponse: ...
```

The Core never knows which provider serves the request. To connect a real
model, implement one class per provider and register it with the
orchestrator's `ModelPortDriver` (optionally through `ModelRouter` for
per-role dispatch). A reference implementation (`ScriptedModelPort`) and
the full integration tests live in `src/models/` and
`tests/integration/test_model_port_flows.py`.

## Provider adapters (Phase 11 — implemented for DeepSeek & Gemini)

Configured adapters under `src/models/`:

| Adapter | Provider / Endpoint | Environment variables | Status |
|---|---|---|---|
| `DeepSeekModelPort` | DeepSeek OpenAI-compatible (`/chat/completions`) | `DEEPSEEK_API_KEY` *(required)*, `DEEPSEEK_MODEL` (`deepseek-chat`), `DEEPSEEK_BASE_URL`, `DEEPSEEK_TIMEOUT` | **Implemented** |
| `GeminiModelPort` | Google Gemini REST (`:generateContent`, `x-goog-api-key` header) | `GEMINI_API_KEY` *(required)*, `GEMINI_MODEL` (`gemini-2.5-flash`), `GEMINI_BASE_URL`, `GEMINI_TIMEOUT` | **Implemented** |
| `ClaudeModelPort` | Anthropic Messages API | `ANTHROPIC_API_KEY` | Optional / not yet implemented |

Rules for adapter implementations:

1. **Credentials come from environment variables only** (or explicit DI in tests). Never write keys into files inside the repository, never put keys in URL query strings, and never log keys (errors are redacted before storage on `port.last_error`).
2. **The adapter returns `ModelResponse` objects.** Missing credentials, HTTP errors, timeouts, or malformed payloads set `port.last_error` and return `ModelResponse(content="", toolCalls=[], finishReason="provider_error")` — a failing provider can never crash the governed loop or emit an unauthorized action.
3. **Selection grants no authority** (INTERFACES s15): `build_deepseek_router()` and `build_gemini_router()` wire roles identically; Policy, resource limits, and the Completion Engine still decide everything.
4. **Every `generate()` call is journaled as `MODEL_CALL`** by `ModelPortDriver` before the response is used, and normalized `usage["total_tokens"]` feeds `token_budget` enforcement uniformly across Gemini (`usageMetadata`), DeepSeek (`usage`), and GenieX.
5. **Stdlib-only HTTPS transport (`src/models/_http.py`):** cloud adapters use `urllib.request` with `https://` enforcement for remote hosts (loopback `http://` is allowed for local mocks) so ADR-001's zero-dependency invariant holds.

Live cloud smoke tests in `tests/integration/test_cloud_models_live.py` are skipped during offline CI (`./ci.sh`) and run only when explicitly enabled with `SAS_LIVE_MODEL_TESTS=1` alongside `GEMINI_API_KEY` and/or `DEEPSEEK_API_KEY`.

## Minimal adapter sketch

```python
from core import ModelRequest, ModelResponse
from models import ModelPort

class ExampleModelPort(ModelPort):
    def generate(self, request: ModelRequest) -> ModelResponse:
        # call the provider with request.messages / request.tools
        # translate the provider result into toolCalls / content
        return ModelResponse(content="...", toolCalls=[...],
                             usage={"inputTokens": n, "outputTokens": m},
                             finishReason="tool_calls")
```

## Wiring

```python
from models import ModelPortDriver, ModelRouter
from orchestration import Orchestrator

driver = ModelPortDriver(task_manager, store, ExampleModelPort())
# or per-role:
router = ModelRouter({ModelRole.PLANNER: planner_port,
                      ModelRole.GENERAL_AGENT: agent_port})
driver = ModelPortDriver(task_manager, store, router)

orchestrator = Orchestrator(task_manager, pipeline, verification,
                            completion, driver)
orchestrator.run(task_id)
```

The orchestrator drives everything else; nothing else changes when a real
provider replaces the scripted test port.

## GenieX local models (implemented — on-device)

The validated on-device path: the Android GenieX inference layer exposes
local NPU models through a loopback HTTP bridge, and SAS talks to it via
the GenieX adapters. See GENIEX_BRIDGE.md for the exact contract (the
GenieX app endpoint must implement those three endpoints; the reference
implementation lives in tests/support/geniex_server.py).

Configuration (environment variables, loopback only):

- GENIEX_BRIDGE_URL (default http://127.0.0.1:8765)
- GENIEX_BRIDGE_TIMEOUT (default 120 seconds)
- GENIEX_LLM_MODEL (default qwen3-4b-instruct-2507)
- GENIEX_VLM_MODEL (default qwen2.5-vl-7b-instruct)

Wiring:

```python
from models import build_geniex_router, ModelPortDriver
from platforms.termux.geniex_bridge import GenieXBridge

bridge = GenieXBridge()                 # env-configured, loopback-checked
router, vision = build_geniex_router(bridge)
driver = ModelPortDriver(task_manager, store, router)

# vision is a separate capability, never part of text routing:
description = vision.describe_image(screenshot_bytes, "describe this screen")
```

Security: the bridge is inference-only and loopback-only; its tool calls
are proposals that pass through Policy like any model output; its content
is never verification. Model weights live under ~/SAS-RUNTIME and are
never committed to the repository.

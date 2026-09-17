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

## Provider adapters (to be implemented — Phase 11)

Planned adapters and their configuration:

| Adapter | Provider | Environment variable |
|---|---|---|
| `DeepSeekModelPort` | api.deepseek.com (Anthropic-compatible) | `DEEPSEEK_API_KEY` |
| `ClaudeModelPort` | Anthropic Messages API | `ANTHROPIC_API_KEY` |
| `GeminiModelPort` | Google Gemini API | `GEMINI_API_KEY` |

Rules for adapter implementations:

1. **Credentials come from environment variables only.** Never write keys
   into files inside the repository.
2. **The adapter returns `ModelResponse` objects.** Anything else is
   malformed and the driver converts it to an empty/malformed response —
   a failing provider can never produce an action.
3. **Selection grants no authority** (INTERFACES s15): a stronger model
   does not get broader permissions, higher limits, or lower verification
   requirements. All of that is decided by Policy, limits, and the
   Completion Engine — none of it consults the adapter.
4. **Every `generate()` call is journaled as `MODEL_CALL`** by the
   `ModelPortDriver` before the response is used, so `modelCalls` limits
   are externally enforceable. Do not bypass the driver.
5. **HTTP clients are allowed inside adapters** (the stdlib-only rule
   applies to the Core and the governed platform; provider adapters may
   use `urllib.request` or vendor-specific SDKs). Prefer `urllib.request`
   to preserve the zero-dependency property.

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

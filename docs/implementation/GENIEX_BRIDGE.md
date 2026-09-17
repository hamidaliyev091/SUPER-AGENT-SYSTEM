# GenieX Bridge Contract (v1)

The Android GenieX inference layer exposes local NPU models to SAS through
a localhost HTTP interface. This document is the **authoritative contract**
the GenieX app endpoint must implement; the SAS-side client is
`src/platforms/termux/geniex_bridge.py` and the reference implementation is
`tests/support/geniex_server.py`.

## Security rules

1. **Loopback only.** The endpoint binds `127.0.0.1` (or `::1`). The SAS
   client refuses any non-loopback URL. No remote host may serve the bridge.
2. **The bridge is inference only.** It has no access to SAS authority and
   cannot execute system actions. Every tool call returned by the bridge
   becomes an ActionRequest proposal and passes through the SAS Policy
   pipeline like any model output. The bridge can never bypass Policy.
3. **Model output is never verification.** Tool calls and content from the
   bridge are model claims; SAS verification remains code-based.
4. **No secrets.** The bridge carries prompts and responses only. API keys,
   tokens, and credentials never appear in this protocol.

## Transport

- HTTP/1.1 over loopback, JSON bodies, `Content-Type: application/json`.
- Timeout: the SAS client defaults to 120 s per request (configurable via
  `GENIEX_BRIDGE_TIMEOUT`).
- Errors: HTTP status + JSON body `{"error": {"code": "...", "message": "..."}}`.

## Endpoints

### GET /v1/health

Response `200`:

```json
{"status": "ok", "models": ["qwen3-4b-instruct-2507", "qwen2.5-vl-7b-instruct"]}
```

### POST /v1/chat/completions  (LLM: reasoning / tool-calling / agent work)

Request:

```json
{
  "model": "qwen3-4b-instruct-2507",
  "messages": [{"role": "system", "content": "..."},
               {"role": "user", "content": "..."}],
  "maxTokens": 2048,
  "temperature": 0.0
}
```

Response `200`:

```json
{
  "content": "assistant text",
  "toolCalls": [{"id": "tc-1", "name": "fs.write_file",
                 "arguments": {"path": "/data/out/x.txt", "content": "..."}}],
  "usage": {"inputTokens": 12, "outputTokens": 40},
  "finishReason": "tool_calls"
}
```

- `content` is a string (may be empty when the turn is tool calls).
- `toolCalls` is an array (may be empty).
- `finishReason` is one of: `stop`, `tool_calls`, `length`.

### POST /v1/vision  (VLM: image / screenshot / UI understanding)

Request:

```json
{
  "model": "qwen2.5-vl-7b-instruct",
  "imageBase64": "<base64-encoded image>",
  "prompt": "describe this screen"
}
```

Response `200`:

```json
{
  "content": "a settings screen with a toggle...",
  "usage": {"inputTokens": 1500, "outputTokens": 120}
}
```

## Model identifiers

| Identifier | Role | Capability |
|---|---|---|
| `qwen3-4b-instruct-2507` | main LLM (agent, tool-calling, reasoning) | text |
| `qwen2.5-vl-7b-instruct` | vision / UI understanding | image+text |

SAS selects them per role through `ModelRouter` (text) and the separate
vision capability (`GenieXVisionModelPort.describe_image`). Replaceable:
any future local or cloud model implements the same two endpoints.

## Configuration (SAS side)

| Environment variable | Default | Meaning |
|---|---|---|
| `GENIEX_BRIDGE_URL` | `http://127.0.0.1:8765` | bridge base URL (loopback only) |
| `GENIEX_BRIDGE_TIMEOUT` | `120` | per-request timeout in seconds |
| `GENIEX_LLM_MODEL` | `qwen3-4b-instruct-2507` | model id for chat completions |
| `GENIEX_VLM_MODEL` | `qwen2.5-vl-7b-instruct` | model id for vision |

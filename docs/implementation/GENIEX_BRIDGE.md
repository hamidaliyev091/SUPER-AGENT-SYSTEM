# GenieX Bridge Contract (v2 — OpenAI-compatible)

The Android GenieX application exposes local NPU inference to SAS through a
loopback HTTP server. The interface is the **standard OpenAI-compatible
chat completions contract** (multimodal included), versioned under `/v1`.

This document is the authoritative contract the Android bridge implements
and the SAS client (`src/platforms/termux/geniex_bridge.py`) consumes.
The reference implementation for tests is `tests/support/geniex_server.py`;
it is NOT part of the production path.

## Security rules

1. **Loopback only.** The server binds `127.0.0.1` only. The SAS client
   refuses any non-loopback URL. No remote host may serve the bridge.
2. **Inference only.** The bridge performs model inference and returns
   model output. It NEVER executes tools, runs commands, or touches SAS
   authority. Every tool call in a response is a proposal that passes
   through the SAS Policy pipeline like any model output.
3. **Model output is never verification.** SAS verification remains
   code-based.
4. **No secrets.** The protocol carries prompts and responses only.

## Transport

- HTTP/1.1 over loopback; JSON; `Content-Type: application/json`.
- Request timeout: the SAS client defaults to 120 s (`GENIEX_BRIDGE_TIMEOUT`);
  the Android bridge enforces its own inference timeout.
- Malformed JSON → `400` with a structured error body.
- All errors: `{"error": {"code": "...", "message": "...", "type": "..."}}`.

## Endpoints

### GET /v1/health

`200`:

```json
{"status": "ok", "models": ["qwen3-4b-instruct-2507", "qwen2.5-vl-7b-instruct"]}
```

### POST /v1/chat/completions (LLM and VLM — OpenAI-compatible)

Text request:

```json
{
  "model": "qwen3-4b-instruct-2507",
  "messages": [
    {"role": "system", "content": "You propose actions for a governed agent."},
    {"role": "user", "content": "write a file containing HELLO"}
  ],
  "max_tokens": 2048,
  "temperature": 0.0
}
```

Multimodal (VLM) request — image content parts use the standard
`image_url` form with a data URL:

```json
{
  "model": "qwen2.5-vl-7b-instruct",
  "messages": [
    {"role": "user",
     "content": [
       {"type": "text", "text": "describe this screen"},
       {"type": "image_url",
        "image_url": {"url": "data:image/png;base64,iVBOR..."}}
     ]}
  ],
  "max_tokens": 512,
  "temperature": 0.0
}
```

OpenAI-compatible response (both cases):

```json
{
  "id": "chatcmpl-...",
  "object": "chat.completion",
  "created": 1758000000,
  "model": "qwen3-4b-instruct-2507",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "assistant text (may be empty when tool calls)",
        "tool_calls": [
          {"id": "tc-1", "type": "function",
           "function": {"name": "fs.write_file",
                        "arguments": "{\"path\": \"/data/out/x.txt\", \"content\": \"HELLO\"}"}}
        ]
      },
      "finish_reason": "tool_calls"
    }
  ],
  "usage": {"prompt_tokens": 12, "completion_tokens": 40, "total_tokens": 52}
}
```

- `finish_reason`: `stop`, `tool_calls`, or `length`.
- `tool_calls[].function.arguments` is a JSON **string** (OpenAI form);
  the SAS client parses it into a dict. If it is already an object the
  client accepts it too (lenient read, strict write).

## Model identifiers

| Identifier | Role | Capability |
|---|---|---|
| `qwen3-4b-instruct-2507` | main LLM (agent, tool-calling, reasoning) | text, NPU |
| `qwen2.5-vl-7b-instruct` | vision / UI understanding | image+text, NPU |

The Android bridge maps these identifiers to its GenieX SDK instances.
Model weights live in GenieX-managed app storage — never in the SAS
repository or `~/SAS-RUNTIME` (metadata only).

## SAS-side configuration

| Environment variable | Default | Meaning |
|---|---|---|
| `GENIEX_BRIDGE_URL` | `http://127.0.0.1:8765` | bridge base URL (loopback only) |
| `GENIEX_BRIDGE_TIMEOUT` | `120` | per-request timeout in seconds |
| `GENIEX_LLM_MODEL` | `qwen3-4b-instruct-2507` | model id for text routing |
| `GENIEX_VLM_MODEL` | `qwen2.5-vl-7b-instruct` | model id for the vision capability |

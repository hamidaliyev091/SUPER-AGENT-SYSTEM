"""Shared stdlib-only HTTP transport and response helpers for cloud ModelPorts.

Confined strictly to `src/models/`: Core and governance engines remain free
of provider SDKs and network code (INTERFACES.md s14, ADR-001, ADR-022).
"""
from __future__ import annotations

import json
import math
import os
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Iterable, Optional

from core import ModelResponse, ToolCall
from core.enums import ModelRole

_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
_MAX_RETRY_AFTER_SEC = 30.0

ALL_MODEL_ROLES = (
    ModelRole.PLANNER,
    ModelRole.RESEARCHER,
    ModelRole.CODER,
    ModelRole.REVIEWER,
    ModelRole.VERIFIER,
    ModelRole.GENERAL_AGENT,
)


def resolve_timeout(timeout: Optional[float], env_var: str, default: float) -> float:
    """Resolve and validate positive finite timeout seconds from arg or environment."""
    raw = timeout if timeout is not None else os.environ.get(env_var, str(default))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ProviderError("CONFIG", f"{env_var} must be a number") from None
    if not math.isfinite(value) or value <= 0:
        raise ProviderError("CONFIG", f"{env_var} must be a positive finite number")
    return value


def extract_numeric_constraints(
    constraints: Optional[dict],
    *,
    default_max_tokens: int,
    default_temperature: float,
) -> tuple[int, float]:
    """Read advisory numeric generation limits without altering policy authority."""
    max_tokens = default_max_tokens
    temperature = default_temperature
    if not isinstance(constraints, dict):
        return max_tokens, temperature
    max_tok = constraints.get("maxOutputTokens", constraints.get("max_tokens"))
    if isinstance(max_tok, int) and not isinstance(max_tok, bool) and max_tok > 0:
        max_tokens = max_tok
    temp = constraints.get("temperature")
    if isinstance(temp, (int, float)) and not isinstance(temp, bool):
        temperature = float(temp)
    return max_tokens, temperature


class ProviderError(Exception):
    """Structured model-provider transport or protocol failure."""

    def __init__(self, code: str, message: str, status: Optional[int] = None,
                 retryable: bool = False):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.status = status
        self.retryable = retryable


def redact_secret(text: str, secrets: Iterable[str]) -> str:
    """Replace any supplied secret value with '[REDACTED]' before surfacing errors."""
    scrubbed = str(text or "")
    for secret in secrets or ():
        if isinstance(secret, str):
            stripped = secret.strip()
            if len(stripped) >= 4:
                scrubbed = scrubbed.replace(stripped, "[REDACTED]")
                if stripped != secret:
                    scrubbed = scrubbed.replace(secret, "[REDACTED]")
    return scrubbed


def validate_base_url(url: str) -> str:
    """Require HTTPS for remote endpoints; permit HTTP on loopback only."""
    cleaned = str(url or "").strip().rstrip("/")
    if not cleaned:
        raise ProviderError("CONFIG", "base URL must not be empty")
    parsed = urllib.parse.urlparse(cleaned)
    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").lower()
    if not host:
        raise ProviderError("CONFIG", f"invalid base URL (missing host): {cleaned!r}")
    if scheme == "https":
        return cleaned
    if scheme == "http" and host in _LOOPBACK_HOSTS:
        return cleaned
    raise ProviderError(
        "CONFIG",
        f"base URL must use https:// (http:// is allowed on loopback only), got {cleaned!r}",
    )


def _error_payload(exc: urllib.error.HTTPError) -> tuple[Optional[dict], str]:
    try:
        with exc:
            raw_text = exc.read().decode("utf-8", errors="replace")
    except Exception:
        return None, ""
    try:
        decoded = json.loads(raw_text)
    except json.JSONDecodeError:
        return None, raw_text[:200]
    if isinstance(decoded, dict):
        return decoded, raw_text[:200]
    return None, raw_text[:200]


def _extract_error_detail(decoded: Optional[dict], raw_snippet: str) -> str:
    if isinstance(decoded, dict):
        err = decoded.get("error")
        if isinstance(err, dict):
            msg = err.get("message") or err.get("status")
            if msg:
                return str(msg)
        if isinstance(err, str) and err:
            return err
        msg = decoded.get("message")
        if isinstance(msg, str) and msg:
            return msg
    return raw_snippet.strip()


def _retry_after_seconds(headers: Any, default_delay: float) -> float:
    if headers is None:
        return default_delay
    raw = headers.get("Retry-After")
    if raw is None:
        return default_delay
    try:
        parsed = float(str(raw).strip())
    except ValueError:
        return default_delay
    if parsed < 0:
        return default_delay
    return min(parsed, _MAX_RETRY_AFTER_SEC)


def post_json(
    url: str,
    body: dict,
    *,
    headers: Optional[dict] = None,
    timeout: float = 60.0,
    max_retries: int = 2,
    backoff_base: float = 0.5,
    sleep: Callable[[float], None] = time.sleep,
    secrets: Iterable[str] = (),
) -> dict:
    """POST JSON with bounded retries and redacted structured errors."""
    secret_tuple = tuple(s for s in (secrets or ()) if isinstance(s, str) and s)
    parsed_url = urllib.parse.urlparse(url)
    ssl_ctx = ssl.create_default_context() if parsed_url.scheme.lower() == "https" else None

    request_headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)
    encoded = json.dumps(body).encode("utf-8")

    attempts = max(0, int(max_retries)) + 1
    last_error: Optional[ProviderError] = None

    for attempt in range(attempts):
        request = urllib.request.Request(url, data=encoded, headers=request_headers, method="POST")
        try:
            if ssl_ctx is not None:
                ctx_manager = urllib.request.urlopen(request, timeout=timeout, context=ssl_ctx)
            else:
                ctx_manager = urllib.request.urlopen(request, timeout=timeout)
            with ctx_manager as response:
                payload = response.read().decode("utf-8", errors="replace")
            break
        except (socket.timeout, TimeoutError):
            last_error = ProviderError(
                "TIMEOUT",
                redact_secret(f"provider did not respond within {timeout}s", secret_tuple),
                retryable=True,
            )
            delay = backoff_base * (2 ** attempt)
        except urllib.error.HTTPError as exc:
            status = exc.code
            resp_headers = exc.headers
            decoded, snippet = _error_payload(exc)
            detail = _extract_error_detail(decoded, snippet)
            suffix = f": {detail}" if detail else ""
            if status in (401, 403):
                raise ProviderError(
                    "AUTH",
                    redact_secret(f"authentication failed ({status}){suffix}", secret_tuple),
                    status=status,
                    retryable=False,
                ) from None
            if status == 429:
                delay = _retry_after_seconds(resp_headers, backoff_base * (2 ** attempt))
                last_error = ProviderError(
                    "RATE_LIMIT",
                    redact_secret(f"rate limited (429){suffix}", secret_tuple),
                    status=429,
                    retryable=True,
                )
            elif 500 <= status <= 599:
                delay = backoff_base * (2 ** attempt)
                last_error = ProviderError(
                    "HTTP",
                    redact_secret(f"provider HTTP {status}{suffix}", secret_tuple),
                    status=status,
                    retryable=True,
                )
            else:
                raise ProviderError(
                    "HTTP",
                    redact_secret(f"provider HTTP {status}{suffix}", secret_tuple),
                    status=status,
                    retryable=False,
                ) from None
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (socket.timeout, TimeoutError)):
                last_error = ProviderError(
                    "TIMEOUT",
                    redact_secret(f"provider did not respond within {timeout}s", secret_tuple),
                    retryable=True,
                )
            else:
                last_error = ProviderError(
                    "CONNECTION",
                    redact_secret(f"provider connection error: {exc.reason}", secret_tuple),
                    retryable=True,
                )
            delay = backoff_base * (2 ** attempt)
        except (ConnectionError, OSError) as exc:
            last_error = ProviderError(
                "CONNECTION",
                redact_secret(f"provider connection error: {exc}", secret_tuple),
                retryable=True,
            )
            delay = backoff_base * (2 ** attempt)

        if attempt + 1 >= attempts:
            assert last_error is not None
            raise last_error
        sleep(delay)
    else:
        assert last_error is not None
        raise last_error

    try:
        decoded_ok = json.loads(payload)
    except json.JSONDecodeError:
        raise ProviderError(
            "PROTOCOL",
            redact_secret(f"provider returned non-JSON: {payload[:120]}", secret_tuple),
            retryable=False,
        ) from None
    if not isinstance(decoded_ok, dict):
        raise ProviderError("PROTOCOL", "provider response is not a JSON object", retryable=False)
    return decoded_ok


def candidate_json_lines(text: str) -> list[str]:
    """Extract bare or markdown-fenced JSON object lines from model text."""
    candidates: list[str] = []
    inside_fence = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            inside_fence = not inside_fence
            if "{" in stripped:
                inner = stripped.split("```", 1)[1].rsplit("```", 1)[0].strip()
                brace = inner.find("{")
                if brace >= 0:
                    candidates.append(inner[brace:])
            continue
        if inside_fence or (stripped.startswith("{") and stripped.endswith("}")):
            candidates.append(stripped)
    return candidates


def parse_tool_calls_from_text(text: str) -> list[ToolCall]:
    """Parse SAS JSON tool-call lines from model text into ToolCall proposals."""
    calls: list[ToolCall] = []
    for trimmed in candidate_json_lines(text):
        if not (trimmed.startswith("{") and trimmed.endswith("}")):
            continue
        try:
            obj = json.loads(trimmed)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        function = obj.get("function") or obj.get("tool_call")
        if not isinstance(function, dict):
            continue
        name = function.get("name")
        if not isinstance(name, str) or not name:
            continue
        arguments = function.get("arguments", {})
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        calls.append(ToolCall(
            id=str(obj.get("id") or f"tc-{len(calls)}"),
            name=name,
            arguments=arguments,
        ))
    return calls


def normalize_usage(raw_usage: Optional[dict]) -> Optional[dict]:
    """Normalize token usage counts into standard OpenAI/driver keys."""
    if not isinstance(raw_usage, dict):
        return None
    prompt = raw_usage.get("prompt_tokens", raw_usage.get("promptTokenCount", raw_usage.get("inputTokens")))
    completion = raw_usage.get("completion_tokens", raw_usage.get("candidatesTokenCount", raw_usage.get("outputTokens")))
    total = raw_usage.get("total_tokens", raw_usage.get("totalTokenCount", raw_usage.get("totalTokens")))

    normalized: dict[str, int] = {}
    if isinstance(prompt, int) and not isinstance(prompt, bool):
        normalized["prompt_tokens"] = prompt
    if isinstance(completion, int) and not isinstance(completion, bool):
        normalized["completion_tokens"] = completion
    if isinstance(total, int) and not isinstance(total, bool):
        normalized["total_tokens"] = total
    elif "prompt_tokens" in normalized and "completion_tokens" in normalized:
        normalized["total_tokens"] = normalized["prompt_tokens"] + normalized["completion_tokens"]
    return normalized or None


def maybe_structured_output(content: str, output_schema: Optional[dict]) -> Optional[dict]:
    """Parse JSON dict structuredOutput only when an outputSchema was requested."""
    if output_schema is None or not content:
        return None
    stripped = content.strip()
    if not stripped:
        return None
    candidates = [stripped]
    candidates.extend(candidate_json_lines(stripped))
    for candidate in candidates:
        try:
            decoded = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(decoded, dict):
            return decoded
    return None


def tool_spec_to_dict(tool: Any) -> Optional[dict]:
    """Normalize a core.Tool or dict tool declaration into {name, description, parameters}."""
    default_params = {"type": "object", "properties": {}}
    if isinstance(tool, dict):
        name = tool.get("name") or tool.get("id")
        if not isinstance(name, str) or not name.strip():
            return None
        params = tool.get("parameters") or tool.get("inputSchema") or default_params
        if not isinstance(params, dict) or not params:
            params = default_params
        return {
            "name": name.strip(),
            "description": str(tool.get("description") or ""),
            "parameters": params,
        }
    tool_id = getattr(tool, "id", None) or getattr(tool, "name", None)
    if not isinstance(tool_id, str) or not tool_id.strip():
        return None
    schema = getattr(tool, "inputSchema", None)
    if not isinstance(schema, dict) or not schema:
        schema = default_params
    return {
        "name": tool_id.strip(),
        "description": str(getattr(tool, "description", "") or ""),
        "parameters": schema,
    }


def parse_openai_response(result: dict, *,
                          output_schema: Optional[dict] = None) -> ModelResponse:
    """Translate an OpenAI-compatible response dict into a ModelResponse."""
    content = ""
    tool_calls: list[ToolCall] = []
    usage = None
    finish_reason = "stop"
    try:
        choices = result.get("choices") or []
        if choices:
            message = choices[0].get("message") or {}
            content = str(message.get("content") or "")
            for index, raw in enumerate(message.get("tool_calls") or []):
                if not isinstance(raw, dict):
                    continue
                function = raw.get("function") if isinstance(raw.get("function"), dict) else {}
                name = str(function.get("name") or "")
                if not name:
                    continue
                arguments = function.get("arguments", {})
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError:
                        arguments = {}
                if not isinstance(arguments, dict):
                    arguments = {}
                call_id = str(raw.get("id") or f"openai_call_{index}")
                tool_calls.append(ToolCall(id=call_id, name=name, arguments=arguments))
            finish_reason = str(choices[0].get("finish_reason") or "stop")
        usage = normalize_usage(result.get("usage"))
    except Exception:
        return ModelResponse(content="", toolCalls=[], finishReason="provider_error")
    if not tool_calls and content:
        tool_calls = parse_tool_calls_from_text(content)
        if tool_calls and finish_reason == "stop":
            finish_reason = "tool_calls"
    structured = maybe_structured_output(content, output_schema)
    return ModelResponse(
        content=content,
        toolCalls=tool_calls,
        structuredOutput=structured,
        usage=usage,
        finishReason=finish_reason,
    )

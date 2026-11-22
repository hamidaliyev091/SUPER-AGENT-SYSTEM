"""Offline unit tests for DeepSeekModelPort and GeminiModelPort (ADR-022)."""
from __future__ import annotations

import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from core import ModelRequest, Tool, ToolCall
from core.enums import Idempotency, ModelRole, Reversibility, RiskLevel, SideEffect
from models import (
    DeepSeekModelPort,
    GeminiModelPort,
    ProviderError,
    build_deepseek_router,
    build_gemini_router,
)
from models.gemini import sanitize_gemini_schema

# Scanner-safe synthetic credentials (no real provider prefixes such as AIza* or sk-*).
FAKE_DEEPSEEK_KEY = "test-deepseek-key-0001-placeholder"
FAKE_GEMINI_KEY = "test-gemini-key-0001-placeholder"


class _MockServer:
    """Minimal loopback HTTP test server with scripted responses and request capture."""

    def __init__(self, handler):
        self.handler = handler
        self.requests: list[dict] = []

        outer = self

        class _ReqHandler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length) if length else b"{}"
                decoded = json.loads(raw.decode("utf-8")) if raw else {}
                rec = {
                    "path": self.path,
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                    "body": decoded,
                }
                outer.requests.append(rec)
                status, resp_headers, payload = outer.handler(rec, len(outer.requests))
                if isinstance(payload, (dict, list)):
                    body_bytes = json.dumps(payload).encode("utf-8")
                    ctype = "application/json"
                else:
                    body_bytes = str(payload).encode("utf-8")
                    ctype = "text/plain"
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                for hk, hv in (resp_headers or {}).items():
                    self.send_header(hk, hv)
                self.send_header("Content-Length", str(len(body_bytes)))
                try:
                    self.end_headers()
                    self.wfile.write(body_bytes)
                except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
                    pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _ReqHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"

    def close(self):
        try:
            self.httpd.shutdown()
        except Exception:
            pass
        try:
            self.httpd.server_close()
        except Exception:
            pass
        self.thread.join(timeout=2)


def _sample_tool() -> Tool:
    return Tool(
        id="fs.write_file",
        name="fs.write_file",
        description="Write text to a file.",
        inputSchema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "path": {"type": "string"},
                "default": {"type": "string", "default": "fallback"},
            },
            "required": ["path"],
        },
        outputSchema={},
        riskLevel=RiskLevel.LOW,
        sideEffect=SideEffect.MUTATING,
        reversibility=Reversibility.REVERSIBLE,
        idempotency=Idempotency.IDEMPOTENT,
    )


class CloudModelPortUnitTests(unittest.TestCase):

    def _start_server(self, handler) -> _MockServer:
        srv = _MockServer(handler)
        self.addCleanup(srv.close)
        return srv

    # -- Request building & schema sanitization ------------------------------

    def test_deepseek_payload_system_only_constraints_tools_and_schema(self):
        port = DeepSeekModelPort(api_key=FAKE_DEEPSEEK_KEY, model="deepseek-chat")
        request = ModelRequest(
            role=ModelRole.PLANNER,
            messages=[{"role": "system", "content": "Task objective: write x.txt"}],
            tools=[_sample_tool()],
            constraints={"max_tokens": 512, "temperature": 0.2},
            outputSchema={"type": "object", "properties": {"ok": {"type": "boolean"}}},
        )
        payload = port.build_payload(request)
        self.assertEqual(payload["model"], "deepseek-chat")
        self.assertEqual(payload["messages"], [
            {"role": "system", "content": "Task objective: write x.txt"},
            {"role": "user", "content": "Proceed."},
        ])
        self.assertEqual(payload["max_tokens"], 512)
        self.assertEqual(payload["temperature"], 0.2)
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(payload["tools"][0]["type"], "function")
        self.assertEqual(payload["tools"][0]["function"]["name"], "fs.write_file")

    def test_gemini_payload_mapping_and_schema_sanitization(self):
        port = GeminiModelPort(api_key=FAKE_GEMINI_KEY, model="gemini-2.5-flash")
        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "https://example.invalid/schema.json",
            "$defs": {"Item": {"type": "string"}},
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "status": {"type": "string", "examples": ["done"]},
                "default": {"type": "string", "default": "ok"},
                "nested": {
                    "type": "object",
                    "additionalProperties": True,
                    "properties": {"count": {"type": "integer"}},
                },
            },
        }
        cleaned = sanitize_gemini_schema(schema)
        self.assertNotIn("$schema", cleaned)
        self.assertNotIn("$id", cleaned)
        self.assertNotIn("$defs", cleaned)
        self.assertNotIn("additionalProperties", cleaned)
        # Field literally named "default" under properties MUST survive, while its schema metadata "default" is stripped.
        self.assertIn("default", cleaned["properties"])
        self.assertNotIn("default", cleaned["properties"]["default"])
        self.assertNotIn("examples", cleaned["properties"]["status"])
        self.assertNotIn("additionalProperties", cleaned["properties"]["nested"])

        # Multi-turn request preserves systemInstruction and maps assistant -> model
        multi = ModelRequest(
            role=ModelRole.CODER,
            messages=[
                {"role": "system", "content": "Follow policy."},
                {"role": "user", "content": "Write file"},
                {"role": "assistant", "content": "Working"},
            ],
            tools=[_sample_tool()],
            constraints={"maxOutputTokens": 777, "temperature": 0.1},
            outputSchema=schema,
        )
        payload = port.build_payload(multi)
        self.assertEqual(payload["systemInstruction"], {"parts": [{"text": "Follow policy."}]})
        self.assertEqual([c["role"] for c in payload["contents"]], ["user", "model"])
        decl = payload["tools"][0]["functionDeclarations"][0]
        self.assertNotIn("$schema", decl["parameters"])
        self.assertNotIn("additionalProperties", decl["parameters"])
        gc = payload["generationConfig"]
        self.assertEqual(gc["maxOutputTokens"], 777)
        self.assertEqual(gc["temperature"], 0.1)
        self.assertEqual(gc["responseMimeType"], "application/json")
        self.assertNotIn("additionalProperties", gc["responseSchema"])

        # System-only prompt promotes prompt into non-empty contents
        sys_only = ModelRequest(
            role=ModelRole.GENERAL_AGENT,
            messages=[{"role": "system", "content": "Objective only"}],
        )
        sys_payload = port.build_payload(sys_only)
        self.assertNotIn("systemInstruction", sys_payload)
        self.assertEqual(sys_only_contents := sys_payload["contents"],
                         [{"role": "user", "parts": [{"text": "Objective only"}]}])
        self.assertTrue(sys_only_contents)

    # -- Response normalization ----------------------------------------------

    def test_deepseek_response_tool_calls_text_fallback_and_structured(self):
        srv = self._start_server(lambda rec, n: (200, {}, {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": '{"answer": "ok"}',
                    "tool_calls": [
                        {
                            "type": "function",
                            "function": {
                                "name": "fs.write_file",
                                "arguments": '{"path": "/data/out/a.txt", "content": "hi"}',
                            },
                        },
                        {
                            "id": "explicit-id",
                            "type": "function",
                            "function": {
                                "name": "fs.read_file",
                                "arguments": {"path": "/data/out/a.txt"},
                            },
                        },
                    ],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7},
        }))
        port = DeepSeekModelPort(api_key=FAKE_DEEPSEEK_KEY, base_url=srv.url)
        req = ModelRequest(
            role=ModelRole.CODER,
            messages=[{"role": "user", "content": "go"}],
            outputSchema={"type": "object"},
        )
        resp = port.generate(req)
        self.assertIsNone(port.last_error)
        self.assertEqual(len(resp.toolCalls), 2)
        self.assertEqual(resp.toolCalls[0], ToolCall(id="openai_call_0", name="fs.write_file",
                                                     arguments={"path": "/data/out/a.txt", "content": "hi"}))
        self.assertEqual(resp.toolCalls[1].id, "explicit-id")
        self.assertEqual(resp.usage, {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18})
        self.assertEqual(resp.structuredOutput, {"answer": "ok"})
        self.assertEqual(resp.finishReason, "tool_calls")
        self.assertEqual(srv.requests[0]["path"], "/chat/completions")
        self.assertEqual(srv.requests[0]["headers"]["authorization"], f"Bearer {FAKE_DEEPSEEK_KEY}")

        # Text fallback (markdown-fenced JSON tool call)
        fenced = DeepSeekModelPort(
            api_key=FAKE_DEEPSEEK_KEY,
            transport=lambda *a, **k: {
                "choices": [{
                    "message": {
                        "content": '```json\n{"function": {"name": "fs.read_file", "arguments": {"path": "/x"}}}\n```'
                    },
                    "finish_reason": "stop",
                }],
            },
        )
        fb = fenced.generate(ModelRequest(role=ModelRole.GENERAL_AGENT, messages=[{"role": "user", "content": "x"}]))
        self.assertEqual(len(fb.toolCalls), 1)
        self.assertEqual(fb.toolCalls[0].name, "fs.read_file")
        self.assertEqual(fb.finishReason, "tool_calls")

    def test_gemini_response_synthesizes_deterministic_tool_call_ids(self):
        srv = self._start_server(lambda rec, n: (200, {}, {
            "candidates": [{
                "content": {
                    "parts": [
                        {"functionCall": {"name": "fs.write_file", "args": {"path": "/data/a", "content": "1"}}},
                        {"functionCall": {"name": "fs.read_file", "args": {"path": "/data/a"}}},
                    ],
                },
                "finishReason": "STOP",
            }],
            "usageMetadata": {"promptTokenCount": 9, "candidatesTokenCount": 6, "totalTokenCount": 15},
        }))
        port = GeminiModelPort(api_key=FAKE_GEMINI_KEY, base_url=srv.url, model="gemini-2.5-flash")
        resp = port.generate(ModelRequest(role=ModelRole.GENERAL_AGENT, messages=[{"role": "user", "content": "hi"}]))
        self.assertIsNone(port.last_error)
        self.assertEqual([tc.id for tc in resp.toolCalls], ["gemini_call_0", "gemini_call_1"])
        self.assertEqual(resp.toolCalls[0].arguments, {"path": "/data/a", "content": "1"})
        self.assertEqual(resp.usage, {"prompt_tokens": 9, "completion_tokens": 6, "total_tokens": 15})
        self.assertEqual(resp.finishReason, "tool_calls")
        # Header auth only — never ?key= in URL
        self.assertEqual(srv.requests[0]["path"], "/v1beta/models/gemini-2.5-flash:generateContent")
        self.assertEqual(srv.requests[0]["headers"].get("x-goog-api-key"), FAKE_GEMINI_KEY)

        # Text fallback & MAX_TOKENS + structured output
        port_text = GeminiModelPort(
            api_key=FAKE_GEMINI_KEY,
            transport=lambda *a, **k: {
                "candidates": [{
                    "content": {"parts": [{"text": '```json\n{"status": "done"}\n```'}]},
                    "finishReason": "MAX_TOKENS",
                }],
            },
        )
        res_struct = port_text.generate(ModelRequest(
            role=ModelRole.REVIEWER,
            messages=[{"role": "user", "content": "json"}],
            outputSchema={"type": "object"},
        ))
        self.assertEqual(res_struct.finishReason, "length")
        self.assertEqual(res_struct.structuredOutput, {"status": "done"})

    # -- Retries, error mapping & fail-closed behavior -----------------------

    def test_auth_401_and_403_never_retry_and_redact_secret(self):
        for code in (401, 403):
            sleeps: list[float] = []
            srv = self._start_server(lambda rec, n, st=code: (
                st, {}, {"error": {"message": f"bad key {FAKE_DEEPSEEK_KEY}"}}
            ))
            port = DeepSeekModelPort(
                api_key=FAKE_DEEPSEEK_KEY, base_url=srv.url, max_retries=2, sleep=sleeps.append,
            )
            resp = port.generate(ModelRequest(role=ModelRole.GENERAL_AGENT, messages=[{"role": "user", "content": "x"}]))
            self.assertEqual(resp.finishReason, "provider_error")
            self.assertEqual(resp.toolCalls, [])
            self.assertEqual(len(srv.requests), 1)
            self.assertEqual(sleeps, [])
            self.assertIsNotNone(port.last_error)
            self.assertEqual(port.last_error.code, "AUTH")
            self.assertEqual(port.last_error.status, code)
            self.assertNotIn(FAKE_DEEPSEEK_KEY, port.last_error.message)
            self.assertIn("[REDACTED]", port.last_error.message)

    def test_rate_limit_429_respects_retry_after_then_succeeds(self):
        sleeps: list[float] = []

        def handler(rec, count):
            if count == 1:
                return (429, {"Retry-After": "2"}, {"error": {"message": "slow down"}})
            return (200, {}, {
                "candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}],
                "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 1, "totalTokenCount": 3},
            })

        srv = self._start_server(handler)
        port = GeminiModelPort(
            api_key=FAKE_GEMINI_KEY, base_url=srv.url, max_retries=2, sleep=sleeps.append,
        )
        resp = port.generate(ModelRequest(role=ModelRole.GENERAL_AGENT, messages=[{"role": "user", "content": "hi"}]))
        self.assertEqual(resp.content, "ok")
        self.assertEqual(resp.finishReason, "stop")
        self.assertIsNone(port.last_error)
        self.assertEqual(len(srv.requests), 2)
        self.assertEqual(sleeps, [2.0])

    def test_rate_limit_429_exhausted_returns_rate_limit_code(self):
        sleeps: list[float] = []
        srv = self._start_server(lambda rec, n: (429, {"Retry-After": "1"}, {"error": {"message": "busy"}}))
        port = GeminiModelPort(
            api_key=FAKE_GEMINI_KEY, base_url=srv.url, max_retries=2, sleep=sleeps.append,
        )
        resp = port.generate(ModelRequest(role=ModelRole.GENERAL_AGENT, messages=[{"role": "user", "content": "hi"}]))
        self.assertEqual(resp.finishReason, "provider_error")
        self.assertEqual(len(srv.requests), 3)
        self.assertEqual(sleeps, [1.0, 1.0])
        self.assertEqual(port.last_error.code, "RATE_LIMIT")
        self.assertEqual(port.last_error.status, 429)

    def test_503_retries_and_400_fails_immediately(self):
        sleeps_503: list[float] = []
        srv_503 = self._start_server(lambda rec, n: (503, {}, {"error": {"message": "unavailable"}}))
        p503 = DeepSeekModelPort(
            api_key=FAKE_DEEPSEEK_KEY, base_url=srv_503.url, max_retries=2, sleep=sleeps_503.append,
        )
        r503 = p503.generate(ModelRequest(role=ModelRole.GENERAL_AGENT, messages=[{"role": "user", "content": "hi"}]))
        self.assertEqual(r503.finishReason, "provider_error")
        self.assertEqual(len(srv_503.requests), 3)
        self.assertEqual(sleeps_503, [0.5, 1.0])
        self.assertEqual(p503.last_error.code, "HTTP")
        self.assertEqual(p503.last_error.status, 503)

        sleeps_400: list[float] = []
        srv_400 = self._start_server(lambda rec, n: (400, {}, {"error": {"message": "bad schema"}}))
        p400 = GeminiModelPort(
            api_key=FAKE_GEMINI_KEY, base_url=srv_400.url, max_retries=2, sleep=sleeps_400.append,
        )
        r400 = p400.generate(ModelRequest(role=ModelRole.GENERAL_AGENT, messages=[{"role": "user", "content": "hi"}]))
        self.assertEqual(r400.finishReason, "provider_error")
        self.assertEqual(len(srv_400.requests), 1)
        self.assertEqual(sleeps_400, [])
        self.assertEqual(p400.last_error.code, "HTTP")
        self.assertEqual(p400.last_error.status, 400)

    def test_malformed_json_maps_to_protocol_without_crashing(self):
        srv = self._start_server(lambda rec, n: (200, {}, "{not valid json"))
        port = DeepSeekModelPort(api_key=FAKE_DEEPSEEK_KEY, base_url=srv.url)
        resp = port.generate(ModelRequest(role=ModelRole.GENERAL_AGENT, messages=[{"role": "user", "content": "hi"}]))
        self.assertEqual(resp.finishReason, "provider_error")
        self.assertEqual(port.last_error.code, "PROTOCOL")

    def test_timeout_and_connection_error_retries(self):
        sleeps_t: list[float] = []

        def slow_handler(rec, n):
            import time
            time.sleep(0.35)
            return (200, {}, {"choices": [{"message": {"content": "late"}}]})

        srv_slow = self._start_server(slow_handler)
        p_timeout = DeepSeekModelPort(
            api_key=FAKE_DEEPSEEK_KEY, base_url=srv_slow.url,
            timeout=0.05, max_retries=1, sleep=sleeps_t.append,
        )
        rt = p_timeout.generate(ModelRequest(role=ModelRole.GENERAL_AGENT, messages=[{"role": "user", "content": "hi"}]))
        self.assertEqual(rt.finishReason, "provider_error")
        self.assertEqual(p_timeout.last_error.code, "TIMEOUT")
        self.assertEqual(sleeps_t, [0.5])

        sleeps_c: list[float] = []
        srv_closed = self._start_server(lambda rec, n: (200, {}, {}))
        closed_url = srv_closed.url
        srv_closed.close()
        p_conn = GeminiModelPort(
            api_key=FAKE_GEMINI_KEY, base_url=closed_url,
            timeout=5.0, max_retries=1, sleep=sleeps_c.append,
        )
        rc = p_conn.generate(ModelRequest(role=ModelRole.GENERAL_AGENT, messages=[{"role": "user", "content": "hi"}]))
        self.assertEqual(rc.finishReason, "provider_error")
        self.assertEqual(p_conn.last_error.code, "CONNECTION")
        self.assertEqual(sleeps_c, [0.5])

    # -- Configuration, URL validation, routers & secret hygiene -------------

    def test_config_validation_missing_key_bad_url_and_bad_timeout(self):
        with self.assertRaises(ProviderError) as err_url:
            DeepSeekModelPort(api_key=FAKE_DEEPSEEK_KEY, base_url="http://api.example.com")
        self.assertEqual(err_url.exception.code, "CONFIG")

        with self.assertRaises(ProviderError) as err_gem_url:
            GeminiModelPort(api_key=FAKE_GEMINI_KEY, base_url="http://generativelanguage.googleapis.com")
        self.assertEqual(err_gem_url.exception.code, "CONFIG")

        with mock.patch.dict(os.environ, {"DEEPSEEK_TIMEOUT": "abc"}, clear=False):
            with self.assertRaises(ProviderError) as err_dt:
                DeepSeekModelPort(api_key=FAKE_DEEPSEEK_KEY)
            self.assertEqual(err_dt.exception.code, "CONFIG")

        with mock.patch.dict(os.environ, {"GEMINI_TIMEOUT": "-5"}, clear=False):
            with self.assertRaises(ProviderError) as err_gt:
                GeminiModelPort(api_key=FAKE_GEMINI_KEY)
            self.assertEqual(err_gt.exception.code, "CONFIG")

        with mock.patch.dict(os.environ, {"GEMINI_TIMEOUT": "nan"}, clear=False):
            with self.assertRaises(ProviderError) as err_nan:
                GeminiModelPort(api_key=FAKE_GEMINI_KEY)
            self.assertEqual(err_nan.exception.code, "CONFIG")

        # Missing env keys fail closed with 0 HTTP calls
        with mock.patch("models._http.urllib.request.urlopen") as urlopen_mock:
            d_missing = DeepSeekModelPort(api_key="")
            rd = d_missing.generate(ModelRequest(role=ModelRole.GENERAL_AGENT, messages=[{"role": "user", "content": "hi"}]))
            self.assertEqual(rd.finishReason, "provider_error")
            self.assertEqual(d_missing.last_error.code, "CONFIG")

            g_missing = GeminiModelPort(api_key="")
            rg = g_missing.generate(ModelRequest(role=ModelRole.GENERAL_AGENT, messages=[{"role": "user", "content": "hi"}]))
            self.assertEqual(rg.finishReason, "provider_error")
            self.assertEqual(g_missing.last_error.code, "CONFIG")
            urlopen_mock.assert_not_called()

    def test_repr_and_routers_expose_no_secrets(self):
        ds_port = DeepSeekModelPort(api_key=FAKE_DEEPSEEK_KEY)
        gm_port = GeminiModelPort(api_key=FAKE_GEMINI_KEY)
        self.assertNotIn(FAKE_DEEPSEEK_KEY, repr(ds_port))
        self.assertNotIn(FAKE_GEMINI_KEY, repr(gm_port))

        ds_router = build_deepseek_router(api_key=FAKE_DEEPSEEK_KEY)
        gm_router = build_gemini_router(api_key=FAKE_GEMINI_KEY)
        for role in (ModelRole.PLANNER, ModelRole.RESEARCHER, ModelRole.CODER,
                     ModelRole.REVIEWER, ModelRole.VERIFIER, ModelRole.GENERAL_AGENT):
            self.assertIs(ds_router.select(role), role)
            self.assertIs(gm_router.select(role), role)


if __name__ == "__main__":
    unittest.main()

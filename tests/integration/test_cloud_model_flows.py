"""Governed integration tests for GeminiModelPort & DeepSeekModelPort (ADR-022).

Verifies full Policy/Verification/Completion parity, token-budget enforcement,
outage resilience, and secret-free durability over loopback HTTP doubles.
"""
from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tests.support.fakes import ScriptedModelPort
from tests.support.harness import VerificationTestBase

from completion import CompletionEngine
from continuity import RecoveryManager
from core import ToolCall
from core.enums import ModelRole, TaskState
from models import (
    DeepSeekModelPort,
    GeminiModelPort,
    ModelPortDriver,
    build_deepseek_router,
    build_gemini_router,
)
from orchestration import Orchestrator

FAKE_DEEPSEEK_KEY = "test-deepseek-key-0001-placeholder"
FAKE_GEMINI_KEY = "test-gemini-key-0001-placeholder"


class _CloudBridgeMockServer:
    """Loopback server implementing both DeepSeek OpenAI and Gemini REST shapes."""

    def __init__(self):
        self.deepseek_handler = None
        self.gemini_handler = None
        self.requests: list[tuple[str, dict]] = []
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length) if length else b"{}"
                body = json.loads(raw.decode("utf-8"))
                outer.requests.append((self.path, body))
                if self.path == "/chat/completions" and outer.deepseek_handler is not None:
                    status, payload = outer.deepseek_handler(body)
                elif ":generateContent" in self.path and outer.gemini_handler is not None:
                    status, payload = outer.gemini_handler(body)
                else:
                    status, payload = 404, {"error": {"message": f"unhandled path {self.path}"}}
                encoded = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
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


class CloudModelFlowTests(VerificationTestBase):

    def setUp(self):
        super().setUp()
        self.server = _CloudBridgeMockServer()
        self.addCleanup(self.server.close)

    def _orchestrator(self, port, *, token_budget=None):
        driver = ModelPortDriver(self.mgr, self.store, port, token_budget=token_budget)
        return driver, Orchestrator(
            self.mgr, self.pipeline, self.engine,
            CompletionEngine(self.mgr, self.store), driver,
            recovery=RecoveryManager(self.mgr, self.store),
        )

    def test_provider_replaceability_same_governed_done_outcome(self):
        good_call = ToolCall(
            id="tc-write", name="fs.write_file",
            arguments={"path": "/data/out/x.txt", "content": "HELLO"},
        )
        ds_count = 0
        gm_count = 0

        def ds_chat(body):
            nonlocal ds_count
            ds_count += 1
            if ds_count == 1:
                return 200, {
                    "choices": [{
                        "message": {"tool_calls": [{
                            "id": "ds-1",
                            "type": "function",
                            "function": {"name": "fs.write_file",
                                         "arguments": json.dumps(good_call.arguments)},
                        }]},
                        "finish_reason": "tool_calls",
                    }],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 4, "total_tokens": 9},
                }
            return 200, {"choices": [{"message": {"content": "complete"}, "finish_reason": "stop"}]}

        def gm_chat(body):
            nonlocal gm_count
            gm_count += 1
            if gm_count == 1:
                return 200, {
                    "candidates": [{
                        "content": {"parts": [{"functionCall": {
                            "name": "fs.write_file", "args": dict(good_call.arguments),
                        }}]},
                        "finishReason": "STOP",
                    }],
                    "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 4, "totalTokenCount": 9},
                }
            return 200, {
                "candidates": [{"content": {"parts": [{"text": "complete"}]}, "finishReason": "STOP"}],
            }

        self.server.deepseek_handler = ds_chat
        self.server.gemini_handler = gm_chat

        providers = (
            ScriptedModelPort(turns=[[good_call]]),
            DeepSeekModelPort(api_key=FAKE_DEEPSEEK_KEY, base_url=self.server.url),
            GeminiModelPort(api_key=FAKE_GEMINI_KEY, base_url=self.server.url),
        )
        outcomes = []
        for port in providers:
            self.files.pop("/data/out/x.txt", None)
            task = self.make_task(stop_at=TaskState.CREATED)
            _, orchestrator = self._orchestrator(port)
            final = orchestrator.run(task.id, max_iterations=50)
            outcomes.append((final.state.value, self.files.get("/data/out/x.txt")))
        self.assertEqual(outcomes, [("DONE", "HELLO"), ("DONE", "HELLO"), ("DONE", "HELLO")])

    def test_malicious_proposals_equally_denied_across_cloud_providers(self):
        self.server.deepseek_handler = lambda body: (200, {
            "choices": [{
                "message": {"tool_calls": [{
                    "id": "ds-evil",
                    "type": "function",
                    "function": {"name": "fs.write_file",
                                 "arguments": json.dumps({"path": "/etc/pwned.txt", "content": "x"})},
                }]},
                "finish_reason": "tool_calls",
            }],
        })
        self.server.gemini_handler = lambda body: (200, {
            "candidates": [{
                "content": {"parts": [{"functionCall": {
                    "name": "fs.write_file",
                    "args": {"path": "/etc/pwned.txt", "content": "x"},
                }}]},
                "finishReason": "STOP",
            }],
        })

        for port in (
            DeepSeekModelPort(api_key=FAKE_DEEPSEEK_KEY, base_url=self.server.url),
            GeminiModelPort(api_key=FAKE_GEMINI_KEY, base_url=self.server.url),
        ):
            task = self.make_task(stop_at=TaskState.CREATED)
            _, orchestrator = self._orchestrator(port)
            final = orchestrator.run(task.id, max_iterations=20)
            self.assertIsNot(final.state, TaskState.DONE)
            self.assertNotIn("/etc/pwned.txt", self.files)
            self.assertEqual(self.writes, [])

    def test_token_budget_enforced_from_journaled_usage(self):
        self.server.deepseek_handler = lambda body: (200, {
            "choices": [{"message": {"content": "turn"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        })
        self.server.gemini_handler = lambda body: (200, {
            "candidates": [{"content": {"parts": [{"text": "turn"}]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 20, "totalTokenCount": 30},
        })

        for port in (
            DeepSeekModelPort(api_key=FAKE_DEEPSEEK_KEY, base_url=self.server.url),
            GeminiModelPort(api_key=FAKE_GEMINI_KEY, base_url=self.server.url),
        ):
            task = self.make_task(stop_at=TaskState.RUNNING)
            driver = ModelPortDriver(self.mgr, self.store, port, token_budget=50)
            r1 = driver.generate(task)
            self.assertEqual(r1.finishReason, "stop")
            r2 = driver.generate(task)
            self.assertEqual(r2.finishReason, "stop")
            # 60 >= 50: third call MUST be blocked before touching provider
            before_count = len(self.server.requests)
            r3 = driver.generate(task)
            self.assertEqual(r3.finishReason, "token_budget_exhausted")
            self.assertEqual(len(self.server.requests), before_count)

    def test_outage_recovery_and_secrets_never_persisted_to_store(self):
        # Force a 401 that echoes both keys to confirm journal/store remain secret-free.
        self.server.deepseek_handler = lambda body: (
            401, {"error": {"message": f"denied {FAKE_DEEPSEEK_KEY}"}}
        )
        self.server.gemini_handler = lambda body: (
            401, {"error": {"message": f"denied {FAKE_GEMINI_KEY}"}}
        )
        ds_router = build_deepseek_router(api_key=FAKE_DEEPSEEK_KEY, base_url=self.server.url, max_retries=0)
        gm_router = build_gemini_router(api_key=FAKE_GEMINI_KEY, base_url=self.server.url, max_retries=0)

        for router in (ds_router, gm_router):
            task = self.make_task(stop_at=TaskState.CREATED)
            driver = ModelPortDriver(self.mgr, self.store, router, role=ModelRole.GENERAL_AGENT)
            orch = Orchestrator(
                self.mgr, self.pipeline, self.engine,
                CompletionEngine(self.mgr, self.store), driver,
                recovery=RecoveryManager(self.mgr, self.store),
            )
            stopped = orch.run(task.id, max_iterations=5)
            self.assertIsNot(stopped.state, TaskState.DONE)

        # Outage -> recovery with server closed then restarted
        closed_srv = _CloudBridgeMockServer()
        closed_url = closed_srv.url
        closed_srv.close()
        down_port = GeminiModelPort(api_key=FAKE_GEMINI_KEY, base_url=closed_url, max_retries=0)
        t_outage = self.make_task(stop_at=TaskState.RUNNING)
        down_resp = down_port.generate(ModelPortDriver(self.mgr, self.store, down_port).build_request(t_outage))
        self.assertEqual(down_resp.finishReason, "provider_error")

        self.server.gemini_handler = lambda body: (200, {
            "candidates": [{"content": {"parts": [{"text": "recovered"}]}, "finishReason": "STOP"}],
        })
        up_port = GeminiModelPort(api_key=FAKE_GEMINI_KEY, base_url=self.server.url, max_retries=0)
        up_resp = up_port.generate(ModelPortDriver(self.mgr, self.store, up_port).build_request(t_outage))
        self.assertEqual(up_resp.content, "recovered")

        # Verify durable task/journal store contains zero secret literals.
        store_files = [p for p in Path(self.store.root).rglob("*") if p.is_file()]
        self.assertTrue(store_files)
        for path in store_files:
            raw_text = path.read_text(encoding="utf-8", errors="replace")
            self.assertNotIn(FAKE_DEEPSEEK_KEY, raw_text)
            self.assertNotIn(FAKE_GEMINI_KEY, raw_text)


if __name__ == "__main__":
    unittest.main()

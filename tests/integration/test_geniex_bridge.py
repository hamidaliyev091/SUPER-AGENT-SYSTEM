"""GenieX bridge integration (contract v2, OpenAI-compatible): routing,
LLM/VLM request-response, malformed requests, bridge failure/recovery,
timeouts, concurrency, provider replaceability, and architecture
boundaries. The reference server is for TESTS ONLY; the production path
is the Android GenieX app endpoint implementing the same contract."""
import base64
import http.client
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tests.support.harness import VerificationTestBase
from tests.support.geniex_server import (
    GenieXReferenceBridge,
    GenieXScriptedFailure,
    openai_response,
    serve,
)

from core import ModelRequest, ToolCall
from core.enums import ModelRole, TaskState
from completion import CompletionEngine
from continuity import RecoveryManager
from models import (
    GenieXModelPort,
    GenieXVisionModelPort,
    ModelPortDriver,
    ScriptedModelPort,
    build_geniex_router,
)
from orchestration import Orchestrator
from platforms.termux.geniex_bridge import GenieXBridge, GenieXBridgeError


class GenieXBridgeTests(VerificationTestBase):

    def setUp(self):
        super().setUp()
        self.bridge = GenieXReferenceBridge()
        self.server, self.thread, self.url = serve(self.bridge)
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.server_close)

    def client(self, **overrides):
        return GenieXBridge(self.url, **overrides)

    def stop_bridge(self):
        """Stop the loop AND close the listening socket so subsequent
        connects are refused instantly (a crash-style failure)."""
        self.server.shutdown()
        self.server.server_close()

    @staticmethod
    def _request(messages=None):
        return ModelRequest(role=ModelRole.GENERAL_AGENT,
                            messages=messages or [{"role": "user",
                                                   "content": "hi"}])

    def test_model_router_selects_text_model_for_all_roles(self):
        router, vision = build_geniex_router(self.client())
        for role in (ModelRole.PLANNER, ModelRole.RESEARCHER, ModelRole.CODER,
                     ModelRole.REVIEWER, ModelRole.VERIFIER,
                     ModelRole.GENERAL_AGENT):
            self.assertIs(router.select(role), role)
            self.assertEqual(router.ports[role].model, "qwen3-4b-instruct-2507")
        self.assertEqual(vision.model, "qwen2.5-vl-7b-instruct")

    def test_health(self):
        health = self.client().health()
        self.assertEqual(health["status"], "ok")
        self.assertEqual(set(health["models"]),
                         {"qwen3-4b-instruct-2507", "qwen2.5-vl-7b-instruct"})

    def test_llm_request_response(self):
        def chat(request):
            self.assertEqual(request["model"], "qwen3-4b-instruct-2507")
            self.assertEqual(request["messages"][0]["role"], "system")
            self.assertEqual(request["max_tokens"], 2048)
            return openai_response(
                request["model"], content="thinking",
                tool_calls=[{"id": "tc-1", "type": "function",
                             "function": {"name": "fs.write_file",
                                          "arguments": json.dumps(
                                              {"path": "/data/out/x.txt",
                                               "content": "HELLO"})}}],
                finish_reason="tool_calls",
                usage={"prompt_tokens": 5, "completion_tokens": 9,
                       "total_tokens": 14})
        self.bridge.chat_handler = chat
        port = GenieXModelPort(self.client())
        response = port.generate(self._request(
            [{"role": "system", "content": "objective"}]))
        self.assertEqual(response.content, "thinking")
        self.assertEqual(len(response.toolCalls), 1)
        self.assertIsInstance(response.toolCalls[0], ToolCall)
        self.assertEqual(response.toolCalls[0].name, "fs.write_file")
        # the OpenAI string-form arguments were parsed into a dict
        self.assertEqual(response.toolCalls[0].arguments["content"], "HELLO")
        self.assertEqual(response.finishReason, "tool_calls")
        self.assertEqual(response.usage["total_tokens"], 14)

    def test_vlm_multimodal_request_response(self):
        def chat(request):
            self.assertEqual(request["model"], "qwen2.5-vl-7b-instruct")
            content = request["messages"][0]["content"]
            self.assertIsInstance(content, list)
            self.assertEqual(content[0]["type"], "text")
            self.assertEqual(content[0]["text"], "describe the screen")
            data_url = content[1]["image_url"]["url"]
            raw = base64.b64decode(data_url.split("base64,", 1)[1])
            self.assertEqual(raw, b"fake-image-bytes")
            return openai_response(request["model"],
                                   content="a settings screen")
        self.bridge.chat_handler = chat
        vision_port = GenieXVisionModelPort(self.client())
        description = vision_port.describe_image(b"fake-image-bytes",
                                                 "describe the screen")
        self.assertEqual(description, "a settings screen")

    def test_malformed_json_rejected_by_server(self):
        """A malformed body gets a structured 400 - the Android bridge must
        behave the same way."""
        host, port = self.url.replace("http://", "").split(":")
        connection = http.client.HTTPConnection(host, int(port), timeout=5)
        connection.request("POST", "/v1/chat/completions",
                           body="{not json", headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        self.assertEqual(response.status, 400)
        error = json.loads(response.read().decode())
        self.assertEqual(error["error"]["code"], "BAD_REQUEST")
        connection.close()
        # and a valid-JSON-but-wrong-shape body too
        connection = http.client.HTTPConnection(host, int(port), timeout=5)
        connection.request("POST", "/v1/chat/completions",
                           body=json.dumps({"messages": "not-a-list"}),
                           headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        self.assertEqual(response.status, 400)
        connection.close()

    def test_bridge_failure_and_recovery(self):
        """Bridge down: the governed loop must survive and continue;
        bridge back: the next call works."""
        self.stop_bridge()  # bridge down (refused connections)
        port = GenieXModelPort(self.client())
        response = port.generate(self._request())
        self.assertEqual(response.finishReason, "provider_error")
        self.assertEqual(response.toolCalls, [])
        # restart the bridge (recovery) and serve again
        self.bridge = GenieXReferenceBridge()
        self.server, self.thread, self.url = serve(self.bridge)
        port = GenieXModelPort(self.client())
        response = port.generate(self._request())
        self.assertEqual(response.finishReason, "stop")

    def test_timeout_handling(self):
        self.bridge.latency = 2.0
        client = self.client(timeout=0.5)
        with self.assertRaises(GenieXBridgeError) as caught:
            client.chat_completions("qwen3-4b-instruct-2507",
                                    [{"role": "user", "content": "hi"}])
        self.assertEqual(caught.exception.code, "TIMEOUT")

    def test_http_error_surfaces_stable_code(self):
        def failing(request):
            raise GenieXScriptedFailure(503, "NPU_BUSY", "NPU is busy")
        self.bridge.chat_handler = failing
        with self.assertRaises(GenieXBridgeError) as caught:
            self.client().chat_completions("qwen3-4b-instruct-2507",
                                           [{"role": "user", "content": "hi"}])
        self.assertEqual(caught.exception.code, "HTTP")
        self.assertIn("NPU is busy", str(caught.exception))

    def test_loopback_only_enforcement(self):
        with self.assertRaises(GenieXBridgeError) as caught:
            GenieXBridge("http://192.168.1.50:8765")
        self.assertEqual(caught.exception.code, "LOOPBACK")

    def test_concurrent_requests(self):
        def chat(request):
            return openai_response(request["model"],
                                   content=f"reply:{len(self.bridge.chat_requests)}")
        self.bridge.chat_handler = chat
        client = self.client()

        def call(index):
            return client.chat_completions(
                "qwen3-4b-instruct-2507", [{"role": "user", "content": str(index)}])

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(call, range(8)))
        self.assertEqual(len(results), 8)
        self.assertEqual(len(self.bridge.chat_requests), 8)
        contents = {r["choices"][0]["message"]["content"] for r in results}
        self.assertEqual(len(contents), 8)  # each request got its own reply

    def test_architecture_boundary_network_confined_to_adapters(self):
        """Network access (urllib/socket connect) must never appear in the
        Core or the governing engines; only platform/model adapters."""
        forbidden_dirs = ("src/core", "src/policy", "src/verification",
                          "src/completion", "src/task", "src/continuity",
                          "src/orchestration", "src/delegation",
                          "src/execution", "src/observability")
        repo = Path(__file__).resolve().parents[2]
        offenders = []
        for directory in forbidden_dirs:
            for path in (repo / directory).glob("*.py"):
                text = path.read_text()
                if "urllib" in text or "socket." in text or "http.client" in text:
                    offenders.append(str(path))
        self.assertEqual(offenders, [])

    def test_provider_replaceability_same_security_outcomes(self):
        """Two different providers (GenieX bridge + scripted port) emitting
        the SAME tool-call script yield identical governed outcomes -
        policy and completion do not care which model is attached."""
        good_write = ToolCall(id="tc-w", name="fs.write_file",
                              arguments={"path": "/data/out/x.txt",
                                         "content": "HELLO"})

        def chat(request):
            if len(self.bridge.chat_requests) == 1:
                return openai_response(
                    request["model"],
                    tool_calls=[{"id": "tc-w", "type": "function",
                                 "function": {"name": "fs.write_file",
                                              "arguments": json.dumps(
                                                  good_write.arguments)}}],
                    finish_reason="tool_calls")
            return openai_response(request["model"])

        self.bridge.chat_handler = chat
        providers = (GenieXModelPort(self.client()),
                     ScriptedModelPort(turns=[[good_write]]))
        results = []
        for port in providers:
            task = self.make_task(stop_at=TaskState.CREATED)
            driver = ModelPortDriver(self.mgr, self.store, port)
            orchestrator = Orchestrator(
                self.mgr, self.pipeline, self.engine,
                CompletionEngine(self.mgr, self.store), driver,
                recovery=RecoveryManager(self.mgr, self.store))
            final = orchestrator.run(task.id, max_iterations=50)
            results.append((final.state.value,
                            self.files.get("/data/out/x.txt")))
            self.files.pop("/data/out/x.txt", None)  # fresh state per provider
        self.assertEqual(results, [("DONE", "HELLO"), ("DONE", "HELLO")])

    def test_provider_replaceability_malicious_calls_equally_denied(self):
        evil = ToolCall(id="tc-evil", name="fs.write_file",
                        arguments={"path": "/etc/pwned.txt", "content": "x"})

        def chat(request):
            if len(self.bridge.chat_requests) == 1:
                return openai_response(
                    request["model"],
                    tool_calls=[{"id": "tc-evil", "type": "function",
                                 "function": {"name": "fs.write_file",
                                              "arguments": json.dumps(evil.arguments)}}],
                    finish_reason="tool_calls")
            return openai_response(request["model"])

        self.bridge.chat_handler = chat
        for port in (GenieXModelPort(self.client()),
                     ScriptedModelPort(turns=[[evil]])):
            task = self.make_task(stop_at=TaskState.CREATED)
            driver = ModelPortDriver(self.mgr, self.store, port)
            orchestrator = Orchestrator(
                self.mgr, self.pipeline, self.engine,
                CompletionEngine(self.mgr, self.store), driver,
                recovery=RecoveryManager(self.mgr, self.store))
            final = orchestrator.run(task.id, max_iterations=50)
            self.assertIsNot(final.state, TaskState.DONE)
            self.assertNotIn("/etc/pwned.txt", self.files)
            self.assertEqual(self.writes, [])
            self.files.pop("/data/out/x.txt", None)

    def test_geniex_tool_calls_still_flow_through_policy(self):
        """The bridge returns tool calls; they are proposals, never
        authority: an out-of-scope call is DENIED, a legitimate one runs."""
        task = self.make_task(stop_at=TaskState.CREATED)

        def chat(request):
            if len(self.bridge.chat_requests) == 1:
                return openai_response(
                    request["model"],
                    tool_calls=[{"id": "evil", "type": "function",
                                 "function": {"name": "fs.write_file",
                                              "arguments": json.dumps(
                                                  {"path": "/etc/pwned.txt",
                                                   "content": "x"})}}],
                    finish_reason="tool_calls")
            return openai_response(
                request["model"],
                tool_calls=[{"id": "good", "type": "function",
                             "function": {"name": "fs.write_file",
                                          "arguments": json.dumps(
                                              {"path": "/data/out/x.txt",
                                               "content": "HELLO"})}}],
                finish_reason="tool_calls")

        self.bridge.chat_handler = chat
        driver = ModelPortDriver(self.mgr, self.store,
                                 GenieXModelPort(self.client()))
        orchestrator = Orchestrator(
            self.mgr, self.pipeline, self.engine,
            CompletionEngine(self.mgr, self.store), driver,
            recovery=RecoveryManager(self.mgr, self.store))
        final = orchestrator.run(task.id, max_iterations=60)
        self.assertIs(final.state, TaskState.DONE)
        self.assertEqual(self.files.get("/data/out/x.txt"), "HELLO")
        self.assertNotIn("/etc/pwned.txt", self.files)
        self.assertEqual(self.writes, ["/data/out/x.txt"])


if __name__ == "__main__":
    unittest.main()

"""Offline contract tests: no AWS calls and no reading real API credentials."""
import io
import contextvars
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "backend"))
os.environ["PYTHON_DOTENV_DISABLED"] = "1"

from starlette.testclient import TestClient

import bootstrap
import client
import entrypoint
import serve
from bedrock_provider import BedrockProvider
from runtime_logging import JsonFormatter
import telemetry
import deploy
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


class BedrockAdapterTests(unittest.TestCase):
    def test_tool_error_credentials_are_scrubbed_before_model_call(self):
        fake = "test-only-model-key-123456"
        with patch.dict(os.environ, {"GEOAPIFY_KEY": fake}):
            _, messages = BedrockProvider.messages([{"role": "tool", "id": "call-1",
                "content": json.dumps({"error": "https://example.test/?apiKey=" + fake})}])
        self.assertNotIn(fake, json.dumps(messages))
        self.assertIn("[REDACTED]", json.dumps(messages))

    def test_tool_use_result_round_trip(self):
        history = [
            {"role": "system", "content": "Use one tool."},
            {"role": "user", "content": "Find Albany."},
            {"role": "assistant", "thought": "Resolving the place.",
             "tool": {"id": "call-123", "name": "geocode_place", "args": {"query": "Albany"}}},
            {"role": "tool", "id": "call-123", "name": "geocode_place",
             "content": json.dumps({"lat": 42.65, "lon": -73.75})}]
        system, messages = BedrockProvider.messages(history)
        self.assertEqual(system, [{"text": "Use one tool."}])
        self.assertEqual(messages[1]["content"][1]["toolUse"]["toolUseId"], "call-123")
        result = messages[2]["content"][0]["toolResult"]
        self.assertEqual(result["toolUseId"], "call-123")
        self.assertEqual(result["content"][0]["json"]["lat"], 42.65)
        self.assertEqual(result["status"], "success")

    def test_truncated_tool_json_remains_valid_converse_content(self):
        _, messages = BedrockProvider.messages([
            {"role": "tool", "id": "call-1", "content": '{"truncated":'},
            {"role": "tool", "id": "call-2", "content": '{"error":"unavailable"}'}])
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["content"][0]["toolResult"]["content"], [{"text": '{"truncated":'}])
        self.assertEqual(messages[0]["content"][1]["toolResult"]["status"], "error")

    def test_decisions_preserve_model_tool_id_and_token_usage(self):
        api = MagicMock()
        api.converse.side_effect = [
            {"output": {"message": {"content": [{"toolUse": {
                "toolUseId": "nova-id", "name": "geocode_place", "input": {"query": "Albany"}}}]}},
             "usage": {"inputTokens": 20, "outputTokens": 5}},
            {"output": {"message": {"content": [{"text": "Your plan."}]}},
             "usage": {"inputTokens": 30, "outputTokens": 10}}]
        provider = BedrockProvider(client=api, model_id="test-profile")
        tools = [{"name": "geocode_place", "description": "Locate a city", "parameters": {"type": "object"}}]
        first = provider.reason([{"role": "user", "content": "Albany"}], tools)
        self.assertEqual(first.tool_call.id, "nova-id")
        self.assertEqual(first.usage, {"prompt": 20, "completion": 5})
        second = provider.reason([{"role": "user", "content": "Albany"}], tools)
        self.assertEqual(second.final, "Your plan.")
        self.assertEqual(api.converse.call_args.kwargs["modelId"], "test-profile")
        self.assertEqual(api.converse.call_args.kwargs["toolConfig"]["tools"][0]["toolSpec"]["inputSchema"], {"json": {"type": "object"}})


class RuntimeContractTests(unittest.TestCase):
    def setUp(self):
        self.http = TestClient(entrypoint.app)

    def test_health_contract(self):
        response = self.http.get("/ping")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "Healthy")

    def test_streamed_events_and_session_context(self):
        loop = MagicMock()
        loop.run.return_value = iter([
            {"type": "start", "engine": "bedrock"},
            {"type": "tool_result", "name": "geocode_place", "ok": True, "result": {"lat": 42.65}},
            {"type": "final", "text": "Your plan."}])
        session = "11111111-1111-4111-8111-111111111111"
        with patch.object(entrypoint, "prepare", return_value=(loop, SimpleNamespace(model_id="test"), "process-1")):
            response = self.http.post("/invocations", json={"goal": "Albany"},
                                      headers={"X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session})
        events = [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:")]
        self.assertIn("text/event-stream", response.headers["content-type"])
        self.assertEqual([e["type"] for e in events], ["runtime", "start", "tool_result", "final"])
        self.assertEqual(events[0]["session_id"], session)
        self.assertEqual(events[0]["instance_id"], "process-1")

    def test_invalid_input_does_not_initialize_aws_clients(self):
        for payload in ({"goal": ""}, {"goal": 1}, {"goal": "x" * 10001}, ["wrong"]):
            with self.subTest(payload_type=type(payload).__name__), patch.object(entrypoint, "prepare") as prepare:
                response = self.http.post("/invocations", json=payload)
                self.assertIn('"type": "error"', response.text)
                prepare.assert_not_called()

    def test_startup_failure_does_not_stream_raw_exception(self):
        with patch.object(entrypoint, "prepare", side_effect=ValueError("sensitive-value")):
            response = self.http.post("/invocations", json={"goal": "Albany"})
        self.assertNotIn("sensitive-value", response.text)
        self.assertIn("ValueError", response.text)

    def test_operational_logs_include_steps_and_exclude_payload_values(self):
        loop = MagicMock()
        loop.run.return_value = iter([
            {"type": "reason", "turn": 1, "thought": "private-model-text",
             "tool": {"name": "geocode_place", "args": {"query": "private-goal"}},
             "latency_ms": 123, "usage": {"prompt": 10, "completion": 5}},
            {"type": "tool_result", "turn": 1, "name": "geocode_place", "ok": True,
             "latency_ms": 100, "result": {"raw": "private-tool-value"}},
            {"type": "final", "text": "private-plan-value"}])
        with patch.object(entrypoint, "prepare", return_value=(loop, SimpleNamespace(model_id="test"), "process-1")), \
             self.assertLogs("trip_agent.agentcore", level="INFO") as captured:
            self.http.post("/invocations", json={"goal": "private-goal"})
        logs = [json.loads(JsonFormatter().format(record)) for record in captured.records]
        self.assertEqual([item["event"] for item in logs], ["invocation_started", "runtime_initialized", "model_step", "tool_completed", "final_response", "invocation_finished"])
        serialized = json.dumps(logs)
        for value in ("private-goal", "private-model-text", "private-tool-value", "private-plan-value"):
            self.assertNotIn(value, serialized)
        self.assertEqual(logs[-1]["outcome"], "success")
        self.assertTrue(logs[0]["request_id"])
        self.assertEqual(logs[2]["usage"]["prompt"], 10)


class SecretAndStreamingTests(unittest.TestCase):
    def test_recursive_url_and_key_redaction(self):
        fake = "test-only-key-123456789"
        with patch.dict(os.environ, {"GEOAPIFY_KEY": fake}):
            event = bootstrap.redact({"result": {"error": "GET /?apiKey=" + fake},
                                      "other": ["access_key=unknown-secret", fake]})
        serialized = json.dumps(event)
        self.assertNotIn(fake, serialized)
        self.assertNotIn("unknown-secret", serialized)
        self.assertIn("[REDACTED]", serialized)

    def test_only_approved_tool_keys_loaded_before_backend_import(self):
        secret = {name: "fake-" + name for name in bootstrap.TOOL_KEYS}
        secret["OPENAI_API_KEY"] = "should-not-be-imported"
        api = MagicMock()
        api.get_secret_value.return_value = {"SecretString": json.dumps(secret)}
        seen = []
        def import_module(name):
            seen.append(name)
            self.assertEqual(os.environ["GEOAPIFY_KEY"], secret["GEOAPIFY_KEY"])
            self.assertNotIn("OPENAI_API_KEY", os.environ)
            return SimpleNamespace(BedrockProvider=lambda: "provider") if name == "bedrock_provider" else "loop"
        with patch.dict(os.environ, {"TRIP_SECRET_ARN": "test-secret"}, clear=True), \
             patch.object(bootstrap, "_runtime", None), \
             patch.object(bootstrap.boto3, "client", return_value=api), \
             patch.object(bootstrap.importlib, "import_module", side_effect=import_module):
            first = bootstrap.prepare()
            self.assertIs(first, bootstrap.prepare())
        self.assertEqual(seen, ["loop", "bedrock_provider"])
        api.get_secret_value.assert_called_once_with(SecretId="test-secret")

    def test_sse_survives_bytewise_unicode_and_crlf_boundaries(self):
        events = [{"type": "reason", "thought": "café"}, {"type": "final", "text": "done"}]
        wire = "".join("data: " + json.dumps(e, ensure_ascii=False) + "\r\n\r\n" for e in events).encode()
        stream = MagicMock()
        stream.iter_chunks.return_value = [wire[i:i+1] for i in range(len(wire))]
        api = MagicMock()
        api.invoke_agent_runtime.return_value = {"response": stream, "contentType": "text/event-stream"}
        with patch.object(client, "deployment", return_value={"region": "us-east-2", "runtime_arn": "test-arn"}), \
             patch.object(client, "runtime_client", return_value=api):
            received = list(client.invoke_events("Albany", "11111111-1111-4111-8111-111111111111"))
        self.assertEqual(received, events)
        stream.close.assert_called_once()


class BrowserBridgeTests(unittest.TestCase):
    def setUp(self):
        self.http = TestClient(serve.app)

    def test_bridge_streams_runtime_events_without_browser_credentials(self):
        events = [{"type": "runtime", "instance_id": "test-process"}, {"type": "final", "text": "done"}]
        with patch.object(serve, "invoke_events", return_value=iter(events)) as invoke:
            response = self.http.post("/run", json={"goal": "Albany", "session_id": "11111111-1111-4111-8111-111111111111"})
        received = [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:")]
        self.assertEqual(received, events)
        invoke.assert_called_once()
        self.assertEqual(response.headers["cache-control"], "no-cache")

    def test_other_origins_cannot_invoke_the_local_bridge(self):
        with patch.object(serve, "invoke_events") as invoke:
            response = self.http.post("/run", headers={"Origin": "https://other.example"},
                                      json={"goal": "Albany", "session_id": "11111111-1111-4111-8111-111111111111"})
        self.assertEqual(response.status_code, 403)
        invoke.assert_not_called()


class TracingTests(unittest.TestCase):
    def setUp(self):
        self.exporter = InMemorySpanExporter()
        self.provider = TracerProvider()
        self.provider.add_span_processor(SimpleSpanProcessor(self.exporter))
        self.patch = patch.object(telemetry, "tracer", self.provider.get_tracer("test"))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.addCleanup(self.provider.shutdown)

    def test_stream_context_switches_preserve_parentage_and_safe_model_tool_spans(self):
        api = MagicMock()
        api.converse.return_value = {
            "output": {"message": {"content": [{"text": "private-response"}]}},
            "usage": {"inputTokens": 12, "outputTokens": 4}}
        provider = BedrockProvider(client=api, model_id="test-profile")

        @telemetry.trace_invocation
        def stream(payload, context):
            yield {"type": "runtime"}
            def loop_events():
                provider.reason([{"role": "user", "content": "private-prompt"}], [])
                yield {"type": "reason", "tool": {"name": "geocode_place", "args": {"query": "private-city"}}}
                yield {"type": "tool_result", "ok": True, "result": "private-tool-value"}
                yield {"type": "final", "text": "private-response"}
            yield from telemetry.trace_events(loop_events())

        events = stream({}, SimpleNamespace(session_id="test-session"))
        received = []
        while True:
            try:
                received.append(contextvars.Context().run(next, events))
            except StopIteration:
                break
        spans = self.exporter.get_finished_spans()
        root = next(s for s in spans if s.attributes.get("gen_ai.operation.name") == "invoke_agent")
        model = next(s for s in spans if s.attributes.get("gen_ai.operation.name") == "chat")
        tool = next(s for s in spans if s.attributes.get("gen_ai.operation.name") == "execute_tool")
        self.assertEqual(model.parent.span_id, root.context.span_id)
        self.assertEqual(tool.parent.span_id, root.context.span_id)
        self.assertEqual(root.attributes["session.id"], "test-session")
        self.assertEqual(model.attributes["gen_ai.usage.input_tokens"], 12)
        self.assertEqual(received[0]["trace_id"], format(root.context.trace_id, "032x"))
        serialized = str([(dict(s.attributes), s.events) for s in spans])
        for value in ("private-prompt", "private-city", "private-tool-value", "private-response"):
            self.assertNotIn(value, serialized)

    def test_trace_errors_do_not_capture_secret_bearing_exception_messages(self):
        with self.assertRaises(ValueError):
            with telemetry.safe_span("tool", {}):
                raise ValueError("https://example.test?apiKey=private-secret")
        span = self.exporter.get_finished_spans()[0]
        self.assertEqual(span.status.status_code.name, "ERROR")
        self.assertEqual(span.attributes["error.type"], "ValueError")
        self.assertFalse(span.events)
        self.assertNotIn("private-secret", str(span.attributes))

    def test_trace_delivery_policy_preserves_existing_access_and_scopes_new_access(self):
        api = MagicMock()
        original = {"Sid": "ExistingAccess", "Effect": "Allow", "Action": "logs:PutLogEvents",
                    "Principal": {"Service": "delivery.logs.amazonaws.com"}, "Resource": "existing"}
        api.describe_resource_policies.return_value = {"resourcePolicies": [{
            "policyDocument": json.dumps({"Version": "2012-10-17", "Statement": [original]}),
            "revisionId": "3"}]}
        config = {"region": "us-east-2", "account_id": "123456789012"}
        headers = deploy.ensure_trace_delivery(api, config, "test-runtime")
        request = api.put_resource_policy.call_args.kwargs
        policy = json.loads(request["policyDocument"])
        self.assertEqual(policy["Statement"][0], original)
        self.assertEqual(policy["Statement"][1]["Resource"], request["resourceArn"] + ":*")
        self.assertEqual(policy["Statement"][1]["Condition"]["StringEquals"]["aws:SourceAccount"], config["account_id"])
        self.assertEqual(request["expectedRevisionId"], "3")
        self.assertNotIn("policyName", request)
        self.assertIn("test-runtime-DEFAULT", headers)


if __name__ == "__main__":
    unittest.main()

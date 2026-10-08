"""The real OpenAI SDK against a stub that speaks the OpenAI streaming protocol, plus the SSE endpoint."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest

from app import main
from app.config import Config
from app.llm import llm_api_call_streamed
from test_agent import FakeApi, ScriptedLlm, turn


def chunk(delta, finish=None):
    return {"id": "x", "object": "chat.completion.chunk", "created": 0, "model": "m",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


TEXT_STREAM = [chunk({"role": "assistant", "content": ""}), chunk({"content": "Hel"}), chunk({"content": "lo!"}), chunk({}, "stop")]
TOOL_STREAM = [
    chunk({"role": "assistant", "content": "Let me check. "}),
    chunk({"tool_calls": [{"index": 0, "id": "call_1", "type": "function", "function": {"name": "search_visitors", "arguments": ""}}]}),
    chunk({"tool_calls": [{"index": 0, "function": {"arguments": '{"query":'}}]}),
    chunk({"tool_calls": [{"index": 0, "function": {"arguments": ' "ana"}'}}]}),
    chunk({"tool_calls": [{"index": 1, "id": "call_2", "type": "function", "function": {"name": "get_working_hours", "arguments": "{}"}}]}),
    chunk({}, "tool_calls"),
]


@pytest.fixture
def stub_llm():
    seen = {"requests": [], "stream": TEXT_STREAM}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen["requests"].append((self.path, self.headers.get("Authorization"), body))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for c in seen["stream"]:
                self.wfile.write(f"data: {json.dumps(c)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    seen["url"] = f"http://127.0.0.1:{server.server_port}/v1/"
    yield seen
    server.shutdown()


def test_streams_text_deltas_and_builds_the_message(stub_llm):
    events = list(llm_api_call_streamed("claude-test", stub_llm["url"], [{"role": "user", "content": "hi"}], "secret"))
    assert [e["delta"] for e in events if e["type"] == "text"] == ["Hel", "lo!"]
    assert events[-1] == {"type": "message", "message": {"role": "assistant", "content": "Hello!"}}
    path, auth, body = stub_llm["requests"][0]
    assert path == "/v1/chat/completions" and auth == "Bearer secret"
    assert body["model"] == "claude-test" and body["stream"] is True and "tools" not in body


def test_reassembles_streamed_tool_calls(stub_llm):
    stub_llm["stream"] = TOOL_STREAM
    tools = [{"type": "function", "function": {"name": "search_visitors", "parameters": {"type": "object", "properties": {}}}}]
    events = list(llm_api_call_streamed("m", stub_llm["url"], [{"role": "user", "content": "x"}], "k", tools))
    message = events[-1]["message"]
    assert message["content"] == "Let me check. "
    assert [(c["id"], c["function"]["name"], c["function"]["arguments"]) for c in message["tool_calls"]] == [
        ("call_1", "search_visitors", '{"query": "ana"}'), ("call_2", "get_working_hours", "{}")]
    assert stub_llm["requests"][0][2]["tools"] == tools


def sse_events(text):
    return [json.loads(line[6:]) for line in text.split("\n\n") if line.startswith("data: ")]


def test_stream_chat_end_to_end_with_stub_model_and_fake_api(stub_llm):
    stub_llm["stream"] = TEXT_STREAM
    cfg = Config(api_key="k", base_url=stub_llm["url"], model="m", api_base_url="http://api", enable_patient_notes=True)
    body = main.ChatRequest(messages=[{"role": "user", "content": "hi"}])
    api = FakeApi()
    out = "".join(main.stream_chat(body, "Bearer tok", cfg, transport=httpx.MockTransport(api)))
    events = sse_events(out)
    assert [e["type"] for e in events] == ["text", "text", "done"]
    assert api.requests[0].headers["authorization"] == "Bearer tok"  # staff token is forwarded


def test_stream_chat_reports_missing_key_and_bad_input():
    cfg = Config(api_key="", base_url="x", model="m", api_base_url="http://api", enable_patient_notes=True)
    ok = main.ChatRequest(messages=[{"role": "user", "content": "hi"}])
    assert "API key is not set" in sse_events("".join(main.stream_chat(ok, None, cfg)))[0]["message"]
    bad = main.ChatRequest(messages=[{"role": "system", "content": "x"}])
    assert sse_events("".join(main.stream_chat(bad, None, cfg)))[0]["type"] == "error"


def test_provider_failures_do_not_leak_details():
    cfg = Config(api_key="k", base_url="x", model="m", api_base_url="http://api", enable_patient_notes=True)

    def boom(messages, tools):
        raise RuntimeError("sk-ant-SECRET invalid x-api-key")
        yield  # pragma: no cover

    body = main.ChatRequest(messages=[{"role": "user", "content": "hi"}])
    out = "".join(main.stream_chat(body, None, cfg, llm=boom, transport=httpx.MockTransport(FakeApi())))
    assert "SECRET" not in out and sse_events(out)[0]["type"] == "error"


def test_health_endpoint():
    from fastapi.testclient import TestClient
    res = TestClient(main.app).get("/health")
    assert res.status_code == 200 and res.json()["ok"] is True

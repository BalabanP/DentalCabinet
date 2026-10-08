import io
import json

import pytest

import lambda_entry
from app import config, main
from app.config import Config
from app.lambda_transport import LambdaTransport
from test_agent import SETTINGS, turn

CLAIMS = {"sub": "user-1", "cognito:groups": "[reception]"}


class FakeLambdaClient:
    """Stands in for boto3's lambda client; plays the backend API."""

    def __init__(self):
        self.invocations: list[tuple[str, dict]] = []

    def invoke(self, FunctionName, Payload):
        event = json.loads(Payload)
        self.invocations.append((FunctionName, event))
        path = event["rawPath"]
        bodies = {
            "/settings/reminders": (200, SETTINGS),
            "/users": (200, {"items": [{"user_id": "u1", "name": "Ana Pop"}], "next_token": None}),
            "/appointments": (201, {"appointment_id": "a1"}),
        }
        status, body = bodies.get(path, (404, {"message": "not found"}))
        return {"Payload": io.BytesIO(json.dumps({"statusCode": status, "body": json.dumps(body)}).encode())}


def test_transport_builds_an_api_gateway_event_with_the_users_claims():
    fake = FakeLambdaClient()
    import httpx

    with httpx.Client(base_url="http://x", transport=LambdaTransport("api-fn", CLAIMS, fake)) as api:
        res = api.get("/users", params={"q": "ana", "limit": 5})
        assert res.status_code == 200 and res.json()["items"][0]["name"] == "Ana Pop"
        api.post("/appointments", json={"user_id": "u1"})

    name, event = fake.invocations[0]
    assert name == "api-fn" and event["rawPath"] == "/users" and event["rawQueryString"] == "q=ana&limit=5"
    assert event["queryStringParameters"] == {"q": "ana", "limit": "5"}
    assert event["requestContext"]["authorizer"]["jwt"]["claims"] == CLAIMS
    assert event["requestContext"]["http"]["method"] == "GET"
    post = fake.invocations[1][1]
    assert post["requestContext"]["http"]["method"] == "POST" and json.loads(post["body"]) == {"user_id": "u1"}


def test_transport_keeps_encoded_ids_and_maps_lambda_failures():
    import httpx

    class Broken(FakeLambdaClient):
        def invoke(self, FunctionName, Payload):
            return {"FunctionError": "Unhandled", "Payload": io.BytesIO(b'{"errorMessage": "boom"}')}

    fake = FakeLambdaClient()
    with httpx.Client(base_url="http://x", transport=LambdaTransport("f", CLAIMS, fake)) as api:
        api.get("/users/a%2Fb")
    assert fake.invocations[0][1]["rawPath"] == "/users/a%2Fb"

    with httpx.Client(base_url="http://x", transport=LambdaTransport("f", CLAIMS, Broken())) as api:
        res = api.get("/users")
    assert res.status_code == 502 and "boom" not in res.text


def apigw_event(body, claims=CLAIMS, path="/chat", method="POST"):
    return {
        "version": "2.0", "routeKey": f"{method} {path}", "rawPath": path, "rawQueryString": "",
        "headers": {"content-type": "application/json", "authorization": "Bearer t"},
        "requestContext": {
            "accountId": "1", "apiId": "x", "domainName": "x", "domainPrefix": "x",
            "http": {"method": method, "path": path, "protocol": "HTTP/1.1", "sourceIp": "1.1.1.1", "userAgent": "t"},
            "requestId": "r", "routeKey": f"{method} {path}", "stage": "$default", "time": "", "timeEpoch": 0,
            **({"authorizer": {"jwt": {"claims": claims, "scopes": None}}} if claims else {}),
        },
        "body": json.dumps(body), "isBase64Encoded": False,
    }


@pytest.fixture
def lambda_env(monkeypatch):
    fake = FakeLambdaClient()
    monkeypatch.setenv("API_FUNCTION_NAME", "api-fn")
    monkeypatch.setattr(main, "LambdaTransport", lambda name, claims: LambdaTransport(name, claims, fake))
    monkeypatch.setattr(main, "get_config", lambda: Config(
        api_key="k", base_url="x", model="m", api_base_url="http://unused", enable_patient_notes=True))
    return fake


def test_lambda_entry_runs_a_chat_turn_with_the_verified_claims(lambda_env, monkeypatch):
    turns = [turn(calls=[("c1", "search_visitors", {"query": "ana"})]), turn("Found Ana Pop.")]

    def scripted(model, base_url, messages, api_key, tools=None):
        yield from turns.pop(0)

    monkeypatch.setattr(main, "llm_api_call_streamed", scripted)
    res = lambda_entry.handler(apigw_event({"messages": [{"role": "user", "content": "find ana"}]}), None)

    assert res["statusCode"] == 200 and res["headers"]["content-type"].startswith("text/event-stream")
    events = [json.loads(line[6:]) for line in res["body"].split("\n\n") if line.startswith("data: ")]
    assert [e["type"] for e in events] == ["tool_start", "tool_result", "text", "done"]
    # every backend call carried the signed-in user's claims, never a header-supplied identity
    assert lambda_env.invocations and all(
        ev["requestContext"]["authorizer"]["jwt"]["claims"] == CLAIMS for _, ev in lambda_env.invocations)


def test_lambda_entry_rejects_requests_without_verified_claims(lambda_env):
    res = lambda_entry.handler(apigw_event({"messages": [{"role": "user", "content": "x"}]}, claims=None), None)
    assert res["statusCode"] == 401 and lambda_env.invocations == []


def test_health_works_under_the_chat_prefix(lambda_env):
    res = lambda_entry.handler(apigw_event({}, path="/chat/health", method="GET") | {"body": None}, None)
    assert res["statusCode"] == 200 and json.loads(res["body"])["api_key_set"] is True


def test_api_key_is_read_from_secrets_manager(monkeypatch):
    calls = []

    class Secrets:
        def get_secret_value(self, SecretId):
            calls.append(SecretId)
            return {"SecretString": " sk-from-secret \n"}

    import boto3

    monkeypatch.setattr(boto3, "client", lambda name, **kw: Secrets())
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("LLM_API_KEY_SECRET_ARN", "arn:secret")
    config._secret_value.cache_clear()
    assert config.load_config().api_key == "sk-from-secret"
    assert config.load_config().api_key == "sk-from-secret" and calls == ["arn:secret"]  # cached per container


def test_empty_secret_means_no_key_yet(monkeypatch):
    from botocore.exceptions import ClientError

    class Secrets:
        def get_secret_value(self, SecretId):
            raise ClientError({"Error": {"Code": "ResourceNotFoundException", "Message": "x"}}, "GetSecretValue")

    import boto3

    monkeypatch.setattr(boto3, "client", lambda name, **kw: Secrets())
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("LLM_API_KEY_SECRET_ARN", "arn:secret2")
    config._secret_value.cache_clear()
    assert config.load_config().api_key == ""

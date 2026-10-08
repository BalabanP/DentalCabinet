"""httpx transport that calls the backend API *Lambda* directly instead of going over HTTP.

The chat Lambda sits behind API Gateway, which has already verified the user's Cognito token. We pass
those verified claims on in a synthetic API Gateway event, so the backend applies the user's own role.
Only the chat Lambda's IAM role may invoke the API Lambda, which is what makes trusting the claims safe.
"""

import json

import httpx


class LambdaTransport(httpx.BaseTransport):
    def __init__(self, function_name: str, claims: dict, client=None):
        self.function_name = function_name
        self.claims = claims
        if client is None:
            import boto3

            client = boto3.client("lambda")
        self.client = client

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        raw_path, _, raw_query = request.url.raw_path.decode().partition("?")
        event = {
            "version": "2.0",
            "routeKey": "$default",
            "rawPath": raw_path,
            "rawQueryString": raw_query,
            "headers": {"content-type": "application/json"},
            "queryStringParameters": dict(request.url.params) or None,
            "requestContext": {
                "accountId": "internal", "apiId": "internal", "domainName": "internal", "domainPrefix": "internal",
                "http": {"method": request.method, "path": raw_path, "protocol": "HTTP/1.1",
                         "sourceIp": "0.0.0.0", "userAgent": "chat-lambda"},
                "requestId": "chat", "routeKey": "$default", "stage": "$default", "time": "", "timeEpoch": 0,
                "authorizer": {"jwt": {"claims": self.claims, "scopes": None}},
            },
            "body": request.content.decode() if request.content else None,
            "isBase64Encoded": False,
        }
        try:
            res = self.client.invoke(FunctionName=self.function_name, Payload=json.dumps(event).encode())
            payload = json.loads(res["Payload"].read())
        except Exception as exc:  # noqa: BLE001
            raise httpx.ConnectError("backend lambda invocation failed") from exc
        if "FunctionError" in res or "statusCode" not in payload:
            return httpx.Response(502, json={"message": "backend error"})
        return httpx.Response(
            payload["statusCode"],
            content=(payload.get("body") or "").encode(),
            headers={"content-type": "application/json"},
        )

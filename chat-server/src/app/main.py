"""Chat HTTP service: POST /chat returns Server-Sent Events.

Locally:  uvicorn app.main:app --app-dir src --port 8100   (calls the backend over HTTP, streams)
On AWS:   lambda_entry.handler behind API Gateway          (calls the backend Lambda directly, buffered)
"""

import json
import logging
import os
from typing import Iterator

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from . import agent
from .config import Config, load_config
from .lambda_transport import LambdaTransport
from .llm import llm_api_call_streamed

log = logging.getLogger("chat")
app = FastAPI(title="Medical Cabinet chat")

IN_LAMBDA = bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))
if not IN_LAMBDA:  # on AWS, API Gateway answers CORS itself
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["POST", "GET"],
        allow_headers=["*"],
    )


class ChatRequest(BaseModel):
    messages: list[dict]
    decisions: dict[str, bool] | None = None


def get_config() -> Config:
    return load_config()


def sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def stream_chat(body: ChatRequest, auth: str | None, cfg: Config, llm=None, transport=None) -> Iterator[str]:
    error = agent.validate_messages(body.messages, cfg)
    if error:
        yield sse({"type": "error", "message": error})
        return
    if llm is None:
        if not cfg.api_key:
            yield sse({"type": "error", "message": "The LLM API key is not set. See chat-server/.env.example "
                                                   "(local) or the ChatApiKeySecret (AWS)."})
            return
        llm = lambda messages, tools: llm_api_call_streamed(  # noqa: E731
            cfg.model, cfg.base_url, messages, cfg.api_key, tools)

    headers = {"Authorization": auth} if auth else {}
    try:
        with httpx.Client(base_url=cfg.api_base_url, headers=headers, timeout=20, transport=transport) as api:
            for event in agent.run_chat(body.messages, body.decisions, api=api, cfg=cfg, llm=llm):
                yield sse(event)
    except Exception:  # noqa: BLE001 - never leak provider/stack details to the browser
        log.exception("chat failed")
        yield sse({"type": "error", "message": "Something went wrong talking to the model. Check the chat-server log."})


def build_transport(request: Request):
    """On AWS: call the backend Lambda with the claims API Gateway verified. Locally: plain HTTP (None)."""
    function_name = os.environ.get("API_FUNCTION_NAME")
    if not function_name:
        return None
    event = request.scope.get("aws.event") or {}
    claims = ((event.get("requestContext") or {}).get("authorizer") or {}).get("jwt", {}).get("claims")
    if not claims:
        raise HTTPException(status_code=401, detail="not authenticated")
    return LambdaTransport(function_name, claims)


@app.post("/chat")
def chat(body: ChatRequest, request: Request):
    transport = build_transport(request)
    return StreamingResponse(
        stream_chat(body, request.headers.get("authorization"), get_config(), transport=transport),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/health")
@app.get("/chat/health")
def health():
    cfg = get_config()
    return {"ok": True, "model": cfg.model, "api_key_set": bool(cfg.api_key), "patient_notes": cfg.enable_patient_notes}

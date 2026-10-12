"""HTTP API (API Gateway v2 + Cognito JWT authorizer) for users, patients, appointments and settings."""

import base64
import json
import re
from datetime import datetime, timedelta

import os

from aws_lambda_powertools.event_handler import APIGatewayHttpResolver, CORSConfig, Response, content_types
from pydantic import ValidationError

from app import notifications, replies, repositories as repo, services
from app.errors import AppError, BadRequest, Forbidden, NotFound
from app.log import logger
from app.models import (
    AppointmentIn,
    AppointmentPatch,
    MessageTemplates,
    PatientIn,
    ReminderSettings,
    PLACEHOLDERS,
    ScheduleSettings,
    TemplatePreviewIn,
    UserIn,
    UserPatch,
)
from app.timeutil import days_between, utc_now

# CORS is handled here, not by API Gateway: the catch-all route with a login authorizer makes the gateway's
# own CORS setting unreliable for browser pre-checks. `CORS_ALLOW_ORIGIN` is one origin, "*", or a
# comma-separated list. The API uses bearer tokens (no cookies), so "*" does not expose anything extra.
_origins = [o.strip() for o in os.environ.get("CORS_ALLOW_ORIGIN", "*").split(",") if o.strip()] or ["*"]
app = APIGatewayHttpResolver(
    cors=CORSConfig(
        allow_origin=_origins[0],
        extra_origins=_origins[1:] or None,
        allow_headers=["Authorization", "Content-Type"],
        max_age=3600,
    )
)

ADMIN = {"admin"}
CLINICAL = {"clinician", "admin"}  # may read/write patient notes
STAFF = {"reception", "clinician", "admin"}


# ---------- auth helpers ----------


def _claims() -> dict:
    return app.current_event.request_context.authorizer.jwt_claim or {}


def _groups() -> set[str]:
    raw = _claims().get("cognito:groups", [])
    if isinstance(raw, str):  # HTTP API serialises array claims as "[a b]"
        raw = [g for g in re.split(r"[\s,\[\]]+", raw) if g]
    return set(raw)


def require(allowed: set[str]) -> None:
    if not _groups() & allowed:
        raise Forbidden("insufficient permissions")


def _actor() -> str:
    return str(_claims().get("sub", "unknown"))


# ---------- request/response helpers ----------


def _body() -> dict:
    body = app.current_event.json_body
    if not isinstance(body, dict):
        raise BadRequest("request body must be a JSON object")
    return body


def _json(status: int, data) -> Response:
    return Response(status_code=status, content_type=content_types.APPLICATION_JSON, body=json.dumps(data))


def _int_param(name: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(app.current_event.get_query_string_value(name, str(default)))
    except ValueError as exc:
        raise BadRequest(f"{name} must be an integer") from exc
    if not lo <= value <= hi:
        raise BadRequest(f"{name} must be between {lo} and {hi}")
    return value


@app.exception_handler(AppError)
def _app_error(exc: AppError):
    return _json(exc.status_code, {"message": exc.message})


@app.exception_handler(ValidationError)
def _validation_error(exc: ValidationError):
    # include_input=False: never echo submitted (possibly personal) values back or into logs
    errors = exc.errors(include_url=False, include_context=False, include_input=False)
    return _json(422, {"message": "validation failed", "errors": errors})


@app.exception_handler(json.JSONDecodeError)
def _bad_json(_exc):
    return _json(400, {"message": "request body is not valid JSON"})


@app.exception_handler(Exception)
def _unexpected(exc: Exception):
    logger.exception("unhandled error")
    return _json(500, {"message": "internal error"})


# ---------- users ----------


@app.post("/users")
def create_user():
    require(STAFF)
    user = repo.create_user(UserIn.model_validate(_body()).model_dump())
    return _json(201, user)


@app.get("/users")
def list_users():
    require(STAFF)
    get = app.current_event.get_query_string_value
    email, q = get("email"), get("q")
    if email:
        user = repo.find_user_by_email(email)
        return {"items": [user] if user else [], "next_token": None}
    if q:
        if len(q.strip()) < 2:
            raise BadRequest("q must have at least 2 characters")
        return {"items": repo.search_users(q, _int_param("limit", 25, 1, 100)), "next_token": None}
    items, token = repo.list_users(
        _int_param("limit", 50, 1, 100), app.current_event.get_query_string_value("next_token")
    )
    return {"items": items, "next_token": token}


@app.get("/users/<user_id>")
def get_user(user_id: str):
    require(STAFF)
    user = repo.get_user(user_id)
    if not user:
        raise NotFound("user not found")
    return user


@app.patch("/users/<user_id>")
def update_user(user_id: str):
    require(STAFF)
    changes = UserPatch.model_validate(_body()).model_dump(exclude_unset=True)
    if not changes:
        raise BadRequest("nothing to update")
    return repo.update_user(user_id, changes)


@app.delete("/users/<user_id>")
def delete_user(user_id: str):
    require(STAFF)
    services.delete_user(user_id)
    return Response(status_code=204, body="")


# ---------- patients (clinical notes) ----------


@app.get("/users/<user_id>/patient")
def get_patient(user_id: str):
    require(CLINICAL)
    if not repo.get_user(user_id):
        raise NotFound("user not found")
    return repo.get_patient(user_id) or {"user_id": user_id, "notes": ""}


@app.put("/users/<user_id>/patient")
def put_patient(user_id: str):
    require(CLINICAL)
    if not repo.get_user(user_id):
        raise NotFound("user not found")
    data = PatientIn.model_validate(_body())
    return repo.upsert_patient(user_id, data.notes, _actor())


# ---------- appointments ----------


@app.post("/appointments")
def create_appointment():
    require(STAFF)
    return _json(201, services.book(AppointmentIn.model_validate(_body())))


@app.get("/appointments")
def list_appointments():
    require(STAFF)
    get = app.current_event.get_query_string_value
    status = get("status")
    if get("user_id"):
        items = repo.query_appointments_by_user(get("user_id"))
    else:
        tz = repo.get_reminder_settings().tz
        today = utc_now().astimezone(tz).date().isoformat()
        try:
            days = days_between(get("from", today), get("to", get("from", today)))
        except ValueError as exc:
            raise BadRequest(str(exc)) from exc
        items = repo.query_appointments_by_days(days)
    if status:
        items = [a for a in items if a["status"] == status]
    return {"items": items}


@app.get("/appointments/<appointment_id>")
def get_appointment(appointment_id: str):
    require(STAFF)
    appt = repo.get_appointment(appointment_id)
    if not appt:
        raise NotFound("appointment not found")
    return appt


@app.patch("/appointments/<appointment_id>")
def patch_appointment(appointment_id: str):
    require(STAFF)
    return services.update(appointment_id, AppointmentPatch.model_validate(_body()))


@app.post("/appointments/<appointment_id>/cancel")
def cancel_appointment(appointment_id: str):
    require(STAFF)
    return services.cancel(appointment_id)


@app.post("/appointments/<appointment_id>/confirm")
def confirm_appointment(appointment_id: str):
    """Staff mark a visit confirmed, e.g. after a phone call."""
    require(STAFF)
    return services.confirm(appointment_id, via="staff")


@app.get("/appointments/<appointment_id>/notifications")
def appointment_notifications(appointment_id: str):
    require(STAFF)
    if not repo.get_appointment(appointment_id):
        raise NotFound("appointment not found")
    return {"items": repo.list_notifications(appointment_id)}


# ---------- visitor SMS replies ----------


@app.post("/webhooks/sms/<token>")
def sms_webhook(token: str):
    """Called by the SMS gateway app when the phone receives an SMS. No login: the secret in the URL and the
    gateway's HMAC signature protect it. Answers 404 to anyone who does not know the secret."""
    cfg = replies.webhook_config()
    event = app.current_event
    raw = event.body or ""
    body = base64.b64decode(raw) if event.is_base64_encoded else raw.encode()
    if not cfg or not replies.authentic(
        cfg,
        token,
        body,
        event.get_header_value("x-signature", case_sensitive=False),
        event.get_header_value("x-timestamp", case_sensitive=False),
    ):
        return _json(404, {"message": "not found"})

    data = json.loads(body or b"{}")
    if data.get("event") == "sms:received":
        payload = data.get("payload") or {}
        message_id = str(payload.get("messageId") or data.get("id") or "")
        if message_id and payload.get("sender"):
            replies.handle_incoming(
                str(payload["sender"]), str(payload.get("message") or ""), message_id, payload.get("receivedAt")
            )
    return _json(200, {"ok": True})  # any 2xx stops the gateway's retries, including for events we ignore


@app.get("/sms-replies")
def list_sms_replies():
    require(STAFF)
    return {"items": repo.list_replies()}


@app.delete("/sms-replies/<reply_id>")
def dismiss_sms_reply(reply_id: str):
    require(STAFF)
    repo.dismiss_reply(reply_id)
    return Response(status_code=204, body="")


# ---------- availability ----------


@app.get("/availability")
def availability():
    require(STAFF)
    get = app.current_event.get_query_string_value
    if not get("from"):
        raise BadRequest("'from' (YYYY-MM-DD) is required")
    duration = _int_param("duration_minutes", 0, 0, 480) or None
    if duration is not None and duration < 5:
        raise BadRequest("duration_minutes must be between 5 and 480")
    return services.free_slots(get("from"), get("to"), duration)


@app.get("/settings/schedule")
def get_schedule():
    require(STAFF)
    return repo.get_schedule_settings().model_dump(mode="json")


@app.put("/settings/schedule")
def put_schedule():
    require(ADMIN)
    return repo.put_schedule_settings(ScheduleSettings.model_validate(_body())).model_dump(mode="json")


# ---------- message texts (e-mail and SMS) ----------


def _templates_response(templates: MessageTemplates) -> dict:
    return {
        "placeholders": list(PLACEHOLDERS),
        "defaults": notifications.all_defaults(),
        "overrides": templates.model_dump(mode="json", exclude_none=True)["overrides"],
    }


@app.get("/settings/messages")
def get_messages():
    require(STAFF)
    return _templates_response(repo.get_message_templates())


@app.put("/settings/messages")
def put_messages():
    require(ADMIN)
    return _templates_response(repo.put_message_templates(MessageTemplates.model_validate(_body())))


@app.post("/settings/messages/preview")
def preview_message():
    """Render one message with sample data, exactly as visitors will receive it. Saves nothing."""
    require(STAFF)
    data = TemplatePreviewIn.model_validate(_body())
    settings = repo.get_reminder_settings()
    sample_start = (utc_now().astimezone(settings.tz) + timedelta(days=3)).replace(hour=10, minute=0, second=0, microsecond=0)
    fields = {k: v for k, v in data.model_dump(exclude={"kind", "locale"}).items() if v}
    templates = MessageTemplates.model_validate({"overrides": {data.kind: {data.locale: fields}}})
    sample_user = {"name": "Ana Pop", "locale": data.locale}
    rendered = notifications.render(data.kind, sample_user, sample_start, settings, templates)
    return {
        "email_subject": rendered.subject,
        "email_html": rendered.html,
        "email_text": rendered.text,
        "sms_text": notifications.sms_text(rendered.sms),
        "sms": notifications.sms_info(rendered.sms),
    }


# ---------- reminder settings ----------


@app.get("/settings/reminders")
def get_settings():
    require(STAFF)
    return repo.get_reminder_settings().model_dump(mode="json")


@app.put("/settings/reminders")
def put_settings():
    require(ADMIN)
    settings = repo.put_reminder_settings(ReminderSettings.model_validate(_body()))
    return settings.model_dump(mode="json")


@app.post("/settings/reminders/apply-to-existing")
def apply_settings_to_existing():
    require(ADMIN)
    return services.reapply_reminders()


@logger.inject_lambda_context
def handler(event, context):
    return app.resolve(event, context)

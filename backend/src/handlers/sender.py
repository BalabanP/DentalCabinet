"""SQS consumer: turns queued reminder/notice messages into e-mails and WhatsApp messages."""

import json

from app import notifications, repositories as repo
from app.log import logger
from app.timeutil import parse_iso, utc_now

# kinds that only make sense while the appointment is still scheduled
ACTIVE_KINDS = {"confirmation", "reminder", "rescheduled"}


def _channels(settings, user) -> list[str]:
    channels = []
    if settings.channels.email and user.get("notify_email", True) and user.get("email"):
        channels.append("email")
    if settings.channels.whatsapp and user.get("notify_whatsapp", True) and user.get("phone"):
        channels.append("whatsapp")
    return channels


def process(msg: dict) -> None:
    appointment_id, kind, start_iso = msg["appointment_id"], msg["kind"], msg["start_at"]
    appt = repo.get_appointment(appointment_id)
    if not appt:
        logger.info("appointment gone, dropping", extra={"appointment_id": appointment_id})
        return

    if kind == "cancelled":
        if appt["status"] != "cancelled":
            return
    elif appt["status"] != "scheduled" or appt["start_at"] != start_iso:
        logger.info("stale message, dropping", extra={"appointment_id": appointment_id, "kind": kind})
        return

    start = parse_iso(start_iso)
    if start <= utc_now():
        return  # visit already started; a reminder would be noise

    user = repo.get_user(appt["user_id"])
    if not user:
        return
    settings = repo.get_reminder_settings()
    rendered = notifications.render(kind, user, start, settings)

    errors = []
    for channel in _channels(settings, user):
        sk = f"{msg['dedupe']}#{channel}"
        if not repo.claim_notification(appointment_id, sk, kind, channel):
            continue  # already sent (SQS redelivery) or in flight
        if channel == "whatsapp" and not notifications.whatsapp_enabled():
            repo.finish_notification(appointment_id, sk, "skipped", error="whatsapp not configured")
            continue
        try:
            if channel == "email":
                provider_id = notifications.send_email(user["email"], rendered)
            else:
                provider_id = notifications.send_whatsapp(user["phone"], rendered)
            repo.finish_notification(appointment_id, sk, "sent", provider_id=provider_id)
        except Exception as exc:  # noqa: BLE001 - recorded, then retried through SQS
            logger.exception("send failed", extra={"appointment_id": appointment_id, "channel": channel})
            repo.finish_notification(appointment_id, sk, "failed", error=type(exc).__name__)
            errors.append(channel)
    if errors:
        raise RuntimeError(f"delivery failed for: {', '.join(errors)}")


@logger.inject_lambda_context
def handler(event, context):
    failures = []
    for record in event["Records"]:
        try:
            process(json.loads(record["body"]))
        except Exception:  # noqa: BLE001
            logger.exception("message failed", extra={"message_id": record["messageId"]})
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}

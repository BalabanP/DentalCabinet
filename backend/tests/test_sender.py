from datetime import timedelta

import pytest

from app import notifications, repositories as repo, services
from app.models import AppointmentIn, AppointmentPatch, ReminderSettings, UserIn
from app.reminders import KIND_CANCELLED, KIND_REMINDER, build_message
from app.timeutil import parse_iso, to_iso, utc_now
from handlers import sender


@pytest.fixture(autouse=True)
def _any_hour(monkeypatch):
    """These tests book relative to "now"; working-hours rules are covered in test_availability."""
    monkeypatch.setattr(services, "_ensure_working_hours", lambda *a, **k: None)


class Provider:
    def __init__(self):
        self.emails, self.smss = [], []
        self.sms_error = None

    def email(self, to, msg):
        self.emails.append((to, msg))
        return "email-id"

    def sms(self, phone, msg):
        if self.sms_error:
            raise self.sms_error
        self.smss.append((phone, msg))
        return "sms-id"


@pytest.fixture
def provider(monkeypatch):
    p = Provider()
    monkeypatch.setattr(notifications, "send_email", p.email)
    monkeypatch.setattr(notifications, "send_sms", p.sms)
    monkeypatch.setattr(notifications, "sms_enabled", lambda: True)
    return p


@pytest.fixture
def appt(aws):
    repo.put_reminder_settings(ReminderSettings(cabinet_name="Dr. Pop", timezone="Europe/Bucharest"))
    user = repo.create_user(UserIn(name="Ana", email="ana@example.com", phone="+40712345678", locale="ro").model_dump())
    start = utc_now() + timedelta(days=10)
    return services.book(AppointmentIn(user_id=user["user_id"], start_at=start))


def enable_sms():
    settings = repo.get_reminder_settings()
    settings.channels.sms = True
    repo.put_reminder_settings(settings)


def reminder_msg(appt, key="m1440"):
    return build_message(appt["appointment_id"], parse_iso(appt["start_at"]), KIND_REMINDER, key)


def statuses(appt):
    return {n["sk"].rsplit("#", 1)[1]: n["status"] for n in repo.list_notifications(appt["appointment_id"])}


def test_sends_on_both_channels_and_logs(provider, appt):
    enable_sms()
    sender.process(reminder_msg(appt))
    assert [to for to, _ in provider.emails] == ["ana@example.com"]
    assert [p for p, _ in provider.smss] == ["+40712345678"]
    assert statuses(appt) == {"email": "sent", "sms": "sent"}
    assert "Dr. Pop" in provider.emails[0][1].text


def test_redelivery_does_not_double_send(provider, appt):
    enable_sms()
    msg = reminder_msg(appt)
    sender.process(msg)
    sender.process(msg)
    assert len(provider.emails) == 1 and len(provider.smss) == 1


def test_consent_and_channel_flags_are_respected(provider, appt):
    enable_sms()
    repo.update_user(appt["user_id"], {"notify_sms": False})
    sender.process(reminder_msg(appt, "m1440"))
    assert len(provider.emails) == 1 and provider.smss == []

    settings = repo.get_reminder_settings()
    settings.channels.email = False
    repo.put_reminder_settings(settings)
    sender.process(reminder_msg(appt, "m120"))
    assert len(provider.emails) == 1 and provider.smss == []


def test_partial_failure_retries_only_the_failed_channel(provider, appt):
    enable_sms()
    provider.sms_error = ConnectionError("boom")
    msg = reminder_msg(appt)
    with pytest.raises(RuntimeError):
        sender.process(msg)
    assert statuses(appt) == {"email": "sent", "sms": "failed"}

    provider.sms_error = None
    sender.process(msg)
    assert len(provider.emails) == 1 and len(provider.smss) == 1
    assert statuses(appt) == {"email": "sent", "sms": "sent"}


def test_failure_log_records_the_aws_error_code_but_no_address(provider, appt, monkeypatch):
    from botocore.exceptions import ClientError

    def rejected(to, msg):
        raise ClientError(
            {"Error": {"Code": "MessageRejected", "Message": f"Email address is not verified: {to}"}}, "SendEmail"
        )

    monkeypatch.setattr(notifications, "send_email", rejected)
    with pytest.raises(RuntimeError):
        sender.process(reminder_msg(appt))
    logged = {n["sk"].rsplit("#", 1)[1]: n for n in repo.list_notifications(appt["appointment_id"])}
    assert logged["email"]["status"] == "failed" and logged["email"]["error"] == "MessageRejected"
    assert "@" not in logged["email"]["error"]


def test_stale_reminder_after_reschedule_is_dropped(provider, appt):
    old = reminder_msg(appt)
    services.update(appt["appointment_id"], AppointmentPatch(start_at=utc_now() + timedelta(days=20)))
    sender.process(old)
    assert provider.emails == [] and provider.smss == []


def test_reminder_for_cancelled_appointment_is_dropped_but_cancel_notice_is_sent(provider, appt):
    reminder = reminder_msg(appt)
    notice = build_message(appt["appointment_id"], parse_iso(appt["start_at"]), KIND_CANCELLED)
    services.cancel(appt["appointment_id"])
    sender.process(reminder)
    assert provider.emails == []
    sender.process(notice)
    assert len(provider.emails) == 1 and "anulat" in provider.emails[0][1].text


def test_unknown_appointment_is_ignored(provider, aws):
    sender.process({"appointment_id": "gone", "kind": "reminder", "start_at": to_iso(utc_now()), "dedupe": "x"})
    assert provider.emails == []


def test_handler_reports_only_failed_records(provider, appt):
    import json

    good = {"messageId": "1", "body": json.dumps(reminder_msg(appt))}
    bad = {"messageId": "2", "body": "not json"}
    assert sender.handler({"Records": [good, bad]}, type("C", (), {
        "function_name": "t", "memory_limit_in_mb": 1, "invoked_function_arn": "a", "aws_request_id": "r"})()) == {
        "batchItemFailures": [{"itemIdentifier": "2"}]
    }


# ---------- SMS channel ----------


def test_sms_is_off_by_default(provider, appt):
    sender.process(reminder_msg(appt))
    assert provider.smss == [] and "sms" not in statuses(appt)


def test_sms_is_sent_when_the_channel_is_on(provider, appt):
    enable_sms()
    sender.process(reminder_msg(appt))
    assert [p for p, _ in provider.smss] == ["+40712345678"]
    assert statuses(appt) == {"email": "sent", "sms": "sent"}


def test_sms_needs_the_visitors_consent_too(provider, appt):
    enable_sms()
    repo.update_user(appt["user_id"], {"notify_sms": False})
    sender.process(reminder_msg(appt))
    assert provider.smss == [] and len(provider.emails) == 1


def test_users_created_before_sms_existed_default_to_consenting(provider, appt):
    from app import clients
    from app.config import get_config

    enable_sms()
    # simulate a visitor stored before the field existed
    clients.table(get_config().users_table).update_item(
        Key={"user_id": appt["user_id"]}, UpdateExpression="REMOVE notify_sms"
    )
    assert "notify_sms" not in repo.get_user(appt["user_id"])
    sender.process(reminder_msg(appt))
    assert len(provider.smss) == 1


def test_sms_not_configured_is_skipped(provider, appt, monkeypatch):
    enable_sms()
    monkeypatch.setattr(notifications, "sms_enabled", lambda: False)
    sender.process(reminder_msg(appt))
    assert provider.smss == [] and statuses(appt)["sms"] == "skipped"


def test_sms_gateway_login_failure_is_logged_as_http_code_and_retried(provider, appt):
    import urllib.error

    enable_sms()
    provider.sms_error = urllib.error.HTTPError("https://gw", 401, "Unauthorized", {}, None)
    msg = reminder_msg(appt)
    with pytest.raises(RuntimeError):
        sender.process(msg)
    logged = {n["sk"].rsplit("#", 1)[1]: n for n in repo.list_notifications(appt["appointment_id"])}
    assert logged["sms"]["status"] == "failed" and logged["sms"]["error"] == "HTTP 401"
    assert logged["email"]["status"] == "sent"  # one failing channel does not undo the others

    provider.sms_error = None
    sender.process(msg)  # SQS retry: only the failed channel is sent again
    assert len(provider.emails) == 1 and len(provider.smss) == 1
    assert statuses(appt)["sms"] == "sent"


def test_reminder_sms_stops_asking_once_the_visit_is_confirmed(provider, appt):
    enable_sms()
    sender.process(reminder_msg(appt))
    assert "CONFIRM" in provider.smss[0][1].sms  # not confirmed yet: invited to answer

    services.confirm(appt["appointment_id"], via="sms")
    sender.process(reminder_msg(appt, key="m120"))
    assert "CONFIRM" not in provider.smss[1][1].sms and "ANULEAZA" not in provider.smss[1][1].sms

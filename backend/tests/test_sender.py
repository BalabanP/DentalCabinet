from datetime import timedelta

import pytest

from app import notifications, repositories as repo, services
from app.models import AppointmentIn, AppointmentPatch, ReminderSettings, UserIn
from app.reminders import KIND_CANCELLED, KIND_REMINDER, build_message
from app.timeutil import parse_iso, to_iso, utc_now
from handlers import sender


class Provider:
    def __init__(self):
        self.emails, self.whatsapps = [], []
        self.fail_whatsapp = False

    def email(self, to, msg):
        self.emails.append((to, msg))
        return "email-id"

    def whatsapp(self, phone, msg):
        if self.fail_whatsapp:
            raise ConnectionError("boom")
        self.whatsapps.append((phone, msg))
        return "wa-id"


@pytest.fixture
def provider(monkeypatch):
    p = Provider()
    monkeypatch.setattr(notifications, "send_email", p.email)
    monkeypatch.setattr(notifications, "send_whatsapp", p.whatsapp)
    return p


@pytest.fixture
def appt(aws):
    repo.put_reminder_settings(ReminderSettings(cabinet_name="Dr. Pop", timezone="Europe/Bucharest"))
    user = repo.create_user(UserIn(name="Ana", email="ana@example.com", phone="+40712345678", locale="ro").model_dump())
    start = utc_now() + timedelta(days=10)
    return services.book(AppointmentIn(user_id=user["user_id"], start_at=start))


def reminder_msg(appt, key="m1440"):
    return build_message(appt["appointment_id"], parse_iso(appt["start_at"]), KIND_REMINDER, key)


def statuses(appt):
    return {n["sk"].rsplit("#", 1)[1]: n["status"] for n in repo.list_notifications(appt["appointment_id"])}


def test_sends_on_both_channels_and_logs(provider, appt):
    sender.process(reminder_msg(appt))
    assert [to for to, _ in provider.emails] == ["ana@example.com"]
    assert [p for p, _ in provider.whatsapps] == ["+40712345678"]
    assert statuses(appt) == {"email": "sent", "whatsapp": "sent"}
    assert "Dr. Pop" in provider.emails[0][1].text


def test_redelivery_does_not_double_send(provider, appt):
    msg = reminder_msg(appt)
    sender.process(msg)
    sender.process(msg)
    assert len(provider.emails) == 1 and len(provider.whatsapps) == 1


def test_consent_and_channel_flags_are_respected(provider, appt):
    repo.update_user(appt["user_id"], {"notify_whatsapp": False})
    sender.process(reminder_msg(appt, "m1440"))
    assert len(provider.emails) == 1 and provider.whatsapps == []

    settings = repo.get_reminder_settings()
    settings.channels.email = False
    repo.put_reminder_settings(settings)
    sender.process(reminder_msg(appt, "m120"))
    assert len(provider.emails) == 1 and provider.whatsapps == []


def test_partial_failure_retries_only_the_failed_channel(provider, appt):
    provider.fail_whatsapp = True
    msg = reminder_msg(appt)
    with pytest.raises(RuntimeError):
        sender.process(msg)
    assert statuses(appt) == {"email": "sent", "whatsapp": "failed"}

    provider.fail_whatsapp = False
    sender.process(msg)
    assert len(provider.emails) == 1 and len(provider.whatsapps) == 1
    assert statuses(appt) == {"email": "sent", "whatsapp": "sent"}


def test_whatsapp_not_configured_is_skipped(provider, appt, monkeypatch):
    from app.config import get_config

    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "")
    get_config.cache_clear()
    sender.process(reminder_msg(appt))
    assert provider.whatsapps == [] and statuses(appt)["whatsapp"] == "skipped"


def test_stale_reminder_after_reschedule_is_dropped(provider, appt):
    old = reminder_msg(appt)
    services.update(appt["appointment_id"], AppointmentPatch(start_at=utc_now() + timedelta(days=20)))
    sender.process(old)
    assert provider.emails == [] and provider.whatsapps == []


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

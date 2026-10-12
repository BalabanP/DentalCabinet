import hashlib
import hmac
import json
from datetime import timedelta

import pytest

from app import replies, services
from app.timeutil import to_iso, utc_now
from conftest import queued_messages
from test_api import book, call, call_raw, make_user

TOKEN = "t" * 24


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    monkeypatch.setattr(services, "_ensure_working_hours", lambda *a, **k: None)
    monkeypatch.delenv("SMS_WEBHOOK_PARAMETER", raising=False)
    monkeypatch.setenv("SMS_WEBHOOK_TOKEN", TOKEN)
    replies._webhook_cache = None
    yield
    replies._webhook_cache = None


def sms_event(sender, text, message_id="m1"):
    return {
        "deviceId": "d", "event": "sms:received", "id": "evt", "webhookId": "w",
        "payload": {"messageId": message_id, "message": text, "sender": sender, "recipient": "+40000000000",
                    "receivedAt": "2026-10-12T10:00:00.000+03:00"},
    }


def post(body, token=TOKEN, headers=None):
    res = call_raw("POST", f"/webhooks/sms/{token}", body, headers=headers, authorized=False)
    return res["statusCode"]


def replies_list():
    status, body = call("GET", "/sms-replies")
    assert status == 200
    return body["items"]


# ---------- reading the answer ----------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("CONFIRM", "confirm"), ("confirm.", "confirm"), ("Da", "confirm"), ("Da, confirm!", "confirm"),
        ("ok", "confirm"), ("Confirmat", "confirm"), ("CANCEL", "cancel"), ("anuleaza", "cancel"),
        ("Anulează", "cancel"), ("vreau sa anulez", "cancel"),
        ("nu", None), ("no", None), ("1", None), ("", None), ("confirm cancel", None),
        ("va rog sa mutati programarea de maine la ora 10 ca nu pot", None),
        ("what time is it", None),
    ],
)
def test_parse_reply(text, expected):
    assert replies.parse_reply(text) == expected


# ---------- webhook ----------


def test_confirm_marks_the_appointment(aws):
    uid = make_user()["user_id"]
    appt = book(uid, timedelta(days=2))
    assert post(sms_event("+40712345678", "CONFIRM")) == 200

    status, got = call("GET", f"/appointments/{appt['appointment_id']}")
    assert status == 200 and got["confirmed_at"] and got["confirmed_via"] == "sms"
    assert got["status"] == "scheduled"
    assert replies_list() == []


def test_sender_number_formats_are_matched(aws):
    uid = make_user()["user_id"]
    appt = book(uid, timedelta(days=2))
    assert post(sms_event("0712345678", "da")) == 200  # national format, as some phones report it
    assert call("GET", f"/appointments/{appt['appointment_id']}")[1]["confirmed_at"]


def test_cancel_cancels_frees_the_slot_and_notifies(aws):
    uid = make_user()["user_id"]
    appt = book(uid, timedelta(days=2))
    queued_messages(aws)  # drain the confirmation
    assert post(sms_event("+40712345678", "CANCEL")) == 200

    got = call("GET", f"/appointments/{appt['appointment_id']}")[1]
    assert got["status"] == "cancelled" and got["cancelled_via"] == "sms" and got["reminders"] == []
    assert [m["kind"] for m in queued_messages(aws)] == ["cancelled"]
    # the slot is free again
    book(uid, timedelta(days=2))


def test_redelivered_webhook_is_applied_once(aws):
    uid = make_user()["user_id"]
    book(uid, timedelta(days=2))
    event = sms_event("+40712345678", "bla bla bla bla bla", message_id="same")
    assert post(event) == 200 and post(event) == 200
    assert len(replies_list()) == 1


def test_unclear_reply_goes_to_the_inbox(aws):
    uid = make_user()["user_id"]
    appt = book(uid, timedelta(days=2))
    assert post(sms_event("+40712345678", "Pot veni mai tarziu?")) == 200

    [item] = replies_list()
    assert item["reason"] == "unrecognised" and item["user_name"] == "Ana Pop" and item["text"] == "Pot veni mai tarziu?"
    assert not call("GET", f"/appointments/{appt['appointment_id']}")[1].get("confirmed_at")


def test_unknown_number_and_no_upcoming_visit_go_to_the_inbox(aws):
    make_user()
    assert post(sms_event("+40799999999", "CONFIRM", "a")) == 200
    assert post(sms_event("+40712345678", "CONFIRM", "b")) == 200  # known visitor, nothing booked
    assert sorted(i["reason"] for i in replies_list()) == ["no_upcoming", "unknown_sender"]


def test_two_upcoming_visits_are_not_guessed(aws):
    uid = make_user()["user_id"]
    first = book(uid, timedelta(days=2))
    book(uid, timedelta(days=3))
    assert post(sms_event("+40712345678", "CONFIRM")) == 200
    assert [i["reason"] for i in replies_list()] == ["several_upcoming"]
    assert not call("GET", f"/appointments/{first['appointment_id']}")[1].get("confirmed_at")


def test_inbox_can_be_dismissed(aws):
    make_user()
    post(sms_event("+40799999999", "hello"))
    [item] = replies_list()
    assert call("DELETE", f"/sms-replies/{item['id']}")[0] == 204
    assert replies_list() == []


def test_moving_a_confirmed_visit_clears_the_confirmation(aws):
    uid = make_user()["user_id"]
    appt = book(uid, timedelta(days=2))
    post(sms_event("+40712345678", "CONFIRM"))
    moved = to_iso(utc_now() + timedelta(days=4))
    status, got = call("PATCH", f"/appointments/{appt['appointment_id']}", {"start_at": moved})
    assert status == 200 and not got.get("confirmed_at")


def test_staff_can_mark_confirmed(aws):
    uid = make_user()["user_id"]
    appt = book(uid, timedelta(days=2))
    status, got = call("POST", f"/appointments/{appt['appointment_id']}/confirm", groups=("reception",))
    assert status == 200 and got["confirmed_via"] == "staff"


# ---------- who may call ----------


def test_wrong_or_missing_secret_is_rejected(aws):
    uid = make_user()["user_id"]
    appt = book(uid, timedelta(days=2))
    assert post(sms_event("+40712345678", "CONFIRM"), token="x" * 24) == 404
    assert not call("GET", f"/appointments/{appt['appointment_id']}")[1].get("confirmed_at")


def test_not_configured_means_not_found(aws, monkeypatch):
    monkeypatch.delenv("SMS_WEBHOOK_TOKEN")
    assert post(sms_event("+40712345678", "CONFIRM")) == 404


def test_signature_is_checked_when_a_signing_key_is_set(aws, monkeypatch):
    monkeypatch.setattr(replies, "webhook_config", lambda: {"token": TOKEN, "signing_key": "k3y"})
    uid = make_user()["user_id"]
    appt = book(uid, timedelta(days=2))
    event = sms_event("+40712345678", "CONFIRM")

    assert post(event) == 404  # unsigned
    assert post(event, headers={"x-timestamp": "1", "x-signature": "00"}) == 404  # wrong signature

    body = json.dumps(event)
    ts = "1760000000"
    sig = hmac.new(b"k3y", body.encode() + ts.encode(), hashlib.sha256).hexdigest()
    assert post(event, headers={"x-timestamp": ts, "x-signature": sig}) == 200
    assert call("GET", f"/appointments/{appt['appointment_id']}")[1]["confirmed_at"]


def test_other_events_are_acknowledged_and_ignored(aws):
    assert post({"event": "sms:delivered", "id": "x", "payload": {}}) == 200
    assert replies_list() == []

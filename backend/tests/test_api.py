import json
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app import services
from app.timeutil import to_iso, utc_now
from conftest import queued_messages, schedules


@pytest.fixture(autouse=True)
def _any_hour(monkeypatch):
    """These tests book relative to "now"; working-hours rules are covered in test_availability."""
    monkeypatch.setattr(services, "_ensure_working_hours", lambda *a, **k: None)

USER = {"name": "Ana Pop", "email": "Ana@example.com", "phone": "+40712345678"}


class Ctx(SimpleNamespace):
    function_name = "t"
    memory_limit_in_mb = 128
    invoked_function_arn = "arn:aws:lambda:eu-central-1:123456789012:function:t"
    aws_request_id = "req"


def call_raw(method, path, body=None, groups=("admin",), query=None, headers=None, authorized=True):
    from handlers import api

    context = {
        "accountId": "1", "apiId": "x", "domainName": "x", "domainPrefix": "x",
        "http": {"method": method, "path": path, "protocol": "HTTP/1.1", "sourceIp": "1.1.1.1", "userAgent": "t"},
        "requestId": "r", "routeKey": "$default", "stage": "$default", "time": "", "timeEpoch": 0,
    }
    if authorized:  # a browser pre-check (OPTIONS) arrives without any token, so without claims
        context["authorizer"] = {
            "jwt": {"claims": {"sub": "staff-1", "cognito:groups": "[" + " ".join(groups) + "]"}, "scopes": None}
        }
    event = {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": path,
        "rawQueryString": "",
        "headers": {"content-type": "application/json", **(headers or {})},
        "queryStringParameters": query,
        "requestContext": context,
        "body": json.dumps(body) if body is not None else None,
        "isBase64Encoded": False,
    }
    return api.handler(event, Ctx())


def call(method, path, body=None, groups=("admin",), query=None):
    res = call_raw(method, path, body, groups, query)
    return res["statusCode"], (json.loads(res["body"]) if res.get("body") else None)


def make_user(**over):
    status, user = call("POST", "/users", {**USER, **over})
    assert status == 201, user
    return user


def book(user_id, delta, **over):
    start = (utc_now() + delta).replace(microsecond=0)
    status, appt = call("POST", "/appointments", {"user_id": user_id, "start_at": to_iso(start), **over})
    assert status == 201, appt
    return appt


# ---------- users ----------


def test_user_crud(aws):
    user = make_user()
    assert user["email"] == "ana@example.com"

    assert call("GET", f"/users/{user['user_id']}")[1]["name"] == "Ana Pop"
    assert call("GET", "/users", query={"email": "ANA@example.com"})[1]["items"][0]["user_id"] == user["user_id"]
    assert len(call("GET", "/users")[1]["items"]) == 1

    status, updated = call("PATCH", f"/users/{user['user_id']}", {"phone": "+40799999999", "notify_sms": False})
    assert status == 200 and updated["phone"] == "+40799999999" and updated["notify_sms"] is False

    assert call("PATCH", "/users/unknown", {"name": "X"})[0] == 404


def test_duplicate_email_and_validation(aws):
    make_user()
    assert call("POST", "/users", USER)[0] == 409
    other = make_user(email="b@example.com")
    assert call("PATCH", f"/users/{other['user_id']}", {"email": "ana@example.com"})[0] == 409
    status, body = call("POST", "/users", {**USER, "email": "c@example.com", "phone": "123"})
    assert status == 422 and "123" not in json.dumps(body)  # submitted value is not echoed
    assert call("POST", "/users", {**USER, "unexpected": 1})[0] == 422


def test_pagination(aws):
    for i in range(3):
        make_user(email=f"u{i}@example.com")
    status, page = call("GET", "/users", query={"limit": "2"})
    assert len(page["items"]) == 2 and page["next_token"]
    _, rest = call("GET", "/users", query={"limit": "2", "next_token": page["next_token"]})
    assert len(rest["items"]) == 1
    assert call("GET", "/users", query={"next_token": "garbage"})[0] == 400


# ---------- patients / roles ----------


def test_patient_notes_are_clinician_only(aws):
    uid = make_user()["user_id"]
    path = f"/users/{uid}/patient"
    assert call("GET", path, groups=("reception",))[0] == 403
    assert call("PUT", path, {"notes": "x"}, groups=("reception",))[0] == 403
    assert call("GET", path, groups=())[0] == 403

    assert call("GET", path, groups=("clinician",))[1]["notes"] == ""
    status, saved = call("PUT", path, {"notes": "Diagnosis: ..."}, groups=("clinician",))
    assert status == 200 and saved["updated_by"] == "staff-1"
    assert call("GET", path, groups=("clinician",))[1]["notes"] == "Diagnosis: ..."
    assert call("PUT", f"/users/nope/patient", {"notes": "x"}, groups=("clinician",))[0] == 404


def test_settings_are_admin_only(aws):
    assert call("PUT", "/settings/reminders", {}, groups=("reception",))[0] == 403
    assert call("GET", "/settings/reminders", groups=("reception",))[0] == 200
    assert call("POST", "/settings/reminders/apply-to-existing", groups=("clinician",))[0] == 403


# ---------- appointments + scheduler ----------


def test_booking_creates_schedules_and_confirmation(aws):
    uid = make_user()["user_id"]
    appt = book(uid, timedelta(days=10))

    assert [r["key"] for r in appt["reminders"]] == ["m4320", "m1440", "m120"]
    assert schedules(aws) == sorted(r["schedule_name"] for r in appt["reminders"])
    msgs = queued_messages(aws)
    assert [m["kind"] for m in msgs] == ["confirmation"]
    assert msgs[0]["appointment_id"] == appt["appointment_id"]

    sched = aws.scheduler.get_schedule(Name=appt["reminders"][0]["schedule_name"], GroupName="reminders")
    assert json.loads(sched["Target"]["Input"])["kind"] == "reminder"


def test_booking_soon_skips_past_reminders(aws):
    uid = make_user()["user_id"]
    assert [r["key"] for r in book(uid, timedelta(hours=26))["reminders"]] == ["m1440", "m120"]
    assert [r["key"] for r in book(uid, timedelta(hours=5))["reminders"]] == ["m120"]


def test_booking_validation(aws):
    uid = make_user()["user_id"]
    past = to_iso(utc_now() - timedelta(hours=1))
    assert call("POST", "/appointments", {"user_id": uid, "start_at": past})[0] == 400
    future = to_iso(utc_now() + timedelta(days=2))
    assert call("POST", "/appointments", {"user_id": "nope", "start_at": future})[0] == 404
    assert call("POST", "/appointments", {"user_id": uid, "start_at": future, "duration_minutes": 1})[0] == 422


def test_reschedule_replaces_schedules_and_notifies(aws):
    uid = make_user()["user_id"]
    appt = book(uid, timedelta(days=10))
    queued_messages(aws)
    old_names = set(schedules(aws))

    new_start = to_iso((utc_now() + timedelta(days=20)).replace(second=0))  # times are minute-aligned
    status, updated = call("PATCH", f"/appointments/{appt['appointment_id']}", {"start_at": new_start})
    assert status == 200 and updated["start_at"] == new_start
    new_names = set(schedules(aws))
    assert len(new_names) == 3 and not new_names & old_names
    assert [m["kind"] for m in queued_messages(aws)] == ["rescheduled"]

    # editing only the note leaves schedules alone and sends nothing
    status, _ = call("PATCH", f"/appointments/{appt['appointment_id']}", {"internal_note": "bring results"})
    assert status == 200 and set(schedules(aws)) == new_names and queued_messages(aws) == []


def test_cancel_removes_schedules_and_notifies(aws):
    uid = make_user()["user_id"]
    appt = book(uid, timedelta(days=10))
    queued_messages(aws)

    status, cancelled = call("POST", f"/appointments/{appt['appointment_id']}/cancel")
    assert status == 200 and cancelled["status"] == "cancelled" and cancelled["reminders"] == []
    assert schedules(aws) == []
    assert [m["kind"] for m in queued_messages(aws)] == ["cancelled"]
    assert call("POST", f"/appointments/{appt['appointment_id']}/cancel")[0] == 200  # idempotent
    assert call("PATCH", f"/appointments/{appt['appointment_id']}", {"start_at": to_iso(utc_now() + timedelta(days=3))})[0] == 409


def test_complete_removes_schedules_without_notice(aws):
    uid = make_user()["user_id"]
    appt = book(uid, timedelta(days=10))
    queued_messages(aws)
    status, done = call("PATCH", f"/appointments/{appt['appointment_id']}", {"status": "completed"})
    assert status == 200 and done["status"] == "completed"
    assert schedules(aws) == [] and queued_messages(aws) == []


def test_list_appointments_by_day_and_user(aws):
    uid = make_user()["user_id"]
    a = book(uid, timedelta(days=3))
    book(uid, timedelta(days=30))
    day = a["day"]
    _, res = call("GET", "/appointments", query={"from": day, "to": day})
    assert [x["appointment_id"] for x in res["items"]] == [a["appointment_id"]]
    _, res = call("GET", "/appointments", query={"user_id": uid})
    assert len(res["items"]) == 2
    assert call("GET", "/appointments", query={"from": "2026-01-01", "to": "2027-01-01"})[0] == 400


def test_custom_reminder_settings_apply_to_new_and_existing(aws):
    uid = make_user()["user_id"]
    existing = book(uid, timedelta(days=10))

    cfg = {"cabinet_name": "Dr. Pop", "timezone": "Europe/Bucharest", "send_on_booking": False,
           "reminders": [{"minutes_before": 7 * 1440}, {"minutes_before": 30}]}
    assert call("PUT", "/settings/reminders", cfg)[0] == 200
    assert call("PUT", "/settings/reminders", {**cfg, "reminders": [{"minutes_before": 5}, {"minutes_before": 5}]})[0] == 422

    queued_messages(aws)
    new = book(uid, timedelta(days=11))
    assert [r["key"] for r in new["reminders"]] == ["m10080", "m30"]
    assert queued_messages(aws) == []  # send_on_booking disabled

    status, result = call("POST", "/settings/reminders/apply-to-existing")
    assert status == 200 and result == {"updated": 2, "failed": 0}
    _, refreshed = call("GET", f"/appointments/{existing['appointment_id']}")
    assert [r["key"] for r in refreshed["reminders"]] == ["m10080", "m30"]
    assert len(schedules(aws)) == 4


def test_delete_user_blocked_by_upcoming_appointments(aws):
    uid = make_user()["user_id"]
    call("PUT", f"/users/{uid}/patient", {"notes": "n"}, groups=("clinician",))
    appt = book(uid, timedelta(days=5))
    assert call("DELETE", f"/users/{uid}")[0] == 409
    call("POST", f"/appointments/{appt['appointment_id']}/cancel")
    assert call("DELETE", f"/users/{uid}")[0] == 204
    assert call("GET", f"/users/{uid}")[0] == 404
    from app import repositories as repo
    assert repo.get_patient(uid) is None


def test_sms_channel_and_consent_flags(aws):
    user = make_user()
    assert user["notify_sms"] is True  # visitors consent by default; the channel itself is off until enabled

    status, updated = call("PATCH", f"/users/{user['user_id']}", {"notify_sms": False})
    assert status == 200 and updated["notify_sms"] is False

    assert call("GET", "/settings/reminders")[1]["channels"]["sms"] is False  # off by default
    cfg = call("GET", "/settings/reminders")[1]
    cfg["channels"]["sms"] = True
    assert call("PUT", "/settings/reminders", cfg)[0] == 200
    assert call("GET", "/settings/reminders")[1]["channels"]["sms"] is True


def test_settings_saved_before_whatsapp_was_removed_still_load(aws):
    """Old stored settings contain channels.whatsapp (and notify_whatsapp on visitors); they must not break."""
    from app import clients, repositories as repo
    from app.config import get_config

    clients.table(get_config().settings_table).put_item(Item={
        "setting_id": "reminders",
        "config": {"cabinet_name": "Old", "timezone": "Europe/Bucharest", "send_on_booking": True, "send_on_change": True,
                   "channels": {"email": True, "whatsapp": True}, "reminders": [{"minutes_before": 1440, "enabled": True}]},
    })
    status, cfg = call("GET", "/settings/reminders")
    assert status == 200 and cfg["channels"] == {"email": True, "sms": False}  # unknown key dropped, sms defaults off

    # an old UI that still sends whatsapp in a save does not fail either
    assert call("PUT", "/settings/reminders", {**cfg, "channels": {"email": True, "whatsapp": True, "sms": True}})[0] == 200
    assert call("GET", "/settings/reminders")[1]["channels"] == {"email": True, "sms": True}

    # visitors are no longer given a whatsapp flag, and sending one is rejected
    assert call("POST", "/users", {**USER, "notify_whatsapp": True})[0] == 422
    assert "notify_whatsapp" not in make_user(email="new@example.com")

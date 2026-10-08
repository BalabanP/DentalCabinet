from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

from app.models import ScheduleSettings
from app.timeutil import utc_now
from test_api import call, make_user


def next_weekday(weekday: int, weeks_ahead: int = 1) -> str:
    """YYYY-MM-DD of a future date with the given weekday (0 = Monday)."""
    d = utc_now().date() + timedelta(days=7 * weeks_ahead)
    return (d + timedelta(days=(weekday - d.weekday()) % 7)).isoformat()


def book_at(uid, day, hhmm, minutes=30, **extra):
    return call("POST", "/appointments", {"user_id": uid, "start_at": f"{day}T{hhmm}:00", "duration_minutes": minutes, **extra})


def slot_times(day, **query):
    status, res = call("GET", "/availability", query={"from": day, **query})
    assert status == 200, res
    return [s["local_time"] for s in res["slots"]]


# ---------- search ----------


def test_search_users_by_name_email_and_phone(aws):
    make_user(name="Ana Pop", email="ana@example.com", phone="+40712345678")
    make_user(name="Mihai Popescu", email="mihai@example.com", phone="+40799990000")
    make_user(name="Dan Ionescu", email="dan@example.com", phone="+40755551111")

    def names(q):
        status, res = call("GET", "/users", query={"q": q})
        assert status == 200
        return [u["name"] for u in res["items"]]

    assert names("pop") == ["Ana Pop", "Mihai Popescu"]
    assert names("POPESCU") == ["Mihai Popescu"]
    assert names("mihai@") == ["Mihai Popescu"]
    assert names("0755551") == ["Dan Ionescu"]
    assert names("+40 799 990") == ["Mihai Popescu"]
    assert names("zzz") == []
    assert call("GET", "/users", query={"q": "a"})[0] == 400


def test_search_ranks_prefix_matches_first_and_respects_limit(aws):
    make_user(name="Ana Maria", email="a1@example.com")
    make_user(name="Maria Pop", email="a2@example.com")
    _, res = call("GET", "/users", query={"q": "maria"})
    assert [u["name"] for u in res["items"]] == ["Maria Pop", "Ana Maria"]
    _, res = call("GET", "/users", query={"q": "maria", "limit": "1"})
    assert len(res["items"]) == 1


# ---------- overlap protection ----------


def test_overlapping_booking_is_rejected(aws):
    uid = make_user()["user_id"]
    day = next_weekday(1)
    assert book_at(uid, day, "10:00", 30)[0] == 201
    assert book_at(uid, day, "10:15", 30)[0] == 409  # starts inside
    assert book_at(uid, day, "09:45", 30)[0] == 409  # ends inside
    assert book_at(uid, day, "09:30", 90)[0] == 409  # swallows it
    assert book_at(uid, day, "10:30", 30)[0] == 201  # back to back is fine
    assert book_at(uid, day, "09:30", 30)[0] == 201


def test_allow_overlap_overrides_and_cancelled_slots_are_free(aws):
    uid = make_user()["user_id"]
    day = next_weekday(1)
    _, first = book_at(uid, day, "10:00")
    assert book_at(uid, day, "10:00", allow_overlap=True)[0] == 201
    call("POST", f"/appointments/{first['appointment_id']}/cancel")
    _, again = book_at(uid, day, "11:00")
    call("POST", f"/appointments/{again['appointment_id']}/cancel")
    assert book_at(uid, day, "11:00")[0] == 201


def test_reschedule_checks_overlap_but_not_against_itself(aws):
    uid = make_user()["user_id"]
    day = next_weekday(2)
    _, a = book_at(uid, day, "10:00")
    _, b = book_at(uid, day, "11:00")
    path = f"/appointments/{b['appointment_id']}"

    assert call("PATCH", path, {"start_at": f"{day}T10:15:00"})[0] == 409
    assert call("PATCH", path, {"start_at": f"{day}T11:10:00"})[0] == 200  # moves within its own slot
    assert call("PATCH", path, {"duration_minutes": 60})[0] == 200
    assert call("PATCH", f"/appointments/{a['appointment_id']}", {"duration_minutes": 90})[0] == 409
    assert call("PATCH", f"/appointments/{a['appointment_id']}", {"duration_minutes": 90, "allow_overlap": True})[0] == 200
    assert call("PATCH", path, {"allow_overlap": True})[0] == 400  # nothing to update


# ---------- free slots ----------


def test_default_working_hours_offer_half_hour_slots(aws):
    times = slot_times(next_weekday(0))
    assert times[0] == "09:00" and times[-1] == "16:30" and len(times) == 16


def test_weekend_is_closed(aws):
    assert slot_times(next_weekday(5)) == []
    assert slot_times(next_weekday(6)) == []


def test_booked_times_are_excluded_and_duration_is_respected(aws):
    uid = make_user()["user_id"]
    day = next_weekday(3)
    book_at(uid, day, "10:00", 60)
    times = slot_times(day)
    assert "09:30" in times and "10:00" not in times and "10:30" not in times and "11:00" in times

    long = slot_times(day, duration_minutes="60")
    assert "09:00" in long and "09:30" not in long  # 09:30-10:30 would hit the booking
    assert "16:00" in long and "16:30" not in long  # would end after closing


def test_free_slots_are_bookable_and_past_times_are_hidden(aws):
    uid = make_user()["user_id"]
    day = next_weekday(1)
    _, res = call("GET", "/availability", query={"from": day})
    first = res["slots"][0]
    assert book_at(uid, day, first["local_time"])[0] == 201
    assert first["local_time"] not in slot_times(day)

    today = utc_now().date().isoformat()
    _, res = call("GET", "/availability", query={"from": today})
    assert all(s["start_at"] > utc_now().strftime("%Y-%m-%dT%H:%M:%SZ") for s in res["slots"])


def test_availability_range_and_validation(aws):
    monday = next_weekday(0)
    sunday = (datetime.fromisoformat(monday) + timedelta(days=6)).date().isoformat()
    _, res = call("GET", "/availability", query={"from": monday, "to": sunday})
    assert len({s["day"] for s in res["slots"]}) == 5  # Mon-Fri only
    assert call("GET", "/availability")[0] == 400
    assert call("GET", "/availability", query={"from": "2026-01-01", "to": "2026-03-01"})[0] == 400
    assert call("GET", "/availability", query={"from": monday, "duration_minutes": "2"})[0] == 400


def test_schedule_settings_change_availability(aws):
    day = next_weekday(5)  # Saturday
    cfg = {"slot_minutes": 60, "working_hours": {"sat": [{"start": "10:00", "end": "12:00"}, {"start": "14:00", "end": "15:00"}]}}
    assert call("PUT", "/settings/schedule", cfg, groups=("reception",))[0] == 403
    assert call("PUT", "/settings/schedule", cfg)[0] == 200
    assert slot_times(day) == ["10:00", "11:00", "14:00"]
    assert call("GET", "/settings/schedule", groups=("reception",))[1]["slot_minutes"] == 60


def test_schedule_uses_cabinet_timezone(aws):
    call("PUT", "/settings/reminders", {"timezone": "Europe/Bucharest"})
    day = next_weekday(1)
    _, res = call("GET", "/availability", query={"from": day})
    assert res["timezone"] == "Europe/Bucharest" and res["slots"][0]["local_time"] == "09:00"
    assert int(res["slots"][0]["start_at"][11:13]) in (6, 7)  # 09:00 Bucharest = 06:00 or 07:00 UTC (DST)


def test_schedule_settings_validation():
    with pytest.raises(ValidationError):
        ScheduleSettings(working_hours={"mon": [{"start": "10:00", "end": "09:00"}]})
    with pytest.raises(ValidationError):
        ScheduleSettings(working_hours={"mon": [{"start": "09:00", "end": "12:00"}, {"start": "11:00", "end": "13:00"}]})
    with pytest.raises(ValidationError):
        ScheduleSettings(working_hours={"mon": [{"start": "9:00", "end": "12:00"}]})
    with pytest.raises(ValidationError):
        ScheduleSettings(working_hours={"funday": []})
    with pytest.raises(ValidationError):
        ScheduleSettings(slot_minutes=1)

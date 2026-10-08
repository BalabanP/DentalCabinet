from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app.models import ReminderRule, ReminderSettings, UserIn
from app.reminders import MIN_LEAD, plan_reminders, schedule_name
from app.timeutil import days_between, to_utc

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
DEFAULT_RULES = ReminderSettings().reminders


def keys(planned):
    return [p.key for p in planned]


def test_default_reminders_are_3_days_1_day_2_hours():
    assert [r.minutes_before for r in DEFAULT_RULES] == [4320, 1440, 120]


def test_far_future_visit_gets_all_reminders():
    start = NOW + timedelta(days=10)
    planned = plan_reminders(start, DEFAULT_RULES, NOW)
    assert keys(planned) == ["m4320", "m1440", "m120"]
    assert planned[0].send_at == start - timedelta(days=3)


def test_reminders_already_in_the_past_are_skipped():
    assert keys(plan_reminders(NOW + timedelta(hours=26), DEFAULT_RULES, NOW)) == ["m1440", "m120"]
    assert keys(plan_reminders(NOW + timedelta(hours=5), DEFAULT_RULES, NOW)) == ["m120"]
    assert plan_reminders(NOW + timedelta(minutes=30), DEFAULT_RULES, NOW) == []


def test_reminder_too_close_to_now_is_skipped():
    start = NOW + timedelta(minutes=120) + MIN_LEAD - timedelta(seconds=1)
    assert plan_reminders(start, DEFAULT_RULES, NOW) == []


def test_disabled_rules_are_ignored():
    rules = [ReminderRule(minutes_before=60, enabled=False), ReminderRule(minutes_before=30)]
    assert keys(plan_reminders(NOW + timedelta(days=1), rules, NOW)) == ["m30"]


def test_schedule_name_is_valid_and_short():
    name = schedule_name("a" * 32, NOW, "m86400")
    assert len(name) <= 64
    assert all(c.isalnum() or c in "-_." for c in name)


def test_naive_time_is_interpreted_in_cabinet_timezone_across_dst():
    tz = ZoneInfo("Europe/Bucharest")  # DST ends 2026-10-25
    assert to_utc(datetime(2026, 10, 24, 9, 0), tz).hour == 6
    assert to_utc(datetime(2026, 10, 26, 9, 0), tz).hour == 7


def test_aware_time_is_converted_to_utc():
    dt = datetime(2026, 10, 24, 9, 0, tzinfo=timezone(timedelta(hours=3)))
    assert to_utc(dt, ZoneInfo("UTC")).hour == 6


def test_days_between_limits():
    assert days_between("2026-10-01", "2026-10-03") == ["2026-10-01", "2026-10-02", "2026-10-03"]
    with pytest.raises(ValueError):
        days_between("2026-10-03", "2026-10-01")
    with pytest.raises(ValueError):
        days_between("2026-01-01", "2026-12-31")


def test_user_validation_normalises_email_and_rejects_bad_phone():
    u = UserIn(name=" Ana ", email="Ana@Example.COM", phone="+40712345678")
    assert u.email == "ana@example.com" and u.name == "Ana"
    for bad in ("0712345678", "+0123456789", "+40 712 345 678", "abc"):
        with pytest.raises(ValidationError):
            UserIn(name="Ana", email="ana@example.com", phone=bad)


def test_settings_validation():
    with pytest.raises(ValidationError):
        ReminderSettings(timezone="Mars/Olympus")
    with pytest.raises(ValidationError):
        ReminderSettings(reminders=[{"minutes_before": 60}, {"minutes_before": 60}])
    with pytest.raises(ValidationError):
        ReminderSettings(reminders=[{"minutes_before": 0}])
    assert ReminderSettings(reminders=[]).reminders == []
    assert ReminderRule(minutes_before=90).key == "m90"

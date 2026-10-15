from datetime import datetime, timezone

import pytest

from app import notifications
from app.models import ReminderSettings


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    from app.config import get_config

    for k, v in {
        "USERS_TABLE": "u", "PATIENTS_TABLE": "p", "APPOINTMENTS_TABLE": "a", "SETTINGS_TABLE": "s",
        "NOTIFICATIONS_TABLE": "n", "REMINDER_QUEUE_URL": "q", "REMINDER_QUEUE_ARN": "arn",
        "SCHEDULER_GROUP": "g", "SCHEDULER_ROLE_ARN": "r",
    }.items():
        monkeypatch.setenv(k, v)
    get_config.cache_clear()
    yield
    get_config.cache_clear()


START = datetime(2026, 10, 24, 6, 0, tzinfo=timezone.utc)  # 09:00 in Bucharest (EEST)
SETTINGS = ReminderSettings(cabinet_name="Dr. Pop Cabinet", timezone="Europe/Bucharest", cabinet_phone="+40211234567")


@pytest.mark.parametrize("kind", ["confirmation", "reminder", "rescheduled", "cancelled"])
@pytest.mark.parametrize("locale", ["en", "ro"])
def test_every_kind_and_locale_renders(kind, locale):
    r = notifications.render(kind, {"name": "Ana", "locale": locale}, START, SETTINGS)
    assert "Ana" in r.text and "Dr. Pop Cabinet" in r.text
    assert "24.10.2026" in r.text and "09:00" in r.text
    assert "+40211234567" in r.text


def test_unknown_locale_falls_back_to_english():
    r = notifications.render("reminder", {"name": "Ana", "locale": "xx"}, START, SETTINGS)
    assert r.subject == "Appointment reminder"


def test_html_is_escaped():
    r = notifications.render("reminder", {"name": "<script>x</script>", "locale": "en"}, START, SETTINGS)
    assert "<script>" not in r.html


@pytest.mark.parametrize("locale,hint", [("en", "Reply CONFIRM or CANCEL"), ("ro", "Raspundeti CONFIRM sau ANULEAZA")])
def test_reminder_sms_asks_for_an_answer_unless_already_confirmed(locale, hint):
    user = {"name": "Ana", "locale": locale}
    asked = notifications.render("reminder", user, START, SETTINGS)
    assert hint in asked.sms

    done = notifications.render("reminder", user, START, SETTINGS, confirmed=True)
    assert hint not in done.sms and "CONFIRM" not in done.sms
    assert done.sms == asked.sms.replace(" " + hint + ".", "")  # nothing else changed
    assert "24.10.2026" in done.sms and "09:00" in done.sms
    assert done.text == asked.text  # the email never carried the sentence


@pytest.mark.parametrize(
    "text",
    ["X: reminder. Raspundeti CONFIRM sau ANULEAZA.", "X: reminder. Răspundeți CONFIRM sau ANULEAZĂ.", "X: reminder. reply confirm or cancel"],
)
def test_without_reply_hint_handles_accents_case_and_missing_full_stop(text):
    assert notifications.without_reply_hint(text) == "X: reminder."


def test_without_reply_hint_leaves_other_text_alone():
    assert notifications.without_reply_hint("Cabinet: va asteptam la 10:00.") == "Cabinet: va asteptam la 10:00."

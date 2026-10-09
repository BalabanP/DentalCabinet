import json
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app import notifications, repositories as repo, services
from app.models import AppointmentIn, MessageTemplates, ReminderSettings, TemplateFields, UserIn
from app.notifications import all_defaults, effective_fields, fill, render, sms_info
from app.reminders import KIND_REMINDER, build_message
from app.timeutil import parse_iso, utc_now
from handlers import sender
from test_api import call, call_raw, make_user

START = datetime(2026, 10, 24, 6, 0, tzinfo=timezone.utc)  # 09:00 in Bucharest
SETTINGS = ReminderSettings(cabinet_name="Dr. Pop Cabinet", timezone="Europe/Bucharest", cabinet_phone="+40211234567")
USER = {"name": "Ana Pop", "locale": "ro"}


# ---------- validation ----------


def test_blank_fields_mean_use_the_built_in_text():
    t = TemplateFields(email_subject="  ", email_body=None, sms_body="  Salut {name}  ")
    assert t.email_subject is None and t.email_body is None and t.sms_body == "Salut {name}"


@pytest.mark.parametrize("bad", ["Hi {nam}", "Hi {name", "Hi name}", "x {{name}}", "{}", "{ name }"])
def test_unknown_or_broken_placeholders_are_rejected(bad):
    with pytest.raises(ValidationError):
        TemplateFields(email_body=bad)


def test_every_known_placeholder_is_accepted():
    TemplateFields(sms_body="{name} {cabinet} {date} {time} {phone}")


def test_length_limits():
    with pytest.raises(ValidationError):
        TemplateFields(email_subject="x" * 151)
    with pytest.raises(ValidationError):
        TemplateFields(sms_body="x" * 601)
    with pytest.raises(ValidationError):
        MessageTemplates.model_validate({"overrides": {"reminder": {"fr": {"sms_body": "x"}}}})  # unknown language
    with pytest.raises(ValidationError):
        MessageTemplates.model_validate({"overrides": {"promo": {"ro": {"sms_body": "x"}}}})  # unknown kind


# ---------- rendering ----------


def test_fill_never_raises_and_leaves_visitor_text_alone():
    assert fill("Hi {name}, {nonsense} {date}", {"name": "Ana {x}", "date": "1"}) == "Hi Ana {x}, {nonsense} 1"


def test_built_in_defaults_exist_for_every_kind_and_language():
    d = all_defaults()
    assert set(d) == {"confirmation", "reminder", "rescheduled", "cancelled"}
    for kind in d:
        assert set(d[kind]) == {"en", "ro"}
        for fields in d[kind].values():
            assert all(fields.values())


def test_sms_defaults_fit_one_part():
    """Even with a long cabinet name, every built-in SMS stays within a single part."""
    for kind, langs in all_defaults().items():
        for loc, fields in langs.items():
            sample = fields["sms_body"].format(
                name="Ana Popescu", cabinet="Cabinet Stomatologic Dr. Popescu", date="24.10.2026", time="09:00"
            )
            assert sms_info(sample)["segments"] == 1, (kind, loc, sample)


def test_other_languages_are_rejected_for_visitors_and_texts(aws):
    assert call("POST", "/users", {"name": "Ivan", "email": "i@example.com", "phone": "+40712345678", "locale": "ru"})[0] == 422
    assert call("PUT", "/settings/messages", {"overrides": {"reminder": {"ru": {"sms_body": "x"}}}})[0] == 422


def test_a_visitor_stored_with_an_old_language_still_gets_english_messages():
    r = render("reminder", {"name": "Ivan", "locale": "ru"}, START, SETTINGS)
    assert r.subject == "Appointment reminder"


def test_customised_text_replaces_only_that_field():
    templates = MessageTemplates.model_validate({"overrides": {"reminder": {"ro": {"sms_body": "{name}, maine la {time}! {phone}"}}}})
    r = render("reminder", USER, START, SETTINGS, templates)
    assert r.sms == "Ana Pop, maine la 09:00! +40211234567"
    assert "Reamintire programare" == r.subject  # not customised: still the built-in subject
    other = render("reminder", {"name": "Ana", "locale": "en"}, START, SETTINGS, templates)
    assert other.subject == "Appointment reminder"  # another language is untouched
    assert render("cancelled", USER, START, SETTINGS, templates).sms.startswith("Dr. Pop Cabinet:")  # another kind too


def test_effective_fields_without_templates_are_the_defaults():
    assert effective_fields("reminder", "ro", None) == notifications.default_fields("reminder", "ro")


def test_email_html_is_a_designed_page_and_escapes_everything():
    templates = MessageTemplates.model_validate({"overrides": {"reminder": {"ro": {"email_body": "Bună {name}!\n\nVă așteptăm.\nLa revedere."}}}})
    r = render("reminder", {"name": "<script>alert(1)</script>", "locale": "ro"}, START, SETTINGS, templates)
    html = r.html
    assert html.startswith("<!doctype html>") and 'lang="ro"' in html
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "24.10.2026" in html and "09:00" in html and "Data" in html and "Ora" in html
    assert "Dr. Pop Cabinet" in html and "+40211234567" in html
    assert html.count("<p ") == 2  # a blank line starts a new paragraph
    assert "Vă așteptăm.<br>La revedere." in html  # a single line break stays a line break
    assert "background:#0f7b8a" in html  # teal header
    assert "background:#b42318" in render("cancelled", USER, START, SETTINGS).html  # red for cancellations


def test_plain_text_email_keeps_the_contact_line_and_works_without_a_phone():
    assert render("reminder", USER, START, SETTINGS).text.endswith("Sunați la +40211234567.")
    no_phone = ReminderSettings(cabinet_name="X", timezone="Europe/Bucharest")
    r = render("reminder", USER, START, no_phone)
    assert "Sunați" not in r.text and "Sunați" not in r.html


def test_sms_info_counts_parts():
    assert sms_info("a" * 160) == {"encoding": "GSM-7", "length": 160, "segments": 1}
    assert sms_info("a" * 161)["segments"] == 2
    assert sms_info("{}")["length"] == 4  # braces cost two characters each in GSM-7
    assert sms_info("ăâîșț" * 10) == {"encoding": "GSM-7", "length": 50, "segments": 1}  # accents are folded first
    assert sms_info("Здравствуйте")["encoding"] == "Unicode"
    assert sms_info("")["segments"] == 0


# ---------- API ----------


def test_api_returns_defaults_and_saves_overrides(aws):
    status, data = call("GET", "/settings/messages", groups=("reception",))
    assert status == 200 and data["overrides"] == {} and data["placeholders"] == ["name", "cabinet", "date", "time", "phone"]
    assert data["defaults"]["reminder"]["ro"]["sms_body"]

    body = {"overrides": {"reminder": {"ro": {"sms_body": "Salut {name}!", "email_subject": "Reamintire"}}}}
    assert call("PUT", "/settings/messages", body, groups=("reception",))[0] == 403  # admin only
    status, saved = call("PUT", "/settings/messages", body)
    assert status == 200 and saved["overrides"] == body["overrides"]
    assert call("GET", "/settings/messages")[1]["overrides"] == body["overrides"]

    # a blank field goes back to following the built-in text
    call("PUT", "/settings/messages", {"overrides": {"reminder": {"ro": {"sms_body": "", "email_subject": "Reamintire"}}}})
    assert call("GET", "/settings/messages")[1]["overrides"] == {"reminder": {"ro": {"email_subject": "Reamintire"}}}


def test_api_rejects_bad_templates(aws):
    assert call("PUT", "/settings/messages", {"overrides": {"reminder": {"ro": {"sms_body": "Salut {nume}"}}}})[0] == 422
    assert call("PUT", "/settings/messages", {"overrides": {"reminder": {"ro": {"sms_body": "x"}}}, "extra": 1})[0] == 422
    assert call("GET", "/settings/messages")[1]["overrides"] == {}


def test_preview_renders_like_a_real_message_and_saves_nothing(aws):
    call("PUT", "/settings/reminders", {"cabinet_name": "Dr. Pop", "timezone": "Europe/Bucharest"})
    status, p = call("POST", "/settings/messages/preview", {"kind": "confirmation", "locale": "ro", "sms_body": "{cabinet}: {name}, {date} {time} ăâîșț"}, groups=("reception",))
    assert status == 200
    assert p["sms_text"].startswith("Dr. Pop: Ana Pop, ") and p["sms_text"].endswith("aaist")  # accents folded for SMS
    assert p["sms"] == {"encoding": "GSM-7", "length": len(p["sms_text"]), "segments": 1}
    assert p["email_subject"] == "Programare confirmată"  # not given: built-in subject
    assert p["email_html"].startswith("<!doctype html>") and "Dr. Pop" in p["email_html"]
    assert call("GET", "/settings/messages")[1]["overrides"] == {}  # nothing was stored

    assert call("POST", "/settings/messages/preview", {"kind": "confirmation", "locale": "ro", "sms_body": "{bad}"})[0] == 422
    assert call("POST", "/settings/messages/preview", {"kind": "nope", "locale": "ro"})[0] == 422


def test_message_texts_carry_cors_headers(aws):
    res = call_raw("GET", "/settings/messages", headers={"origin": "https://site.example.com"})
    assert {k.lower(): v for k, v in res["headers"].items()}["access-control-allow-origin"] == "https://site.example.com"


# ---------- the sender uses the customised texts ----------


@pytest.fixture
def sent(aws, monkeypatch):
    out = {"emails": [], "smss": []}
    monkeypatch.setattr(notifications, "send_email", lambda to, msg: out["emails"].append(msg) or "id")
    monkeypatch.setattr(notifications, "send_sms", lambda phone, msg: out["smss"].append(msg) or "id")
    monkeypatch.setattr(notifications, "sms_enabled", lambda: True)
    settings = ReminderSettings(cabinet_name="Dr. Pop", timezone="Europe/Bucharest")
    settings.channels.sms = True
    repo.put_reminder_settings(settings)
    user = repo.create_user(UserIn(name="Ana", email="ana@example.com", phone="+40712345678", locale="ro").model_dump())
    appt = services.book(AppointmentIn(user_id=user["user_id"], start_at=utc_now() + timedelta(days=10)))
    return out, appt


def test_sender_sends_the_customised_texts_on_each_channel(sent):
    out, appt = sent
    repo.put_message_templates(MessageTemplates.model_validate({"overrides": {"reminder": {"ro": {
        "email_subject": "Vă așteptăm!", "email_body": "Salut {name}, ne vedem la {time}.", "sms_body": "{name}: {date} {time}"}}}}))
    sender.process(build_message(appt["appointment_id"], parse_iso(appt["start_at"]), KIND_REMINDER, "m1440"))

    email, sms = out["emails"][0], out["smss"][0]
    assert email.subject == "Vă așteptăm!" and "Salut Ana, ne vedem la" in email.text and "Salut Ana" in email.html
    assert sms.sms.startswith("Ana: ") and "Salut" not in sms.sms  # the SMS has its own text, not the email's


def test_sender_uses_built_in_texts_until_something_is_saved(sent):
    out, appt = sent
    sender.process(build_message(appt["appointment_id"], parse_iso(appt["start_at"]), KIND_REMINDER, "m1440"))
    assert out["emails"][0].subject == "Reamintire programare"
    assert out["smss"][0].sms.startswith("Dr. Pop: va reamintim")

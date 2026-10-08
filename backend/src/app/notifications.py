"""Message rendering (i18n) and the e-mail / WhatsApp providers.

Messages contain only name, cabinet, date and time. Never medical information.
"""

import html
import json
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from . import clients
from .config import get_config
from .models import ReminderSettings

# kind -> locale -> (subject, body). Placeholders: name, cabinet, date, time.
TEXTS = {
    "confirmation": {
        "en": ("Appointment confirmed", "Hello {name}, your appointment at {cabinet} is confirmed for {date} at {time}."),
        "ro": ("Programare confirmată", "Bună {name}, programarea dumneavoastră la {cabinet} este confirmată pentru {date}, ora {time}."),
        "ru": ("Запись подтверждена", "Здравствуйте, {name}! Ваша запись в {cabinet} подтверждена на {date} в {time}."),
    },
    "reminder": {
        "en": ("Appointment reminder", "Hello {name}, this is a reminder of your appointment at {cabinet} on {date} at {time}."),
        "ro": ("Reamintire programare", "Bună {name}, vă reamintim programarea la {cabinet} din {date}, ora {time}."),
        "ru": ("Напоминание о записи", "Здравствуйте, {name}! Напоминаем о вашей записи в {cabinet} на {date} в {time}."),
    },
    "rescheduled": {
        "en": ("Appointment rescheduled", "Hello {name}, your appointment at {cabinet} has been moved to {date} at {time}."),
        "ro": ("Programare reprogramată", "Bună {name}, programarea la {cabinet} a fost mutată pe {date}, ora {time}."),
        "ru": ("Запись перенесена", "Здравствуйте, {name}! Ваша запись в {cabinet} перенесена на {date} в {time}."),
    },
    "cancelled": {
        "en": ("Appointment cancelled", "Hello {name}, your appointment at {cabinet} on {date} at {time} has been cancelled."),
        "ro": ("Programare anulată", "Bună {name}, programarea la {cabinet} din {date}, ora {time} a fost anulată."),
        "ru": ("Запись отменена", "Здравствуйте, {name}! Ваша запись в {cabinet} на {date} в {time} отменена."),
    },
}
CONTACT = {
    "en": "Questions? Call {phone}.",
    "ro": "Întrebări? Sunați la {phone}.",
    "ru": "Вопросы? Звоните {phone}.",
}


@dataclass(frozen=True)
class Rendered:
    subject: str
    text: str
    html: str
    whatsapp_template: str
    whatsapp_language: str
    whatsapp_params: list[str]  # {{1}} name, {{2}} cabinet, {{3}} date, {{4}} time


def render(kind: str, user: dict, start_utc: datetime, settings: ReminderSettings) -> Rendered:
    locale = user.get("locale", "en")
    if locale not in CONTACT:
        locale = "en"
    local = start_utc.astimezone(ZoneInfo(settings.timezone))
    ctx = {
        "name": user["name"],
        "cabinet": settings.cabinet_name,
        "date": local.strftime("%d.%m.%Y"),
        "time": local.strftime("%H:%M"),
    }
    subject, body = TEXTS[kind][locale]
    text = body.format(**ctx)
    if settings.cabinet_phone:
        text += "\n\n" + CONTACT[locale].format(phone=settings.cabinet_phone)
    html_body = "".join(f"<p>{html.escape(p)}</p>" for p in text.split("\n\n"))
    return Rendered(
        subject=subject,
        text=text,
        html=html_body,
        whatsapp_template=f"{get_config().whatsapp_template_prefix}{kind}",
        whatsapp_language=locale,
        whatsapp_params=[ctx["name"], ctx["cabinet"], ctx["date"], ctx["time"]],
    )


# ---------- providers ----------


def send_email(to_address: str, msg: Rendered) -> str:
    cfg = get_config()
    res = clients.sesv2().send_email(
        FromEmailAddress=cfg.ses_from_address,
        Destination={"ToAddresses": [to_address]},
        Content={
            "Simple": {
                "Subject": {"Data": msg.subject, "Charset": "UTF-8"},
                "Body": {
                    "Text": {"Data": msg.text, "Charset": "UTF-8"},
                    "Html": {"Data": msg.html, "Charset": "UTF-8"},
                },
            }
        },
    )
    return res["MessageId"]


def whatsapp_enabled() -> bool:
    return bool(get_config().whatsapp_phone_number_id)


def send_whatsapp(phone_e164: str, msg: Rendered) -> str:
    """Business-initiated WhatsApp messages must use a pre-approved template."""
    cfg = get_config()
    payload = {
        "messaging_product": "whatsapp",
        "to": phone_e164.lstrip("+"),
        "type": "template",
        "template": {
            "name": msg.whatsapp_template,
            "language": {"code": msg.whatsapp_language},
            "components": [
                {"type": "body", "parameters": [{"type": "text", "text": p} for p in msg.whatsapp_params]}
            ],
        },
    }
    res = clients.social_messaging().send_whatsapp_message(
        originationPhoneNumberId=cfg.whatsapp_phone_number_id,
        metaApiVersion=cfg.whatsapp_meta_api_version,
        message=json.dumps(payload).encode(),
    )
    return res["messageId"]

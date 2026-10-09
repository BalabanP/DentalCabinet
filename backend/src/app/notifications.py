"""Message rendering (i18n) and the e-mail / SMS providers.

Messages contain only name, cabinet, date and time. Never medical information.
"""

import base64
import json
import os
import re
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from botocore.exceptions import ClientError

from . import clients
from .config import get_config
from .log import logger
from .email_template import render_email
from .models import PLACEHOLDERS, MessageTemplates, ReminderSettings

# kind -> locale -> (e-mail subject, e-mail body). The built-in texts; Settings can override any of them.
# Placeholders: {name} {cabinet} {date} {time} {phone}
TEXTS = {
    "confirmation": {
        "en": ("Appointment confirmed", "Hello {name}, your appointment at {cabinet} is confirmed for {date} at {time}."),
        "ro": ("Programare confirmată", "Bună {name}, programarea dumneavoastră la {cabinet} este confirmată pentru {date}, ora {time}."),
    },
    "reminder": {
        "en": ("Appointment reminder", "Hello {name}, this is a reminder of your appointment at {cabinet} on {date} at {time}."),
        "ro": ("Reamintire programare", "Bună {name}, vă reamintim programarea la {cabinet} din {date}, ora {time}."),
    },
    "rescheduled": {
        "en": ("Appointment rescheduled", "Hello {name}, your appointment at {cabinet} has been moved to {date} at {time}."),
        "ro": ("Programare reprogramată", "Bună {name}, programarea la {cabinet} a fost mutată pe {date}, ora {time}."),
    },
    "cancelled": {
        "en": ("Appointment cancelled", "Hello {name}, your appointment at {cabinet} on {date} at {time} has been cancelled."),
        "ro": ("Programare anulată", "Bună {name}, programarea la {cabinet} din {date}, ora {time} a fost anulată."),
    },
}
# The built-in SMS texts are short on purpose: one SMS part holds 160 plain characters (70 with accents).
SMS_TEXTS = {
    "confirmation": {
        "en": "{cabinet}: your appointment is confirmed for {date} at {time}.",
        "ro": "{cabinet}: programarea dvs. este confirmata pe {date}, ora {time}.",
    },
    "reminder": {
        "en": "{cabinet}: reminder of your appointment on {date} at {time}.",
        "ro": "{cabinet}: va reamintim programarea din {date}, ora {time}.",
    },
    "rescheduled": {
        "en": "{cabinet}: your appointment was moved to {date} at {time}.",
        "ro": "{cabinet}: programarea a fost mutata pe {date}, ora {time}.",
    },
    "cancelled": {
        "en": "{cabinet}: your appointment on {date} at {time} was cancelled.",
        "ro": "{cabinet}: programarea din {date}, ora {time} a fost anulata.",
    },
}
CONTACT = {
    "en": "Questions? Call {phone}.",
    "ro": "Întrebări? Sunați la {phone}.",
}

_PLACEHOLDER = re.compile(r"\{(" + "|".join(PLACEHOLDERS) + r")\}")


def fill(template: str, ctx: dict) -> str:
    """Replace known {placeholders}. Unlike str.format this can never raise on odd text."""
    return _PLACEHOLDER.sub(lambda m: str(ctx.get(m.group(1), "")), template)


def default_fields(kind: str, locale: str) -> dict:
    subject, body = TEXTS[kind][locale]
    return {"email_subject": subject, "email_body": body, "sms_body": SMS_TEXTS[kind][locale]}


def all_defaults() -> dict:
    return {k: {loc: default_fields(k, loc) for loc in CONTACT} for k in TEXTS}


def effective_fields(kind: str, locale: str, templates: MessageTemplates | None) -> dict:
    """The built-in text, with any field customised in Settings replacing it."""
    fields = default_fields(kind, locale)
    custom = templates.overrides.get(kind, {}).get(locale) if templates else None
    if custom:
        fields.update({k: v for k, v in custom.model_dump(exclude_none=True).items() if v})
    return fields


# ---------- SMS length ----------

_GSM_BASIC = set(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
_GSM_EXTENDED = set("^{}\\[~]|€")


def sms_info(text: str) -> dict:
    """How many SMS parts a text needs once sent. Accents are folded first, as in `send_sms`."""
    text = sms_text(text)
    if all(c in _GSM_BASIC or c in _GSM_EXTENDED for c in text):
        length = sum(2 if c in _GSM_EXTENDED else 1 for c in text)
        single, multi, encoding = 160, 153, "GSM-7"
    else:
        length = len(text)
        single, multi, encoding = 70, 67, "Unicode"
    segments = 0 if length == 0 else 1 if length <= single else -(-length // multi)
    return {"encoding": encoding, "length": length, "segments": segments}


@dataclass(frozen=True)
class Rendered:
    subject: str
    text: str  # plain-text e-mail
    html: str  # designed e-mail
    sms: str  # SMS text before accent folding


def render(
    kind: str, user: dict, start_utc: datetime, settings: ReminderSettings, templates: MessageTemplates | None = None
) -> Rendered:
    locale = user.get("locale", "en")
    if locale not in CONTACT:
        locale = "en"
    local = start_utc.astimezone(ZoneInfo(settings.timezone))
    ctx = {
        "name": user["name"],
        "cabinet": settings.cabinet_name,
        "date": local.strftime("%d.%m.%Y"),
        "time": local.strftime("%H:%M"),
        "phone": settings.cabinet_phone or "",
    }
    fields = effective_fields(kind, locale, templates)
    subject = fill(fields["email_subject"], ctx)
    body = fill(fields["email_body"], ctx)
    contact = CONTACT[locale].format(phone=settings.cabinet_phone) if settings.cabinet_phone else ""
    text = body + ("\n\n" + contact if contact else "")
    html_body = render_email(
        kind=kind, locale=locale, subject=subject, body=body, cabinet=ctx["cabinet"],
        date=ctx["date"], time=ctx["time"], contact=contact,
    )
    return Rendered(subject=subject, text=text, html=html_body, sms=fill(fields["sms_body"], ctx))


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


# ---------- SMS through an Android phone ("SMS Gateway for Android") ----------

DEFAULT_SMS_URL = "https://api.sms-gate.app/3rdparty/v1/messages"
_sms_gateway_cache: dict | None = None


def sms_text(text: str) -> str:
    """Fold Latin letters with diacritics to plain ASCII (ă→a, ș→s) so a message fits fewer SMS parts.

    One non-GSM letter makes a phone send UCS-2, which allows only 70 characters per part instead of 160.
    Other scripts are left untouched: transliterating them would change the words.
    """
    out = []
    for ch in text:
        if ord(ch) > 127 and unicodedata.name(ch, "").startswith("LATIN"):
            ch = "".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c))
        out.append(ch)
    return "".join(out)


def _sms_gateway() -> dict | None:
    """Gateway URL and credentials from Parameter Store (a SecureString holding JSON), or None if not set up.

    The value looks like {"username": "...", "password": "...", "url": "<optional>"}. A found value is
    cached for the life of the container; "not configured" is re-checked every time.
    """
    global _sms_gateway_cache
    if _sms_gateway_cache is not None:
        return _sms_gateway_cache
    name = os.environ.get("SMS_GATEWAY_PARAMETER")
    if not name:
        return None
    try:
        value = clients.ssm().get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ParameterNotFound":
            return None
        raise
    cfg = json.loads(value)
    if not cfg.get("username") or not cfg.get("password"):
        return None
    cfg["url"] = cfg.get("url") or DEFAULT_SMS_URL
    if not cfg["url"].startswith(("https://", "http://")):
        raise ValueError("SMS gateway url must be http(s)")
    _sms_gateway_cache = cfg
    return cfg


def sms_enabled() -> bool:
    return _sms_gateway() is not None


def send_sms(phone_e164: str, msg: Rendered) -> str:
    """Hand the text to the gateway. The phone sends it, so "accepted" means queued, not yet delivered."""
    gateway = _sms_gateway()
    if gateway is None:
        raise RuntimeError("SMS gateway is not configured")
    token = base64.b64encode(f"{gateway['username']}:{gateway['password']}".encode()).decode()
    request = urllib.request.Request(
        gateway["url"],
        data=json.dumps({"textMessage": {"text": sms_text(msg.sms)}, "phoneNumbers": [phone_e164]}).encode(),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Basic {token}",
            # Web firewalls in front of APIs often answer 403 to Python's default "Python-urllib" agent.
            "User-Agent": "medical-cabinet/1.0 (+reminder-sender)",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as res:
            body = json.loads(res.read() or b"{}")
    except urllib.error.HTTPError as exc:
        # The reply text says why (blocked agent, bad login, no device online). Logged, never stored in the DB.
        try:
            reply = exc.read(300).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            reply = ""
        logger.warning("sms gateway refused the request", extra={"status": exc.code, "reply": reply})
        raise
    return str(body.get("id") or "accepted")

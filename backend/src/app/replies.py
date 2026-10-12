"""Visitors answering a reminder SMS ("CONFIRM" / "CANCEL"), delivered by the SMS gateway's webhook."""

import hashlib
import hmac
import json
import os
import re
import unicodedata

from botocore.exceptions import ClientError

from . import clients, repositories as repo, services
from .log import logger
from .timeutil import to_iso, utc_now

CONFIRM_WORDS = {"confirm", "confirma", "confirmat", "confirmare", "da", "yes", "ok", "okay"}
# "no" / "nu" are deliberately not here: "nu pot veni" may mean "move it", so staff should read it.
CANCEL_WORDS = {"cancel", "cancelled", "anuleaza", "anulez", "anulare", "anulat"}
MAX_WORDS = 4  # longer messages are conversations, not answers

_webhook_cache: dict | None = None


def parse_reply(text: str) -> str | None:
    """"confirm", "cancel", or None when the text is not a clear answer."""
    plain = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    words = re.findall(r"[a-z0-9]+", plain)
    if not words or len(words) > MAX_WORDS:
        return None
    confirm = any(w in CONFIRM_WORDS for w in words)
    cancel = any(w in CANCEL_WORDS for w in words)
    if confirm == cancel:  # neither, or contradictory
        return None
    return "confirm" if confirm else "cancel"


# ---------- who is calling ----------


def webhook_config() -> dict | None:
    """{"token": "...", "signing_key": "<optional>"} from Parameter Store, or None when not set up.

    SMS_WEBHOOK_TOKEN is a shortcut for local development only.
    """
    global _webhook_cache
    if _webhook_cache is not None:
        return _webhook_cache
    local = os.environ.get("SMS_WEBHOOK_TOKEN")
    if local:
        return {"token": local}
    name = os.environ.get("SMS_WEBHOOK_PARAMETER")
    if not name:
        return None
    try:
        value = clients.ssm().get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ParameterNotFound":
            return None
        raise
    cfg = json.loads(value)
    if not cfg.get("token") or len(cfg["token"]) < 16:
        return None
    _webhook_cache = cfg
    return cfg


def authentic(cfg: dict, token: str, body: bytes, signature: str | None, timestamp: str | None) -> bool:
    """The secret in the URL must match; when a signing key is configured the HMAC must match too."""
    if not hmac.compare_digest(token.encode(), cfg["token"].encode()):
        return False
    key = cfg.get("signing_key")
    if not key:
        return True
    if not signature or not timestamp:
        return False
    expected = hmac.new(key.encode(), body + timestamp.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.strip().lower())


# ---------- what to do with a reply ----------


def _inbox(reason: str, sender: str, text: str, received_at: str, user: dict | None, appointment_id: str | None):
    repo.add_reply(
        {
            "sender": sender[:30],
            "text": text[:320],
            "received_at": received_at,
            "reason": reason,
            "user_id": user["user_id"] if user else None,
            "user_name": user["name"] if user else None,
            "appointment_ref": appointment_id,
        }
    )


def handle_incoming(sender: str, text: str, message_id: str, received_at: str | None = None) -> str:
    """Apply a visitor's SMS answer. Returns what happened (for tests and logs; never the text itself)."""
    if not repo.claim_inbound(message_id):  # the gateway retries until it gets a 2xx
        return "duplicate"
    received_at = received_at or to_iso(utc_now())

    users = repo.find_users_by_phone(sender)
    if not users:
        _inbox("unknown_sender", sender, text, received_at, None, None)
        return "unknown_sender"
    user = users[0]

    now = to_iso(utc_now())
    upcoming = [
        a
        for u in users
        for a in repo.query_appointments_by_user(u["user_id"])
        if a["status"] == "scheduled" and a["start_at"] > now
    ]

    action = parse_reply(text)
    if action is None:
        _inbox("unrecognised", sender, text, received_at, user, upcoming[0]["appointment_id"] if len(upcoming) == 1 else None)
        return "unrecognised"
    if not upcoming:
        _inbox("no_upcoming", sender, text, received_at, user, None)
        return "no_upcoming"
    if len(upcoming) > 1:  # which visit does "CONFIRM" mean? Let reception sort it out.
        _inbox("several_upcoming", sender, text, received_at, user, None)
        return "several_upcoming"

    appointment_id = upcoming[0]["appointment_id"]
    if action == "confirm":
        services.confirm(appointment_id, via="sms")
    else:
        services.cancel(appointment_id, via="sms")
    logger.info("sms reply applied", extra={"appointment_id": appointment_id, "action": action})
    return action

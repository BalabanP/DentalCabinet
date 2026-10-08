"""DynamoDB access. No business rules live here beyond uniqueness/existence checks."""

import base64
import json
import re
import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any, Optional

from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError

from . import clients
from .config import get_config
from .errors import BadRequest, Conflict, NotFound
from .models import ReminderSettings, ScheduleSettings
from .timeutil import to_iso, utc_now

NOTIFICATION_RETENTION_DAYS = 365
CLAIM_STALE_MINUTES = 5


# ---------- helpers ----------


def clean(obj: Any) -> Any:
    """Convert DynamoDB Decimals into int/float so results are JSON-serialisable."""
    if isinstance(obj, Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    if isinstance(obj, list):
        return [clean(v) for v in obj]
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items()}
    return obj


def encode_token(key: Optional[dict]) -> Optional[str]:
    if not key:
        return None
    return base64.urlsafe_b64encode(json.dumps(key).encode()).decode()


def decode_token(token: Optional[str]) -> Optional[dict]:
    if not token:
        return None
    try:
        return json.loads(base64.urlsafe_b64decode(token.encode()))
    except Exception as exc:  # noqa: BLE001
        raise BadRequest("invalid next_token") from exc


def _is_conditional_failure(exc: ClientError) -> bool:
    return exc.response["Error"]["Code"] == "ConditionalCheckFailedException"


def _update(tbl, key: dict, changes: dict, key_attr: str) -> dict:
    """SET the given attributes on an existing item; NotFound when the item does not exist."""
    names = {f"#f{i}": k for i, k in enumerate(changes)}
    values = {f":v{i}": v for i, v in enumerate(changes.values())}
    expr = "SET " + ", ".join(f"#f{i} = :v{i}" for i in range(len(changes)))
    try:
        res = tbl.update_item(
            Key=key,
            UpdateExpression=expr,
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
            ConditionExpression=Attr(key_attr).exists(),
            ReturnValues="ALL_NEW",
        )
    except ClientError as exc:
        if _is_conditional_failure(exc):
            raise NotFound("not found") from exc
        raise
    return clean(res["Attributes"])


def _query_all(tbl, **kwargs) -> list[dict]:
    items: list[dict] = []
    while True:
        res = tbl.query(**kwargs)
        items.extend(res["Items"])
        if "LastEvaluatedKey" not in res:
            return clean(items)
        kwargs["ExclusiveStartKey"] = res["LastEvaluatedKey"]


# ---------- users ----------


def _users():
    return clients.table(get_config().users_table)


def find_user_by_email(email: str) -> Optional[dict]:
    res = _users().query(IndexName="email-index", KeyConditionExpression=Key("email").eq(email.lower()), Limit=1)
    return clean(res["Items"][0]) if res["Items"] else None


def create_user(data: dict) -> dict:
    if find_user_by_email(data["email"]):
        raise Conflict("a user with this email already exists")
    now = to_iso(utc_now())
    item = {"user_id": uuid.uuid4().hex, **data, "created_at": now, "updated_at": now}
    _users().put_item(Item=item, ConditionExpression=Attr("user_id").not_exists())
    return item


def get_user(user_id: str) -> Optional[dict]:
    res = _users().get_item(Key={"user_id": user_id})
    return clean(res["Item"]) if "Item" in res else None


def list_users(limit: int, token: Optional[str]) -> tuple[list[dict], Optional[str]]:
    kwargs: dict = {"Limit": limit}
    start = decode_token(token)
    if start:
        kwargs["ExclusiveStartKey"] = start
    res = _users().scan(**kwargs)
    return clean(res["Items"]), encode_token(res.get("LastEvaluatedKey"))


def search_users(query: str, limit: int) -> list[dict]:
    """Case-insensitive substring match on name, email or phone digits.

    Scans the table, which is fine for one cabinet. Add a search index if it grows large.
    """
    q = query.strip().lower()
    q_digits = re.sub(r"\D", "", q)
    matches: list[dict] = []
    kwargs: dict = {}
    while True:
        res = _users().scan(**kwargs)
        for u in clean(res["Items"]):
            if (
                q in u["name"].lower()
                or q in u["email"]
                or (len(q_digits) >= 3 and q_digits in re.sub(r"\D", "", u["phone"]))
            ):
                matches.append(u)
        if "LastEvaluatedKey" not in res:
            break
        kwargs["ExclusiveStartKey"] = res["LastEvaluatedKey"]
    matches.sort(key=lambda u: (not u["name"].lower().startswith(q), u["name"].lower()))
    return matches[:limit]


def update_user(user_id: str, changes: dict) -> dict:
    if "email" in changes:
        other = find_user_by_email(changes["email"])
        if other and other["user_id"] != user_id:
            raise Conflict("a user with this email already exists")
    changes = {**changes, "updated_at": to_iso(utc_now())}
    return _update(_users(), {"user_id": user_id}, changes, "user_id")


def delete_user(user_id: str) -> None:
    _users().delete_item(Key={"user_id": user_id})


# ---------- patients ----------


def _patients():
    return clients.table(get_config().patients_table)


def get_patient(user_id: str) -> Optional[dict]:
    res = _patients().get_item(Key={"user_id": user_id})
    return clean(res["Item"]) if "Item" in res else None


def upsert_patient(user_id: str, notes: str, updated_by: str) -> dict:
    item = {"user_id": user_id, "notes": notes, "updated_at": to_iso(utc_now()), "updated_by": updated_by}
    _patients().put_item(Item=item)
    return item


def delete_patient(user_id: str) -> None:
    _patients().delete_item(Key={"user_id": user_id})


# ---------- appointments ----------


def _appointments():
    return clients.table(get_config().appointments_table)


def put_appointment(item: dict) -> None:
    _appointments().put_item(Item=item, ConditionExpression=Attr("appointment_id").not_exists())


def get_appointment(appointment_id: str) -> Optional[dict]:
    res = _appointments().get_item(Key={"appointment_id": appointment_id})
    return clean(res["Item"]) if "Item" in res else None


def update_appointment(appointment_id: str, changes: dict) -> dict:
    changes = {**changes, "updated_at": to_iso(utc_now())}
    return _update(_appointments(), {"appointment_id": appointment_id}, changes, "appointment_id")


def delete_appointment(appointment_id: str) -> None:
    _appointments().delete_item(Key={"appointment_id": appointment_id})


def query_appointments_by_days(days: list[str]) -> list[dict]:
    items: list[dict] = []
    for day in days:
        items.extend(
            _query_all(_appointments(), IndexName="day-index", KeyConditionExpression=Key("day").eq(day))
        )
    return sorted(items, key=lambda a: a["start_at"])


def query_appointments_by_user(user_id: str) -> list[dict]:
    return _query_all(
        _appointments(),
        IndexName="user-index",
        KeyConditionExpression=Key("user_id").eq(user_id),
        ScanIndexForward=False,
    )


def scan_future_scheduled(now_iso: str) -> list[dict]:
    items: list[dict] = []
    kwargs: dict = {"FilterExpression": Attr("status").eq("scheduled") & Attr("start_at").gt(now_iso)}
    while True:
        res = _appointments().scan(**kwargs)
        items.extend(res["Items"])
        if "LastEvaluatedKey" not in res:
            return clean(items)
        kwargs["ExclusiveStartKey"] = res["LastEvaluatedKey"]


# ---------- settings ----------

def _get_setting(setting_id: str, model):
    res = clients.table(get_config().settings_table).get_item(Key={"setting_id": setting_id})
    if "Item" not in res:
        return model()
    return model.model_validate(clean(res["Item"]["config"]))


def _put_setting(setting_id: str, settings):
    clients.table(get_config().settings_table).put_item(
        Item={"setting_id": setting_id, "config": settings.model_dump(mode="json"), "updated_at": to_iso(utc_now())}
    )
    return settings


def get_reminder_settings() -> ReminderSettings:
    return _get_setting("reminders", ReminderSettings)


def put_reminder_settings(settings: ReminderSettings) -> ReminderSettings:
    return _put_setting("reminders", settings)


def get_schedule_settings() -> ScheduleSettings:
    return _get_setting("schedule", ScheduleSettings)


def put_schedule_settings(settings: ScheduleSettings) -> ScheduleSettings:
    return _put_setting("schedule", settings)


# ---------- notifications (delivery log + idempotency) ----------


def _notifications():
    return clients.table(get_config().notifications_table)


def list_notifications(appointment_id: str) -> list[dict]:
    return _query_all(_notifications(), KeyConditionExpression=Key("appointment_id").eq(appointment_id))


def claim_notification(appointment_id: str, sk: str, kind: str, channel: str) -> bool:
    """Reserve the right to send. False when already sent or currently being sent by someone else."""
    now = utc_now()
    item = {
        "appointment_id": appointment_id,
        "sk": sk,
        "kind": kind,
        "channel": channel,
        "status": "sending",
        "updated_at": to_iso(now),
        "ttl": int((now + timedelta(days=NOTIFICATION_RETENTION_DAYS)).timestamp()),
    }
    stale = to_iso(now - timedelta(minutes=CLAIM_STALE_MINUTES))
    try:
        _notifications().put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(sk) OR #s = :failed OR (#s = :sending AND updated_at < :stale)",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":failed": "failed", ":sending": "sending", ":stale": stale},
        )
    except ClientError as exc:
        if _is_conditional_failure(exc):
            return False
        raise
    return True


def finish_notification(
    appointment_id: str, sk: str, status: str, provider_id: Optional[str] = None, error: Optional[str] = None
) -> None:
    changes: dict = {"status": status, "updated_at": to_iso(utc_now())}
    if provider_id:
        changes["provider_id"] = provider_id
    if error:
        changes["error"] = error[:300]
    _update(_notifications(), {"appointment_id": appointment_id, "sk": sk}, changes, "appointment_id")

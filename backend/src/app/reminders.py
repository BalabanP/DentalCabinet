"""Reminder planning and the EventBridge Scheduler / SQS plumbing."""

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable

from botocore.exceptions import ClientError

from . import clients
from .config import get_config
from .log import logger
from .models import ReminderRule
from .timeutil import to_iso

# EventBridge Scheduler rejects `at()` times in the past, so require some lead time.
MIN_LEAD = timedelta(seconds=90)

KIND_REMINDER = "reminder"
KIND_CONFIRMATION = "confirmation"
KIND_RESCHEDULED = "rescheduled"
KIND_CANCELLED = "cancelled"


@dataclass(frozen=True)
class PlannedReminder:
    key: str
    minutes_before: int
    send_at: datetime  # UTC


def plan_reminders(start_utc: datetime, rules: Iterable[ReminderRule], now: datetime) -> list[PlannedReminder]:
    """Reminders that are enabled and still in the future, earliest first.

    A visit booked tomorrow therefore never gets a "3 days before" reminder.
    """
    planned = []
    for rule in sorted(rules, key=lambda r: -r.minutes_before):
        if not rule.enabled:
            continue
        send_at = start_utc - timedelta(minutes=rule.minutes_before)
        if send_at - now < MIN_LEAD:
            continue
        planned.append(PlannedReminder(rule.key, rule.minutes_before, send_at))
    return planned


def schedule_name(appointment_id: str, start_utc: datetime, key: str) -> str:
    # <=64 chars, [A-Za-z0-9-_.]; includes the start time so a reschedule never collides.
    return f"{appointment_id}-{start_utc:%Y%m%d%H%M}-{key}"


def build_message(appointment_id: str, start_utc: datetime, kind: str, key: str | None = None) -> dict:
    start_iso = to_iso(start_utc)
    # `dedupe` identifies one logical notification. Reminders are deduped per (start time, offset);
    # one-off notices get a nonce so that e.g. A->B->A->B reschedules each notify.
    dedupe = f"{kind}#{start_iso}#{key}" if kind == KIND_REMINDER else f"{kind}#{uuid.uuid4().hex}"
    return {"appointment_id": appointment_id, "start_at": start_iso, "kind": kind, "dedupe": dedupe}


def create_schedules(appointment_id: str, start_utc: datetime, planned: list[PlannedReminder]) -> list[dict]:
    """Create one one-time schedule per planned reminder. All-or-nothing."""
    cfg = get_config()
    created: list[dict] = []
    try:
        for p in planned:
            name = schedule_name(appointment_id, start_utc, p.key)
            clients.scheduler().create_schedule(
                Name=name,
                GroupName=cfg.scheduler_group,
                ScheduleExpression=f"at({p.send_at:%Y-%m-%dT%H:%M:%S})",
                ScheduleExpressionTimezone="UTC",
                FlexibleTimeWindow={"Mode": "OFF"},
                ActionAfterCompletion="DELETE",
                Target={
                    "Arn": cfg.queue_arn,
                    "RoleArn": cfg.scheduler_role_arn,
                    "Input": json.dumps(build_message(appointment_id, start_utc, KIND_REMINDER, p.key)),
                    "RetryPolicy": {"MaximumRetryAttempts": 5, "MaximumEventAgeInSeconds": 3600},
                },
            )
            created.append(
                {
                    "key": p.key,
                    "minutes_before": p.minutes_before,
                    "send_at": to_iso(p.send_at),
                    "schedule_name": name,
                }
            )
    except Exception:
        delete_schedules(created)
        raise
    return created


def delete_schedules(reminders: list[dict]) -> None:
    """Best effort: a leftover schedule is harmless because the sender drops stale messages."""
    cfg = get_config()
    for r in reminders:
        try:
            clients.scheduler().delete_schedule(Name=r["schedule_name"], GroupName=cfg.scheduler_group)
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ResourceNotFoundException":
                logger.warning("could not delete schedule", extra={"schedule": r["schedule_name"]})


def enqueue_notice(appointment_id: str, start_utc: datetime, kind: str) -> None:
    """Send a one-off notice (confirmation / rescheduled / cancelled) through the same queue."""
    try:
        clients.sqs().send_message(
            QueueUrl=get_config().queue_url,
            MessageBody=json.dumps(build_message(appointment_id, start_utc, kind)),
        )
    except Exception:  # the appointment is already saved; do not fail the request over a notice
        logger.exception("could not enqueue notice", extra={"appointment_id": appointment_id, "kind": kind})

"""Business rules: booking, rescheduling, cancelling and keeping reminder schedules in sync."""

import uuid
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from . import reminders, repositories as repo
from .errors import BadRequest, Conflict, NotFound
from .log import logger
from .models import WEEKDAYS, AppointmentIn, AppointmentPatch
from .timeutil import days_between, local_day, parse_iso, to_iso, to_utc, utc_now

MAX_AVAILABILITY_DAYS = 14


def _get_or_404(appointment_id: str) -> dict:
    appt = repo.get_appointment(appointment_id)
    if not appt:
        raise NotFound("appointment not found")
    return appt


# ---------- overlap checks and free slots ----------


def _scheduled_around(start: datetime, tz: ZoneInfo, days_before: int = 1, days_after: int = 1) -> list[dict]:
    """Scheduled appointments on the local day of `start` and its neighbours (covers midnight spill)."""
    day = start.astimezone(tz).date()
    days = [(day + timedelta(days=i)).isoformat() for i in range(-days_before, days_after + 1)]
    return [a for a in repo.query_appointments_by_days(days) if a["status"] == "scheduled"]


def _overlaps(start: datetime, minutes: int, existing: list[dict], exclude_id: str | None = None) -> bool:
    end = start + timedelta(minutes=minutes)
    for a in existing:
        if a["appointment_id"] == exclude_id:
            continue
        a_start = parse_iso(a["start_at"])
        if start < a_start + timedelta(minutes=a["duration_minutes"]) and a_start < end:
            return True
    return False


def _ensure_free(start: datetime, minutes: int, tz: ZoneInfo, exclude_id: str | None = None) -> None:
    if _overlaps(start, minutes, _scheduled_around(start, tz), exclude_id):
        raise Conflict("this time overlaps another appointment (use allow_overlap to override)")


def _ensure_working_hours(start: datetime, minutes: int, tz: ZoneInfo) -> None:
    """The whole visit must fit inside one working-hours window of the cabinet's local day."""
    local = start.astimezone(tz)
    end = local + timedelta(minutes=minutes)
    if end.date() == local.date():
        day = WEEKDAYS[local.weekday()]
        begin, finish = local.strftime("%H:%M"), end.strftime("%H:%M")
        for window in repo.get_schedule_settings().working_hours.get(day, []):
            if window.start <= begin and finish <= window.end:
                return
    raise BadRequest("this time is outside working hours")


def free_slots(date_from: str, date_to: str | None, duration_minutes: int | None) -> dict:
    """Start times inside working hours that are in the future and do not overlap a booking."""
    schedule = repo.get_schedule_settings()
    tz = repo.get_reminder_settings().tz
    duration = duration_minutes or schedule.slot_minutes
    try:
        days = days_between(date_from, date_to or date_from, MAX_AVAILABILITY_DAYS)
    except ValueError as exc:
        raise BadRequest(str(exc)) from exc

    booked = [
        a
        for a in repo.query_appointments_by_days(
            [(datetime.fromisoformat(days[0]).date() - timedelta(days=1)).isoformat(), *days,
             (datetime.fromisoformat(days[-1]).date() + timedelta(days=1)).isoformat()]
        )
        if a["status"] == "scheduled"
    ]
    now = utc_now()
    slots = []
    for day in days:
        d = datetime.fromisoformat(day).date()
        for window in schedule.working_hours.get(WEEKDAYS[d.weekday()], []):
            w_start = datetime.combine(d, time.fromisoformat(window.start), tzinfo=tz)
            w_end = datetime.combine(d, time.fromisoformat(window.end), tzinfo=tz)
            cursor = w_start
            while cursor + timedelta(minutes=duration) <= w_end:
                start_utc = to_utc(cursor, tz)
                if start_utc > now and not _overlaps(start_utc, duration, booked):
                    slots.append({"day": day, "local_time": cursor.strftime("%H:%M"), "start_at": to_iso(start_utc)})
                cursor += timedelta(minutes=schedule.slot_minutes)
    return {"duration_minutes": duration, "timezone": str(tz), "slots": slots}


# ---------- appointments ----------


def book(data: AppointmentIn) -> dict:
    settings = repo.get_reminder_settings()
    if not repo.get_user(data.user_id):
        raise NotFound("user not found")

    now = utc_now()
    start = to_utc(data.start_at, settings.tz)
    if start <= now:
        raise BadRequest("start_at must be in the future")
    _ensure_working_hours(start, data.duration_minutes, settings.tz)
    if not data.allow_overlap:
        _ensure_free(start, data.duration_minutes, settings.tz)

    appointment_id = uuid.uuid4().hex
    created = reminders.create_schedules(
        appointment_id, start, reminders.plan_reminders(start, settings.reminders, now)
    )
    item = {
        "appointment_id": appointment_id,
        "user_id": data.user_id,
        "start_at": to_iso(start),
        "day": local_day(start, settings.tz),
        "duration_minutes": data.duration_minutes,
        "status": "scheduled",
        "reminders": created,
        "created_at": to_iso(now),
        "updated_at": to_iso(now),
    }
    if data.internal_note:
        item["internal_note"] = data.internal_note
    try:
        repo.put_appointment(item)
    except Exception:
        reminders.delete_schedules(created)
        raise

    if settings.send_on_booking:
        reminders.enqueue_notice(appointment_id, start, reminders.KIND_CONFIRMATION)
    return item


def update(appointment_id: str, patch: AppointmentPatch) -> dict:
    appt = _get_or_404(appointment_id)
    fields = patch.model_dump(exclude_unset=True)
    allow_overlap = fields.pop("allow_overlap", False)
    if not fields:
        raise BadRequest("nothing to update")
    if "status" in fields and "start_at" in fields:
        raise BadRequest("change either the time or the status, not both")

    changes: dict = {}
    for name in ("duration_minutes", "internal_note"):
        if name in fields:
            changes[name] = fields[name]

    old_reminders = appt.get("reminders", [])
    new_reminders: list[dict] | None = None
    notice_start = None

    if "status" in fields:
        if appt["status"] != "scheduled":
            raise Conflict(f"appointment is already {appt['status']}")
        changes["status"] = fields["status"]
        changes["reminders"] = []
    elif "start_at" in fields:
        if appt["status"] != "scheduled":
            raise Conflict(f"cannot reschedule an appointment that is {appt['status']}")
        settings = repo.get_reminder_settings()
        now = utc_now()
        start = to_utc(fields["start_at"], settings.tz)
        if start <= now:
            raise BadRequest("start_at must be in the future")
        minutes = fields.get("duration_minutes") or appt["duration_minutes"]
        _ensure_working_hours(start, minutes, settings.tz)
        if not allow_overlap:
            _ensure_free(start, minutes, settings.tz, appointment_id)
        if to_iso(start) != appt["start_at"]:
            new_reminders = reminders.create_schedules(
                appointment_id, start, reminders.plan_reminders(start, settings.reminders, now)
            )
            changes.update(start_at=to_iso(start), day=local_day(start, settings.tz), reminders=new_reminders)
            if settings.send_on_change:
                notice_start = start
    elif "duration_minutes" in fields and appt["status"] == "scheduled" and not allow_overlap:
        _ensure_free(
            parse_iso(appt["start_at"]), fields["duration_minutes"], repo.get_reminder_settings().tz, appointment_id
        )

    try:
        saved = repo.update_appointment(appointment_id, changes)
    except Exception:
        if new_reminders:
            reminders.delete_schedules(new_reminders)
        raise

    if "reminders" in changes:
        reminders.delete_schedules(old_reminders)
    if notice_start:
        reminders.enqueue_notice(appointment_id, notice_start, reminders.KIND_RESCHEDULED)
    return saved


def cancel(appointment_id: str) -> dict:
    appt = _get_or_404(appointment_id)
    if appt["status"] == "cancelled":
        return appt
    if appt["status"] != "scheduled":
        raise Conflict(f"cannot cancel an appointment that is {appt['status']}")

    saved = repo.update_appointment(appointment_id, {"status": "cancelled", "reminders": []})
    reminders.delete_schedules(appt.get("reminders", []))

    start = parse_iso(appt["start_at"])
    if repo.get_reminder_settings().send_on_change and start > utc_now():
        reminders.enqueue_notice(appointment_id, start, reminders.KIND_CANCELLED)
    return saved


def reapply_reminders() -> dict:
    """Re-plan reminders of all future scheduled appointments after the settings changed.

    Does not send a new confirmation. Appointments are processed independently; failures are counted.
    """
    settings = repo.get_reminder_settings()
    now = utc_now()
    updated = failed = 0
    for appt in repo.scan_future_scheduled(to_iso(now)):
        start = parse_iso(appt["start_at"])
        try:
            reminders.delete_schedules(appt.get("reminders", []))
            created = reminders.create_schedules(
                appt["appointment_id"], start, reminders.plan_reminders(start, settings.reminders, now)
            )
            repo.update_appointment(appt["appointment_id"], {"reminders": created})
            updated += 1
        except Exception:
            failed += 1
            logger.exception("failed to re-plan reminders", extra={"appointment_id": appt["appointment_id"]})
    return {"updated": updated, "failed": failed}


def delete_user(user_id: str) -> None:
    if not repo.get_user(user_id):
        raise NotFound("user not found")
    now = to_iso(utc_now())
    upcoming = [
        a for a in repo.query_appointments_by_user(user_id) if a["status"] == "scheduled" and a["start_at"] > now
    ]
    if upcoming:
        raise Conflict("user has upcoming appointments; cancel them first")
    repo.delete_patient(user_id)
    repo.delete_user(user_id)

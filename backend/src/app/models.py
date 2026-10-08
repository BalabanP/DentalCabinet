from datetime import datetime
from typing import Annotated, Literal, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
    computed_field,
    field_validator,
    model_validator,
)

Phone = Annotated[str, StringConstraints(pattern=r"^\+[1-9]\d{6,14}$")]
Locale = Literal["en", "ro", "ru"]

MAX_REMINDER_MINUTES = 60 * 24 * 60  # 60 days
MAX_REMINDERS = 10


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ---------- users ----------


class UserIn(_Strict):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    phone: Phone
    locale: Locale = "en"
    notify_email: bool = True
    notify_whatsapp: bool = True

    @field_validator("email")
    @classmethod
    def _lower_email(cls, v: str) -> str:
        return v.lower()


class UserPatch(_Strict):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    email: Optional[EmailStr] = None
    phone: Optional[Phone] = None
    locale: Optional[Locale] = None
    notify_email: Optional[bool] = None
    notify_whatsapp: Optional[bool] = None

    @field_validator("email")
    @classmethod
    def _lower_email(cls, v: Optional[str]) -> Optional[str]:
        return v.lower() if v else v


# ---------- patients ----------


class PatientIn(_Strict):
    notes: str = Field(max_length=20000)


# ---------- appointments ----------


class AppointmentIn(_Strict):
    user_id: str = Field(min_length=1, max_length=64)
    start_at: datetime  # timezone-aware, or naive (interpreted in the cabinet timezone)
    duration_minutes: int = Field(default=30, ge=5, le=480)
    internal_note: Optional[str] = Field(default=None, max_length=500)
    allow_overlap: bool = False  # explicit staff override of the double-booking check


class AppointmentPatch(_Strict):
    start_at: Optional[datetime] = None
    duration_minutes: Optional[int] = Field(default=None, ge=5, le=480)
    internal_note: Optional[str] = Field(default=None, max_length=500)
    status: Optional[Literal["completed", "no_show"]] = None
    allow_overlap: bool = False


# ---------- working hours (availability) ----------

Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WEEKDAYS: tuple[str, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
HHMM = Annotated[str, StringConstraints(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]


class TimeWindow(_Strict):
    start: HHMM
    end: HHMM

    @model_validator(mode="after")
    def _ordered(self):
        if self.end <= self.start:
            raise ValueError("end must be after start")
        return self


def _default_hours() -> dict[str, list[TimeWindow]]:
    weekday = [TimeWindow(start="09:00", end="17:00")]
    return {d: (list(weekday) if d in WEEKDAYS[:5] else []) for d in WEEKDAYS}


class ScheduleSettings(_Strict):
    slot_minutes: int = Field(default=30, ge=5, le=240)  # spacing of offered start times
    working_hours: dict[Weekday, list[TimeWindow]] = Field(default_factory=_default_hours)

    @field_validator("working_hours")
    @classmethod
    def _no_overlapping_windows(cls, v):
        for day, windows in v.items():
            ordered = sorted(windows, key=lambda w: w.start)
            for a, b in zip(ordered, ordered[1:]):
                if b.start < a.end:
                    raise ValueError(f"overlapping working hours on {day}")
        return v


# ---------- reminder settings ----------


class ReminderRule(BaseModel):
    model_config = ConfigDict(extra="ignore")

    minutes_before: int = Field(gt=0, le=MAX_REMINDER_MINUTES)
    enabled: bool = True

    @computed_field  # type: ignore[prop-decorator]
    @property
    def key(self) -> str:
        return f"m{self.minutes_before}"


class Channels(_Strict):
    email: bool = True
    whatsapp: bool = True


class ReminderSettings(_Strict):
    cabinet_name: str = Field(default="Medical Cabinet", min_length=1, max_length=120)
    cabinet_phone: Optional[str] = Field(default=None, max_length=32)
    timezone: str = "UTC"
    send_on_booking: bool = True  # confirmation right after the appointment is made
    send_on_change: bool = True  # notice on reschedule / cancel
    channels: Channels = Channels()
    reminders: list[ReminderRule] = Field(
        default_factory=lambda: [
            ReminderRule(minutes_before=3 * 24 * 60),
            ReminderRule(minutes_before=24 * 60),
            ReminderRule(minutes_before=2 * 60),
        ],
        max_length=MAX_REMINDERS,
    )

    @field_validator("timezone")
    @classmethod
    def _valid_tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone: {v}") from exc
        return v

    @model_validator(mode="after")
    def _unique_offsets(self):
        minutes = [r.minutes_before for r in self.reminders]
        if len(minutes) != len(set(minutes)):
            raise ValueError("reminders must have unique minutes_before values")
        return self

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

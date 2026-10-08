from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ISO_FMT = "%Y-%m-%dT%H:%M:%SZ"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(dt: datetime) -> str:
    """UTC ISO-8601 with second precision; sorts lexicographically."""
    return dt.astimezone(timezone.utc).strftime(ISO_FMT)


def parse_iso(value: str) -> datetime:
    return datetime.strptime(value, ISO_FMT).replace(tzinfo=timezone.utc)


def to_utc(dt: datetime, default_tz: ZoneInfo) -> datetime:
    """Naive datetimes are interpreted in the cabinet timezone; result is minute-aligned UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=default_tz)
    return dt.astimezone(timezone.utc).replace(second=0, microsecond=0)


def local_day(dt_utc: datetime, tz: ZoneInfo) -> str:
    return dt_utc.astimezone(tz).date().isoformat()


def days_between(start: str, end: str, max_days: int = 62) -> list[str]:
    """Inclusive list of YYYY-MM-DD strings."""
    d0 = datetime.fromisoformat(start).date()
    d1 = datetime.fromisoformat(end).date()
    if d1 < d0:
        raise ValueError("'to' must not be before 'from'")
    if (d1 - d0).days + 1 > max_days:
        raise ValueError(f"range is limited to {max_days} days")
    return [(d0 + timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]

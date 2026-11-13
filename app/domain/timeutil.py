"""Pure time helpers: local/UTC conversion, HH:MM clocks, service windows, scheduled departures.

Catalog conventions (DATA_DICTIONARY §5): times are local to the place, without offsets;
"24:00" is midnight at the end of the day; a window whose end is not after its start runs
past midnight; both window ends are inclusive. India/Japan/UAE have no daylight saving time,
so a fixed offset table is exact and avoids depending on OS tz data.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

UTC_OFFSET_MINUTES: dict[str, int] = {
    "Asia/Kolkata": 330,  # UTC+05:30
    "Asia/Tokyo": 540,  # UTC+09:00
    "Asia/Dubai": 240,  # UTC+04:00
}

MINUTES_PER_DAY = 1440
_HHMM = re.compile(r"([01][0-9]|2[0-3]):([0-5][0-9])|24:00")


def parse_hhmm(value: str) -> int:
    """'HH:MM' -> minutes after midnight (0..1440). '24:00' -> 1440."""
    m = _HHMM.fullmatch(value) if isinstance(value, str) else None
    if not m:
        raise ValueError(f"invalid clock time {value!r}")
    return MINUTES_PER_DAY if value == "24:00" else int(m.group(1)) * 60 + int(m.group(2))


def at_clock(day: date, hhmm: str) -> datetime:
    """Naive local datetime for a clock time on a date ('24:00' -> next day 00:00)."""
    return datetime(day.year, day.month, day.day) + timedelta(minutes=parse_hhmm(hhmm))


def parse_local(value: str) -> datetime:
    """'YYYY-MM-DDTHH:MM[:SS]' -> naive local datetime (no offset allowed)."""
    if not isinstance(value, str) or len(value) not in (16, 19) or "T" not in value:
        raise ValueError(f"invalid local date-time {value!r}")
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is not None:
        raise ValueError(f"local date-time must not carry an offset: {value!r}")
    return dt


def format_local(dt: datetime) -> str:
    """Naive local datetime -> trip format 'YYYY-MM-DDTHH:MM'."""
    return dt.strftime("%Y-%m-%dT%H:%M")


def offset_minutes(tz_name: str) -> int:
    try:
        return UTC_OFFSET_MINUTES[tz_name]
    except KeyError:
        raise ValueError(f"unsupported time zone {tz_name!r}") from None


def to_utc(local: datetime, tz_name: str) -> datetime:
    """Naive local time in tz_name -> aware UTC datetime."""
    return (local - timedelta(minutes=offset_minutes(tz_name))).replace(tzinfo=timezone.utc)


def from_utc(utc: datetime, tz_name: str) -> datetime:
    """Aware UTC datetime -> naive local time in tz_name."""
    return (utc.astimezone(timezone.utc) + timedelta(minutes=offset_minutes(tz_name))).replace(tzinfo=None)


def service_window(service_date: date, start: str, end: str) -> tuple[datetime, datetime]:
    """Local [open, close] of a service that starts on service_date. Wraps past midnight when
    end <= start (e.g. 06:00-00:30); '24:00' closes at the next midnight."""
    open_at = at_clock(service_date, start)
    close_at = at_clock(service_date, end)
    if parse_hhmm(end) <= parse_hhmm(start):
        close_at += timedelta(days=1)
    return open_at, close_at


def _candidate_windows(t: datetime, start: str, end: str):
    """Windows that could contain t: those starting the day before, on, or after t's date."""
    for delta in (-1, 0, 1):
        yield service_window(t.date() + timedelta(days=delta), start, end)


def in_service(t: datetime, start: str, end: str) -> bool:
    """True if local time t lies inside some daily service window (inclusive ends)."""
    return any(o <= t <= c for o, c in _candidate_windows(t, start, end))


def is_scheduled_departure(t: datetime, start: str, end: str, frequency_minutes: int | None) -> bool:
    """In service and, when a frequency is defined, exactly on a departure (start + n * freq)."""
    for o, c in _candidate_windows(t, start, end):
        if o <= t <= c:
            if not frequency_minutes:
                return True
            if (t - o) % timedelta(minutes=frequency_minutes) == timedelta(0):
                return True
    return False


def next_departure(earliest: datetime, start: str, end: str,
                   frequency_minutes: int | None) -> datetime | None:
    """Earliest legal departure at or after `earliest`, searching the window that contains it
    and the next one (so a request after closing returns next day's first departure)."""
    best: datetime | None = None
    for o, c in _candidate_windows(earliest, start, end):
        if c < earliest:
            continue
        if earliest <= o:
            cand = o
        elif not frequency_minutes:
            cand = earliest
        else:
            step = timedelta(minutes=frequency_minutes)
            cand = o + -((o - earliest) // step) * step  # ceil to the next departure
        if cand <= c and (best is None or cand < best):
            best = cand
    return best


def latest_departure(latest: datetime, start: str, end: str,
                     frequency_minutes: int | None) -> datetime | None:
    """Latest legal departure at or before `latest` (mirror of next_departure)."""
    best: datetime | None = None
    for o, c in _candidate_windows(latest, start, end):
        if o > latest:
            continue
        cand = min(c, latest)
        if frequency_minutes:
            step = timedelta(minutes=frequency_minutes)
            cand = o + ((cand - o) // step) * step
        if best is None or cand > best:
            best = cand
    return best


WEEKDAY_CODES = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")


def weekday_code(day: date) -> str:
    return WEEKDAY_CODES[day.weekday()]


def flight_utc(flight, airports) -> tuple[datetime, datetime]:
    """(departure_utc, arrival_utc) of a catalog flight using each airport's own time zone.
    Never subtract the two local strings: they are in different zones (FL-11)."""
    dep = to_utc(parse_local(flight["departure_local"]), airports[flight["from"]]["timezone"])
    arr = to_utc(parse_local(flight["arrival_local"]), airports[flight["to"]]["timezone"])
    return dep, arr


def flight_is_chronological(flight, airports) -> bool:
    """Arrival after departure in UTC and consistent with the catalog duration_minutes."""
    dep, arr = flight_utc(flight, airports)
    return arr > dep and arr - dep == timedelta(minutes=flight["duration_minutes"])

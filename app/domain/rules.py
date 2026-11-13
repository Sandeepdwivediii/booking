"""Pure rule predicates over catalog records (no trip structure, no I/O)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from app.domain.timeutil import in_service, is_scheduled_departure, service_window, weekday_code


# ------------------------------------------------------------------ hotels

def rooms_fit(adults: int, children: int, k: int, room) -> bool:
    """Problem §4: k rooms fit if adults <= k*max_adults, adults+children <= k*(max_adults+max_children)
    and adults >= k (every room needs an adult)."""
    if k < 1:
        return False
    return (adults <= k * room["max_adults"]
            and adults + children <= k * (room["max_adults"] + room["max_children"])
            and adults >= k)


def min_rooms(adults: int, children: int, room) -> int | None:
    """Smallest k that fits the party, or None if no k works (k is bounded by adults)."""
    for k in range(1, adults + 1):
        if rooms_fit(adults, children, k, room):
            return k
    return None


def stay_nights(check_in: str, check_out: str) -> list[str]:
    """Night dates of a stay: check-in .. check-out minus one (HT-11, SY-14).
    Raises ValueError unless both are strict 'YYYY-MM-DD' strings."""
    if not (isinstance(check_in, str) and isinstance(check_out, str) and len(check_in) == len(check_out) == 10):
        raise ValueError("stay dates must be YYYY-MM-DD")
    start, end = date.fromisoformat(check_in), date.fromisoformat(check_out)
    return [(start + timedelta(days=i)).isoformat() for i in range((end - start).days)]


def unavailable_nights(catalog, room_id: str, check_in: str, check_out: str, k: int) -> list[str]:
    """Nights whose rooms_left < k. A missing availability row counts as unavailable (HT-02).
    Each night is looked up individually, so duplicate/out-of-range rows cannot fake coverage (HT-14)."""
    out = []
    for night in stay_nights(check_in, check_out):
        left = catalog.rooms_left(room_id, night)
        if left is None or left < k:
            out.append(night)
    return out


def room_stay_available(catalog, room_id: str, check_in: str, check_out: str, k: int) -> bool:
    return bool(stay_nights(check_in, check_out)) and not unavailable_nights(catalog, room_id, check_in, check_out, k)


# ------------------------------------------------------------------ transfers

def transfer_departure_ok(transfer, pickup: datetime) -> bool:
    """Pickup inside the (possibly overnight / 24:00) service window and, for scheduled
    services with frequency_minutes, exactly on a departure (TR-09, TR-10, TR-13)."""
    return is_scheduled_departure(pickup, transfer["service_from"], transfer["service_until"],
                                  transfer.get("frequency_minutes"))


def transfer_in_service(transfer, t: datetime) -> bool:
    return in_service(t, transfer["service_from"], transfer["service_until"])


def transfer_arrival(transfer, pickup: datetime) -> datetime:
    """Local arrival time (same zone: transfers stay in one country)."""
    return pickup + timedelta(minutes=transfer["duration_minutes"])


def transfer_serves_area(transfer, city: str, area: str | None) -> bool:
    """Airport/city transfer serves a hotel: same city and area, area None = whole city."""
    return transfer.get("city") == city and (transfer.get("area") is None or transfer.get("area") == area)


# ------------------------------------------------------------------ attractions

def attraction_open_on(attraction, day: date) -> bool:
    """Open that weekday and not on a closed date."""
    return weekday_code(day) in attraction["open_days"] and day.isoformat() not in attraction["closed_dates"]


def attraction_visit_ok(attraction, start: datetime) -> bool:
    """Visit starting at local `start` (that day) is open and fits wholly inside opening hours."""
    day = start.date()
    if not attraction_open_on(attraction, day):
        return False
    open_at, close_at = service_window(day, attraction["open_from"], attraction["open_until"])
    return open_at <= start and start + timedelta(minutes=attraction["visit_minutes"]) <= close_at


def attraction_start(attraction, day: date, not_before: datetime | None = None,
                     not_after: datetime | None = None, preferred: str = "10:00") -> datetime | None:
    """A legal visit start on `day` whose visit ends by `not_after` and starts at/after `not_before`.
    Prefers `preferred` (display choice) and falls back to the earliest legal start, so the
    preference never changes feasibility. None if no legal start exists."""
    if not attraction_open_on(attraction, day):
        return None
    open_at, close_at = service_window(day, attraction["open_from"], attraction["open_until"])
    visit = timedelta(minutes=attraction["visit_minutes"])
    lo = max(open_at, datetime(day.year, day.month, day.day), not_before or open_at)
    hi = min(close_at - visit, datetime(day.year, day.month, day.day, 23, 59),
             (not_after - visit) if not_after else close_at - visit)
    if lo > hi:
        return None
    pref = datetime.combine(day, datetime.strptime(preferred, "%H:%M").time())
    return pref if lo <= pref <= hi else lo

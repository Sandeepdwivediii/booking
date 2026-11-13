"""Pure rule predicates over catalog records (no trip structure, no I/O)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from app.domain.timeutil import in_service, is_scheduled_departure


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

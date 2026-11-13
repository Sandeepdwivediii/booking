"""TEST-ONLY synthetic catalog builder. Never used by production code.

`synthetic_catalog()` returns a minimal valid 8-file catalog (Japan, Tokyo <-> Kyoto, one
Nara-style orphan attraction). Tests mutate the dict, then `write_catalog(dir, data)`.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
OFFICIAL_CATALOG_DIR = REPO_ROOT / "data" / "catalog"

# Phase 0 baseline of the supplied official files (SY-03). Any change = files were modified.
OFFICIAL_SHA256 = {
    "airports": "9ea953a1b55d09c24811cf7bef24b61338c696b4d08e5a7aadea766feffda44f",
    "attractions": "f2f63184bd722c917050d0920c4c5161525e60df851a41b00c70ea7d7a822b2b",
    "availability": "b1253a75ea79407a12074337ed899c9639cd01bc21cf9db08428ef15185e131d",
    "destinations": "bdfb653f574f5019e9337353a06c384732400d727d21b65dfaed3e87a9aac2c1",
    "flights": "2d3cb6582b59da9a34770ac7caaa097bbab0742c2e45663ebcf3274051979907",
    "hotels": "4d5523990334d67f48544185b625c8d7b9557384763bf173e33768cbe709ebef",
    "rooms": "9645d705cb560b4b255888d402c92261b2c9662930c3c6abb99021d58b1fe705",
    "transfers": "232a5046b19e72c98d3324e65659ea4544c801871a8d5fe3559b66aac1c78f2e",
}

# SY-01 expected counts for the official dataset.
OFFICIAL_COUNTS = {
    "airports": 10, "flights": 469, "hotels": 49, "rooms": 125,
    "availability": 2500, "transfers": 66, "attractions": 56,
}


def _inr(amount: int) -> dict:
    return {"amount": amount, "currency": "INR"}


_BASE = {
    "destinations": {
        "departure_cities": [{"airport": "BLR", "city": "Bengaluru"}],
        "countries": [{
            "code": "JP", "name": "Japan", "timezone": "Asia/Tokyo",
            "cities": [{"name": "Tokyo", "airports": ["NRT"]}, {"name": "Kyoto", "airports": ["KIX"]}],
            "links": [{"from_city": "Tokyo", "to_city": "Kyoto", "transfer_ids": ["TR-TYO-KYO"]}],
        }],
        "rules": {"min_nights": 3, "max_nights": 10, "min_nights_per_city": 2,
                  "data_window": {"first_night": "2027-04-01", "last_departure": "2027-04-20"}},
    },
    "airports": [
        {"code": "BLR", "name": "Bengaluru Intl", "city": "Bengaluru", "country": "IN", "timezone": "Asia/Kolkata"},
        {"code": "NRT", "name": "Narita", "city": "Tokyo", "country": "JP", "timezone": "Asia/Tokyo",
         "serves_cities": ["Tokyo"]},
        {"code": "KIX", "name": "Kansai", "city": "Osaka", "country": "JP", "timezone": "Asia/Tokyo",
         "serves_cities": ["Kyoto"]},
    ],
    "flights": [
        {"flight_id": "FL-1", "airline": "T", "flight_number": "T 1", "from": "BLR", "to": "NRT",
         "departure_local": "2027-04-09T23:50:00", "arrival_local": "2027-04-10T10:30:00", "stops": 0,
         "duration_minutes": 490, "price_per_seat": _inr(25000), "seats_left": 3, "cabin": "ECONOMY"},
        {"flight_id": "FL-2", "airline": "T", "flight_number": "T 2", "from": "KIX", "to": "BLR",
         "departure_local": "2027-04-14T12:00:00", "arrival_local": "2027-04-14T18:00:00", "stops": 1,
         "duration_minutes": 540, "price_per_seat": _inr(24000), "seats_left": 3, "cabin": "ECONOMY"},
    ],
    "hotels": [
        {"hotel_id": "HTL-T1", "name": "Tokyo One", "aliases": [], "city": "Tokyo", "area": "Ginza", "stars": 4},
        {"hotel_id": "HTL-K1", "name": "Kyoto One", "aliases": [], "city": "Kyoto", "area": "Gion", "stars": 3},
    ],
    "rooms": [
        {"room_id": "RM-T1-A", "hotel_id": "HTL-T1", "name": "Twin", "room_type": "TWIN",
         "max_adults": 2, "max_children": 0, "price_per_night": _inr(6000)},
        {"room_id": "RM-K1-A", "hotel_id": "HTL-K1", "name": "Family", "room_type": "FAMILY",
         "max_adults": 2, "max_children": 2, "price_per_night": _inr(9000)},
    ],
    "availability": [
        {"room_id": room, "date": f"2027-04-{day:02d}", "rooms_left": 2}
        for room in ("RM-T1-A", "RM-K1-A") for day in range(10, 15)
    ],
    "transfers": [
        {"transfer_id": "TR-NRT-GNZ", "type": "AIRPORT_TAXI", "name": "NRT taxi", "airport": "NRT",
         "city": "Tokyo", "area": "Ginza", "max_passengers": 4, "service_from": "06:00",
         "service_until": "23:00", "duration_minutes": 75, "pricing": "PER_VEHICLE", "price": _inr(13000)},
        {"transfer_id": "TR-KIX-KYO", "type": "AIRPORT_BUS", "name": "KIX bus", "airport": "KIX",
         "city": "Kyoto", "area": None, "max_passengers": 99, "service_from": "06:00",
         "service_until": "00:30", "duration_minutes": 90, "pricing": "PER_PERSON",
         "price": _inr(2000), "child_price": _inr(1000)},
        {"transfer_id": "TR-TYO-KYO", "type": "TRAIN", "name": "Tokyo-Kyoto train", "airport": None,
         "city": None, "area": None, "from_city": "Tokyo", "to_city": "Kyoto", "max_passengers": 99,
         "service_from": "06:00", "service_until": "21:30", "frequency_minutes": 30,
         "duration_minutes": 135, "pricing": "PER_PERSON", "price": _inr(14000)},
        {"transfer_id": "TR-TYO-TAXI", "type": "CITY_TAXI", "name": "Tokyo taxi", "airport": None,
         "city": "Tokyo", "area": None, "max_passengers": 4, "service_from": "05:00",
         "service_until": "24:00", "duration_minutes": 25, "pricing": "PER_VEHICLE", "price": _inr(3000)},
    ],
    "attractions": [
        {"attraction_id": "ATT-T1", "name": "Tokyo Tower", "city": "Tokyo",
         "open_days": ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"], "open_from": "09:00",
         "open_until": "22:00", "visit_minutes": 90, "price_per_person": _inr(1200),
         "child_price": _inr(600), "closed_dates": ["2027-04-12"]},
        {"attraction_id": "ATT-K1", "name": "Free Shrine", "city": "Kyoto",
         "open_days": ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"], "open_from": "00:00",
         "open_until": "24:00", "visit_minutes": 60, "price_per_person": _inr(0)},
        {"attraction_id": "ATT-N1", "name": "Deer Park", "city": "Nara",
         "open_days": ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"], "open_from": "00:00",
         "open_until": "24:00", "visit_minutes": 60, "price_per_person": _inr(0)},
    ],
}


def synthetic_catalog() -> dict:
    """Fresh deep copy of the valid synthetic catalog (file name -> JSON content)."""
    return copy.deepcopy(_BASE)


def write_catalog(directory: Path, data: dict, *, omit: tuple[str, ...] = ()) -> Path:
    """Write each file as <name>.json into directory (skipping `omit`). Returns directory."""
    directory.mkdir(parents=True, exist_ok=True)
    for name, content in data.items():
        if name not in omit:
            (directory / f"{name}.json").write_text(json.dumps(content, indent=1), encoding="utf-8")
    return directory

"""Read-only, in-memory index of the eight official catalog JSON files.

`load_catalog(root)` validates everything up front and either returns a fully indexed,
immutable `Catalog` or raises `CatalogUnavailableError` listing every problem found
(no partial loads). The source files are only ever opened for reading.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from app.contracts import CatalogUnavailableError

CATALOG_FILES = (
    "airports", "attractions", "availability", "destinations",
    "flights", "hotels", "rooms", "transfers",
)

_LIST_FILES = tuple(f for f in CATALOG_FILES if f != "destinations")

REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "airports": ("code", "name", "city", "country", "timezone"),
    "flights": ("flight_id", "from", "to", "departure_local", "arrival_local",
                "duration_minutes", "price_per_seat", "seats_left"),
    "hotels": ("hotel_id", "name", "city", "area", "stars"),
    "rooms": ("room_id", "hotel_id", "name", "max_adults", "max_children", "price_per_night"),
    "availability": ("room_id", "date", "rooms_left"),
    "transfers": ("transfer_id", "type", "name", "max_passengers", "service_from",
                  "service_until", "duration_minutes", "pricing", "price"),
    "attractions": ("attraction_id", "name", "city", "open_days", "open_from", "open_until",
                    "visit_minutes", "price_per_person"),
}

ID_FIELDS = {
    "airports": "code", "flights": "flight_id", "hotels": "hotel_id", "rooms": "room_id",
    "transfers": "transfer_id", "attractions": "attraction_id",
}

AIRPORT_TRANSFER_TYPES = frozenset({"AIRPORT_TAXI", "AIRPORT_VAN", "AIRPORT_BUS"})
INTERCITY_TRANSFER_TYPES = frozenset({"TRAIN", "INTERCITY_BUS", "INTERCITY_TAXI"})
CITY_TRANSFER_TYPES = frozenset({"CITY_TAXI", "CITY_BUS"})
PRICING_MODES = frozenset({"PER_PERSON", "PER_VEHICLE"})
WEEKDAYS = frozenset({"MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"})

_HHMM = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]|24:00")


def _freeze(value: Any) -> Any:
    """Deep read-only copy: dict -> MappingProxyType, list -> tuple."""
    if isinstance(value, dict):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(v) for v in value)
    return value


def _is_int(v: Any, minimum: int | None = None) -> bool:
    ok = isinstance(v, int) and not isinstance(v, bool)
    return ok and (minimum is None or v >= minimum)


def _is_date(v: Any) -> bool:
    if not isinstance(v, str) or len(v) != 10:
        return False
    try:
        date.fromisoformat(v)
    except ValueError:
        return False
    return True


def _is_local_datetime(v: Any) -> bool:
    if not isinstance(v, str) or len(v) != 19:
        return False
    try:
        datetime.fromisoformat(v)
    except ValueError:
        return False
    return True


def _is_money(v: Any) -> bool:
    return isinstance(v, dict) and _is_int(v.get("amount"), 0) and v.get("currency") == "INR"


@dataclass(frozen=True)
class Catalog:
    """Immutable catalog indexes. All records are read-only mappings/tuples."""

    source_sha256: Mapping[str, str]
    rules: Mapping[str, Any]
    departure_cities: tuple  # ({airport, city}, ...)
    countries: Mapping[str, Mapping]  # country name -> raw country record
    country_of_city: Mapping[str, str]  # destination city -> country name
    airports: Mapping[str, Mapping]
    airports_serving_city: Mapping[str, tuple[str, ...]]  # city -> airport codes (serves_cities)
    flights: Mapping[str, Mapping]
    flights_by_departure: Mapping[tuple[str, str, str], tuple]  # (from, to, local dep date)
    flights_by_arrival: Mapping[tuple[str, str, str], tuple]  # (from, to, local arr date)
    hotels: Mapping[str, Mapping]
    hotels_by_city: Mapping[str, tuple]
    rooms: Mapping[str, Mapping]
    rooms_by_hotel: Mapping[str, tuple]
    availability: Mapping[tuple[str, str], int]  # (room_id, night) -> rooms_left
    transfers: Mapping[str, Mapping]
    airport_transfers: Mapping[tuple[str, str], tuple]  # (airport, city) -> records (any area)
    intercity_transfers: Mapping[tuple[str, str], tuple]  # directed (from_city, to_city) -> linked records
    city_transfers: Mapping[str, tuple]  # city -> CITY_TAXI / CITY_BUS records
    attractions: Mapping[str, Mapping]  # every record, including non-plannable ones
    attractions_by_city: Mapping[str, tuple]  # destination cities only (plannable)
    excluded_attraction_ids: tuple[str, ...]  # attractions outside every destination city (Nara)

    def rooms_left(self, room_id: str, night: str) -> int | None:
        """Rooms left for one night, or None when the availability row is missing."""
        return self.availability.get((room_id, night))

    def counts(self) -> dict[str, int]:
        return {
            "airports": len(self.airports), "flights": len(self.flights), "hotels": len(self.hotels),
            "rooms": len(self.rooms), "availability": len(self.availability),
            "transfers": len(self.transfers), "attractions": len(self.attractions),
        }

    def options(self) -> dict:
        """Contract Options: a fresh, JSON-serializable dict derived from destinations.json."""
        return {
            "departure_cities": [{"airport": d["airport"], "city": d["city"]} for d in self.departure_cities],
            "countries": [
                {"name": c["name"], "code": c["code"], "timezone": c["timezone"],
                 "cities": [city["name"] for city in c["cities"]]}
                for c in self.countries.values()
            ],
            "rules": {
                "min_nights": self.rules["min_nights"],
                "max_nights": self.rules["max_nights"],
                "min_nights_per_city": self.rules["min_nights_per_city"],
                "first_night": self.rules["data_window"]["first_night"],
                "last_departure": self.rules["data_window"]["last_departure"],
            },
        }


def _read_sources(root: Path, errs: list[str]) -> tuple[dict[str, Any], dict[str, str]]:
    raw: dict[str, Any] = {}
    hashes: dict[str, str] = {}
    if not root.is_dir():
        errs.append(f"catalog directory not found: {root}")
        return raw, hashes
    for name in CATALOG_FILES:
        path = root / f"{name}.json"
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            errs.append(f"missing catalog file: {name}.json")
            continue
        except OSError as exc:
            errs.append(f"cannot read {name}.json: {exc.__class__.__name__}")
            continue
        hashes[name] = hashlib.sha256(data).hexdigest()
        try:
            raw[name] = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            errs.append(f"{name}.json is not valid UTF-8 JSON: {exc}")
            continue
        expected = dict if name == "destinations" else list
        if not isinstance(raw[name], expected):
            errs.append(f"{name}.json must contain a JSON {expected.__name__}")
            del raw[name]
    return raw, hashes


def _check_records(raw: dict[str, Any], errs: list[str]) -> dict[str, dict[str, dict]]:
    """Required fields + unique natural IDs. Returns id -> record maps."""
    by_id: dict[str, dict[str, dict]] = {}
    for name in _LIST_FILES:
        records = raw.get(name, [])
        for i, rec in enumerate(records):
            if not isinstance(rec, dict):
                errs.append(f"{name}[{i}] is not an object")
                continue
            missing = [k for k in REQUIRED_FIELDS[name] if k not in rec]
            if missing:
                errs.append(f"{name}[{i}] missing fields {missing}")
        if name not in ID_FIELDS:
            continue
        key = ID_FIELDS[name]
        index: dict[str, dict] = {}
        for rec in records:
            if not isinstance(rec, dict) or key not in rec:
                continue
            rid = rec[key]
            if not isinstance(rid, str) or not rid:
                errs.append(f"{name}: {key} must be a non-empty string, got {rid!r}")
            elif rid in index:
                errs.append(f"{name}: duplicate {key} {rid!r}")
            else:
                index[rid] = rec
        by_id[name] = index
    return by_id


def _check_values(raw: dict[str, Any], by_id: dict[str, dict[str, dict]], errs: list[str]) -> None:
    ok = lambda name: [r for r in raw.get(name, []) if isinstance(r, dict)
                       and all(k in r for k in REQUIRED_FIELDS[name])]
    for f in ok("flights"):
        fid = f["flight_id"]
        if f["from"] not in by_id["airports"] or f["to"] not in by_id["airports"]:
            errs.append(f"flight {fid}: unknown airport {f['from']!r}->{f['to']!r}")
        if not (_is_local_datetime(f["departure_local"]) and _is_local_datetime(f["arrival_local"])):
            errs.append(f"flight {fid}: departure_local/arrival_local must be YYYY-MM-DDTHH:MM:SS")
        if not _is_money(f["price_per_seat"]):
            errs.append(f"flight {fid}: price_per_seat must be an INR integer amount")
        if not _is_int(f["seats_left"], 0) or not _is_int(f["duration_minutes"], 1):
            errs.append(f"flight {fid}: seats_left/duration_minutes must be non-negative/positive ints")
    for r in ok("rooms"):
        if r["hotel_id"] not in by_id["hotels"]:
            errs.append(f"room {r['room_id']}: unknown hotel_id {r['hotel_id']!r}")
        if not _is_int(r["max_adults"], 1) or not _is_int(r["max_children"], 0):
            errs.append(f"room {r['room_id']}: max_adults >= 1 and max_children >= 0 required")
        if not _is_money(r["price_per_night"]):
            errs.append(f"room {r['room_id']}: price_per_night must be an INR integer amount")
    seen_nights: set[tuple[str, str]] = set()
    for a in ok("availability"):
        key = (a["room_id"], a["date"])
        if a["room_id"] not in by_id["rooms"]:
            errs.append(f"availability: unknown room_id {a['room_id']!r}")
        if not _is_date(a["date"]):
            errs.append(f"availability {a['room_id']}: bad date {a['date']!r}")
        if not _is_int(a["rooms_left"], 0):
            errs.append(f"availability {key}: rooms_left must be an int >= 0")
        if key in seen_nights:
            errs.append(f"availability: duplicate row for {key}")
        seen_nights.add(key)
    for t in ok("transfers"):
        tid = t["transfer_id"]
        if t["pricing"] not in PRICING_MODES:
            errs.append(f"transfer {tid}: unknown pricing {t['pricing']!r}")
        if not _is_money(t["price"]) or ("child_price" in t and t["child_price"] is not None
                                          and not _is_money(t["child_price"])):
            errs.append(f"transfer {tid}: price/child_price must be INR integer amounts")
        if not _is_int(t["max_passengers"], 1) or not _is_int(t["duration_minutes"], 0):
            errs.append(f"transfer {tid}: max_passengers >= 1 and duration_minutes >= 0 required")
        if not (isinstance(t["service_from"], str) and _HHMM.fullmatch(t["service_from"])
                and isinstance(t["service_until"], str) and _HHMM.fullmatch(t["service_until"])):
            errs.append(f"transfer {tid}: service window must be HH:MM (24:00 allowed)")
        if t.get("frequency_minutes") is not None and not _is_int(t["frequency_minutes"], 1):
            errs.append(f"transfer {tid}: frequency_minutes must be a positive int")
        kind = t["type"]
        if kind in AIRPORT_TRANSFER_TYPES:
            if t.get("airport") not in by_id["airports"] or not t.get("city"):
                errs.append(f"transfer {tid}: airport transfer needs a known airport and a city")
        elif kind in INTERCITY_TRANSFER_TYPES:
            if not t.get("from_city") or not t.get("to_city"):
                errs.append(f"transfer {tid}: intercity transfer needs from_city and to_city")
        elif kind in CITY_TRANSFER_TYPES:
            if not t.get("city"):
                errs.append(f"transfer {tid}: city transfer needs a city")
        else:
            errs.append(f"transfer {tid}: unknown type {kind!r}")
    for a in ok("attractions"):
        aid = a["attraction_id"]
        days = a["open_days"]
        if not isinstance(days, list) or not set(days) <= WEEKDAYS:
            errs.append(f"attraction {aid}: open_days must be a list of MON..SUN")
        if not (isinstance(a["open_from"], str) and _HHMM.fullmatch(a["open_from"])
                and isinstance(a["open_until"], str) and _HHMM.fullmatch(a["open_until"])):
            errs.append(f"attraction {aid}: opening hours must be HH:MM (24:00 allowed)")
        if not _is_int(a["visit_minutes"], 1):
            errs.append(f"attraction {aid}: visit_minutes must be a positive int")
        if not _is_money(a["price_per_person"]) or ("child_price" in a and a["child_price"] is not None
                                                     and not _is_money(a["child_price"])):
            errs.append(f"attraction {aid}: price_per_person/child_price must be INR integer amounts")
        closed = a.get("closed_dates")
        if closed is not None and (not isinstance(closed, list) or not all(_is_date(d) for d in closed)):
            errs.append(f"attraction {aid}: closed_dates must be a list of YYYY-MM-DD")
    for h in ok("hotels"):
        if not _is_int(h["stars"], 0):
            errs.append(f"hotel {h['hotel_id']}: stars must be an int")


def _check_destinations(dest: dict, by_id: dict[str, dict[str, dict]], errs: list[str]) -> None:
    missing = [k for k in ("departure_cities", "countries", "rules") if k not in dest]
    if missing or not isinstance(dest["countries"], list) or not isinstance(dest["departure_cities"], list):
        errs.append(f"destinations.json needs list departure_cities/countries and rules (missing {missing})")
        return
    rules = dest["rules"]
    window = rules.get("data_window") if isinstance(rules, dict) else None
    if (not isinstance(rules, dict) or not all(_is_int(rules.get(k), 1) for k in
            ("min_nights", "max_nights", "min_nights_per_city"))
            or not isinstance(window, dict) or not _is_date(window.get("first_night"))
            or not _is_date(window.get("last_departure"))):
        errs.append("destinations.rules must have int nights limits and a data_window of dates")
    for d in dest["departure_cities"]:
        if not isinstance(d, dict) or d.get("airport") not in by_id["airports"] or not d.get("city"):
            errs.append(f"departure city {d!r}: needs a known airport and a city")
    all_cities: set[str] = set()
    names: set[str] = set()
    for c in dest["countries"]:
        if not isinstance(c, dict) or any(k not in c for k in ("code", "name", "timezone", "cities", "links")):
            errs.append("country entries need code, name, timezone, cities, links")
            continue
        if c["name"] in names:
            errs.append(f"duplicate country {c['name']!r}")
        names.add(c["name"])
        cities = set()
        for city in c["cities"]:
            name = city.get("name") if isinstance(city, dict) else None
            if not name or name in all_cities:
                errs.append(f"country {c['name']}: city {city!r} missing or duplicated")
                continue
            cities.add(name)
            all_cities.add(name)
            for code in city.get("airports", []):
                ap = by_id["airports"].get(code)
                if ap is None or name not in (ap.get("serves_cities") or []):
                    errs.append(f"city {name}: airport {code!r} unknown or does not serve it")
        seen_links = set()
        for link in c["links"]:
            if not isinstance(link, dict) or not isinstance(link.get("transfer_ids"), list):
                errs.append(f"country {c['name']}: link {link!r} needs from_city, to_city, transfer_ids")
                continue
            pair = (link.get("from_city"), link.get("to_city"))
            if pair[0] not in cities or pair[1] not in cities or pair[0] == pair[1]:
                errs.append(f"country {c['name']}: link {pair} must join two different cities of the country")
            if pair in seen_links:
                errs.append(f"country {c['name']}: duplicate link {pair}")
            seen_links.add(pair)
            for tid in link.get("transfer_ids", []):
                t = by_id["transfers"].get(tid)
                if t is None:
                    errs.append(f"link {pair}: unknown transfer_id {tid!r}")
                elif (t.get("from_city"), t.get("to_city")) != pair or t.get("type") not in INTERCITY_TRANSFER_TYPES:
                    errs.append(f"link {pair}: transfer {tid} is not an intercity transfer for that direction")
    for ap in by_id["airports"].values():
        for city in ap.get("serves_cities") or []:
            if city not in all_cities:
                errs.append(f"airport {ap['code']}: serves unknown city {city!r}")
    for h in by_id["hotels"].values():
        if h.get("city") not in all_cities:
            errs.append(f"hotel {h['hotel_id']}: city {h.get('city')!r} is not a destination city")
    for t in by_id["transfers"].values():
        for k in ("city", "from_city", "to_city"):
            if t.get(k) is not None and t[k] not in all_cities:
                errs.append(f"transfer {t['transfer_id']}: {k} {t[k]!r} is not a destination city")


def _group(records, key) -> dict:
    out: dict = {}
    for r in records:
        out.setdefault(key(r), []).append(r)
    return out


def _build(raw: dict[str, Any], by_id: dict[str, dict[str, dict]], hashes: dict[str, str]) -> Catalog:
    dest = raw["destinations"]
    countries = {c["name"]: c for c in dest["countries"]}
    country_of_city = {city["name"]: c["name"] for c in dest["countries"] for city in c["cities"]}

    serving: dict[str, list[str]] = {}
    for ap in by_id["airports"].values():
        for city in ap.get("serves_cities") or []:
            serving.setdefault(city, []).append(ap["code"])

    flights = by_id["flights"]
    flight_order = lambda f: (f["price_per_seat"]["amount"], f["departure_local"], f["flight_id"])
    by_dep = _group(flights.values(), lambda f: (f["from"], f["to"], f["departure_local"][:10]))
    by_arr = _group(flights.values(), lambda f: (f["from"], f["to"], f["arrival_local"][:10]))

    room_order = lambda r: (r["price_per_night"]["amount"], r["room_id"])
    transfers = by_id["transfers"]
    airport_tr = _group([t for t in transfers.values() if t["type"] in AIRPORT_TRANSFER_TYPES],
                        lambda t: (t["airport"], t["city"]))
    city_tr = _group([t for t in transfers.values() if t["type"] in CITY_TRANSFER_TYPES], lambda t: t["city"])
    intercity = {(l["from_city"], l["to_city"]): [transfers[tid] for tid in l["transfer_ids"]]
                 for c in dest["countries"] for l in c["links"]}

    attractions = {}
    for aid, a in by_id["attractions"].items():
        a = dict(a)
        a["closed_dates"] = list(a.get("closed_dates") or [])  # optional field -> empty list
        attractions[aid] = a
    plannable = [a for a in attractions.values() if a["city"] in country_of_city]
    excluded = tuple(sorted(aid for aid, a in attractions.items() if a["city"] not in country_of_city))

    def sort_ids(groups: dict, order) -> dict:
        return {k: tuple(_freeze(x) for x in sorted(v, key=order)) for k, v in groups.items()}

    return Catalog(
        source_sha256=MappingProxyType(dict(hashes)),
        rules=_freeze(dest["rules"]),
        departure_cities=_freeze(dest["departure_cities"]),
        countries=MappingProxyType({k: _freeze(v) for k, v in countries.items()}),
        country_of_city=MappingProxyType(country_of_city),
        airports=MappingProxyType({k: _freeze(v) for k, v in by_id["airports"].items()}),
        airports_serving_city=MappingProxyType({k: tuple(sorted(v)) for k, v in serving.items()}),
        flights=MappingProxyType({k: _freeze(v) for k, v in flights.items()}),
        flights_by_departure=MappingProxyType(sort_ids(by_dep, flight_order)),
        flights_by_arrival=MappingProxyType(sort_ids(by_arr, flight_order)),
        hotels=MappingProxyType({k: _freeze(v) for k, v in by_id["hotels"].items()}),
        hotels_by_city=MappingProxyType(sort_ids(_group(by_id["hotels"].values(), lambda h: h["city"]),
                                                 lambda h: h["hotel_id"])),
        rooms=MappingProxyType({k: _freeze(v) for k, v in by_id["rooms"].items()}),
        rooms_by_hotel=MappingProxyType(sort_ids(_group(by_id["rooms"].values(), lambda r: r["hotel_id"]),
                                                 room_order)),
        availability=MappingProxyType({(a["room_id"], a["date"]): a["rooms_left"] for a in raw["availability"]}),
        transfers=MappingProxyType({k: _freeze(v) for k, v in transfers.items()}),
        airport_transfers=MappingProxyType(sort_ids(airport_tr, lambda t: t["transfer_id"])),
        intercity_transfers=MappingProxyType({k: tuple(_freeze(t) for t in v) for k, v in intercity.items()}),
        city_transfers=MappingProxyType(sort_ids(city_tr, lambda t: t["transfer_id"])),
        attractions=MappingProxyType({k: _freeze(v) for k, v in attractions.items()}),
        attractions_by_city=MappingProxyType(sort_ids(_group(plannable, lambda a: a["city"]),
                                                      lambda a: a["attraction_id"])),
        excluded_attraction_ids=excluded,
    )


def load_catalog(root: str | Path) -> Catalog:
    """Load and validate the catalog directory. Raises CatalogUnavailableError on any problem."""
    root = Path(root)
    errs: list[str] = []
    raw, hashes = _read_sources(root, errs)
    if not errs:
        by_id = _check_records(raw, errs)
        if not errs:
            _check_values(raw, by_id, errs)
            _check_destinations(raw["destinations"], by_id, errs)
    if errs:
        raise CatalogUnavailableError(
            f"catalog at {root} is unavailable: {len(errs)} problem(s); first: {errs[0]}",
            {"errors": errs[:50], "error_count": len(errs)},
        )
    return _build(raw, by_id, hashes)

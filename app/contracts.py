"""Frozen integration contract between the domain lane (Tanish) and the platform lane (Sandeep).

Pure standard library: no FastAPI, SQLAlchemy, Pydantic or database imports, so the
domain package can depend on this module without pulling in platform code.

Human-readable companion: docs/INTEGRATION_CONTRACT.md. Changing anything here needs
approval from both owners and a single dedicated integration commit.

Requires Python 3.11+ (typing.NotRequired). Docker images must use python:3.11 or newer.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any, Literal, NotRequired, Protocol, TypedDict, runtime_checkable

CONTRACT_VERSION = "1.2.0"

# Import path of the real adapter factory (owned by the domain lane):
#   from app.domain.adapter import create_domain
#   domain: DomainPort = create_domain(catalog_dir)
DOMAIN_FACTORY = "app.domain.adapter:create_domain"

# --------------------------------------------------------------------------- enums

BuildStatus = Literal["BUILT", "OVER_BUDGET", "NO_PACKAGE"]
BUILD_STATUSES: frozenset[str] = frozenset({"BUILT", "OVER_BUDGET", "NO_PACKAGE"})

ValidationMode = Literal["initial", "modified", "confirm"]
VALIDATION_MODES: frozenset[str] = frozenset({"initial", "modified", "confirm"})

ItemType = Literal["flight", "hotel", "transfer", "attraction"]
ITEM_TYPES: frozenset[str] = frozenset({"flight", "hotel", "transfer", "attraction"})

ItemStatus = Literal["PLANNED", "BOOKED"]
ITEM_STATUSES: frozenset[str] = frozenset({"PLANNED", "BOOKED"})

# Extra (allowed) item field so the UI/change logic can tell legs apart without guessing.
ItemRole = Literal[
    "outbound_flight",
    "return_flight",
    "hotel_stay",
    "arrival_transfer",
    "departure_transfer",
    "intercity_transfer",
    "city_taxi",
    "attraction_visit",
]
ITEM_ROLES: frozenset[str] = frozenset(ItemRole.__args__)  # type: ignore[attr-defined]
ROLE_TYPE: dict[str, str] = {
    "outbound_flight": "flight",
    "return_flight": "flight",
    "hotel_stay": "hotel",
    "arrival_transfer": "transfer",
    "departure_transfer": "transfer",
    "intercity_transfer": "transfer",
    "city_taxi": "transfer",
    "attraction_visit": "attraction",
}

AlternativeComponent = Literal["hotel", "outbound_flight", "return_flight", "attraction"]
ALTERNATIVE_COMPONENTS: frozenset[str] = frozenset(
    {"hotel", "outbound_flight", "return_flight", "attraction"}
)

ChangeOp = Literal["replace_hotel", "replace_flight", "add_attraction", "remove_attraction"]
CHANGE_OPS: frozenset[str] = frozenset(
    {"replace_hotel", "replace_flight", "add_attraction", "remove_attraction"}
)

# Recommended NO_PACKAGE reason codes. The domain may add more UPPER_SNAKE codes.
NO_PACKAGE_REASONS: frozenset[str] = frozenset(
    {
        "OUTSIDE_DATA_WINDOW",
        "NO_CITY_PLAN",
        "NO_OUTBOUND_FLIGHT",
        "NO_RETURN_FLIGHT",
        "NO_HOTEL_AVAILABILITY",
        "NO_AIRPORT_TRANSFER",
        "NO_INTERCITY_TRANSFER",
        "NO_ATTRACTION_SCHEDULE",
        "NO_FEASIBLE_COMBINATION",
    }
)

# ------------------------------------------------------------- request limits (API)
# Enforced by the API (HTTP 422) before the domain is called. The domain may rely on them.
CHILD_MIN_AGE = 0
CHILD_MAX_AGE = 17
MIN_ADULTS = 1
MAX_ADULTS = 20  # platform sanity cap (IN-16), not a catalog rule
MAX_CHILDREN = 20  # platform sanity cap (IN-16), not a catalog rule
MAX_BUDGET_INR = 100_000_000  # platform sanity cap (IN-16)
REFERENCE_LENGTH = 10  # reservation reference: exactly 10 digits, always a str
REFERENCE_RE = re.compile(r"[0-9]{10}")  # use .fullmatch(); ASCII digits only, no trailing newline

# ------------------------------------------------------------- attraction day rules
# Day kinds (all local dates): arrival day = start_date; city-change day = check-in date of
# every hotel segment after the first; return day = end_date; every other day = full day.
MAX_ATTRACTIONS_PER_DAY = 2  # every mode, every day
MIN_ATTRACTIONS_PER_FULL_DAY_INITIAL = 1  # full days, mode "initial" only
# Arrival / city-change days: 0..2 in every mode. Return day: exactly 0 in every mode.

BUILD_REQUEST_TYPES: dict[str, type] = {
    "departure_city": str,
    "destination_country": str,
    "start_date": str,
    "nights": int,
    "adults": int,
    "children_ages": list,
    "budget_inr": int,
}


# ---------------------------------------------------------------- domain payloads


class BuildRequest(TypedDict):
    """Normalized, API-validated request. Exactly these seven keys."""

    departure_city: str  # home airport code from options().departure_cities[].airport, e.g. "BLR"
    destination_country: str  # country name from options().countries[].name, e.g. "Japan"
    start_date: str  # "YYYY-MM-DD"; first day in destination (arrival date, local)
    nights: int  # rules.min_nights..rules.max_nights
    adults: int  # >= 1
    children_ages: list[int]  # each 0..17; may be empty
    budget_inr: int  # >= 0, whole rupees


class DepartureCity(TypedDict):
    airport: str
    city: str


class CountryOption(TypedDict):
    name: str
    code: str
    timezone: str
    cities: list[str]


class Rules(TypedDict):
    min_nights: int
    max_nights: int
    min_nights_per_city: int
    first_night: str  # "YYYY-MM-DD"
    last_departure: str  # "YYYY-MM-DD"


class Options(TypedDict):
    departure_cities: list[DepartureCity]
    countries: list[CountryOption]
    rules: Rules


class Traveler(TypedDict):
    name: str | None
    home_airport: str
    adults: int
    children: int
    child_ages: list[int]


class Booking(TypedDict):
    reference: str  # 10 digits, string


class TripItem(TypedDict):
    item_id: str
    type: ItemType
    role: ItemRole  # extra field (allowed by the judge format)
    name: str
    city: str
    status: ItemStatus
    catalog_id: str
    room_id: str | None
    start: str  # local "YYYY-MM-DD" (hotel) or "YYYY-MM-DDTHH:MM"
    end: str | None
    quantity: int
    price_inr: int
    booking: Booking | None
    sources: list[Any]  # always []
    warnings: list[str]


class Trip(TypedDict):
    trip_id: str
    traveler: Traveler
    country: str
    start_date: str
    end_date: str  # start_date + nights (return-flight day)
    budget_inr: int
    total_inr: int
    items: list[TripItem]
    status: NotRequired[str]  # top-level; set by the platform only ("DRAFT" / "BOOKED")


class BuildResult(TypedDict):
    status: BuildStatus
    trip: Trip | None  # only for BUILT
    # Exact global cheapest complete feasible total. BUILT: None or int <= trip.total_inr;
    # OVER_BUDGET: int > budget_inr; NO_PACKAGE: None.
    cheapest_total_inr: int | None
    shortfall_inr: int | None  # OVER_BUDGET only: cheapest_total_inr - budget_inr (>= 1)
    reason_code: str | None  # NO_PACKAGE only: one of NO_PACKAGE_REASONS (or new UPPER_SNAKE)
    reason: str | None  # required non-empty for OVER_BUDGET and NO_PACKAGE; optional for BUILT
    suggestion: str | None  # OVER_BUDGET (required) / NO_PACKAGE (optional)


class Violation(TypedDict):
    code: str  # stable UPPER_SNAKE code, e.g. "HOTEL_NO_AVAILABILITY"
    message: str
    item_id: str | None


class AlternativeFilters(TypedDict, total=False):
    item_id: str  # hotel / flight item being replaced (required for hotel)
    date: str  # attraction: day to add "YYYY-MM-DD" (required for attraction)
    limit: int  # default 20


class ReplaceHotelChange(TypedDict):
    op: Literal["replace_hotel"]
    item_id: str
    hotel_id: str
    room_id: str


class ReplaceFlightChange(TypedDict):
    op: Literal["replace_flight"]
    item_id: str
    flight_id: str


class AddAttractionChange(TypedDict):
    op: Literal["add_attraction"]
    attraction_id: str
    date: str
    # "HH:MM". Clients may omit it (domain picks earliest legal start); Alternatives always set it.
    start_time: NotRequired[str]


class RemoveAttractionChange(TypedDict):
    op: Literal["remove_attraction"]
    item_id: str


ChangeRequest = ReplaceHotelChange | ReplaceFlightChange | AddAttractionChange | RemoveAttractionChange


# ---- G3 extension (v1.2.0, additive; agreed from Tanish's 0d7c931 proposal). NOT a DomainPort.change op:
# CHANGE_OPS and DomainPort are unchanged. The platform routes this PATCH body to the OPTIONAL domain method
# rebuild_city_plan(trip, cities, nights) -> ChangeResult, and lists choices via the OPTIONAL
# city_plan_options(trip) -> [CityPlanOption]. A domain without them -> 503 SERVICE_UNAVAILABLE.
CITY_PLAN_OP = "change_city_plan"


class CityPlanChange(TypedDict):
    op: Literal["change_city_plan"]
    cities: list[str]  # ordered city names of the destination country
    nights: list[int]  # nights per city, same length, sums to the trip's nights


class CityPlanOption(TypedDict):
    cities: list[str]
    nights: list[int]
    total_inr: int  # cheapest complete package for exactly this plan
    price_diff_inr: int  # total_inr - current trip total
    within_budget: bool

class Alternative(TypedDict):
    component: AlternativeComponent
    change: ChangeRequest  # ready to pass unchanged to change()/PATCH
    name: str
    catalog_id: str
    room_id: str | None
    start: str | None
    end: str | None
    price_inr: int  # price of the replacement component itself
    new_total_inr: int | None  # trip total if applied, when cheaply known
    price_diff_inr: int | None  # new_total_inr - trip.total_inr (G2)


class ChangeResult(TypedDict):
    ok: bool
    trip: Trip | None  # new trip when ok, else None
    violations: list[Violation]  # empty when ok
    adjustments: list[str]  # human notes on dependent re-selections (e.g. transfer swapped)


# ------------------------------------------------------------------- the port


@runtime_checkable
class DomainPort(Protocol):
    """Pure domain service. No HTTP, no DB, no global mutation; safe for concurrent calls.

    Inputs are never mutated; outputs are fresh JSON-serializable dicts (str/int/bool/None/
    list/dict only; no float prices, no datetime objects).
    """

    def options(self) -> dict:
        """Return an Options dict DERIVED from destinations.json (not the raw JSON)."""
        ...

    def build(self, request: dict, trip_id: str) -> dict:
        """BuildRequest -> BuildResult. Raises InvalidRequestError if build_request_errors()
        is non-empty; never raises for infeasibility (out-of-window -> OUTSIDE_DATA_WINDOW)."""
        ...

    def validate(self, trip: dict, mode: str) -> list[dict]:
        """Full re-check of a trip against the catalog. Returns Violations; [] means valid.
        Ignores top-level/item status and booking metadata (booked snapshots validate too)."""
        ...

    def alternatives(self, trip: dict, component: str, filters: dict) -> list[dict]:
        """List Alternatives for one component, cheapest first. Malformed filters raise
        InvalidRequestError; well-formed dates with nothing addable return []."""
        ...

    def change(self, trip: dict, change: dict) -> dict:
        """Apply one ChangeRequest; return ChangeResult (all-or-nothing)."""
        ...


# ------------------------------------------------------------------- errors


class DomainError(Exception):
    """Base class for errors raised across the DomainPort boundary."""

    code = "DOMAIN_ERROR"

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class CatalogUnavailableError(DomainError):
    """Catalog missing/corrupt/not loaded. API: 503, health not ready."""

    code = "CATALOG_UNAVAILABLE"


class InvalidRequestError(DomainError):
    """Structurally bad input the API should have rejected (bad component/mode/filters). API: 422."""

    code = "INVALID_REQUEST"


# API error envelope: {"error": {"code": str, "message": str, "details": object}}
API_ERROR_STATUS: dict[str, int] = {
    "VALIDATION_ERROR": 422,
    "INVALID_CHANGE": 422,
    "CONFIRM_REJECTED": 422,
    "INVALID_REQUEST": 422,
    "NOT_FOUND": 404,
    "VERSION_CONFLICT": 409,
    "ALREADY_BOOKED": 409,
    "IDEMPOTENCY_CONFLICT": 409,
    "PRECONDITION_REQUIRED": 428,
    "CATALOG_UNAVAILABLE": 503,
    "SERVICE_UNAVAILABLE": 503,
    "INTERNAL_ERROR": 500,
}


# ------------------------------------------------- executable shape checks
# Structural conformance only (types, required keys, arithmetic invariants). These are NOT
# travel-rule validation; DomainPort.validate owns the rules.


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


_ITEM_KEYS = (
    "item_id", "type", "role", "name", "city", "status", "catalog_id", "room_id",
    "start", "end", "quantity", "price_inr", "booking", "sources", "warnings",
)
_TRIP_KEYS = (
    "trip_id", "traveler", "country", "start_date", "end_date", "budget_inr", "total_inr", "items",
)
_RESULT_KEYS = (
    "status", "trip", "cheapest_total_inr", "shortfall_inr", "reason_code", "reason", "suggestion",
)


def _parse_date(v: Any) -> date | None:
    if not isinstance(v, str) or len(v) != 10:
        return None
    try:
        return date.fromisoformat(v)
    except ValueError:
        return None


def build_request_errors(request: Any) -> list[str]:
    """Structural problems with a BuildRequest (exact 7 keys, correct types).
    DomainPort.build raises InvalidRequestError when this is non-empty."""
    if not isinstance(request, dict):
        return ["request is not an object"]
    errs = [f"request missing '{k}'" for k in BUILD_REQUEST_TYPES if k not in request]
    errs += [f"request has unexpected key '{k}'" for k in request if k not in BUILD_REQUEST_TYPES]
    for k, typ in BUILD_REQUEST_TYPES.items():
        if k not in request:
            continue
        v = request[k]
        ok = _is_int(v) if typ is int else isinstance(v, typ)
        if not ok:
            errs.append(f"request.{k} must be {typ.__name__}")
    if isinstance(request.get("children_ages"), list) and not all(_is_int(a) for a in request["children_ages"]):
        errs.append("request.children_ages must contain only ints")
    if isinstance(request.get("start_date"), str) and _parse_date(request["start_date"]) is None:
        errs.append("request.start_date must be YYYY-MM-DD")
    return errs


def trip_shape_errors(trip: Any, *, draft: bool = True) -> list[str]:
    """Structural problems with a trip.
    draft=True: every item PLANNED with booking null (domain output / draft export).
    draft=False: status/booking metadata not checked here; use booked_trip_shape_errors."""
    if not isinstance(trip, dict):
        return ["trip is not an object"]
    errs = [f"trip missing '{k}'" for k in _TRIP_KEYS if k not in trip]
    if errs:
        return errs
    if not isinstance(trip["trip_id"], str) or not trip["trip_id"]:
        errs.append("trip_id must be a non-empty string")
    for k in ("budget_inr", "total_inr"):
        if not _is_int(trip[k]):
            errs.append(f"{k} must be an int")
    for k in ("start_date", "end_date"):
        if _parse_date(trip[k]) is None:
            errs.append(f"{k} must be YYYY-MM-DD")
    travelers = None
    t = trip["traveler"]
    if not isinstance(t, dict) or any(k not in t for k in ("name", "home_airport", "adults", "children", "child_ages")):
        errs.append("traveler must have name, home_airport, adults, children, child_ages")
    elif not isinstance(t["child_ages"], list) or t["children"] != len(t["child_ages"]):
        errs.append("traveler.children must equal len(child_ages)")
    elif _is_int(t["adults"]):
        travelers = t["adults"] + t["children"]
    items = trip["items"]
    if not isinstance(items, list) or not items:
        return errs + ["items must be a non-empty list"]
    seen: set[str] = set()
    total = 0
    for i, it in enumerate(items):
        tag = f"items[{i}]"
        if not isinstance(it, dict):
            errs.append(f"{tag} is not an object")
            continue
        missing = [k for k in _ITEM_KEYS if k not in it]
        if missing:
            errs.append(f"{tag} missing {missing}")
            continue
        if it["item_id"] in seen:
            errs.append(f"{tag} duplicate item_id {it['item_id']!r}")
        seen.add(it["item_id"])
        if it["type"] not in ITEM_TYPES:
            errs.append(f"{tag} bad type {it['type']!r}")
        if it["role"] not in ITEM_ROLES or ROLE_TYPE.get(it["role"]) != it["type"]:
            errs.append(f"{tag} role {it['role']!r} does not match type {it['type']!r}")
        if not isinstance(it["catalog_id"], str) or not it["catalog_id"]:
            errs.append(f"{tag} catalog_id must be a non-empty string")
        if it["type"] == "hotel":
            if not isinstance(it["room_id"], str) or not it["room_id"]:
                errs.append(f"{tag} hotel item needs room_id")
        elif it["room_id"] is not None:
            errs.append(f"{tag} non-hotel item must have room_id null")
        if not _is_int(it["quantity"]) or it["quantity"] < 1:
            errs.append(f"{tag} quantity must be an int >= 1")
        elif travelers is not None and it["type"] in ("flight", "attraction") and it["quantity"] != travelers:
            errs.append(f"{tag} {it['type']} quantity must equal traveler count {travelers}")
        if not _is_int(it["price_inr"]) or it["price_inr"] < 0:
            errs.append(f"{tag} price_inr must be an int >= 0")
        else:
            total += it["price_inr"]
        if it["sources"] != []:
            errs.append(f"{tag} sources must be []")
        if not isinstance(it["warnings"], list):
            errs.append(f"{tag} warnings must be a list")
        if draft:
            if it["status"] != "PLANNED" or it["booking"] is not None:
                errs.append(f"{tag} draft item must be PLANNED with booking null")
        elif it["status"] not in ITEM_STATUSES:
            errs.append(f"{tag} bad status {it['status']!r}")
    if _is_int(trip["total_inr"]) and total != trip["total_inr"]:
        errs.append(f"total_inr {trip['total_inr']} != sum(items.price_inr) {total}")
    return errs


def booked_trip_shape_errors(trip: Any, reference: Any) -> list[str]:
    """Booked export/snapshot: top-level BOOKED, every item BOOKED with the same 10-digit
    string reference (SY-10, SY-11, BK-14)."""
    errs = []
    if not isinstance(reference, str) or not REFERENCE_RE.fullmatch(reference):
        errs.append("reference must be a string of exactly 10 digits")
    errs += trip_shape_errors(trip, draft=False)
    if not isinstance(trip, dict) or not isinstance(trip.get("items"), list):
        return errs
    if trip.get("status") != "BOOKED":
        errs.append("top-level status must be 'BOOKED'")
    for i, it in enumerate(trip["items"]):
        if not isinstance(it, dict):
            continue
        if it.get("status") != "BOOKED":
            errs.append(f"items[{i}] status must be 'BOOKED'")
        if it.get("booking") != {"reference": reference}:
            errs.append(f"items[{i}] booking must equal {{'reference': {reference!r}}}")
    return errs


def attraction_count_errors(trip: dict, mode: str) -> list[str]:
    """Per-day attraction COUNT rules only (city/opening-hours/overlap stay in validate()):
    max 2 every day in every mode; return day 0; arrival and city-change days 0..2;
    full days need >= 1 in mode 'initial' only."""
    if mode not in VALIDATION_MODES:
        return [f"unknown mode {mode!r}"]
    start, end = _parse_date(trip.get("start_date")), _parse_date(trip.get("end_date"))
    if start is None or end is None or end <= start:
        return ["trip start_date/end_date invalid"]
    items = trip.get("items") or []
    hotel_starts = sorted(
        d for d in (_parse_date(str(i.get("start", ""))[:10]) for i in items if i.get("type") == "hotel") if d
    )
    change_days = set(hotel_starts[1:])
    counts: dict[date, int] = {}
    errs = []
    for it in items:
        if it.get("type") != "attraction":
            continue
        d = _parse_date(str(it.get("start", ""))[:10])
        if d is None or not (start <= d <= end):
            errs.append(f"attraction {it.get('item_id')!r} is outside the trip dates")
            continue
        counts[d] = counts.get(d, 0) + 1
    day = start
    while day <= end:
        n = counts.get(day, 0)
        if day == end:
            if n:
                errs.append(f"{day} is the return day and must have 0 attractions (has {n})")
        elif n > MAX_ATTRACTIONS_PER_DAY:
            errs.append(f"{day} has {n} attractions; max is {MAX_ATTRACTIONS_PER_DAY}")
        elif (mode == "initial" and day != start and day not in change_days
              and n < MIN_ATTRACTIONS_PER_FULL_DAY_INITIAL):
            errs.append(f"{day} is a full day and needs >= {MIN_ATTRACTIONS_PER_FULL_DAY_INITIAL} attraction in initial mode")
        day += timedelta(days=1)
    return errs


def build_result_shape_errors(result: Any, request: dict, trip_id: str) -> list[str]:
    """Structural/invariant problems with a BuildResult for the given request."""
    if not isinstance(result, dict):
        return ["result is not an object"]
    errs = [f"result missing '{k}'" for k in _RESULT_KEYS if k not in result]
    if errs:
        return errs
    status, budget = result["status"], request["budget_inr"]
    if status not in BUILD_STATUSES:
        return [f"bad status {status!r}"]
    if status in ("OVER_BUDGET", "NO_PACKAGE") and (not isinstance(result["reason"], str) or not result["reason"].strip()):
        errs.append(f"{status} needs a non-empty reason")
    if status == "BUILT":
        trip = result["trip"]
        errs += trip_shape_errors(trip, draft=True)
        if isinstance(trip, dict) and not errs:
            if trip["trip_id"] != trip_id:
                errs.append("trip.trip_id must equal the trip_id passed to build")
            if trip["total_inr"] > budget:
                errs.append("BUILT trip exceeds budget")
            if trip["budget_inr"] != budget:
                errs.append("trip.budget_inr must equal request budget_inr")
            if trip["start_date"] != request["start_date"]:
                errs.append("trip.start_date must equal request start_date")
            else:
                expected_end = (date.fromisoformat(request["start_date"]) + timedelta(days=request["nights"])).isoformat()
                if trip["end_date"] != expected_end:
                    errs.append(f"trip.end_date must equal start_date + nights ({expected_end})")
            if trip["traveler"]["child_ages"] != request["children_ages"]:
                errs.append("traveler.child_ages must equal request children_ages")
            if trip["traveler"]["adults"] != request["adults"]:
                errs.append("traveler.adults must equal request adults")
            if trip["traveler"]["home_airport"] != request["departure_city"]:
                errs.append("traveler.home_airport must equal request departure_city")
            if trip["country"] != request["destination_country"]:
                errs.append("trip.country must equal request destination_country")
            cheapest = result["cheapest_total_inr"]
            if cheapest is not None and (not _is_int(cheapest) or cheapest < 0 or cheapest > trip["total_inr"]):
                errs.append("BUILT cheapest_total_inr must be None or an int <= trip.total_inr")
            errs += attraction_count_errors(trip, "initial")
        if result["shortfall_inr"] is not None or result["reason_code"] is not None:
            errs.append("BUILT must have shortfall_inr and reason_code null")
    elif status == "OVER_BUDGET":
        c, s = result["cheapest_total_inr"], result["shortfall_inr"]
        if result["trip"] is not None:
            errs.append("OVER_BUDGET must have trip null")
        if result["reason_code"] is not None:
            errs.append("OVER_BUDGET must have reason_code null")
        if not _is_int(c) or c <= budget:
            errs.append("OVER_BUDGET needs int cheapest_total_inr > budget_inr")
        elif s != c - budget:
            errs.append("shortfall_inr must equal cheapest_total_inr - budget_inr")
        if not result["suggestion"]:
            errs.append("OVER_BUDGET needs a suggestion")
    else:  # NO_PACKAGE
        if result["trip"] is not None or result["cheapest_total_inr"] is not None or result["shortfall_inr"] is not None:
            errs.append("NO_PACKAGE must have trip, cheapest_total_inr, shortfall_inr null")
        rc = result["reason_code"]
        if not isinstance(rc, str) or not rc or rc.upper() != rc:
            errs.append("NO_PACKAGE needs an UPPER_SNAKE reason_code")
    return errs


def violation_shape_errors(violations: Any) -> list[str]:
    if not isinstance(violations, list):
        return ["violations must be a list"]
    errs = []
    for i, v in enumerate(violations):
        if not isinstance(v, dict) or set(v) != {"code", "message", "item_id"}:
            errs.append(f"violations[{i}] must have exactly code, message, item_id")
        elif not isinstance(v["code"], str) or not v["code"] or v["code"].upper() != v["code"]:
            errs.append(f"violations[{i}].code must be UPPER_SNAKE")
    return errs


_FIXED_TRIP_FIELDS = ("trip_id", "traveler", "country", "start_date", "end_date", "budget_inr")


def change_result_shape_errors(result: Any, original_trip: dict) -> list[str]:
    """Structural/invariant problems with a ChangeResult relative to the trip it was applied to."""
    if not isinstance(result, dict) or any(k not in result for k in ("ok", "trip", "violations", "adjustments")):
        return ["change result must have ok, trip, violations, adjustments"]
    errs = violation_shape_errors(result["violations"])
    if not isinstance(result["adjustments"], list):
        errs.append("adjustments must be a list")
    if result["ok"] is True:
        if result["violations"]:
            errs.append("ok change must have no violations")
        new = result["trip"]
        shape = trip_shape_errors(new, draft=True)
        errs += shape
        if not shape:
            for k in _FIXED_TRIP_FIELDS:
                if new[k] != original_trip.get(k):
                    errs.append(f"change must not alter {k}")
            if new["total_inr"] > new["budget_inr"]:
                errs.append("changed trip exceeds budget")
            errs += attraction_count_errors(new, "modified")
    elif result["ok"] is False:
        if result["trip"] is not None or not result["violations"]:
            errs.append("rejected change must have trip null and >= 1 violation")
    else:
        errs.append("ok must be a bool")
    return errs


_ALT_KEYS = ("component", "change", "name", "catalog_id", "room_id", "start", "end",
             "price_inr", "new_total_inr", "price_diff_inr")
_ALT_OP = {"hotel": "replace_hotel", "outbound_flight": "replace_flight",
           "return_flight": "replace_flight", "attraction": "add_attraction"}
_HHMM_RE = re.compile(r"([01][0-9]|2[0-3]):[0-5][0-9]")  # use .fullmatch()


def alternatives_shape_errors(alternatives: Any, component: str) -> list[str]:
    """Structural problems with an alternatives() result for one component."""
    if not isinstance(alternatives, list):
        return ["alternatives must be a list"]
    errs = []
    for i, a in enumerate(alternatives):
        tag = f"alternatives[{i}]"
        if not isinstance(a, dict) or any(k not in a for k in _ALT_KEYS):
            errs.append(f"{tag} must have {list(_ALT_KEYS)}")
            continue
        if a["component"] != component:
            errs.append(f"{tag} component must be {component!r}")
        ch = a["change"]
        if not isinstance(ch, dict) or ch.get("op") != _ALT_OP.get(component):
            errs.append(f"{tag} change.op must be {_ALT_OP.get(component)!r}")
        elif component == "attraction" and not _HHMM_RE.fullmatch(str(ch.get("start_time", ""))):
            errs.append(f"{tag} attraction change must include start_time HH:MM")
        if not _is_int(a["price_inr"]) or a["price_inr"] < 0:
            errs.append(f"{tag} price_inr must be an int >= 0")
        if component == "hotel" and not a["room_id"]:
            errs.append(f"{tag} hotel alternative needs room_id")
    return errs

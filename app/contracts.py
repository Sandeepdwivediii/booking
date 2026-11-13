"""Frozen integration contract between the domain lane (Tanish) and the platform lane (Sandeep).

Pure standard library: no FastAPI, SQLAlchemy, Pydantic or database imports, so the
domain package can depend on this module without pulling in platform code.

Human-readable companion: docs/INTEGRATION_CONTRACT.md. Changing anything here needs
approval from both owners and a single dedicated integration commit.
"""

from __future__ import annotations

from typing import Any, Literal, NotRequired, Protocol, TypedDict, runtime_checkable

CONTRACT_VERSION = "1.0.0"

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
    cheapest_total_inr: int | None  # BUILT: None or int; OVER_BUDGET: int; NO_PACKAGE: None
    shortfall_inr: int | None  # OVER_BUDGET only: cheapest_total_inr - budget_inr (>= 1)
    reason_code: str | None  # NO_PACKAGE: one of NO_PACKAGE_REASONS (or new UPPER_SNAKE)
    reason: str | None  # human-readable explanation (NO_PACKAGE / OVER_BUDGET)
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
    start_time: NotRequired[str]  # "HH:MM"; absent -> domain picks earliest legal start


class RemoveAttractionChange(TypedDict):
    op: Literal["remove_attraction"]
    item_id: str


ChangeRequest = ReplaceHotelChange | ReplaceFlightChange | AddAttractionChange | RemoveAttractionChange


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
        """Return an Options dict built from destinations.json."""
        ...

    def build(self, request: dict, trip_id: str) -> dict:
        """BuildRequest -> BuildResult. Never raises for infeasibility."""
        ...

    def validate(self, trip: dict, mode: str) -> list[dict]:
        """Full re-check of a trip against the catalog. Returns Violations; [] means valid."""
        ...

    def alternatives(self, trip: dict, component: str, filters: dict) -> list[dict]:
        """List Alternatives for one component, cheapest first."""
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


def trip_shape_errors(trip: Any, *, draft: bool = True) -> list[str]:
    """Return structural problems with a trip. draft=True requires every item PLANNED/booking None."""
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
    t = trip["traveler"]
    if not isinstance(t, dict) or any(k not in t for k in ("name", "home_airport", "adults", "children", "child_ages")):
        errs.append("traveler must have name, home_airport, adults, children, child_ages")
    elif not isinstance(t["child_ages"], list) or t["children"] != len(t["child_ages"]):
        errs.append("traveler.children must equal len(child_ages)")
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


def build_result_shape_errors(result: Any, request: dict, trip_id: str) -> list[str]:
    """Return structural/invariant problems with a BuildResult for the given request."""
    if not isinstance(result, dict):
        return ["result is not an object"]
    errs = [f"result missing '{k}'" for k in _RESULT_KEYS if k not in result]
    if errs:
        return errs
    status, budget = result["status"], request["budget_inr"]
    if status not in BUILD_STATUSES:
        return [f"bad status {status!r}"]
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
            if trip["traveler"]["child_ages"] != request["children_ages"]:
                errs.append("traveler.child_ages must equal request children_ages")
            if trip["traveler"]["adults"] != request["adults"]:
                errs.append("traveler.adults must equal request adults")
            if trip["traveler"]["home_airport"] != request["departure_city"]:
                errs.append("traveler.home_airport must equal request departure_city")
            if trip["country"] != request["destination_country"]:
                errs.append("trip.country must equal request destination_country")
        if result["shortfall_inr"] is not None or result["reason_code"] is not None:
            errs.append("BUILT must have shortfall_inr and reason_code null")
    elif status == "OVER_BUDGET":
        c, s = result["cheapest_total_inr"], result["shortfall_inr"]
        if result["trip"] is not None:
            errs.append("OVER_BUDGET must have trip null")
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
        if not result["reason"]:
            errs.append("NO_PACKAGE needs a human-readable reason")
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


def change_result_shape_errors(result: Any, original_trip_id: str) -> list[str]:
    if not isinstance(result, dict) or any(k not in result for k in ("ok", "trip", "violations", "adjustments")):
        return ["change result must have ok, trip, violations, adjustments"]
    errs = violation_shape_errors(result["violations"])
    if not isinstance(result["adjustments"], list):
        errs.append("adjustments must be a list")
    if result["ok"] is True:
        if result["violations"]:
            errs.append("ok change must have no violations")
        errs += trip_shape_errors(result["trip"], draft=True)
        if isinstance(result["trip"], dict) and result["trip"].get("trip_id") != original_trip_id:
            errs.append("change must keep trip_id")
    elif result["ok"] is False:
        if result["trip"] is not None or not result["violations"]:
            errs.append("rejected change must have trip null and >= 1 violation")
    else:
        errs.append("ok must be a bool")
    return errs

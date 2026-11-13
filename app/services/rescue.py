"""Trip Rescue (Budget Rescue) platform layer over Tanish's optional `domain.rescue(trip, target, locked, max_changes)`.

Preview persists nothing and never returns trips to the client. Applying sends only the rescue parameters and the
chosen candidate_id through the normal PATCH transaction; the server re-runs the rescue on the locked, current
draft and saves only that candidate's trip (If-Match, idempotency, atomic write unchanged).
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import sessionmaker

from app.api.errors import ApiError
from app.contracts import InvalidRequestError, trip_shape_errors
from app.services.packages import get_package, transaction

log = logging.getLogger(__name__)
RESCUE_OP = "apply_rescue"
STATUSES = {"RESCUED", "NO_RESCUE_FOUND", "NO_CHANGES_NEEDED"}
LABEL = {"hotel": "Hotel", "outbound_flight": "Flight out", "return_flight": "Flight home", "attraction": "Attraction",
         "arrival_transfer": "Airport transfer", "departure_transfer": "Transfer to airport"}
UNAVAILABLE = "Finding savings is not available right now."


def _run(domain, trip: dict, params: dict) -> dict:
    fn = getattr(domain, "rescue", None)
    if not callable(fn):
        raise ApiError(503, "SERVICE_UNAVAILABLE", UNAVAILABLE)
    try:
        result = fn(trip, params["target_budget_inr"], list(params.get("locked_attraction_ids") or []),
                    params.get("max_changes", 3))
    except InvalidRequestError as e:
        raise ApiError(422, "VALIDATION_ERROR", "These savings settings can't be used for this trip.",
                       [{"msg": str(e)}]) from None
    problems = _shape_problems(domain, trip, params["target_budget_inr"], result)
    if problems:  # never show or save an unverified proposal
        log.error("domain.rescue violated expectations: %s", problems[:5])
        raise ApiError(500, "INTERNAL_ERROR", "Planner returned invalid savings suggestions.")
    return result


def _shape_problems(domain, trip, target, r) -> list[str]:
    if not isinstance(r, dict) or r.get("status") not in STATUSES or not isinstance(r.get("proposals"), list):
        return ["bad rescue result"]
    if r["original_total_inr"] != trip["total_inr"] or (r["status"] == "RESCUED") != bool(r["proposals"]):
        return ["status/proposals/original total mismatch"]
    errs = []
    for p in r["proposals"][:3]:
        t = p.get("trip")
        if trip_shape_errors(t):
            errs.append("proposal trip malformed")
            continue
        fixed = ("trip_id", "traveler", "country", "start_date", "end_date", "budget_inr")
        if any(t.get(k) != trip.get(k) for k in fixed):
            errs.append("proposal changed fixed trip fields")
        if t["total_inr"] != sum(i["price_inr"] for i in t["items"]) or t["total_inr"] != p.get("proposed_total_inr"):
            errs.append("proposal total mismatch")
        if t["total_inr"] > target or p.get("savings_inr") != trip["total_inr"] - t["total_inr"]:
            errs.append("proposal over target or wrong savings")
        if not isinstance(p.get("candidate_id"), str) or not p["candidate_id"]:
            errs.append("proposal without candidate_id")
        if not errs and domain.validate(t, "modified"):
            errs.append("proposal fails validation")
    return errs


def _name(trip, item_id=None, catalog_id=None, role=None):
    for i in trip["items"]:
        if (item_id and i["item_id"] == item_id) or (catalog_id and i["catalog_id"] == catalog_id and i["role"] == role):
            return i["name"]
    return None


def _describe(current: dict, p: dict) -> list[dict]:
    new = p["trip"]
    out = []
    for c in p.get("changes", []):
        old = _name(current, item_id=c["item_id"])
        if c["action"] == "remove":
            nxt = None
        elif c["component"] == "attraction":
            nxt = _name(new, catalog_id=c["new_catalog_id"], role="attraction_visit")
        else:
            nxt = _name(new, item_id=c["item_id"])
        out.append({"label": LABEL.get(c["component"], "Item"), "from": old, "to": nxt, "action": c["action"],
                    "price_diff_inr": c["price_diff_inr"], "dependent": False})
    for d in p.get("dependent_adjustments", []):
        old = next((i["name"] for i in current["items"] if i["role"] == d["component"]), None)
        nxt = next((i["name"] for i in new["items"] if i["role"] == d["component"]), None)
        out.append({"label": LABEL.get(d["component"], "Transfer"), "from": old, "to": nxt, "action": "replace",
                    "price_diff_inr": d["price_diff_inr"], "dependent": True})
    return out


def _note(d: dict) -> str:
    if d["action"] == "remove":
        return f"{d['label']}: {d['from']} removed."
    if d["from"] == d["to"]:
        return f"{d['label']}: {d['from']} re-timed to fit."
    return f"{d['label']}: {d['from']} changed to {d['to']}."


def preview(factory: sessionmaker, domain, package_id: str, params: dict) -> dict:
    with transaction(factory) as session:
        pkg = get_package(session, package_id)
        status, trip, version = pkg.status, pkg.trip, pkg.version
    if status == "BOOKED":
        raise ApiError(409, "ALREADY_BOOKED", "This package is already booked and can no longer be changed.")
    r = _run(domain, trip, params)
    base = {"op": RESCUE_OP, "target_budget_inr": params["target_budget_inr"],
            "locked_attraction_ids": sorted(params.get("locked_attraction_ids") or []),
            "max_changes": params.get("max_changes", 3)}
    proposals = [{"candidate_id": p["candidate_id"], "proposed_total_inr": p["proposed_total_inr"],
                  "savings_inr": p["savings_inr"], "price_diff_inr": p["proposed_total_inr"] - trip["total_inr"],
                  "number_of_changes": p["number_of_changes"], "details": _describe(trip, p),
                  "change": {**base, "candidate_id": p["candidate_id"]}} for p in r["proposals"][:3]]
    return {"package_id": package_id, "version": version, "status": r["status"], "reason": r.get("reason"),
            "original_total_inr": trip["total_inr"], "target_budget_inr": params["target_budget_inr"],
            "proposals": proposals}


def apply(domain, trip: dict, change: dict) -> dict:
    """ChangeResult for an apply_rescue PATCH, computed on the locked current draft."""
    try:
        r = _run(domain, trip, change)
    except ApiError as e:
        if e.status != 422:
            raise
        return {"ok": False, "trip": None, "adjustments": [],
                "violations": [{"code": "RESCUE_INVALID", "message": e.message, "item_id": None}]}
    p = next((p for p in r["proposals"] if p["candidate_id"] == change["candidate_id"]), None)
    if p is None:
        return {"ok": False, "trip": None, "adjustments": [], "violations": [{
            "code": "RESCUE_UNAVAILABLE", "item_id": None,
            "message": "This saving is no longer available for your trip. Please look for savings again."}]}
    return {"ok": True, "trip": p["trip"], "violations": [], "adjustments": [_note(d) for d in _describe(trip, p)]}

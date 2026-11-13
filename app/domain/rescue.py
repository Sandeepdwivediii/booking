"""Budget Rescue: affordable variants of a saved DRAFT trip that keep locked experiences.

rescue(catalog, trip, target_budget_inr, locked_attraction_ids, max_changes) -> result dict.

Every candidate is produced by the existing change engine (edits.apply_change: catalog repricing,
dependent airport-transfer re-selection, validate_trip "modified"), so there is no second pricing
system or validator. Travel dates, home airport, country, city order and nights, travellers and
locked attractions are never changed.

Search space (documented, exact within it):
  * a "component" is one outbound flight, one return flight, one hotel stay (whole segment, any hotel/room
    of that city; minimal room count) or one unlocked attraction visit (replace on the same day by a
    cheaper attraction of that night's city, or remove it if it is paid);
  * an option is kept if, applied ALONE to the trip, it is legal and lowers the total;
  * a proposal combines <= max_changes options on distinct components, applied jointly and fully
    validated; dependent airport-transfer re-selections are reported but not counted as changes.
Ranking (lexicographic, feasible proposals only):
  (number_of_changes, preference_penalty, target_budget_inr - total_inr, stable_candidate_id)
  preference_penalty: attraction replaced +1, attraction removed +2, hotel star loss x nights,
  extra flight stops +1 each.
Groups are verified in (changes, penalty) order; within a verified group every combination is applied,
so the returned top three are exact within the search space unless the verification cap is reached,
in which case optimality is reported as BEST_FOUND.
"""

from __future__ import annotations

import copy
import itertools
from datetime import date

from app.contracts import InvalidRequestError, trip_shape_errors
from app.domain.edits import apply_change
from app.domain.pricing import attraction_price
from app.domain.validator import validate_trip

MAX_CHANGES_LIMIT = 4
MAX_PROPOSALS = 3
VERIFY_CAP = 4000  # joint applications; beyond this results are reported as BEST_FOUND


def _role(trip, role):
    return [i for i in trip["items"] if i["role"] == role]


def _apply_ops(catalog, trip, ops):
    """Apply change ops in order with the existing engine; None if any step is refused."""
    current, notes = trip, []
    for op in ops:
        result = apply_change(catalog, current, op)
        if not result["ok"]:
            return None, None
        current, notes = result["trip"], notes + result["adjustments"]
    return current, notes


def _check_inputs(catalog, trip, target, locked, max_changes):
    if not isinstance(trip, dict) or trip_shape_errors(trip, draft=True):
        raise InvalidRequestError("trip must be a well-formed DRAFT trip")
    if trip.get("status") == "BOOKED":
        raise InvalidRequestError("a booked trip cannot be rescued")
    if not isinstance(target, int) or isinstance(target, bool) or target < 0:
        raise InvalidRequestError("target_budget_inr must be a non-negative integer")
    if not isinstance(max_changes, int) or isinstance(max_changes, bool) or not 1 <= max_changes <= MAX_CHANGES_LIMIT:
        raise InvalidRequestError(f"max_changes must be an integer 1..{MAX_CHANGES_LIMIT}")
    if not isinstance(locked, (list, tuple)) or not all(isinstance(a, str) for a in locked):
        raise InvalidRequestError("locked_attraction_ids must be a list of attraction IDs")
    unknown = sorted({a for a in locked if a not in catalog.attractions})
    if unknown:
        raise InvalidRequestError(f"unknown attraction IDs: {unknown}", {"unknown": unknown})
    in_trip = {i["catalog_id"] for i in _role(trip, "attraction_visit")}
    absent = sorted({a for a in locked if a not in in_trip})
    if absent:
        raise InvalidRequestError(f"locked attractions are not in this itinerary: {absent}", {"absent": absent})
    if validate_trip(catalog, trip, "modified"):
        raise InvalidRequestError("the saved draft is not valid against the catalog; reload it first")


def _components(catalog, trip, locked):
    """Standalone-saving options per component."""
    A = trip["traveler"]["adults"]
    C = len(trip["traveler"]["child_ages"])
    base = trip["total_inr"]
    comps = []

    def keep(key, item, ops, penalty, oid, new_id, new_room=None):
        new, _ = _apply_ops(catalog, trip, ops)
        if new is None or new["total_inr"] >= base:
            return None
        return {"id": oid, "ops": ops, "penalty": penalty, "delta": new["total_inr"] - base, "component": key,
                "item_id": item["item_id"], "old_catalog_id": item["catalog_id"], "old_room_id": item["room_id"],
                "new_catalog_id": new_id, "new_room_id": new_room}

    hotels = sorted(_role(trip, "hotel_stay"), key=lambda i: i["start"])
    for role, key in (("outbound_flight", "outbound_flight"), ("return_flight", "return_flight")):
        item = _role(trip, role)[0]
        cur = catalog.flights[item["catalog_id"]]
        city = hotels[0]["city"] if role == "outbound_flight" else hotels[-1]["city"]
        home = trip["traveler"]["home_airport"]
        pool = []
        for ap in catalog.airports_serving_city.get(city, ()):
            pool += (catalog.flights_by_arrival.get((home, ap, trip["start_date"]), ()) if role == "outbound_flight"
                     else catalog.flights_by_departure.get((ap, home, trip["end_date"]), ()))
        opts = [keep(key, item, [{"op": "replace_flight", "item_id": item["item_id"], "flight_id": f["flight_id"]}],
                     max(0, f["stops"] - cur["stops"]), f"{key}:{f['flight_id']}", f["flight_id"])
                for f in pool if f["flight_id"] != cur["flight_id"]]
        comps.append((key, [o for o in opts if o]))
    for h in hotels:
        key = f"hotel:{h['item_id']}"
        stars = catalog.hotels[h["catalog_id"]]["stars"]
        nights = (date.fromisoformat(h["end"]) - date.fromisoformat(h["start"])).days
        opts = []
        for hotel in catalog.hotels_by_city.get(h["city"], ()):
            for room in catalog.rooms_by_hotel.get(hotel["hotel_id"], ()):
                if (hotel["hotel_id"], room["room_id"]) == (h["catalog_id"], h["room_id"]):
                    continue
                opts.append(keep(key, h, [{"op": "replace_hotel", "item_id": h["item_id"], "hotel_id": hotel["hotel_id"],
                                           "room_id": room["room_id"]}], max(0, stars - hotel["stars"]) * nights,
                                 f"{key}:{hotel['hotel_id']}:{room['room_id']}", hotel["hotel_id"], room["room_id"]))
        comps.append((key, [o for o in opts if o]))
    in_trip = {i["catalog_id"] for i in _role(trip, "attraction_visit")}
    for v in _role(trip, "attraction_visit"):
        if v["catalog_id"] in locked:
            continue  # locked: never removed or substituted
        key = f"attraction:{v['item_id']}"
        day = v["start"][:10]
        opts = []
        for a in catalog.attractions_by_city.get(v["city"], ()):
            if a["attraction_id"] in in_trip or attraction_price(a, A, C) >= v["price_inr"]:
                continue
            opts.append(keep(key, v, [{"op": "remove_attraction", "item_id": v["item_id"]},
                                      {"op": "add_attraction", "attraction_id": a["attraction_id"], "date": day}],
                             1, f"{key}:replace:{a['attraction_id']}", a["attraction_id"]))
        if v["price_inr"] > 0:
            opts.append(keep(key, v, [{"op": "remove_attraction", "item_id": v["item_id"]}], 2,
                             f"{key}:remove", None))
        comps.append((key, [o for o in opts if o]))
    return [(k, sorted(o, key=lambda x: x["id"])) for k, o in comps if o]


def _describe(original, new, combo, locked):
    changes = []
    for o in combo:
        old_item = next(i for i in original["items"] if i["item_id"] == o["item_id"])
        if o["new_catalog_id"] is None:
            new_price = 0
        elif o["component"].startswith("attraction"):
            new_price = next(i for i in new["items"] if i["catalog_id"] == o["new_catalog_id"]
                             and i["role"] == "attraction_visit")["price_inr"]
        else:
            new_price = next(i for i in new["items"] if i["item_id"] == o["item_id"])["price_inr"]
        kind = o["component"].split(":")[0]
        changes.append({"component": kind, "item_id": o["item_id"],
                        "action": "remove" if o["new_catalog_id"] is None else "replace",
                        "old_catalog_id": o["old_catalog_id"], "new_catalog_id": o["new_catalog_id"],
                        "old_room_id": o["old_room_id"], "new_room_id": o["new_room_id"],
                        "price_diff_inr": new_price - old_item["price_inr"]})
    dependent = []
    for role in ("arrival_transfer", "departure_transfer"):
        old_t, new_t = _role(original, role)[0], _role(new, role)[0]
        if (old_t["catalog_id"], old_t["start"], old_t["price_inr"]) != (new_t["catalog_id"], new_t["start"], new_t["price_inr"]):
            dependent.append({"component": role, "item_id": new_t["item_id"], "old_catalog_id": old_t["catalog_id"],
                              "new_catalog_id": new_t["catalog_id"], "old_start": old_t["start"], "new_start": new_t["start"],
                              "price_diff_inr": new_t["price_inr"] - old_t["price_inr"]})
    return changes, dependent


def rescue(catalog, trip: dict, target_budget_inr: int, locked_attraction_ids=(), max_changes: int = 3) -> dict:
    locked_list = list(locked_attraction_ids or [])
    _check_inputs(catalog, trip, target_budget_inr, locked_list, max_changes)
    locked = set(locked_list)
    original = copy.deepcopy(trip)  # never mutated below; apply_change works on copies
    base = original["total_inr"]
    result = {"status": None, "original_total_inr": base, "target_budget_inr": target_budget_inr,
              "locked_attraction_ids": sorted(locked), "proposals": [], "reason": None,
              "optimality": "OPTIMAL_WITHIN_SEARCH_SPACE", "search": {"candidates_verified": 0, "cap_reached": False}}
    if base <= target_budget_inr:
        result.update(status="NO_CHANGES_NEEDED", reason="The saved package already fits the target budget.")
        return result

    comps = _components(catalog, original, locked)
    max_saving = sum(min(o["delta"] for o in opts) for _, opts in comps)
    found = []
    verified = 0
    for k in range(1, max_changes + 1):
        groups: dict[int, list] = {}
        for subset in itertools.combinations(comps, k):
            for combo in itertools.product(*(opts for _, opts in subset)):
                groups.setdefault(sum(o["penalty"] for o in combo), []).append(combo)
        for penalty in sorted(groups):
            for combo in sorted(groups[penalty], key=lambda c: tuple(o["id"] for o in c)):
                if verified >= VERIFY_CAP:
                    result["optimality"] = "BEST_FOUND"
                    result["search"]["cap_reached"] = True
                    break
                verified += 1
                ops = [op for o in sorted(combo, key=lambda o: o["id"]) for op in o["ops"]]
                new, _ = _apply_ops(catalog, original, ops)
                if new is None or new["total_inr"] > target_budget_inr or validate_trip(catalog, new, "modified"):
                    continue
                key = (k, penalty, target_budget_inr - new["total_inr"], "|".join(sorted(o["id"] for o in combo)))
                found.append((key, combo, new))
            if len(found) >= MAX_PROPOSALS or result["search"]["cap_reached"]:
                break
        if len(found) >= MAX_PROPOSALS or result["search"]["cap_reached"]:
            break
    result["search"]["candidates_verified"] = verified

    if not found:
        reachable = base + max_saving if comps else base
        result.update(status="NO_RESCUE_FOUND", reason=(
            f"No legal combination of up to {max_changes} change(s) brings INR {base:,} down to INR "
            f"{target_budget_inr:,} while keeping dates, cities, travellers and locked attractions. "
            f"Single-change savings add up to at most INR {base - reachable:,}."))
        return result

    for key, combo, new in sorted(found, key=lambda f: f[0])[:MAX_PROPOSALS]:
        changes, dependent = _describe(original, new, combo, locked)
        result["proposals"].append({
            "trip": new, "original_total_inr": base, "proposed_total_inr": new["total_inr"],
            "savings_inr": base - new["total_inr"], "number_of_changes": key[0], "preference_penalty": key[1],
            "changes": changes, "dependent_adjustments": dependent,
            "locked_attraction_ids": sorted(locked), "target_achieved": new["total_inr"] <= target_budget_inr,
            "candidate_id": key[3]})
    result["status"] = "RESCUED"
    return result

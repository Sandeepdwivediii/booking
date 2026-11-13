"""Catalog-dependent request checks (M1) using the derived domain.options() structure."""

from __future__ import annotations

from datetime import date, timedelta

from app.schemas import BuildRequestIn


def _err(field: str, msg: str) -> dict:
    return {"loc": ["body", field], "msg": msg, "type": "value_error"}


def validate_against_options(req: BuildRequestIn, options: dict) -> list[dict]:
    errs: list[dict] = []
    airports = [d["airport"] for d in options["departure_cities"]]
    if req.departure_city not in airports:
        errs.append(_err("departure_city", f"Departure city must be one of: {', '.join(airports)}."))
    countries = [c["name"] for c in options["countries"]]
    if req.destination_country not in countries:
        errs.append(_err("destination_country", f"Destination country must be one of: {', '.join(countries)}."))

    rules = options["rules"]
    lo, hi = rules["min_nights"], rules["max_nights"]
    if not lo <= req.nights <= hi:
        errs.append(_err("nights", f"Nights must be between {lo} and {hi}."))
        return errs  # window check needs a valid night count

    start = date.fromisoformat(req.start_date)
    first, last = date.fromisoformat(rules["first_night"]), date.fromisoformat(rules["last_departure"])
    if start < first:
        errs.append(_err("start_date", f"The holiday must start on or after {first.isoformat()}."))
    else:
        ret = start + timedelta(days=req.nights)
        if ret > last:
            errs.append(_err(
                "start_date",
                f"Return would be on {ret.isoformat()}, after the last permitted return date {last.isoformat()}.",
            ))
    return errs

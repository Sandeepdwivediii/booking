"""TEST-ONLY independent judge oracle for BOOKED exports (BK-14, SY-11).

Deliberately does not use app.contracts checkers, so it can audit them and the platform output.
"""

from __future__ import annotations

import re

_REF = re.compile(r"[0-9]{10}")
_STAMPED = ("status", "booking")


def booked_export_problems(booked: dict, reference, draft: dict | None = None) -> list[str]:
    """Problems with a booked export/snapshot. With `draft`, also require it to be exactly that draft
    with only the booking stamp added (top-level status + per-item status/booking)."""
    out = []
    if not isinstance(reference, str) or not _REF.fullmatch(reference):
        out.append(f"reference {reference!r} is not a string of exactly 10 ASCII digits")
    if not isinstance(booked, dict) or not isinstance(booked.get("items"), list) or not booked["items"]:
        return out + ["booked export has no items"]
    if booked.get("status") != "BOOKED":
        out.append("top-level status is not BOOKED")
    for i, item in enumerate(booked["items"]):
        if item.get("status") != "BOOKED":
            out.append(f"items[{i}] status {item.get('status')!r} is not BOOKED")  # SY-11
        booking = item.get("booking")
        if not isinstance(booking, dict) or set(booking) != {"reference"} or booking["reference"] != reference:
            out.append(f"items[{i}] booking {booking!r} does not carry reference {reference!r}")
    if sum(i.get("price_inr", 0) for i in booked["items"]) != booked.get("total_inr"):
        out.append("total_inr is not the sum of item prices")
    if draft is not None:
        strip = lambda t: ({k: v for k, v in t.items() if k not in ("status", "items")},  # noqa: E731
                           [{k: v for k, v in it.items() if k not in _STAMPED} for it in t["items"]])
        if strip(booked) != strip(draft):
            out.append("booked export differs from the confirmed draft beyond the booking stamp")
    return out


def stamp_booked(draft: dict, reference: str) -> dict:
    """Reference stamping of a draft (what a correct confirm produces)."""
    import copy
    booked = copy.deepcopy(draft)
    booked["status"] = "BOOKED"
    for item in booked["items"]:
        item["status"], item["booking"] = "BOOKED", {"reference": reference}
    return booked

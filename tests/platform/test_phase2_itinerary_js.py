"""Phase 2 UI logic: app/static/itinerary.js (day grouping, day kinds, price breakdown) run under Node."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from tests.platform.fixtures.contract_fixtures import REPO_ROOT, sample_trip
from tests.platform.fixtures.fake_domain import make_trip

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="BLOCKED: node not installed")
MODULE = (REPO_ROOT / "app" / "static" / "itinerary.js").as_posix()
SCRIPT = f"""
const it = require({json.dumps(MODULE)});
let s = ""; process.stdin.on("data", c => s += c).on("end", () => {{
  const trip = JSON.parse(s);
  console.log(JSON.stringify({{ days: it.buildDays(trip), money: it.priceBreakdown(trip) }}));
}});
"""


def run(trip: dict) -> dict:
    out = subprocess.run([NODE, "-e", SCRIPT], input=json.dumps(trip), capture_output=True, text=True,
                         check=True, timeout=30)
    return json.loads(out.stdout)


def test_two_city_days_kinds_and_order():
    trip = sample_trip()
    res = run(trip)
    days = res["days"]
    assert [d["date"] for d in days] == sorted(d["date"] for d in days)
    kinds = {d["date"]: d["kind"] for d in days}
    assert kinds == {"2027-04-09": "departure", "2027-04-10": "arrival", "2027-04-11": "full", "2027-04-12": "full",
                     "2027-04-13": "change", "2027-04-14": "full", "2027-04-15": "full", "2027-04-16": "return"}
    assert days[1]["day_number"] == 1 and days[-1]["day_number"] == 7
    # every item appears exactly once (check-out markers aside), in domain order
    seen = [e["item_id"] for d in days for e in d["entries"] if e["kind"] != "check_out"]
    assert seen == [i["item_id"] for i in trip["items"]]
    # leaving the hotel comes first on change and return days
    for date in ("2027-04-13", "2027-04-16"):
        day = next(d for d in days if d["date"] == date)
        assert day["entries"][0]["kind"] == "check_out"


def test_cross_zone_order_kept_from_domain():  # SY-15 groundwork: no naive string re-sort
    trip = sample_trip()
    ret = next(d for d in run(trip)["days"] if d["date"] == "2027-04-16")
    roles = [e["role"] for e in ret["entries"]]
    assert roles == ["hotel_stay", "departure_transfer", "return_flight"]


def test_price_breakdown_within_and_over_budget():
    trip = make_trip({"departure_city": "BLR", "destination_country": "Japan", "start_date": "2027-04-10",
                      "nights": 4, "adults": 2, "children_ages": [6], "budget_inr": 350000}, "t")
    m = run(trip)["money"]
    assert sum(r["amount"] for r in m["rows"]) == m["total"] == trip["total_inr"]
    assert m["state"] == "within" and m["left"] == 350000 - trip["total_inr"] and m["consistent"]
    trip["budget_inr"] = trip["total_inr"] - 1
    m = run(trip)["money"]
    assert m["state"] == "over" and m["over"] == 1 and m["left"] == 0

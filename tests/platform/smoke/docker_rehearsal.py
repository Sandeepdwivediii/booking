"""Docker rehearsal (not collected by pytest): concurrency + restart against the RUNNING app container.

Start the app on the disposable DB first:
  APP_DB=bookingDB_test docker compose -f docker/docker-compose.yml up -d --build --no-deps app
Then: python tests/platform/smoke/docker_rehearsal.py [base_url]
"""
from __future__ import annotations

import subprocess
import sys
import threading
import time
import uuid

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
COMPOSE = ["docker", "compose", "-f", "docker/docker-compose.yml"]
MENON = {"departure_city": "BLR", "destination_country": "Japan", "start_date": "2027-04-10", "nights": 6,
         "adults": 2, "children_ages": [6], "budget_inr": 350000}
results: list[tuple[str, str]] = []


def check(ok: bool, label: str) -> None:
    results.append(("PASS" if ok else "FAIL", label))


def key() -> str:
    return f"rehearsal-{uuid.uuid4()}"


def burst(calls):
    barrier, out = threading.Barrier(len(calls)), [None] * len(calls)

    def go(i, fn):
        with httpx.Client(base_url=BASE, timeout=60) as c:
            barrier.wait()
            out[i] = fn(c)

    ts = [threading.Thread(target=go, args=(i, f)) for i, f in enumerate(calls)]
    [t.start() for t in ts]
    [t.join(120) for t in ts]
    return out


def wait_ready(timeout=60) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if httpx.get(f"{BASE}/api/health", timeout=3).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(1)
    return False


c = httpx.Client(base_url=BASE, timeout=60)
check(wait_ready(), "app container ready (DB + real catalog)")

# 20 concurrent confirms on one package (5 share a key = double clicks, 15 distinct = other tabs)
p1 = c.post("/api/packages/build", json=MENON).json()["package_id"]
shared = key()
rs = burst([(lambda cl, k=(shared if i < 5 else key()): cl.post(f"/api/packages/{p1}/confirm", headers={"Idempotency-Key": k}))
            for i in range(20)])
refs = {r.json().get("reference") for r in rs}
check(all(r.status_code in (200, 201) for r in rs) and len(refs) == 1,
      f"BK-01 20 concurrent confirms -> 1 reference {refs} (statuses {sorted(r.status_code for r in rs)})")
ref = refs.pop()
before_restart = c.get(f"/api/reservations/{ref}").json()

# two packages at once -> distinct references
p2, p3 = (c.post("/api/packages/build", json=MENON).json()["package_id"] for _ in range(2))
a, b = burst([lambda cl: cl.post(f"/api/packages/{p2}/confirm", headers={"Idempotency-Key": key()}),
              lambda cl: cl.post(f"/api/packages/{p3}/confirm", headers={"Idempotency-Key": key()})])
check(a.status_code == b.status_code == 201 and a.json()["reference"] != b.json()["reference"], "BK-04 two packages -> distinct references")

# two tabs edit the same version with the real domain
p4 = c.post("/api/packages/build", json=MENON).json()
atts = [i["item_id"] for i in p4["trip"]["items"] if i["type"] == "attraction"]
e1, e2 = burst([lambda cl, it=it: cl.patch(f"/api/packages/{p4['package_id']}", json={"op": "remove_attraction", "item_id": it},
                                           headers={"If-Match": '"1"', "Idempotency-Key": key()}) for it in atts[:2]])
check(sorted([e1.status_code, e2.status_code]) == [200, 409], f"ED-12 two tabs -> {sorted([e1.status_code, e2.status_code])}")
draft_before = c.get(f"/api/packages/{p4['package_id']}").json()

# restart the backend container
subprocess.run(COMPOSE + ["restart", "app"], check=True, capture_output=True)
check(wait_ready(), "app ready again after `docker compose restart app`")
c = httpx.Client(base_url=BASE, timeout=60)
check(c.get(f"/api/reservations/{ref}").json() == before_restart, "BK-03 reservation identical after restart")
replay = c.post(f"/api/packages/{p1}/confirm", headers={"Idempotency-Key": shared})
check(replay.json()["reference"] == ref, "BK-03 retry with original key after restart -> same reference")
check(c.get(f"/api/packages/{p4['package_id']}").json() == draft_before, "BK-15 edited draft + version identical after restart")
exp = c.get(f"/api/packages/{p1}/export").json()
check(exp["status"] == "BOOKED" and all(i["booking"] == {"reference": ref} for i in exp["items"]), "booked export after restart")
check(c.get("/api/reservations/0000000000").status_code == 404, "BK-12 unknown reference -> 404")

for status, label in results:
    print(f"{status}  {label}")
print("SUMMARY:", sum(s == "PASS" for s, _ in results), "pass /", sum(s == "FAIL" for s, _ in results), "fail")

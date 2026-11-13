"""Manual browser smoke test (not collected by pytest): real app + TEST-ONLY fake domain on the
disposable test DB, driven through headless Chrome via the DevTools protocol.

Usage: python tests/platform/smoke/browser_smoke.py <repo_root> <chrome.exe> <screenshot_dir> [real]
  real -> production loader (Tanish's domain on the original catalog) instead of the fake.
"""
import dataclasses
import json
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

REPO, CHROME, SHOTS = sys.argv[1], sys.argv[2], sys.argv[3]
REAL = len(sys.argv) > 4 and sys.argv[4] == "real"
sys.path.insert(0, REPO)

import uvicorn  # noqa: E402
from websockets.sync.client import connect  # noqa: E402

from app.config import get_settings, get_test_database_url  # noqa: E402
from app.main import create_app  # noqa: E402
from tests.platform.fixtures.fake_domain import ApiFakeDomain  # noqa: E402

APP_PORT, CDP_PORT = 8767, 9333
fake = ApiFakeDomain()
if REAL:  # BK-06 in the UI: force a leading-zero reservation number
    import random
    from app.services import transactions as tx
    tx.random_reference = lambda: "00" + f"{random.randrange(10**8):08d}"
app = create_app(dataclasses.replace(get_settings(), database_url=get_test_database_url()),
                 domain_loader=None if REAL else (lambda _d: (fake, None)))
server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=APP_PORT, log_level="warning"))
threading.Thread(target=server.run, daemon=True).start()

profile = tempfile.mkdtemp()
chrome = subprocess.Popen([CHROME, "--headless=new", f"--remote-debugging-port={CDP_PORT}", f"--user-data-dir={profile}",
                           "--no-first-run", "--disable-gpu", "--remote-allow-origins=*", "--window-size=1100,1400",
                           "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
results, errors = [], []
try:
    for _ in range(50):
        try:
            targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/list"))
            page = next(t for t in targets if t["type"] == "page")
            break
        except Exception:
            time.sleep(0.2)
    ws = connect(page["webSocketDebuggerUrl"], max_size=50_000_000, legacy=True)
    seq = [0]

    def cdp(method, **params):
        seq[0] += 1
        ws.send(json.dumps({"id": seq[0], "method": method, "params": params}))
        while True:
            msg = json.loads(ws.recv())
            if msg.get("method") == "Runtime.exceptionThrown":
                errors.append(msg["params"]["exceptionDetails"].get("exception", {}).get("description", "exception"))
            if msg.get("method") == "Runtime.consoleAPICalled" and msg["params"]["type"] == "error":
                errors.append(str(msg["params"]["args"]))
            if msg.get("id") == seq[0]:
                return msg.get("result", {})

    def js(expr):
        r = cdp("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True)
        if "exceptionDetails" in r:
            raise RuntimeError(r["exceptionDetails"])
        return r["result"].get("value")

    def wait(expr, label, timeout=10):
        t0 = time.time()
        while time.time() - t0 < timeout:
            if js(expr):
                results.append(("PASS", label))
                return True
            time.sleep(0.15)
        banners = js("[...document.querySelectorAll('.banner')].filter(b => !b.hidden).map(b => b.innerText).join(' | ')")
        results.append(("FAIL", f"{label}  -> banners: {banners}"))
        return False

    def text():
        return js("document.body.innerText")

    def click(label, nth=0):
        return js(f"(() => {{ const b = [...document.querySelectorAll('#package button')].filter(b => b.textContent.trim() === {json.dumps(label)}); if (!b[{nth}]) return false; b[{nth}].click(); return true; }})()")

    def shot(name):
        data = cdp("Page.captureScreenshot", format="png", captureBeyondViewport=True)["data"]
        import base64
        open(f"{SHOTS}/{name}.png", "wb").write(base64.b64decode(data))

    cdp("Runtime.enable"); cdp("Page.enable")
    base = f"http://127.0.0.1:{APP_PORT}"
    cdp("Page.navigate", url=base + "/")
    wait("document.querySelectorAll('#departure_city option').length > 1", "options loaded from API")
    wait("document.getElementById('health').textContent === 'ready'", "health pill ready")

    # invalid input shows a field error (client sends, server 422)
    js("document.getElementById('nights').value = '2'; document.getElementById('departure_city').value = 'BLR'; document.getElementById('destination_country').value = 'Japan'; document.getElementById('start_date').value = '2027-04-10'; true")
    js("document.getElementById('submit').click(); true")
    wait("document.querySelector('.field-error[data-for=nights]').textContent.includes('between 3 and 10')", "422 field error shown (nights)")

    js("document.getElementById('nights').value = '6'; document.getElementById('add-child').click(); document.querySelector('#children input').value = '6'; true")
    js("document.getElementById('submit').click(); true")
    wait("document.body.innerText.includes('Day-by-day plan') && document.body.innerText.includes('Draft · version 1')", "build -> package view v1")
    results.append(("PASS" if "?package=" in js("location.search") else "FAIL", "URL has ?package=<id>"))
    shot("1_package")

    if REAL:  # edits arrive with Tanish's Phase 5; UI must say so honestly
        click("Change flight")
        wait("document.body.innerText.includes('not available yet') || [...document.querySelectorAll('#package button')].some(b => b.textContent === 'Choose') || document.body.innerText.includes('No other valid options')",
             "real domain: change flight -> options or honest not-available message")
        if not js("document.body.innerText.includes('not available yet')"):  # real edits implemented
            click("Remove")
            wait("document.body.innerText.includes('Draft · version 2') && document.body.innerText.includes('Package updated')",
                 "real remove attraction -> v2 (re-validated by domain)")
        real_pid = js("new URLSearchParams(location.search).get('package')")
        click("Confirm and book")
        wait("/Booked! Your reservation number is [0-9]{10}/.test(document.body.innerText)", "real confirm -> reservation number")
        ref = js("new URLSearchParams(location.search).get('reservation')")
        shot("real_booked")
        results.append(("PASS" if ref.startswith("00") and len(ref) == 10 else "FAIL", f"BK-06 UI keeps leading zeros: {ref}"))
        cdp("Page.reload")
        wait(f"document.body.innerText.includes('Booked · reservation {ref}')", "real: refresh after booking shows reservation")
        js("history.back(); true")  # SY-16: Back to the draft URL must show the persisted booked state
        wait(f"location.search.includes('package={real_pid}') && document.body.innerText.includes('Booked · reservation {ref}')",
             "SY-16 real: Back shows booked state")
        results.append(("PASS" if not js("[...document.querySelectorAll('#package button')].some(b => ['Remove','Change hotel','Change flight','Confirm and book','+ Add attraction'].includes(b.textContent))") else "FAIL",
                        "SY-16 real: no edit/confirm controls after Back"))
        raise SystemExit
    click("Remove")
    wait("document.body.innerText.includes('Draft · version 2') && document.body.innerText.includes('Package updated')", "remove attraction -> v2")
    click("+ Add attraction")
    wait("[...document.querySelectorAll('button')].some(b => b.textContent === 'Choose')", "attraction alternatives listed")
    click("Choose")
    wait("document.body.innerText.includes('Draft · version 3')", "add attraction -> v3")

    click("Change hotel")
    wait("[...document.querySelectorAll('button')].filter(b => b.textContent === 'Choose').length === 2", "hotel alternatives listed")
    click("Choose", 1)  # pricey -> refused
    wait("document.body.innerText.includes('The change was refused') && document.body.innerText.includes('exceed the budget') && document.body.innerText.includes('Draft · version 3')", "over-budget change refused, still v3")
    shot("2_refused")

    # another tab edits the same package -> this tab's next edit gets VERSION_CONFLICT
    pid = js("new URLSearchParams(location.search).get('package')")
    js(f"fetch('/api/packages/{pid}', {{method:'PATCH', headers:{{'Content-Type':'application/json','If-Match':'\"3\"','Idempotency-Key':'other-tab-0001'}}, body: JSON.stringify({{op:'remove_attraction', item_id:'i5'}})}}).then(r => r.status)")
    click("Remove")
    wait("document.body.innerText.includes('changed in another tab')", "stale tab -> VERSION_CONFLICT message")
    click("Reload package")
    wait("document.body.innerText.includes('Draft · version 4')", "reload shows v4")

    cdp("Page.reload")
    wait("document.body.innerText.includes('Draft · version 4') && document.body.innerText.includes('Day-by-day plan')", "browser refresh keeps package v4")

    click("Confirm and book")
    wait("/Booked! Your reservation number is [0-9]{10}/.test(document.body.innerText)", "confirm -> reservation number")
    ref = js("new URLSearchParams(location.search).get('reservation')")
    results.append(("PASS" if ref and len(ref) == 10 else "FAIL", f"URL has ?reservation={ref}"))
    results.append(("PASS" if not js("[...document.querySelectorAll('button')].some(b => ['Remove','Change hotel','Change flight','Confirm and book'].includes(b.textContent))") else "FAIL", "booked view is read-only"))
    shot("3_booked")

    cdp("Page.reload")
    wait(f"document.body.innerText.includes('Booked · reservation {ref}')", "refresh after booking shows reservation")
    cdp("Page.navigate", url=f"{base}/?package={pid}")  # SY-16: old draft URL
    wait(f"document.body.innerText.includes('Booked · reservation {ref}')", "SY-16: draft URL shows booked state")
    results.append(("PASS" if not js("[...document.querySelectorAll('#package button')].some(b => ['Remove','Change hotel','Change flight','Confirm and book','+ Add attraction'].includes(b.textContent))") else "FAIL",
                    "SY-16: no edit/confirm controls on booked package"))
    js("document.getElementById('lookup-ref').value = '0000000000'; document.querySelector('#lookup-form button').click(); true")
    wait("document.body.innerText.includes('Reservation not found.')", "unknown reference -> not found")
    js("document.getElementById('lookup-ref').value = '12ab'; document.querySelector('#lookup-form button').click(); true")
    wait("document.body.innerText.includes('exactly 10 digits')", "malformed reference -> message")
    js(f"document.getElementById('lookup-ref').value = '{ref}'; document.querySelector('#lookup-form button').click(); true")
    wait(f"document.body.innerText.includes('Booked · reservation {ref}')", "lookup by reference opens summary")
    ws.close()
except SystemExit:
    pass
finally:
    chrome.terminate()
    server.should_exit = True
    time.sleep(0.5)

for status, label in results:
    print(f"{status}  {label}")
print("JS errors:", errors or "none")
print("SUMMARY:", sum(s == "PASS" for s, _ in results), "pass /", sum(s == "FAIL" for s, _ in results), "fail")

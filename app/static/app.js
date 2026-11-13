"use strict";
// All data comes from the backend; all rendering uses textContent (no HTML strings with data).
/* global Itinerary */

const $ = (id) => document.getElementById(id);
const inr = (n) => "₹" + Number(n).toLocaleString("en-IN");
const ROLE_LABEL = {
  outbound_flight: "Flight out", return_flight: "Flight home", hotel_stay: "Hotel",
  arrival_transfer: "Airport transfer", departure_transfer: "Transfer to airport",
  intercity_transfer: "Transfer to next city", city_taxi: "City taxi", attraction_visit: "Attraction",
};
const KIND_LABEL = { departure: "Departure from home", arrival: "Arrival day", change: "City change",
  full: "Full day", return: "Return day" };

function el(tag, text, cls) {
  const e = document.createElement(tag);
  if (text !== undefined && text !== null) e.textContent = text;
  if (cls) e.className = cls;
  return e;
}

async function api(path, options) {
  const res = await fetch(path, options);
  let body = null;
  try { body = await res.json(); } catch { /* non-JSON */ }
  return { status: res.status, body, headers: res.headers };
}

// ---------------------------------------------------------------- health + options

async function checkHealth() {
  const pill = $("health");
  try {
    const { status, body } = await api("/api/health");
    pill.textContent = status === 200 ? "ready"
      : "not ready: " + (body?.checks?.catalog !== "ok" ? body?.checks?.catalog : "database unavailable");
    pill.className = "pill " + (status === 200 ? "ready" : "down");
  } catch {
    pill.textContent = "backend unreachable";
    pill.className = "pill down";
  }
}

function fillSelect(select, items) {
  const placeholder = el("option", "Select…");
  placeholder.value = "";
  select.replaceChildren(placeholder);
  for (const { value, label } of items) {
    const o = el("option", label);
    o.value = value;
    select.appendChild(o);
  }
}

async function loadOptions() {
  const { status, body } = await api("/api/catalog/options");
  if (status !== 200) {
    showFormError(body?.error?.message || "Could not load destinations.");
    $("submit").disabled = true;
    return;
  }
  fillSelect($("departure_city"), body.departure_cities.map((d) => ({ value: d.airport, label: `${d.city} (${d.airport})` })));
  fillSelect($("destination_country"), body.countries.map((c) => ({ value: c.name, label: c.name })));
  const r = body.rules;
  $("nights").min = r.min_nights; $("nights").max = r.max_nights;
  $("start_date").min = r.first_night; $("start_date").max = r.last_departure;
  $("date-hint").textContent = `First night on/after ${r.first_night}; return on/before ${r.last_departure}. ${r.min_nights}–${r.max_nights} nights.`;
}

// ---------------------------------------------------------------- form

function addChild(age = "") {
  const row = el("div", undefined, "child-row");
  const input = document.createElement("input");
  Object.assign(input, { type: "number", min: 0, max: 17, step: 1, value: age, required: true });
  input.setAttribute("aria-label", "Child age");
  const remove = el("button", "Remove", "secondary");
  remove.type = "button";
  remove.onclick = () => row.remove();
  row.append(el("span", "Age"), input, remove);
  $("children").appendChild(row);
}

function clearErrors() {
  $("form-error").hidden = true;
  document.querySelectorAll(".field-error").forEach((e) => (e.textContent = ""));
  document.querySelectorAll("[aria-invalid]").forEach((e) => e.removeAttribute("aria-invalid"));
}

function showFormError(msg) {
  $("form-error").textContent = msg;
  $("form-error").hidden = false;
}

function setFieldError(field, msg) {
  const slot = document.querySelector(`.field-error[data-for="${CSS.escape(field)}"]`);
  if (!slot) return false;
  slot.textContent = msg;
  const input = $(field);
  if (input) input.setAttribute("aria-invalid", "true");
  return true;
}

// Integers only; anything else is sent as-is so the server returns a clear 422.
function intOrRaw(v) { return /^-?\d+$/.test(v.trim()) ? Number(v) : v; }

function readForm() {
  const f = $("build-form");
  return {
    departure_city: f.departure_city.value,
    destination_country: f.destination_country.value,
    start_date: f.start_date.value,
    nights: intOrRaw(f.nights.value),
    adults: intOrRaw(f.adults.value),
    children_ages: [...$("children").querySelectorAll("input")].map((i) => intOrRaw(i.value)),
    budget_inr: intOrRaw(f.budget_inr.value),
  };
}

// ---------------------------------------------------------------- package view (M4, M5)

function showView(name) {
  $("planner").hidden = name !== "planner";
  $("result").hidden = name !== "result";
  $("package").hidden = name !== "package";
}

function renderBudget(trip) {
  const b = Itinerary.priceBreakdown(trip);
  const box = el("section", undefined, "card");
  box.append(el("h2", "Total and budget"));
  const table = el("table", undefined, "breakdown");
  for (const r of b.rows) {
    const tr = el("tr");
    tr.append(el("td", r.label), el("td", inr(r.amount), "num"));
    table.append(tr);
  }
  for (const [label, value, cls] of [["Total", inr(b.total), "total"], ["Budget", inr(b.budget), ""],
    [b.state === "within" ? "Left" : "Over budget", inr(b.state === "within" ? b.left : b.over), b.state]]) {
    const tr = el("tr", undefined, cls);
    tr.append(el("td", label), el("td", value, "num"));
    table.append(tr);
  }
  box.append(table);
  if (b.state === "over") box.append(el("div", "This package is over your budget.", "banner warn"));
  return box;
}

function entryLine(e) {
  const li = el("li", undefined, "entry " + e.type);
  let when = "";
  if (e.kind === "check_in") when = "Check-in";
  else if (e.kind === "check_out") when = "Check-out";
  else when = e.time ? (e.end_time ? `${e.time}–${e.end_time}` : e.time) : "";
  li.append(el("span", when, "when"));
  const what = el("div", undefined, "what");
  const title = e.kind === "check_out" ? `Check out of ${e.name}` : `${ROLE_LABEL[e.role] || e.type}: ${e.name}`;
  what.append(el("strong", title));
  const meta = [e.city, e.catalog_id];
  if (e.type === "hotel" && e.kind === "check_in") meta.push(`room ${e.room_id}`, `${e.quantity} room(s)`, `until ${e.end_date}`);
  else if (e.type === "flight" && e.end_time) meta.push(`arrives ${e.end_date} ${e.end_time} local`, `${e.quantity} seat(s)`);
  else if (e.kind === "event") meta.push(`qty ${e.quantity}`);
  what.append(el("small", meta.filter(Boolean).join(" · ")));
  for (const w of e.warnings) what.append(el("small", "⚠ " + w, "warning"));
  li.append(what);
  li.append(el("span", e.kind === "check_out" ? "" : inr(e.price_inr), "num"));
  return li;
}

function renderItinerary(trip) {
  const box = el("section", undefined, "card");
  box.append(el("h2", "Day-by-day plan"), el("p", "All times are local to where the event happens.", "hint"));
  for (const day of Itinerary.buildDays(trip)) {
    const d = el("div", undefined, "day");
    const label = day.kind === "departure" ? `${day.date} · ${KIND_LABEL.departure}`
      : `Day ${day.day_number} · ${day.date} · ${KIND_LABEL[day.kind]}`;
    d.append(el("h3", label));
    const ul = el("ul");
    day.entries.forEach((e) => ul.append(entryLine(e)));
    d.append(ul);
    box.append(d);
  }
  return box;
}

function renderPackage(view) {
  const t = view.trip;
  const box = $("package");
  box.replaceChildren();
  const head = el("section", undefined, "card");
  head.append(el("h2", `${t.country} · ${t.start_date} → ${t.end_date}`));
  const tr = t.traveler;
  const party = `${tr.adults} adult(s)` + (tr.children ? `, ${tr.children} child(ren) aged ${tr.child_ages.join(", ")}` : "");
  head.append(el("p", `From ${tr.home_airport} · ${party}`));
  const status = view.status === "BOOKED" ? `Booked · reference ${view.reference}` : `Draft · version ${view.version}`;
  head.append(el("div", status, "banner " + (view.status === "BOOKED" ? "ok" : "info")));
  const actions = el("div", undefined, "actions");
  const exp = el("a", "Download JSON", "button");
  exp.href = `/api/packages/${encodeURIComponent(view.package_id)}/export`;
  exp.setAttribute("download", `trip-${view.package_id}.json`);
  const again = el("button", "Plan another trip", "secondary");
  again.type = "button";
  again.onclick = () => { history.pushState(null, "", "/"); showView("planner"); };
  actions.append(exp, again);
  head.append(actions, el("small", `Package ID: ${view.package_id}`, "hint"));
  box.append(head, renderBudget(t), renderItinerary(t));
  showView("package");
}

async function openPackage(id) {
  $("package").replaceChildren(el("section", "Loading your package…", "card"));
  showView("package");
  const { status, body } = await api(`/api/packages/${encodeURIComponent(id)}`);
  if (status === 200) return renderPackage(body);
  const msg = status === 404 ? "Package not found. It may have been mistyped." : (body?.error?.message || `Could not load package (${status}).`);
  $("package").replaceChildren(el("section", undefined, "card"));
  $("package").firstChild.append(el("div", msg, "banner error"));
  const back = el("button", "Plan a trip", "secondary");
  back.onclick = () => { history.pushState(null, "", "/"); showView("planner"); };
  $("package").firstChild.append(back);
}

// ---------------------------------------------------------------- build results

function renderBuildOutcome(status, body) {
  const box = $("result");
  box.replaceChildren();
  if (status === 201) {
    history.pushState(null, "", `/?package=${encodeURIComponent(body.package_id)}`);
    renderPackage({ ...body, reference: null });
    return;
  }
  if (body?.status === "OVER_BUDGET") {
    box.append(el("h2", "No package fits your budget"),
      el("div", `${body.reason} Cheapest possible total: ${inr(body.cheapest_total_inr)} (${inr(body.shortfall_inr)} over your budget).`, "banner warn"));
    if (body.suggestion) box.append(el("p", "Suggestion: " + body.suggestion));
    $("planner").hidden = false; box.hidden = false;
    return;
  }
  if (body?.status === "NO_PACKAGE") {
    box.append(el("h2", "No package is possible"), el("div", body.reason, "banner error"));
    if (body.suggestion) box.append(el("p", "Suggestion: " + body.suggestion));
    $("planner").hidden = false; box.hidden = false;
    return;
  }
  box.hidden = true;
  const err = body?.error;
  let placed = false;
  for (const d of err?.details || []) {
    const field = (d.loc || []).filter((p) => p !== "body")[0];
    if (field && setFieldError(field, d.msg)) placed = true;
  }
  showFormError(placed ? "Please fix the highlighted fields." : (err?.message || `Request failed (${status}).`));
}

async function onSubmit(ev) {
  ev.preventDefault();
  clearErrors();
  $("result").hidden = true;
  const btn = $("submit");
  btn.disabled = true;
  btn.textContent = "Building…";
  try {
    const { status, body } = await api("/api/packages/build", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(readForm()),
    });
    renderBuildOutcome(status, body);
  } catch {
    showFormError("Could not reach the server. Please try again.");
  } finally {
    btn.disabled = false;
    btn.textContent = "Build my holiday";
  }
}

// ---------------------------------------------------------------- routing (refresh keeps the package)

function route() {
  const id = new URLSearchParams(location.search).get("package");
  if (id) openPackage(id); else showView("planner");
}

$("add-child").onclick = () => addChild();
$("build-form").addEventListener("submit", onSubmit);
window.addEventListener("popstate", route);
checkHealth();
loadOptions();
route();

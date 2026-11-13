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

function entryLine(e, ctx) {
  const li = el("li", undefined, "entry " + e.type);
  let when = "";
  if (e.kind === "check_in") when = "Check-in";
  else if (e.kind === "check_out") when = "Check-out";
  else if (e.type === "flight") when = e.time ? `${e.time} dep` : "";  // arrival is in another time zone: shown in details
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
  const side = el("div", undefined, "side");
  side.append(el("span", e.kind === "check_out" ? "" : inr(e.price_inr), "num"));
  if (ctx?.editable && e.kind !== "check_out") {
    const action = ENTRY_ACTIONS[e.role];
    if (action) side.append(actionButton(action.label, () => action.run(ctx, e)));
  }
  li.append(side);
  return li;
}

// ---------------------------------------------------------------- edits (M6): the backend is the only source of truth

const ENTRY_ACTIONS = {
  hotel_stay: { label: "Change hotel", run: (ctx, e) => showAlternatives(ctx, "hotel", { item_id: e.item_id }, `Other hotels in ${e.city}`, e.price_inr) },
  outbound_flight: { label: "Change flight", run: (ctx, e) => showAlternatives(ctx, "outbound_flight", {}, "Other outbound flights", e.price_inr) },
  return_flight: { label: "Change flight", run: (ctx, e) => showAlternatives(ctx, "return_flight", {}, "Other return flights", e.price_inr) },
  attraction_visit: { label: "Remove", run: (ctx, e) => applyChange(ctx, { op: "remove_attraction", item_id: e.item_id }) },
};

function actionButton(label, onClick) {
  const b = el("button", label, "secondary small");
  b.type = "button";
  b.onclick = async () => { b.disabled = true; try { await onClick(); } finally { b.disabled = false; } };
  return b;
}

function newKey() {
  return crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function packageMessage(ctx, text, cls, extra) {
  ctx.message.replaceChildren();
  if (!text) { ctx.message.hidden = true; return; }
  ctx.message.className = "banner " + cls;
  ctx.message.append(el("div", text));
  if (extra) ctx.message.append(extra);
  ctx.message.hidden = false;
}

function explainError(ctx, status, body) {
  const err = body?.error;
  const reload = actionButton("Reload package", () => openPackage(ctx.view.package_id));
  switch (err?.code) {
    case "INVALID_CHANGE":
    case "CONFIRM_REJECTED": {
      const ul = el("ul");
      for (const v of err.details || []) ul.append(el("li", affectedLabel(ctx, v.item_id) + v.message));
      return packageMessage(ctx, err.code === "INVALID_CHANGE" ? "The change was refused. Nothing was saved." : err.message, "error", ul);
    }
    case "VERSION_CONFLICT":
      ctx.panel.hidden = true;  // listed options/prices belong to an older version
      return packageMessage(ctx, "This package was changed in another tab. Reload to see the latest version.", "warn", reload);
    case "ALREADY_BOOKED":
      return packageMessage(ctx, "This package is already booked and can no longer be changed.", "warn", reload);
    default:
      return packageMessage(ctx, err?.message || `Request failed (${status}).`, "error");
  }
}

function affectedLabel(ctx, itemId) {
  const item = itemId && ctx.view.trip.items.find((i) => i.item_id === itemId);
  return item ? `${ROLE_LABEL[item.role] || item.type}: ${item.name} — ` : "";
}

function signedDiff(diff) {
  if (diff === 0) return "same price";
  return `${diff > 0 ? "+" : "−"}${inr(Math.abs(diff))}`;
}

async function showAlternatives(ctx, component, filters, title, currentPrice) {
  const panel = ctx.panel;
  packageMessage(ctx, null);  // never keep a warning that belongs to another candidate
  panel.replaceChildren(el("h3", title), el("p", "Loading options…", "hint"));
  panel.hidden = false;
  const q = new URLSearchParams({ component, ...filters });
  const { status, body } = await api(`/api/packages/${encodeURIComponent(ctx.view.package_id)}/alternatives?${q}`);
  panel.replaceChildren(el("h3", title));
  if (status !== 200) { panel.hidden = true; return explainError(ctx, status, body); }
  if (!body.alternatives.length) panel.append(el("p", "No other valid options for this choice.", "hint"));
  const ul = el("ul", undefined, "alts");
  for (const a of body.alternatives) {
    const li = el("li");
    const diff = a.price_diff_inr ?? (a.price_inr - currentPrice);  // server value: whole-trip difference vs saved version
    const meta = [a.catalog_id, a.room_id && `room ${a.room_id}`, a.start && `${a.start}${a.end ? " → " + a.end : ""}`]
      .filter(Boolean).join(" · ");
    const info = el("div");
    info.append(el("strong", a.name), el("small", meta));
    if (a.new_total_inr != null) {
      const left = ctx.view.trip.budget_inr - a.new_total_inr;
      info.append(el("small", `New total ${inr(a.new_total_inr)} · ${left >= 0 ? inr(left) + " left" : inr(-left) + " over budget"}`,
        left >= 0 ? "budget-ok" : "warning"));
    }
    const price = el("span", undefined, "num price-col");
    price.append(el("span", inr(a.price_inr)), el("span", signedDiff(diff), "diff " + (diff > 0 ? "up" : diff < 0 ? "down" : "same")));
    li.append(info, price,
      actionButton("Choose", () => applyChange(ctx, a.change)));
    ul.append(li);
  }
  panel.append(ul, actionButton("Close", async () => { panel.hidden = true; }));
}

async function applyChange(ctx, change) {
  packageMessage(ctx, "Checking the whole package…", "info");
  let res;
  try {
    res = await api(`/api/packages/${encodeURIComponent(ctx.view.package_id)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", "If-Match": `"${ctx.view.version}"`, "Idempotency-Key": newKey() },
      body: JSON.stringify(change),
    });
  } catch {
    return packageMessage(ctx, "Could not reach the server. Nothing was changed.", "error");
  }
  if (res.status === 200) {
    renderPackage({ ...res.body, reference: null });
    const adj = res.body.adjustments || [];
    let list;
    if (adj.length) {
      list = el("ul");
      for (const a of adj) list.append(el("li", a));
    }
    return packageMessage(currentCtx, adj.length ? "Package updated and re-checked. Also changed to keep it valid:" : "Package updated and re-checked.", "ok", list);
  }
  explainError(ctx, res.status, res.body);
}

async function confirmBooking(ctx) {
  // One key per package version: double clicks or a retry after a lost response reuse it.
  const storeKey = `confirm:${ctx.view.package_id}:${ctx.view.version}`;
  const key = sessionStorage.getItem(storeKey) || newKey();
  sessionStorage.setItem(storeKey, key);
  packageMessage(ctx, "Confirming…", "info");
  let res;
  try {
    res = await api(`/api/packages/${encodeURIComponent(ctx.view.package_id)}/confirm`, {
      method: "POST", headers: { "Idempotency-Key": key },
    });
  } catch {
    return packageMessage(ctx, "Could not reach the server. Press Confirm again; you will not be booked twice.", "error");
  }
  if (res.status === 200 || res.status === 201) {
    history.pushState(null, "", `/?reservation=${encodeURIComponent(res.body.reference)}`);
    return renderReservation(res.body);
  }
  explainError(ctx, res.status, res.body);
}

// ---------------------------------------------------------------- reservations (M7)

function renderReservation(res) {
  renderPackage({ package_id: res.package_id, version: null, status: "BOOKED", reference: res.reference, trip: res.trip });
  packageMessage(currentCtx, `Booked! Your reservation number is ${res.reference}.`, "ok");
}

function reservationNotFound(msg) {
  const card = el("section", undefined, "card");
  card.append(el("div", msg, "banner error"));
  const back = el("button", "Plan a trip", "secondary");
  back.onclick = () => { history.pushState(null, "", "/"); showView("planner"); };
  card.append(back);
  $("package").replaceChildren(card);
}

async function openReservation(ref) {
  $("package").replaceChildren(el("section", "Looking up your reservation…", "card"));
  showView("package");
  if (!/^[0-9]{10}$/.test(ref)) return reservationNotFound("A reservation number has exactly 10 digits.");
  const { status, body } = await api(`/api/reservations/${encodeURIComponent(ref)}`);
  if (status === 200) return renderReservation(body);
  reservationNotFound(status === 404 ? "Reservation not found." : (body?.error?.message || `Lookup failed (${status}).`));
}

function renderItinerary(trip, ctx) {
  const box = el("section", undefined, "card");
  box.append(el("h2", "Day-by-day plan"), el("p", "All times are local to where the event happens.", "hint"));
  for (const day of Itinerary.buildDays(trip)) {
    const d = el("div", undefined, "day");
    const label = day.kind === "departure" ? `${day.date} · ${KIND_LABEL.departure}`
      : `Day ${day.day_number} · ${day.date} · ${KIND_LABEL[day.kind]}`;
    d.append(el("h3", label));
    const ul = el("ul");
    day.entries.forEach((e) => ul.append(entryLine(e, ctx)));
    d.append(ul);
    if (ctx?.editable && ["arrival", "full", "change"].includes(day.kind)) {
      d.append(actionButton("+ Add attraction", () => showAlternatives(ctx, "attraction", { date: day.date }, `Attractions on ${day.date}`, 0)));
    }
    box.append(d);
  }
  return box;
}

let currentCtx = null;

function renderPackage(view) {
  const t = view.trip;
  const box = $("package");
  box.replaceChildren();
  const head = el("section", undefined, "card");
  head.append(el("h2", `${t.country} · ${t.start_date} → ${t.end_date}`));
  const tr = t.traveler;
  const party = `${tr.adults} adult(s)` + (tr.children ? `, ${tr.children} child(ren) aged ${tr.child_ages.join(", ")}` : "");
  head.append(el("p", `From ${tr.home_airport} · ${party}`));
  const editable = view.status === "DRAFT";
  const status = view.status === "BOOKED" ? `Booked · reservation ${view.reference}` : `Draft · version ${view.version}`;
  head.append(el("div", status, "banner " + (view.status === "BOOKED" ? "ok" : "info")));
  const message = el("div", undefined, "banner");
  message.hidden = true;
  const panel = el("div", undefined, "alt-panel");
  panel.hidden = true;
  const ctx = { view, editable, message, panel };
  currentCtx = ctx;
  const actions = el("div", undefined, "actions");
  if (editable) {
    const c = actionButton("Confirm and book", () => confirmBooking(ctx));
    c.className = "primary";
    c.id = "confirm-btn";
    actions.append(c);
  }
  const exp = el("a", "Download JSON", "button");
  exp.href = `/api/packages/${encodeURIComponent(view.package_id)}/export`;
  exp.setAttribute("download", `trip-${view.package_id}.json`);
  const again = el("button", "Plan another trip", "secondary");
  again.type = "button";
  again.onclick = () => { history.pushState(null, "", "/"); showView("planner"); };
  actions.append(exp, again);
  head.append(actions, el("small", `Package ID: ${view.package_id}`, "hint"), message, panel);
  box.append(head, renderBudget(t), renderItinerary(t, ctx));
  showView("package");
  window.scrollTo({ top: 0 });
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
    box.scrollIntoView({ behavior: "smooth", block: "start" });
    return;
  }
  if (body?.status === "NO_PACKAGE") {
    box.append(el("h2", "No package is possible"), el("div", body.reason, "banner error"));
    if (body.suggestion) box.append(el("p", "Suggestion: " + body.suggestion));
    $("planner").hidden = false; box.hidden = false;
    box.scrollIntoView({ behavior: "smooth", block: "start" });
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
  const params = new URLSearchParams(location.search);
  const ref = params.get("reservation"), id = params.get("package");
  if (ref) openReservation(ref); else if (id) openPackage(id); else showView("planner");
}

$("lookup-form").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const ref = $("lookup-ref").value.trim();
  history.pushState(null, "", `/?reservation=${encodeURIComponent(ref)}`);
  openReservation(ref);
});

$("add-child").onclick = () => addChild();
$("build-form").addEventListener("submit", onSubmit);
window.addEventListener("popstate", route);
checkHealth();
loadOptions();
route();

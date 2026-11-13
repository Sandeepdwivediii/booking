"use strict";
// All data comes from the backend; all rendering uses textContent (no innerHTML with data).

const $ = (id) => document.getElementById(id);
const inr = (n) => "₹" + Number(n).toLocaleString("en-IN");

function el(tag, text, cls) {
  const e = document.createElement(tag);
  if (text !== undefined) e.textContent = text;
  if (cls) e.className = cls;
  return e;
}

async function api(path, options) {
  const res = await fetch(path, options);
  let body = null;
  try { body = await res.json(); } catch { /* non-JSON */ }
  return { status: res.status, body };
}

async function checkHealth() {
  const pill = $("health");
  try {
    const { status, body } = await api("/api/health");
    pill.textContent = status === 200 ? "ready" : "not ready: " + (body?.checks?.catalog !== "ok" ? body?.checks?.catalog : "database unavailable");
    pill.className = "pill " + (status === 200 ? "ready" : "down");
  } catch {
    pill.textContent = "backend unreachable";
    pill.className = "pill down";
  }
}

function fillSelect(select, items) {
  select.replaceChildren(el("option", "Select…"));
  select.firstChild.value = "";
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
  const slot = document.querySelector(`.field-error[data-for="${field}"]`);
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

function renderResult(status, body) {
  const box = $("result");
  box.replaceChildren();
  box.hidden = false;
  if (status === 201) {
    const t = body.trip, left = t.budget_inr - t.total_inr;
    box.append(el("h2", "Your holiday package is ready"), el("div", "Draft saved. Day-by-day plan comes next.", "banner ok"));
    const dl = el("dl");
    for (const [k, v] of [["Package ID", body.package_id], ["Country", t.country], ["Dates", `${t.start_date} → ${t.end_date}`],
      ["Total", inr(t.total_inr)], ["Budget", inr(t.budget_inr)], [left >= 0 ? "Left" : "Over", inr(Math.abs(left))]]) {
      dl.append(el("dt", k), el("dd", v));
    }
    box.append(dl);
  } else if (body?.status === "OVER_BUDGET") {
    box.append(el("h2", "No package fits your budget"),
      el("div", `${body.reason} Cheapest possible total: ${inr(body.cheapest_total_inr)} (${inr(body.shortfall_inr)} over).`, "banner warn"));
    if (body.suggestion) box.append(el("p", body.suggestion));
  } else if (body?.status === "NO_PACKAGE") {
    box.append(el("h2", "No package is possible"), el("div", body.reason, "banner error"));
    if (body.suggestion) box.append(el("p", body.suggestion));
  } else {
    box.hidden = true;
    const err = body?.error;
    let placed = false;
    for (const d of err?.details || []) {
      const field = (d.loc || []).filter((p) => p !== "body")[0];
      if (field && setFieldError(field, d.msg)) placed = true;
    }
    showFormError(placed ? "Please fix the highlighted fields." : (err?.message || `Request failed (${status}).`));
  }
}

async function onSubmit(ev) {
  ev.preventDefault();
  clearErrors();
  const btn = $("submit");
  btn.disabled = true;
  btn.textContent = "Building…";
  try {
    const { status, body } = await api("/api/packages/build", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(readForm()),
    });
    renderResult(status, body);
  } catch {
    showFormError("Could not reach the server. Please try again.");
  } finally {
    btn.disabled = false;
    btn.textContent = "Build my holiday";
  }
}

$("add-child").onclick = () => addChild();
$("build-form").addEventListener("submit", onSubmit);
checkHealth();
loadOptions();

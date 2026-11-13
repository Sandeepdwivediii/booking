"use strict";
// Pure view-model helpers (no DOM). Unit-tested with Node from tests/platform.
// Times are catalog local times; the domain orders items chronologically, so we keep that
// order inside a day and never re-sort cross-time-zone timestamps as strings.
(function (root) {
  const TYPE_LABEL = { flight: "Flights", hotel: "Hotels", transfer: "Transfers", attraction: "Attractions" };

  const datePart = (s) => String(s).slice(0, 10);
  const timePart = (s) => (s && String(s).length > 10 ? String(s).slice(11, 16) : null);

  function addDays(iso, n) {
    const d = new Date(iso + "T00:00:00Z");
    d.setUTCDate(d.getUTCDate() + n);
    return d.toISOString().slice(0, 10);
  }

  function daysBetween(a, b) {
    return Math.round((Date.parse(b + "T00:00:00Z") - Date.parse(a + "T00:00:00Z")) / 86400000);
  }

  // arrival / change / full / return, matching the contract's attraction day rules.
  function dayKind(trip, date) {
    const hotelStarts = trip.items.filter((i) => i.type === "hotel").map((i) => datePart(i.start)).sort();
    if (date === trip.end_date) return "return";
    if (date === trip.start_date) return "arrival";
    if (date < trip.start_date) return "departure";
    if (hotelStarts.slice(1).includes(date)) return "change";
    return "full";
  }

  function entry(item, kind) {
    const isHotel = item.type === "hotel";
    return {
      item_id: item.item_id, type: item.type, role: item.role, kind, name: item.name, city: item.city,
      catalog_id: item.catalog_id, room_id: item.room_id, quantity: item.quantity, price_inr: item.price_inr,
      status: item.status, warnings: item.warnings || [],
      time: isHotel ? null : timePart(item.start),
      end_time: isHotel ? null : timePart(item.end),
      end_date: item.end ? datePart(item.end) : null,
    };
  }

  // Days in date order; each day's entries in domain order. Hotels add a check-out entry.
  function buildDays(trip) {
    const days = new Map();
    const push = (date, e) => { if (!days.has(date)) days.set(date, []); days.get(date).push(e); };
    for (const item of trip.items) {
      push(datePart(item.start), entry(item, item.type === "hotel" ? "check_in" : "event"));
    }
    for (const item of trip.items) {
      if (item.type === "hotel" && item.end) {
        const out = entry(item, "check_out");
        out.price_inr = 0; // display marker; the stay is priced on the check-in entry
        out.warnings = []; // warnings are shown once, on the check-in entry
        const d = datePart(item.end);
        days.set(d, [out, ...(days.get(d) || [])]); // leave the hotel first that day
      }
    }
    return [...days.keys()].sort().map((date) => ({
      date,
      day_number: daysBetween(trip.start_date, date) + 1,
      kind: dayKind(trip, date),
      entries: days.get(date),
    }));
  }

  function priceBreakdown(trip) {
    const byType = {};
    for (const t of Object.keys(TYPE_LABEL)) byType[t] = 0;
    for (const i of trip.items) byType[i.type] = (byType[i.type] || 0) + i.price_inr;
    const itemsTotal = trip.items.reduce((s, i) => s + i.price_inr, 0);
    const diff = trip.budget_inr - trip.total_inr;
    return {
      rows: Object.keys(TYPE_LABEL).map((t) => ({ type: t, label: TYPE_LABEL[t], amount: byType[t] })),
      total: trip.total_inr, budget: trip.budget_inr,
      state: diff >= 0 ? "within" : "over", left: Math.max(diff, 0), over: Math.max(-diff, 0),
      consistent: itemsTotal === trip.total_inr,
    };
  }

  const api = { buildDays, priceBreakdown, dayKind, addDays, datePart, timePart };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.Itinerary = api;
})(typeof window !== "undefined" ? window : globalThis);

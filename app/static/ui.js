"use strict";
// Presentation helpers (no data logic): toasts, success modal, scroll reveal, number count-up, skeletons,
// button loading state. DOM APIs only (never innerHTML). Motion respects prefers-reduced-motion.
(function (root) {
  const reduced = () => !!(root.matchMedia && root.matchMedia("(prefers-reduced-motion: reduce)").matches);
  const make = (tag, text, cls) => {
    const e = document.createElement(tag);
    if (text != null) e.textContent = text;
    if (cls) e.className = cls;
    return e;
  };

  // ---------------------------------------------------------------- toasts (aria-live region)
  function toast(message, kind = "info", ms = 4200) {
    let region = document.getElementById("toasts");
    if (!region) {
      region = make("div", undefined, "toasts");
      region.id = "toasts";
      region.setAttribute("role", "status");
      region.setAttribute("aria-live", "polite");
      document.body.append(region);
    }
    const t = make("div", undefined, "toast " + kind);
    const close = make("button", "×");
    close.type = "button";
    close.setAttribute("aria-label", "Dismiss notification");
    const dismiss = () => {
      if (!t.isConnected) return;
      if (reduced()) return t.remove();
      t.classList.add("leaving");
      t.addEventListener("animationend", () => t.remove(), { once: true });
    };
    close.addEventListener("click", dismiss);
    t.append(make("span", message), close);
    region.append(t);
    while (region.children.length > 3) region.firstElementChild.remove();  // never stack more than three
    setTimeout(dismiss, ms);
    return t;
  }

  // ---------------------------------------------------------------- booking success modal
  function bookedModal(reference, total) {
    let dlg = document.getElementById("booked-modal");
    if (dlg) dlg.remove();
    dlg = make("dialog", undefined, "modal");
    dlg.id = "booked-modal";
    dlg.setAttribute("aria-labelledby", "booked-modal-title");
    const head = make("div", undefined, "modal-head");
    head.append(make("div", "✓", "modal-icon"));
    const h = make("h2", "Holiday booked");
    h.id = "booked-modal-title";
    head.append(h, make("p", `Keep your reservation number. Total ${total}.`, "muted"));
    const ref = make("div", reference, "modal-ref");
    const actions = make("div", undefined, "modal-actions");
    const copy = make("button", "Copy number", "secondary");
    copy.type = "button";
    copy.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(reference); toast("Reservation number copied.", "ok"); }
      catch { toast("Copy not available; please note the number.", "info"); }
    });
    const done = make("button", "View booking");
    done.type = "button";
    done.addEventListener("click", () => dlg.close());
    actions.append(copy, done);
    dlg.append(head, ref, actions);
    dlg.addEventListener("close", () => dlg.remove());
    document.body.append(dlg);
    if (typeof dlg.showModal === "function") dlg.showModal(); else dlg.setAttribute("open", "");
    done.focus();
  }

  // ---------------------------------------------------------------- scroll reveal with stagger
  let observer = null;
  function reveal(elements) {
    const list = [...elements];
    if (reduced() || !("IntersectionObserver" in root)) return;
    observer = observer || new IntersectionObserver((entries) => {
      for (const e of entries) if (e.isIntersecting) { e.target.classList.add("is-visible"); observer.unobserve(e.target); }
    }, { rootMargin: "0px 0px -8% 0px" });
    list.forEach((el, i) => {
      el.style.setProperty("--i", String(i % 8));
      el.classList.add("reveal");
      observer.observe(el);
    });
  }

  // ---------------------------------------------------------------- animated numbers
  function countUp(el, to, format, ms = 650) {
    const from = Number(el.dataset.value || 0);
    el.dataset.value = String(to);
    if (reduced() || from === to) { el.textContent = format(to); return; }
    const start = performance.now();
    const step = (now) => {
      const p = Math.min(1, (now - start) / ms);
      const eased = 1 - Math.pow(1 - p, 3);
      el.textContent = format(Math.round(from + (to - from) * eased));
      if (p < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
    if (from) { el.classList.remove("bump"); void el.offsetWidth; el.classList.add("bump"); }
  }

  // ---------------------------------------------------------------- skeleton + loading button
  function skeleton(lines = 4) {
    const box = make("section", undefined, "card skeleton");
    box.setAttribute("aria-busy", "true");
    box.setAttribute("aria-label", "Loading");
    box.append(make("div", undefined, "sk title"));
    for (let i = 0; i < lines; i++) box.append(make("div", undefined, "sk " + (i % 3 === 2 ? "line short" : i % 2 ? "block" : "line")));
    return box;
  }

  function busy(button, on) {
    button.classList.toggle("is-loading", on);
    button.setAttribute("aria-busy", String(on));
  }

  root.UI = { toast, bookedModal, reveal, countUp, skeleton, busy, reduced };
})(window);

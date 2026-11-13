"use strict";
// Theme: system preference first, explicit choice persisted in localStorage. Loaded in <head> to avoid a flash.
(function () {
  const KEY = "bmh-theme";
  const media = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  const stored = () => { try { return localStorage.getItem(KEY); } catch { return null; } };
  const system = () => (media && media.matches ? "dark" : "light");

  function apply(theme) {
    document.documentElement.dataset.theme = theme;
    const btn = document.getElementById("theme-toggle");
    if (btn) {
      const next = theme === "dark" ? "light" : "dark";
      btn.setAttribute("aria-label", `Switch to ${next} mode`);
      btn.setAttribute("aria-pressed", String(theme === "dark"));
    }
  }

  apply(stored() || system());
  if (media) {
    media.addEventListener("change", () => { if (!stored()) apply(system()); });
  }
  document.addEventListener("DOMContentLoaded", () => {
    apply(document.documentElement.dataset.theme);
    document.getElementById("theme-toggle")?.addEventListener("click", () => {
      const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      try { localStorage.setItem(KEY, next); } catch { /* private mode: not persisted */ }
      apply(next);
    });
  });
})();

"use strict";
// Homepage sections. Destinations come from the real /api/catalog/options response; photos are the
// official dataset images (credits rendered in the footer). Holiday ideas only pre-fill the search form:
// prices and availability are calculated by the backend when the user builds.
(function () {
  const PHOTOS = {
    "Tokyo": { file: "ATT-TYO-004.jpg", subject: "Senso-ji", author: "Zairon", license: "CC BY-SA 4.0", url: "https://creativecommons.org/licenses/by-sa/4.0" },
    "Kyoto": { file: "ATT-KYO-002.jpg", subject: "Fushimi Inari Taisha", author: "Basile Morin", license: "CC BY-SA 4.0", url: "https://creativecommons.org/licenses/by-sa/4.0" },
    "Osaka": { file: "ATT-OSA-001.jpg", subject: "Dotonbori", author: "Joli Rumi", license: "CC BY-SA 4.0", url: "https://creativecommons.org/licenses/by-sa/4.0" },
    "Dubai": { file: "ATT-DXB-001.jpg", subject: "Burj Khalifa", author: "Laika ac", license: "CC BY-SA 2.0", url: "https://creativecommons.org/licenses/by-sa/2.0" },
    "Abu Dhabi": { file: "ATT-AUH-001.jpg", subject: "Sheikh Zayed Grand Mosque", author: "Guilhem Vellut", license: "CC BY 2.0", url: "https://creativecommons.org/licenses/by/2.0" },
  };
  const IDEAS = [
    { title: "Family week in Japan", text: "Bengaluru → Japan · 6 nights · 2 adults + 1 child", values: { departure_city: "BLR", destination_country: "Japan", start_date: "2027-04-10", nights: 6, adults: 2, budget_inr: 350000 }, children: [6] },
    { title: "Dubai & Abu Dhabi escape", text: "Chennai → UAE · 10 nights · 2 adults + 1 child", values: { departure_city: "MAA", destination_country: "United Arab Emirates", start_date: "2027-04-10", nights: 10, adults: 2, budget_inr: 1000000 }, children: [5] },
    { title: "Solo city break", text: "Delhi → UAE · 4 nights · 1 adult", values: { departure_city: "DEL", destination_country: "United Arab Emirates", start_date: "2027-04-05", nights: 4, adults: 1, budget_inr: 200000 }, children: [] },
  ];

  const make = (tag, text, cls) => {
    const e = document.createElement(tag);
    if (text != null) e.textContent = text;
    if (cls) e.className = cls;
    return e;
  };

  function focusSearch() {
    document.querySelector(".search-card")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function setValue(id, value) {
    const el = document.getElementById(id);
    if (!el) return;
    el.value = String(value);
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function renderDestinations(options) {
    const grid = document.getElementById("destinations");
    grid.replaceChildren();
    for (const country of options.countries) {
      for (const city of country.cities) {
        const photo = PHOTOS[city];
        const card = make("button", undefined, "dest-card");
        card.type = "button";
        card.setAttribute("aria-label", `Plan a holiday in ${city}, ${country.name}`);
        const media = make("div", undefined, "dest-media");
        if (photo) {
          const img = document.createElement("img");
          Object.assign(img, { src: `/static/img/destinations/${photo.file}`, alt: `${photo.subject}, ${city}`, loading: "lazy", decoding: "async" });
          media.append(img);
        }
        const body = make("div", undefined, "dest-body");
        body.append(make("strong", city), make("span", country.name, "muted"));
        card.append(media, body);
        card.addEventListener("click", () => {
          setValue("destination_country", country.name);
          window.UI?.toast(`Destination set to ${country.name}.`, "info", 2600);
          focusSearch();
        });
        grid.append(card);
      }
    }
    window.UI?.reveal(grid.children);
  }

  function renderIdeas() {
    const grid = document.getElementById("ideas");
    for (const idea of IDEAS) {
      const card = make("article", undefined, "idea-card card");
      card.append(make("h3", idea.title), make("p", idea.text, "muted"),
        make("p", `Budget ₹${idea.values.budget_inr.toLocaleString("en-IN")} · from ${idea.values.start_date}`, "idea-meta"));
      const btn = make("button", "Use this trip", "secondary");
      btn.type = "button";
      btn.addEventListener("click", () => {
        for (const [k, v] of Object.entries(idea.values)) setValue(k, v);
        const kids = document.getElementById("children");
        kids.replaceChildren();
        for (const age of idea.children) {
          document.getElementById("add-child").click();
          kids.lastElementChild.querySelector("input").value = age;
        }
        window.UI?.toast(`"${idea.title}" loaded into the search.`, "info", 2600);
        focusSearch();
      });
      card.append(btn);
      grid.append(card);
    }
    window.UI?.reveal(grid.children);
    window.UI?.reveal(document.querySelectorAll(".how-step"));
  }

  function renderCredits() {
    const ul = document.getElementById("photo-credits");
    for (const [city, p] of Object.entries(PHOTOS)) {
      const li = make("li", `${city}: ${p.subject} — photo by ${p.author}, `);
      const a = make("a", p.license);
      Object.assign(a, { href: p.url, target: "_blank", rel: "noopener noreferrer" });
      li.append(a, document.createTextNode(" (Wikimedia Commons)"));
      ul.append(li);
    }
  }

  async function init() {
    renderIdeas();
    renderCredits();
    const grid = document.getElementById("destinations");
    grid.append(make("p", "Loading destinations…", "muted"));
    try {
      const res = await fetch("/api/catalog/options");
      if (!res.ok) throw new Error(String(res.status));
      renderDestinations(await res.json());
    } catch {
      grid.replaceChildren(make("p", "Destinations are unavailable right now.", "muted"));
    }
  }

  init();
})();

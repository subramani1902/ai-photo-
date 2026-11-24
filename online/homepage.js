import { loadPublishedPortfolio } from "./homepage-content.mjs";
import { createEnquiryDraft } from "./enquiry.mjs";
import { formatRupees, loadWeddingPackages, weddingPackages } from "./studio-packages.mjs";

const $ = selector => document.querySelector(selector);
const grid = $("#portfolio-grid"), preview = $("#portfolio-preview");
const cards = [];
const portfolio = await loadPublishedPortfolio();
const hero = portfolio[0], detail = portfolio[1];
for (const [selector, photo] of [["#hero-photo", hero], ["#detail-photo", detail]]) {
  const image = $(selector);
  image.src = photo.image; image.alt = photo.alt; image.style.objectPosition = photo.position;
}
$("#hero-sample-label").hidden = !hero.sample;
$("#sample-note").hidden = !portfolio.some(photo => photo.sample);
$("#sample-credits").hidden = !portfolio.some(photo => photo.sample);
$("#copyright-year").textContent = String(new Date().getFullYear());

for (const photo of portfolio) {
  const card = document.createElement("article"); card.className = "portfolio-card";
  const open = document.createElement("button"); open.type = "button"; open.className = "portfolio-open";
  open.setAttribute("aria-label", "View " + photo.title);
  const image = document.createElement("img");
  image.src = photo.image; image.alt = photo.alt; image.loading = "lazy"; image.decoding = "async";
  image.width = 1000; image.height = 1200; image.style.objectPosition = photo.position;
  const mark = document.createElement("span"); mark.className = "open-mark"; mark.textContent = "↗"; mark.setAttribute("aria-hidden", "true");
  open.append(image, mark);
  open.addEventListener("click", () => {
    $("#preview-title").textContent = photo.title;
    $("#portfolio-preview-image").src = photo.image;
    $("#portfolio-preview-image").alt = photo.alt;
    $("#portfolio-preview-caption").textContent = photo.sample ? "Sample portfolio photograph · not SM Photography client work." : photo.label;
    preview.showModal();
  });
  const caption = document.createElement("div"); caption.className = "portfolio-caption";
  const category = document.createElement("span"); category.textContent = photo.label;
  const title = document.createElement("h3"); title.textContent = photo.title;
  caption.append(category, title);
  if (photo.instagramUrl) {
    const url = new URL(photo.instagramUrl);
    if (url.protocol === "https:" && ["instagram.com", "www.instagram.com"].includes(url.hostname) && !url.username && !url.password) {
      const link = document.createElement("a"); link.className = "portfolio-instagram";
      link.href = url.href; link.target = "_blank"; link.rel = "noopener noreferrer"; link.textContent = "View on Instagram ↗";
      caption.append(link);
    }
  }
  card.append(open, caption); grid.append(card); cards.push({ card, photo });
}

for (const button of document.querySelectorAll("[data-portfolio-filter]")) {
  button.addEventListener("click", () => {
    const filter = button.dataset.portfolioFilter;
    grid.dataset.filter = filter;
    for (const item of document.querySelectorAll("[data-portfolio-filter]")) item.setAttribute("aria-pressed", String(item === button));
    let count = 0;
    for (const { card, photo } of cards) { card.hidden = filter !== "all" && photo.category !== filter; if (!card.hidden) count++; }
    $("#portfolio-status").textContent = `${count} photographs shown.`;
  });
}
$("#close-portfolio-preview").addEventListener("click", () => preview.close());
preview.addEventListener("click", event => { if (event.target === preview) preview.close(); });

const form = $("#enquiry-form"), review = $("#enquiry-review"), status = $("#enquiry-status");
const motion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
$("#packages").addEventListener("click", event => {
    const button = event.target.closest("[data-enquire]");
    if (!button) return;
    $("#enquiry-package").value = button.dataset.enquire;
    form.hidden = false; review.hidden = true; status.textContent = "";
    $("#enquire").scrollIntoView({ behavior: motion(), block: "start" });
    $("#enquiry-name").focus({ preventScroll: true });
});
form.addEventListener("submit", event => {
  event.preventDefault(); status.textContent = "";
  if (!form.reportValidity()) return;
  try {
    const fields = Object.fromEntries(new FormData(form));
    const source = new URLSearchParams(location.search).get("utm_source");
    const draft = createEnquiryDraft({ ...fields, source });
    $("#enquiry-draft").textContent = draft.message;
    $("#send-enquiry").href = draft.url;
    form.hidden = true; review.hidden = false;
    $("#send-enquiry").focus({ preventScroll: true });
    status.textContent = "Your message is ready. Open WhatsApp, review it, and press Send there.";
  } catch (error) { status.textContent = error.message; }
});
$("#edit-enquiry").addEventListener("click", () => {
  review.hidden = true; form.hidden = false; status.textContent = "";
  $("#enquiry-name").focus({ preventScroll: true });
});

async function showWeddingPackages() {
  const grid = $("#wedding-package-grid");
  try {
    const { packages, brochure } = await loadWeddingPackages();
    const cards = document.createDocumentFragment();
    for (const [key, name] of Object.entries(weddingPackages)) {
      const details = packages[key];
      const card = document.createElement("article");
      card.className = key === "platinum" ? "package package-featured wedding-card" : "package wedding-card";
      const title = document.createElement("h4"), price = document.createElement("p");
      title.textContent = name;
      price.className = "wedding-price";
      price.textContent = details.starting_paise == null ? "Quote on enquiry" : formatRupees(details.starting_paise);
      card.append(title, price);
      if (details.description) {
        const description = document.createElement("p");
        description.textContent = details.description;
        card.append(description);
      }
      const inclusions = document.createElement("ul");
      for (const line of details.inclusions.split(/\r?\n/).map(line => line.trim()).filter(Boolean)) {
        const item = document.createElement("li");
        item.textContent = line;
        inclusions.append(item);
      }
      if (inclusions.childElementCount) card.append(inclusions);
      if (details.deposit_paise != null || details.payment_terms) {
        const terms = document.createElement("div");
        terms.className = "wedding-terms";
        if (details.deposit_paise != null) {
          const deposit = document.createElement("p");
          deposit.textContent = "Booking deposit " + formatRupees(details.deposit_paise);
          terms.append(deposit);
        }
        if (details.payment_terms) {
          const copy = document.createElement("p");
          copy.textContent = details.payment_terms;
          terms.append(copy);
        }
        card.append(terms);
      }
      const contact = document.createElement("button");
      contact.className = "package-link";
      contact.type = "button";
      contact.dataset.enquire = name;
      contact.textContent = "Enquire about " + name;
      card.append(contact);
      cards.append(card);
    }
    grid.replaceChildren(cards);
    if (brochure) $("#wedding-package-status").textContent = "Brochure prices shown. Contact the studio to confirm current rates.";
  } catch {
    $("#wedding-package-status").textContent = "Package details are temporarily unavailable. Ask the studio about Silver, Gold and Platinum.";
  }
}

showWeddingPackages();

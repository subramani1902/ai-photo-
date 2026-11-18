import { loadPublishedPortfolio } from "./homepage-content.mjs";
import { createEnquiryDraft } from "./enquiry.mjs";

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
for (const button of document.querySelectorAll("[data-enquire]")) {
  button.addEventListener("click", () => {
    $("#enquiry-package").value = button.dataset.enquire;
    form.hidden = false; review.hidden = true; status.textContent = "";
    $("#enquire").scrollIntoView({ behavior: motion(), block: "start" });
    $("#enquiry-name").focus({ preventScroll: true });
  });
}
form.addEventListener("submit", event => {
  event.preventDefault(); status.textContent = "";
  if (!form.reportValidity()) return;
  try {
    const fields = Object.fromEntries(new FormData(form));
    const draft = createEnquiryDraft(fields);
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

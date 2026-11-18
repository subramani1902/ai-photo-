import { studio } from "./homepage-content.mjs";

export const packageNames = Object.freeze(["Wedding & reception", "Pre-wedding", "Celebration", "Help me choose"]);

function line(value, maximum, label, required = false) {
  const result = String(value ?? "").replace(/\s+/gu, " ").trim();
  if ((required && !result) || result.length > maximum) throw new Error(`Please check ${label}.`);
  return result;
}

export function createEnquiryDraft(fields) {
  const name = line(fields.name, 100, "your name", true);
  const phone = line(fields.phone, 30, "your phone number", true);
  if (!/^\+?[\d ()-]+$/.test(phone) || !/^\d{10,15}$/.test(phone.replace(/\D/g, ""))) {
    throw new Error("Please enter a valid phone number, including the country code if outside India.");
  }
  const selectedPackage = line(fields.package, 60, "your package", true);
  if (!packageNames.includes(selectedPackage)) throw new Error("Please choose a package.");
  const place = line(fields.location, 160, "your venue or city", true);
  const date = line(fields.date, 10, "your event date");
  if (date && (!/^\d{4}-\d{2}-\d{2}$/.test(date) || Number.isNaN(Date.parse(date)) || new Date(date).toISOString().slice(0, 10) !== date)) {
    throw new Error("Please check your event date.");
  }
  const notes = line(fields.notes, 1000, "your message");
  const message = [
    "Hello SM Photography! I'd like to enquire about a shoot.", "",
    `Name: ${name}`, `Phone: ${phone}`, `Coverage: ${selectedPackage}`,
    `Event date: ${date || "Still deciding"}`, `Venue / city: ${place}`,
    ...(notes ? [`A little about our plans: ${notes}`] : []), "",
    "Please share availability and a tailored package quote. Thank you!",
  ].join("\n");
  const url = new URL(`https://wa.me/${studio.phone.replace(/\D/g, "")}`);
  url.searchParams.set("text", message);
  return { message, url: url.href };
}

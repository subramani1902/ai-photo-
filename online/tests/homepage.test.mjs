import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";
import { createEnquiryDraft } from "../enquiry.mjs";

const redirect = await readFile(new URL("../legacy-photo-route.js", import.meta.url), "utf8");
const fields = { name: "Test couple", phone: "+91 90000 00000", package: "Wedding & reception",
  location: "Chennai", date: "2027-02-21", notes: "Photography & film, please." };

test("existing event QR links retain query and fragment tokens when forwarding to the photo finder", () => {
  for (const suffix of ["?token=" + "a".repeat(64), "#token=" + "b".repeat(64),
    "?source=qr&token=" + "c".repeat(64) + "#event=ceremony"]) {
    const original = new URL("https://studio.example/" + suffix);
    let destination;
    vm.runInNewContext(redirect, { URL, URLSearchParams,
      window: { location: { href: original.href, replace(url) { destination = new URL(url); } } } });
    assert.equal(destination.origin, original.origin);
    assert.equal(destination.pathname, "/find-photos.html");
    assert.equal(destination.search, original.search);
    assert.equal(destination.hash, original.hash);
  }
});

test("homepage sections and ordinary marketing links do not redirect into photo search", () => {
  for (const suffix of ["", "#our-work", "#enquire", "?utm_source=instagram#packages"]) {
    vm.runInNewContext(redirect, { URL, URLSearchParams,
      window: { location: { href: "https://studio.example/" + suffix, replace() { assert.fail("Not an event QR link"); } } } });
  }
});

test("package enquiry creates an unsent, correctly encoded draft to the existing studio contact", () => {
  const draft = createEnquiryDraft({ ...fields, name: "Test & தமிழ்", notes: "Film + photos? Budget to discuss." });
  const destination = new URL(draft.url);
  assert.equal(destination.origin, "https://wa.me");
  assert.equal(destination.pathname, "/919840108540");
  assert.equal(destination.searchParams.get("text"), draft.message);
  assert.ok(draft.message.includes("Test & தமிழ்"));
  assert.ok(draft.message.includes("Wedding & reception"));
  assert.ok(draft.message.includes("2027-02-21"));
  assert.ok(draft.message.includes("Chennai"));
  assert.ok(draft.message.includes("Film + photos? Budget to discuss."));
});

test("undecided dates work, while missing details, invalid dates and oversized text are rejected", () => {
  assert.ok(createEnquiryDraft({ ...fields, date: "", package: "Help me choose" }).message.includes("Still deciding"));
  for (const invalid of [{ name: " " }, { location: "" }, { phone: "letters9000000000" },
    { phone: "123" }, { package: "Unexpected package" }, { date: "2027-02-30" },
    { date: "not-a-date" }, { notes: "a".repeat(1001) }]) {
    assert.throws(() => createEnquiryDraft({ ...fields, ...invalid }));
  }
});

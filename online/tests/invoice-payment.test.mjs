import assert from "node:assert/strict";
import test from "node:test";
import { invoiceShareUrl, upiPaymentUrl, whatsappInvoiceUrl } from "../invoice-payment.mjs";

test("the WhatsApp draft contains the HTTPS invoice link with its private token in a fragment", () => {
  const token = "a".repeat(43);
  const share = invoiceShareUrl(token, "https://studio.example/admin/index.html");
  assert.equal(share, "https://studio.example/pay-invoice.html#" + token);
  const message = new URL(whatsappInvoiceUrl("INV-2026-0001", share));
  assert.equal(message.origin, "https://wa.me");
  assert.equal(message.searchParams.get("text").split("\n").at(-1), share);
  assert.throws(() => invoiceShareUrl("../private", "https://studio.example/admin/index.html"));
});

test("the UPI intent includes an exact INR amount, recipient and distinct attempt reference", () => {
  const unpaid = { upi_id: "studio-4@bank", invoice_number: "INV-2026-0001", balance_paise: 3_000_025 };
  const link = new URL(upiPaymentUrl(unpaid, "0123456789ab"));
  assert.equal(link.protocol, "upi:");
  assert.equal(link.hostname, "pay");
  assert.equal(link.searchParams.get("am"), "30000.25");
  assert.equal(link.searchParams.get("pa"), "studio-4@bank");
  assert.equal(link.searchParams.get("cu"), "INR");
  assert.equal(link.searchParams.get("tr"), "INV-2026-0001-0123456789ab");
  assert.throws(() => upiPaymentUrl({ ...unpaid, balance_paise: 0 }, "0123456789ab"));
  assert.throws(() => upiPaymentUrl({ ...unpaid, upi_id: "studio@bank&am=1" }, "0123456789ab"));
});

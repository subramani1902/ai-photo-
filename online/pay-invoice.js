import { formatRupees, trustedPhotoApiOrigin } from "./studio-packages.mjs";
import { paymentTokenPattern, upiPaymentUrl } from "./invoice-payment.mjs";

const $ = selector => document.querySelector(selector);
const token = window.location.hash.slice(1);
const status = $("#payment-status"), refresh = $("#refresh-invoice"), pay = $("#pay-balance");
let origin, timer, running = false, invoice;

function clearDetails() {
  invoice = undefined;
  $("#invoice-details").hidden = $("#upi-payment").hidden = true;
  $("#upi-id").value = "";
  pay.removeAttribute("href");
}

function setAttempt() {
  const attempt = crypto.randomUUID().replaceAll("-", "").slice(0, 12);
  pay.href = upiPaymentUrl(invoice, attempt);
}

async function load() {
  if (running || !origin) return;
  running = refresh.disabled = true;
  clearTimeout(timer);
  try {
    const response = await fetch(origin + "/api/studio/invoice-payment", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token }),
      cache: "no-store", referrerPolicy: "no-referrer", signal: AbortSignal.timeout(10_000),
    });
    if (response.status === 404) {
      clearDetails();
      status.textContent = "This payment link is unavailable. Ask the studio for a current link.";
      return;
    }
    if (!response.ok) throw new Error();
    invoice = await response.json();
    if (!Number.isSafeInteger(invoice.balance_paise) || invoice.balance_paise < 0) throw new Error();
    if (invoice.balance_paise) {
      setAttempt();
      $("#upi-id").value = invoice.upi_id;
    } else {
      pay.removeAttribute("href");
      $("#upi-id").value = "";
    }
    $("#invoice-number").textContent = invoice.invoice_number;
    $("#invoice-amount").textContent = formatRupees(invoice.balance_paise);
    $("#invoice-details").hidden = false;
    $("#upi-payment").hidden = !invoice.balance_paise;
    status.textContent = invoice.balance_paise ? "" : "This invoice has been paid.";
    if (invoice.balance_paise && !document.hidden) timer = setTimeout(load, 30_000);
  } catch {
    clearDetails();
    status.textContent = "We couldn’t load the balance. Refresh the page or contact the studio.";
  } finally {
    running = refresh.disabled = false;
  }
}

refresh.addEventListener("click", load);
pay.addEventListener("click", event => {
  if (!invoice?.balance_paise) { event.preventDefault(); return; }
  setAttempt();
});
$("#copy-upi").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText($("#upi-id").value);
    status.textContent = "UPI ID copied.";
  } catch {
    $("#upi-id").select();
    status.textContent = "Select and copy the UPI ID.";
  }
});
document.addEventListener("visibilitychange", () => {
  if (document.hidden) clearTimeout(timer);
  else load();
});

if (!paymentTokenPattern.test(token)) {
  status.textContent = "Open the complete payment link shared by SM Photography.";
} else {
  try {
    const { default: config } = await import("./firebase-config.js");
    origin = trustedPhotoApiOrigin(config.photoApiBaseUrl);
    if (!origin) throw new Error();
    refresh.hidden = false;
    await load();
  } catch {
    status.textContent = "Online invoice payment is temporarily unavailable. Contact SM Photography.";
  }
}

export const paymentTokenPattern = /^[A-Za-z0-9_-]{43}$/;
const upiIdPattern = /^[A-Za-z0-9._-]{1,96}@[A-Za-z0-9.-]{1,64}$/;
const invoiceNumberPattern = /^INV-\d{4}-\d{4,}$/;

export function invoiceShareUrl(token, adminUrl) {
  if (!paymentTokenPattern.test(token)) throw new Error("Invalid invoice payment link.");
  const url = new URL("../pay-invoice.html", adminUrl);
  url.hash = token;
  return url.href;
}

export function whatsappInvoiceUrl(invoiceNumber, shareUrl) {
  const url = new URL("https://wa.me/");
  url.searchParams.set("text", `SM Photography invoice ${invoiceNumber}\nOpen this link to see the balance and pay by UPI:\n${shareUrl}`);
  return url.href;
}

export function upiPaymentUrl(invoice, attemptId) {
  if (!upiIdPattern.test(invoice.upi_id) || !invoiceNumberPattern.test(invoice.invoice_number)
      || !Number.isSafeInteger(invoice.balance_paise) || invoice.balance_paise <= 0
      || !/^[a-f0-9]{12}$/.test(attemptId)) throw new Error("Invalid UPI invoice details.");
  const amount = String(Math.floor(invoice.balance_paise / 100)) + "." + String(invoice.balance_paise % 100).padStart(2, "0");
  const fields = {
    pa: invoice.upi_id, pn: "SM Photography", am: amount, cu: "INR",
    tr: invoice.invoice_number + "-" + attemptId, tn: "Invoice " + invoice.invoice_number,
  };
  return "upi://pay?" + Object.entries(fields).map(([name, value]) => name + "=" + encodeURIComponent(value)).join("&");
}

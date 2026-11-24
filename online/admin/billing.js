import { formatRupees } from "../studio-packages.mjs";
import { readFields, setFields } from "./forms.js";

const $ = selector => document.querySelector(selector);
const dateFormat = new Intl.DateTimeFormat("en-IN", { dateStyle: "medium", timeZone: "UTC" });
const dateLabel = value => value ? dateFormat.format(new Date(value)) : "—";
const studioToday = () => new Date(Date.now() + 330 * 60_000).toISOString().slice(0, 10);
const methods = { upi: "UPI", bank_transfer: "Bank transfer", card: "Card", cash: "Cash", other: "Other" };
const columns = {
  invoices: ["Invoice", "Client & shoot", "Paid / total", "Status", "Actions"],
  bookings: ["Client & shoot", "Paid / total", "Balance", "Status", "Actions"],
  transactions: ["Date", "Client & shoot", "Method", "Reference", "Amount"],
};

function element(tag, text = "", className = "") {
  const node = document.createElement(tag);
  if (text) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function stack(primary, secondary) {
  const node = element("div", "", "billing-stack");
  node.append(element("strong", primary));
  if (secondary) node.append(element("small", secondary));
  return node;
}

export function bookingBalance(booking) {
  const total = booking.invoice?.total_paise ?? booking.quote_paise;
  const paid = booking.payments.reduce((sum, payment) => sum + payment.amount_paise, 0);
  const balance = total == null ? null : Math.max(0, total - paid);
  if (total == null) return { total, paid, balance, status: "Quote pending", tone: "neutral" };
  if (paid > total) return { total, paid, balance, status: "Overpaid", tone: "credit" };
  if (paid === total) return { total, paid, balance, status: "Paid", tone: "paid" };
  if (paid > 0) return { total, paid, balance, status: "Partially paid", tone: "partial" };
  return { total, paid, balance, status: "Unpaid", tone: "unpaid" };
}

function shootName(booking) {
  return booking.invoice?.description || booking.event_name || booking.package || "Photography booking";
}

function bookingCell(booking) {
  return stack(shootName(booking), booking.invoice?.client_name || booking.name);
}

function statusCell(booking, totals) {
  const node = element("div", "", "billing-stack");
  const pill = element("span", totals.status, "billing-pill");
  pill.dataset.tone = totals.tone;
  node.append(pill);
  if (totals.balance > 0 && booking.invoice?.due_date && booking.invoice.due_date < studioToday()) {
    node.append(element("small", "Overdue", "billing-overdue"));
  }
  return node;
}

function cells(...values) {
  const row = element("tr");
  for (const value of values) {
    const cell = element("td");
    if (typeof value === "string") cell.textContent = value;
    else cell.append(value);
    row.append(cell);
  }
  return row;
}

function action(label, run, primary = false) {
  const button = element("button", label, primary ? "billing-record" : "selection-button");
  button.type = "button";
  button.addEventListener("click", run);
  return button;
}

function printInvoice(booking) {
  const print = $("#billing-print"), invoice = booking.invoice, totals = bookingBalance(booking);
  const term = (label, value) => {
    const pair = element("div");
    pair.append(element("dt", label), element("dd", value));
    return pair;
  };
  const header = element("header");
  header.append(element("p", "SM Photography · Chennai"), element("h1", "Invoice " + invoice.number));
  const details = element("dl");
  details.append(term("Bill to", invoice.client_name), term("Coverage", invoice.description),
    term("Issued", dateLabel(invoice.issued_on)), term("Due", dateLabel(invoice.due_date)));
  const amounts = element("dl");
  amounts.append(term("Invoice total", formatRupees(totals.total)), term("Payments received", formatRupees(totals.paid)),
    term("Outstanding balance", formatRupees(totals.balance)));
  if (totals.paid > totals.total) amounts.append(term("Credit", formatRupees(totals.paid - totals.total)));
  print.replaceChildren(header, details, amounts);
  if (booking.payments.length) {
    const payments = element("ul");
    for (const payment of [...booking.payments].sort((a, b) => a.date.localeCompare(b.date))) {
      payments.append(element("li", [dateLabel(payment.date), methods[payment.method], formatRupees(payment.amount_paise),
        payment.reference].filter(Boolean).join(" · ")));
    }
    print.append(element("h2", "Recorded payments"), payments);
  }
  print.hidden = false;
  document.body.classList.add("printing-studio-invoice");
  window.addEventListener("afterprint", clearPrint, { once: true });
  window.print();
}

function clearPrint() {
  const print = $("#billing-print");
  print.hidden = true;
  print.replaceChildren();
  document.body.classList.remove("printing-studio-invoice");
}

export function createBillingManager(request) {
  const table = $(".billing-table"), head = $("#billing-table-head"), body = $("#billing-table-body");
  const status = $("#billing-status"), create = $("#new-invoice"), refresh = $("#reload-billing");
  const dialog = $("#billing-dialog"), form = $("#billing-form"), submit = $("#billing-submit");
  const invoiceFields = $("#billing-invoice-fields"), paymentFields = $("#billing-payment-fields");
  const select = $("#billing-client"), message = $("#billing-form-message"), cancel = $("#billing-cancel");
  const views = [...document.querySelectorAll("[data-ledger-view]")];
  let bookings = [], view = "invoices", session = 0, loading = false, saving = false, ready = false, mode, bookingId, attempt;

  function render() {
    if (!ready) return;
    const invoices = bookings.filter(booking => booking.invoice);
    $("#billing-collected").textContent = formatRupees(bookings.reduce((sum, item) => sum + bookingBalance(item).paid, 0));
    $("#billing-outstanding").textContent = formatRupees(invoices.reduce((sum, item) => sum + bookingBalance(item).balance, 0));
    $("#billing-count").textContent = String(invoices.length);
    create.disabled = !bookings.some(booking => !booking.invoice);
    const headings = element("tr");
    for (const title of columns[view]) {
      const th = element("th", title);
      th.scope = "col";
      headings.append(th);
    }
    head.replaceChildren(headings);
    table.setAttribute("aria-label", views.find(button => button.dataset.ledgerView === view).textContent);

    const invoiceRows = () => [...invoices].sort((a, b) => b.invoice.number.localeCompare(a.invoice.number, "en", { numeric: true }));
    let rows;
    if (view === "transactions") {
      const transactions = bookings.flatMap(booking => booking.payments.map(payment => ({ booking, payment })));
      transactions.sort((a, b) => b.payment.date.localeCompare(a.payment.date));
      rows = transactions.map(({ booking, payment }) => cells(dateLabel(payment.date), bookingCell(booking),
        methods[payment.method], stack(payment.reference || "—", payment.notes), formatRupees(payment.amount_paise)));
    } else {
      const records = view === "invoices" ? invoiceRows() : bookings.filter(item => item.invoice || item.quote_paise != null || item.payments.length);
      rows = records.map(booking => {
        const totals = bookingBalance(booking);
        const amount = stack(formatRupees(totals.paid), totals.total == null ? "Quote pending" : "of " + formatRupees(totals.total));
        const controls = element("div", "", "billing-actions");
        if (booking.payments.length < 100) controls.append(action("Record payment", () => openDialog("payment", booking.id), true));
        if (view === "invoices") {
          controls.append(action("Print / PDF", () => printInvoice(booking)));
          const invoice = booking.invoice;
          const date = invoice.due_date ? "Due " + dateLabel(invoice.due_date) : "Issued " + dateLabel(invoice.issued_on);
          return cells(stack(invoice.number, date), bookingCell(booking), amount, statusCell(booking, totals), controls);
        }
        return cells(bookingCell(booking), amount, totals.balance == null ? "—" : formatRupees(totals.balance),
          statusCell(booking, totals), controls);
      });
    }
    body.replaceChildren(...rows);
    table.hidden = rows.length === 0;
    const empty = {
      invoices: bookings.length ? "No invoices yet. Select New invoice to issue one for a booking." : "Add a client booking before issuing an invoice.",
      bookings: "No booking balances to show. Add a quote or a payment to a booking.",
      transactions: "No payments have been recorded.",
    };
    status.textContent = rows.length ? "" : empty[view];
  }

  async function load() {
    if (loading) return;
    const version = session;
    loading = true; refresh.disabled = true; create.disabled = true; body.inert = true;
    status.textContent = "Loading billing records…";
    try {
      const data = await request("/admin/billing");
      if (version !== session) return;
      bookings = data.bookings; ready = true; render();
    } catch (error) {
      if (version === session) status.textContent = error.message;
    } finally {
      if (version === session) {
        loading = false; refresh.disabled = false; body.inert = false;
        create.disabled = !ready || !bookings.some(booking => !booking.invoice);
      }
    }
  }

  function fillInvoice() {
    const booking = bookings.find(item => item.id === select.value);
    const amount = invoiceFields.querySelector('[name="total_paise"]');
    invoiceFields.querySelector('[name="description"]').value = booking ? shootName(booking) : "";
    amount.value = booking?.quote_paise == null ? "" : String(booking.quote_paise / 100);
  }

  function openDialog(nextMode, id) {
    if (loading || saving) return;
    mode = nextMode; bookingId = id; attempt = undefined;
    form.reset(); message.textContent = ""; paymentFields.replaceChildren();
    const isInvoice = mode === "invoice";
    invoiceFields.hidden = !isInvoice; invoiceFields.disabled = !isInvoice;
    paymentFields.hidden = isInvoice; paymentFields.disabled = isInvoice;
    submit.textContent = isInvoice ? "Issue invoice" : "Save payment";
    if (isInvoice) {
      $("#billing-dialog-title").textContent = "New invoice";
      select.replaceChildren(...bookings.filter(item => !item.invoice).map(booking => {
        const option = element("option", booking.name + " · " + shootName(booking) +
          (booking.event_date ? " · " + dateLabel(booking.event_date) : ""));
        option.value = booking.id;
        return option;
      }));
      fillInvoice();
    } else {
      const booking = bookings.find(item => item.id === id);
      $("#billing-dialog-title").textContent = "Record payment";
      const row = $("#payment-template").content.firstElementChild.cloneNode(true);
      row.querySelector(".remove-payment").remove();
      setFields(row, { date: studioToday(), method: "upi" }, "[data-payment]");
      paymentFields.append(element("p", booking.name + " · " + shootName(booking), "studio-hint"), row);
    }
    dialog.showModal();
  }

  select.addEventListener("change", fillInvoice);
  create.addEventListener("click", () => openDialog("invoice"));
  refresh.addEventListener("click", load);
  cancel.addEventListener("click", () => dialog.close());
  dialog.addEventListener("cancel", event => { if (saving) event.preventDefault(); });
  dialog.addEventListener("close", () => {
    if (attempt) load();
    attempt = undefined;
  });
  for (const button of views) {
    button.addEventListener("click", () => {
      view = button.dataset.ledgerView;
      for (const item of views) item.setAttribute("aria-pressed", String(item === button));
      render();
    });
  }
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (saving) return;
    const version = session;
    try {
      const isInvoice = mode === "invoice";
      const booking = bookings.find(item => item.id === (isInvoice ? select.value : bookingId));
      if (!booking) throw new Error("Select a client booking.");
      let details;
      if (isInvoice) {
        const { booking: _, ...invoice } = readFields(invoiceFields);
        details = invoice;
      } else {
        details = { payment: readFields(paymentFields, "[data-payment]") };
      }
      const payload = { revision: booking.revision, ...details };
      const signature = JSON.stringify([booking.id, mode, payload]);
      if (attempt?.signature !== signature) attempt = { signature, id: crypto.randomUUID().replaceAll("-", "") };
      saving = submit.disabled = cancel.disabled = true;
      const fields = isInvoice ? invoiceFields : paymentFields;
      fields.disabled = true;
      message.textContent = "Saving…";
      const updated = await request("/admin/clients/" + booking.id + (isInvoice ? "/invoice" : "/payments"), {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...payload, operation_id: attempt.id }),
      });
      if (version !== session) return;
      bookings = bookings.map(item => item.id === updated.id ? updated : item);
      attempt = undefined;
      dialog.close();
      if (isInvoice) views.find(button => button.dataset.ledgerView === "invoices").click();
      else render();
      status.textContent = isInvoice ? "Invoice " + updated.invoice.number + " issued." : "Payment recorded.";
    } catch (error) {
      if (version === session) {
        message.textContent = error.message;
        if (error.status === 409) await load();
      }
    } finally {
      if (version === session) {
        saving = submit.disabled = cancel.disabled = false;
        invoiceFields.disabled = mode !== "invoice";
        paymentFields.disabled = mode !== "payment";
      }
    }
  });

  return {
    open: load,
    isDirty: () => dialog.open,
    reset() {
      session++; ready = loading = saving = false; bookings = []; attempt = undefined;
      if (dialog.open) dialog.close();
      form.reset(); select.replaceChildren(); paymentFields.replaceChildren();
      head.replaceChildren(); body.replaceChildren(); body.inert = false; table.hidden = true;
      refresh.disabled = submit.disabled = cancel.disabled = false; create.disabled = true;
      $("#billing-collected").textContent = $("#billing-outstanding").textContent = $("#billing-count").textContent = "—";
      status.textContent = message.textContent = "";
      views[0].click();
      clearPrint();
    },
  };
}

import { formatRupees } from "../studio-packages.mjs";
import { putJson, readFields, rupeesToPaise, setFields } from "./forms.js";

const $ = selector => document.querySelector(selector);
const label = value => value[0].toUpperCase() + value.slice(1);
const dateLabel = value => new Intl.DateTimeFormat("en-IN", { dateStyle: "medium", timeZone: "UTC" }).format(new Date(value));

export function createClientManager(request) {
  const form = $("#client-form"), list = $("#client-list"), search = $("#client-search");
  const listStatus = $("#client-list-status"), saveStatus = $("#client-save-status"), notice = $("#clients-message");
  const payments = $("#client-payments"), more = $("#more-clients");
  let generation = 0, next, term = "", current, dirty = false, saving = false, opening = false;

  function moneySummary(quote, paid) {
    if (quote == null) return ["Quote pending", "—"];
    if (paid > quote) return ["Overpaid", formatRupees(paid - quote)];
    return ["Balance", formatRupees(quote - paid)];
  }

  function updateSummary() {
    const safeRead = field => {
      try { return rupeesToPaise(field.value); }
      catch { return null; }
    };
    const quote = safeRead(form.elements.quote_paise);
    const invoiceTotal = current?.invoice?.total_paise;
    const paid = Array.from(payments.querySelectorAll('[data-payment="amount_paise"]'), safeRead).reduce((sum, amount) => sum + (amount ?? 0), 0);
    const total = invoiceTotal ?? quote;
    const values = [[invoiceTotal == null ? "Quote" : "Invoice total", total == null ? "—" : formatRupees(total)],
      ["Received", formatRupees(paid)], moneySummary(total, paid)];
    const summary = $("#payment-summary");
    summary.replaceChildren();
    for (const [key, value] of values) {
      const entry = document.createElement("div");
      const title = document.createElement("dt"), amount = document.createElement("dd");
      title.textContent = key; amount.textContent = value; entry.append(title, amount); summary.append(entry);
    }
  }

  function showPayment(payment = { method: "upi" }) {
    const row = $("#payment-template").content.firstElementChild.cloneNode(true);
    setFields(row, payment, "[data-payment]");
    row.querySelector("button").addEventListener("click", () => {
      row.remove();
      dirty = true;
      $("#add-payment").disabled = false;
      $("#add-payment").focus();
      updateSummary();
    });
    payments.append(row);
    $("#add-payment").disabled = payments.children.length >= 100;
    return row;
  }

  function selectDirectoryRecord() {
    for (const button of list.querySelectorAll("button")) {
      if (button.dataset.id === current?.id) button.setAttribute("aria-current", "true");
      else button.removeAttribute("aria-current");
    }
  }

  function addDirectoryRecord(client) {
    const item = document.createElement("li"), button = document.createElement("button");
    const name = document.createElement("strong"), stage = document.createElement("span"), details = document.createElement("small");
    const [balance, amount] = moneySummary(client.invoice_total_paise ?? client.quote_paise, client.paid_paise);
    button.type = "button";
    button.dataset.id = client.id;
    name.textContent = client.name;
    stage.textContent = label(client.status);
    stage.className = "client-stage";
    stage.dataset.stage = client.status;
    details.textContent = [client.package, label(client.source), client.event_date && dateLabel(client.event_date),
      client.follow_up_date && "Follow up " + dateLabel(client.follow_up_date), amount !== "—" && balance + " " + amount].filter(Boolean).join(" · ");
    button.append(name, stage, details);
    button.addEventListener("click", () => openClient(client.id));
    item.append(button);
    list.append(item);
  }

  async function loadList(append = false) {
    const version = ++generation;
    const query = { search: term, after: append ? next : null };
    listStatus.textContent = "Loading clients…";
    more.disabled = true;
    if (!append) {
      list.replaceChildren();
      more.hidden = true;
      next = null;
    }
    try {
      const data = await request("/admin/clients/search", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(query),
      });
      if (version !== generation) return;
      for (const client of data.clients) {
        if (!Array.from(list.children).some(item => item.firstElementChild.dataset.id === client.id)) addDirectoryRecord(client);
      }
      next = data.next;
      more.hidden = !next;
      selectDirectoryRecord();
      const count = list.children.length;
      listStatus.textContent = count ? `${count} client${count === 1 ? "" : "s"}${next ? " shown" : ""}.`
        : term ? "No matching clients." : "No client records yet.";
    } catch (error) {
      if (version === generation) listStatus.textContent = error.message;
    } finally {
      if (version === generation) more.disabled = false;
    }
  }

  function showRecord(record) {
    current = { id: record.id, revision: record.revision, invoice: record.invoice };
    setFields(form, { source: "other", status: "enquiry", ...record });
    payments.replaceChildren();
    $("#add-payment").disabled = false;
    for (const payment of record.payments ?? []) showPayment(payment);
    $("#client-form-title").textContent = record.revision ? record.name : "New client";
    saveStatus.textContent = "";
    dirty = false;
    form.hidden = false;
    updateSummary();
    selectDirectoryRecord();
    form.elements.name.focus();
  }

  const canLeave = () => !saving && !opening && (!dirty || window.confirm("Discard the unsaved changes to this client?"));
  let session = 0;

  async function openClient(id, refresh = false) {
    if ((!refresh && current?.id === id) || !canLeave()) return;
    const version = session;
    opening = true;
    form.inert = true;
    notice.textContent = "Opening client record…";
    try {
      const record = await request("/admin/clients/" + id);
      if (version === session) {
        form.inert = false;
        showRecord(record);
        notice.textContent = "";
      }
    } catch (error) {
      if (version === session) notice.textContent = error.message;
    } finally {
      if (version === session) { opening = false; form.inert = false; }
    }
  }

  $("#add-client").addEventListener("click", () => {
    if (canLeave()) {
      notice.textContent = "";
      showRecord({ id: crypto.randomUUID().replaceAll("-", ""), revision: 0 });
    }
  });
  $("#close-client").addEventListener("click", () => {
    if (!canLeave()) return;
    clearEditor();
    $("#add-client").focus();
  });
  $("#add-payment").addEventListener("click", () => {
    dirty = true;
    showPayment().querySelector("input").focus();
  });
  form.addEventListener("input", () => { dirty = true; updateSummary(); });
  form.addEventListener("change", () => { dirty = true; });
  search.addEventListener("submit", event => {
    event.preventDefault();
    term = $("#client-query").value.trim();
    loadList();
  });
  more.addEventListener("click", () => { if (next) loadList(true); });

  const saveButton = form.querySelector('[type="submit"]');
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (!current || saving || opening) return;
    const version = session;
    saving = true; saveButton.disabled = true;
    try {
      const data = { ...readFields(form), revision: current.revision,
        payments: Array.from(payments.children, row => readFields(row, "[data-payment]")) };
      for (const fieldset of form.querySelectorAll("fieldset")) fieldset.disabled = true;
      saveStatus.textContent = "Saving client…";
      const client = await request("/admin/clients/" + current.id, putJson(data));
      if (version !== session) return;
      current.revision = client.revision;
      $("#client-form-title").textContent = client.name;
      dirty = false;
      saveStatus.textContent = "Client and payments saved.";
      loadList();
    } catch (error) {
      if (version === session) saveStatus.textContent = error.message;
    } finally {
      if (version === session) finishSave();
    }
  });

  function finishSave() {
    saving = false;
    saveButton.disabled = false;
    for (const fieldset of form.querySelectorAll("fieldset")) fieldset.disabled = false;
  }

  function clearEditor() {
    current = undefined; dirty = false; form.hidden = true; form.reset();
    setFields(form, { status: "enquiry", source: "other" });
    payments.replaceChildren(); $("#payment-summary").replaceChildren();
    $("#client-form-title").textContent = "New client";
    saveStatus.textContent = "";
    selectDirectoryRecord();
  }

  return {
    open() {
      loadList();
      if (current?.revision && !dirty) openClient(current.id, true);
    },
    isDirty: () => dirty,
    reset() {
      session++; generation++; next = null; term = ""; opening = false;
      form.inert = false;
      finishSave(); clearEditor(); search.reset(); list.replaceChildren();
      more.hidden = true; notice.textContent = ""; listStatus.textContent = "";
    },
  };
}

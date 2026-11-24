export function rupeesToPaise(value) {
  if (value === "") return null;
  if (!/^\d+(?:\.\d{1,2})?$/.test(value)) throw new Error("Enter an amount in rupees with up to two decimal places.");
  const [whole, fraction = ""] = value.split(".");
  const amount = Number(whole) * 100 + Number(fraction.padEnd(2, "0"));
  if (!Number.isSafeInteger(amount) || amount > 10_000_000_000) throw new Error("The amount entered is too large.");
  return amount;
}

export function setFields(root, values, selector = "[name]") {
  for (const field of root.querySelectorAll(selector)) {
    const key = field.dataset.payment ?? field.name;
    const value = values[key];
    field.value = value == null ? "" : key.endsWith("_paise") ? String(value / 100) : value;
  }
}

export function readFields(root, selector = "[name]") {
  return Object.fromEntries(Array.from(root.querySelectorAll(selector), field => {
    const key = field.dataset.payment ?? field.name;
    return [key, key.endsWith("_paise") ? rupeesToPaise(field.value) : field.type === "date" ? field.value || null : field.value.trim()];
  }));
}

export function putJson(data) {
  return { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) };
}

// Editor handoff contains photo identifiers, never passwords, tokens or drive paths.
function cell(value) {
  let text = String(value ?? "");
  if (/^[\s\u0000-\u001f]*[=+\-@]/.test(text)) text = "'" + text;
  return '"' + text.replaceAll('"', '""') + '"';
}

export function buildSelectionCsv(photos, { eventId = "", eventName = "" } = {}) {
  if (!Array.isArray(photos) || photos.length > 10000) throw new Error("Invalid photo selection.");
  const seen = new Set();
  const rows = [["selection_number", "event_id", "event_name", "photo_id", "source_folder", "filename", "selected_for_editing"]];
  for (const photo of photos) {
    if (!photo || !Number.isSafeInteger(photo.id) || photo.id < 1 || typeof photo.filename !== "string"
        || !photo.filename || photo.filename.length > 255 || /[\u0000-\u001f]/.test(photo.filename)) {
      throw new Error("A selected photo has invalid details. Reload the gallery and try again.");
    }
    if (seen.has(photo.id)) continue;
    seen.add(photo.id);
    rows.push([seen.size, eventId, eventName, photo.id, photo.folder_name || "", photo.filename, "yes"]);
  }
  return "\ufeff" + rows.map(row => row.map(cell).join(",")).join("\r\n") + "\r\n";
}

export function createSelectionCsv(photos, context) {
  return new Blob([buildSelectionCsv(photos, context)], { type: "text/csv;charset=utf-8" });
}

export function restoreSelection(text) {
  try {
    const rows = JSON.parse(text || "[]");
    if (!Array.isArray(rows) || rows.length > 10000) return new Map();
    const selection = new Map();
    for (const row of rows) {
      if (Number.isSafeInteger(row?.id) && row.id > 0 && typeof row.filename === "string"
          && row.filename.length > 0 && row.filename.length <= 255 && !/[\u0000-\u001f]/.test(row.filename)) {
        const folder = typeof row.folder_name === "string" && row.folder_name.length <= 128 && !/[\/\\:\u0000-\u001f]/.test(row.folder_name) ? row.folder_name : "";
        selection.set(row.id, { id: row.id, filename: row.filename, ...(folder ? { folder_name: folder } : {}) });
      }
    }
    return selection;
  } catch { return new Map(); }
}

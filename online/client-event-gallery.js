// All image requests carry a verified Firebase ID token; no tokens go in URLs.
export function createClientGallery({ client, onClose, configuration } = {}) {
  const $ = selector => document.querySelector(selector);
  const section = $("#client-gallery"), grid = $("#client-photos"), message = $("#client-gallery-message");
  const more = $("#client-load-more"), zip = $("#download-event-selection");
  let base, event, after = 0, category = "all", generation = 0, busy = false;
  const selected = new Map(), urls = new Set();
  let abort = new AbortController();

  function resetPhotos() {
    generation++;
    abort.abort();
    abort = new AbortController();
    for (const url of urls) URL.revokeObjectURL(url);
    urls.clear(); selected.clear(); grid.replaceChildren();
    after = 0;
    zip.disabled = true;
  }

  function close() {
    resetPhotos();
    event = undefined;
    section.hidden = true;
    onClose?.();
  }

  async function request(path, options = {}, retry = true) {
    const token = await client.getIdToken();
    const response = await fetch(base + path, { ...options, signal: abort.signal,
      headers: { ...options.headers, Authorization: "Bearer " + token }, cache: "no-store", credentials: "omit" });
    if (response.status === 401 && retry) {
      await client.getIdToken(true);
      return request(path, options, false);
    }
    if (!response.ok) {
      let text = response.status === 403 ? "This event has not been shared with your account, or access has expired." : "The gallery is temporarily unavailable. Please try again.";
      if (response.status === 413) text = "Choose fewer photos. ZIP downloads are limited to 512 MB.";
      if (response.status === 429) text = "Please wait a moment before trying again.";
      if (response.status === 403 && event) { close(); }
      throw new Error(text);
    }
    return response;
  }

  function endpoint(suffix = "") { return "/api/firebase/events/" + event.event_id + suffix; }

  function save(blob, filename) {
    const url = URL.createObjectURL(blob), link = document.createElement("a");
    link.href = url; link.download = filename; link.rel = "noopener";
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
  }

  function updateSelection() {
    zip.disabled = !selected.size || busy;
    zip.textContent = selected.size ? "Download selected (" + selected.size + ") .zip" : "Download selected (.zip)";
  }

  async function render(photo, version) {
    const card = document.createElement("article");
    card.className = "result client-photo";
    const image = document.createElement("img");
    image.alt = photo.filename; image.loading = "lazy";
    const name = document.createElement("p");
    name.className = "client-photo-name"; name.textContent = photo.filename;
    const controls = document.createElement("div"); controls.className = "client-photo-controls";
    if (event.permissions.includes("favourite")) {
      const favourite = document.createElement("button"); favourite.type = "button";
      const setLabel = value => { favourite.textContent = value ? "♥ Favourite" : "♡ Favourite"; favourite.setAttribute("aria-pressed", String(value)); };
      setLabel(photo.favourite);
      favourite.addEventListener("click", async () => {
        favourite.disabled = true;
        try {
          await request(endpoint("/photos/" + photo.id + "/favourite"), { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ selected: !photo.favourite }) });
          photo.favourite = !photo.favourite; setLabel(photo.favourite);
          if (category === "favourites" && !photo.favourite) card.remove();
        } catch (error) { message.textContent = error.message; }
        finally { favourite.disabled = false; }
      });
      controls.append(favourite);
    }
    if (event.permissions.includes("download")) {
      const download = document.createElement("button"); download.type = "button"; download.textContent = "Download ↓";
      download.addEventListener("click", async () => {
        download.disabled = true;
        try { save(await (await request(endpoint("/photos/" + photo.id + "/download"))).blob(), photo.filename); }
        catch (error) { message.textContent = error.message; }
        finally { download.disabled = false; }
      });
      const label = document.createElement("label"), input = document.createElement("input");
      input.type = "checkbox"; input.setAttribute("aria-label", "Select " + photo.filename);
      input.addEventListener("change", () => {
        if (input.checked && selected.size >= event.zip_max_photos) {
          input.checked = false; message.textContent = "Select up to " + event.zip_max_photos + " photos per ZIP download."; return;
        }
        if (input.checked) selected.set(photo.id, photo); else selected.delete(photo.id);
        updateSelection();
      });
      label.append(input, document.createTextNode("Select")); controls.append(download, label);
    }
    card.append(image, controls, name); grid.append(card);
    try {
      const blob = await (await request(endpoint("/photos/" + photo.id + "/preview"))).blob();
      if (version !== generation) return;
      const url = URL.createObjectURL(blob); urls.add(url); image.src = url;
    } catch (error) { if (version === generation) { image.alt = "Preview unavailable: " + photo.filename; message.textContent = error.message; } }
  }

  async function loadPhotos() {
    if (busy || !event) return;
    busy = true; more.disabled = true; updateSelection();
    const version = generation;
    try {
      const page = await (await request(endpoint("/photos?after=" + after + "&limit=24&category=" + category))).json();
      if (version !== generation) return;
      after = page.next_after;
      more.hidden = after === null;
      message.textContent = !page.photos.length && !grid.childElementCount ? "No photos in this category yet." : "";
      // Keep concurrent HDD preview requests bounded.
      for (let i = 0; i < page.photos.length; i += 4) {
        if (version !== generation) return;
        await Promise.all(page.photos.slice(i, i + 4).map(photo => render(photo, version)));
      }
    } finally { busy = false; more.disabled = false; updateSelection(); }
  }

  async function open(eventId) {
    if (!Number.isSafeInteger(eventId) || eventId < 1 || eventId > 2147483647) throw new Error("Enter a valid Event ID.");
    const config = configuration ?? (await import("./firebase-config.js")).default;
    if (!config.photoApiBaseUrl) throw new Error("Your studio gallery service is not available yet. Please contact your photographer.");
    const parsed = new URL(config.photoApiBaseUrl);
    if (parsed.protocol !== "https:" || parsed.username || parsed.password || parsed.pathname !== "/" || parsed.search || parsed.hash) throw new Error("The gallery is temporarily unavailable.");
    base = parsed.origin;
    resetPhotos();
    const details = await (await request("/api/firebase/events/" + eventId)).json();
    event = details; category = "all";
    $("#client-event-name").textContent = details.event_name;
    $("#client-event-summary").textContent = "Event " + details.event_id + " · Available until " + new Date(details.expires_at).toLocaleDateString();
    document.querySelectorAll("[data-client-category]").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.clientCategory === "all")));
    section.hidden = false;
    try { await loadPhotos(); }
    catch (error) { close(); throw error; }
  }

  $("#close-event").addEventListener("click", close);
  more.addEventListener("click", () => loadPhotos().catch(error => { message.textContent = error.message; }));
  document.querySelectorAll("[data-client-category]").forEach(button => button.addEventListener("click", async () => {
    if (busy || !event) return;
    category = button.dataset.clientCategory; resetPhotos();
    document.querySelectorAll("[data-client-category]").forEach(item => { const active = item === button; item.classList.toggle("active", active); item.setAttribute("aria-pressed", String(active)); });
    try { await loadPhotos(); } catch (error) { message.textContent = error.message; }
  }));
  zip.addEventListener("click", async () => {
    if (busy || !event || !selected.size) return;
    busy = true; updateSelection(); message.textContent = "Preparing your ZIP download…";
    try {
      const result = await request(endpoint("/download-zip"), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ photo_ids: [...selected.keys()] }) });
      save(await result.blob(), "event-" + event.event_id + "-photos.zip"); message.textContent = "Your ZIP download is ready.";
    } catch (error) { message.textContent = error.message; }
    finally { busy = false; updateSelection(); }
  });
  window.addEventListener("pagehide", close);
  return { open, close };
}

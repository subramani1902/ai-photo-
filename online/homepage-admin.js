import { validatePublishedPortfolio } from "./homepage-content.mjs";

export function createHomepageManager(request) {
  const $ = selector => document.querySelector(selector);
  const names = { celebration: "Large cover photo", bride: "Small cover photo", wedding: "Candid portfolio photo", together: "Pre-wedding portfolio photo" };
  let snapshot, selected = "celebration", busy = false, version = 0, pollTimer, localPreview, dirty = false;
  const cachedPreviews = new Map();
  const notice = $("#homepage-admin-message"), form = $("#homepage-photo-form");

  function clearPreviews() {
    if (localPreview) URL.revokeObjectURL(localPreview);
    localPreview = undefined;
    for (const url of cachedPreviews.values()) URL.revokeObjectURL(url);
    cachedPreviews.clear();
  }
  function updateControls() {
    const publishing = snapshot?.job?.status === "publishing";
    for (const selector of ["#save-homepage-photo", "#homepage-photo-file", "#homepage-photo-title", "#homepage-photo-alt", "#homepage-photo-instagram", "#homepage-photo-position"]) $(selector).disabled = busy || publishing || !snapshot;
    $("#publish-homepage").disabled = busy || publishing || !snapshot?.has_changes;
    $("#refresh-homepage").disabled = busy;
    for (const button of document.querySelectorAll(".homepage-slot")) button.disabled = busy || publishing;
  }
  async function previewFor(photo) {
    if (cachedPreviews.has(photo.image)) return cachedPreviews.get(photo.image);
    const generation = version;
    const blob = await (await request("/previews/" + encodeURIComponent(photo.id))).blob();
    if (generation !== version) throw new Error("Preview cancelled.");
    const url = URL.createObjectURL(blob);
    cachedPreviews.set(photo.image, url);
    return url;
  }
  async function selectPhoto(id) {
    const generation = version;
    dirty = false;
    selected = id;
    if (localPreview) URL.revokeObjectURL(localPreview);
    localPreview = undefined;
    const photo = snapshot.photos.find(item => item.id === id);
    $("#slot-description").textContent = id === "celebration" ? "This photo appears in the large homepage cover and the first portfolio position." : id === "bride" ? "This photo appears in the small cover frame and the second portfolio position." : names[id] + " on the homepage.";
    $("#homepage-photo-title").value = photo.title;
    $("#homepage-photo-alt").value = photo.alt;
    $("#homepage-photo-instagram").value = photo.instagramUrl || "";
    $("#homepage-photo-file").value = "";
    const position = $("#homepage-photo-position");
    for (const option of [...position.querySelectorAll("[data-custom]")]) option.remove();
    if (![...position.options].some(option => option.value === photo.position)) {
      const option = document.createElement("option"); option.value = photo.position; option.textContent = "Current framing"; option.dataset.custom = "true"; position.append(option);
    }
    position.value = photo.position;
    const image = $("#homepage-image-preview"); image.hidden = true;
    $("#editor-placeholder").hidden = false; $("#editor-placeholder").textContent = "Loading saved preview…";
    for (const button of document.querySelectorAll(".homepage-slot")) button.setAttribute("aria-pressed", String(button.dataset.slot === id));
    try {
      const url = await previewFor(photo);
      if (generation !== version || selected !== id || localPreview) return;
      image.src = url; image.alt = photo.alt; image.style.objectPosition = photo.position; image.hidden = false; $("#editor-placeholder").hidden = true;
    } catch { if (generation === version && selected === id) $("#editor-placeholder").textContent = "Preview unavailable. Press Check status to try again."; }
  }
  function applyState(result, select = true) {
    const photos = validatePublishedPortfolio({ schema_version: 1, photos: result.photos });
    snapshot = { ...result, photos };
    $("#publish-summary").textContent = snapshot.job?.status === "publishing" ? "Publishing your homepage…" : snapshot.has_changes ? "You have saved changes ready to publish." : "Your homepage is up to date.";
    $("#publish-progress").textContent = snapshot.job?.stage || "Save one or more photos, then publish your changes together.";
    const grid = $("#homepage-slots"); grid.replaceChildren();
    for (const photo of photos) {
      const generation = version;
      const button = document.createElement("button"); button.type = "button"; button.className = "homepage-slot"; button.dataset.slot = photo.id;
      button.setAttribute("aria-pressed", String(photo.id === selected));
      const image = document.createElement("img"); image.alt = photo.alt; image.decoding = "async"; image.style.objectPosition = photo.position;
      const copy = document.createElement("span"); copy.className = "homepage-slot-copy";
      const title = document.createElement("strong"); title.textContent = names[photo.id];
      const status = document.createElement("small"); status.textContent = photo.sample ? "Sample photo" : "Your photo";
      copy.append(title, status); button.append(image, copy); grid.append(button);
      button.addEventListener("click", () => {
        if (!busy && selected !== photo.id && (!dirty || window.confirm("Discard the unsaved photo changes?"))) selectPhoto(photo.id);
      });
      previewFor(photo).then(url => { if (version === generation) image.src = url; }).catch(() => { status.textContent += " · preview unavailable"; });
    }
    if (select) selectPhoto(selected);
    updateControls();
  }
  async function refresh(select = true) {
    const generation = version;
    const result = await (await request("/admin")).json();
    if (generation !== version) return;
    applyState(result, select);
    return result;
  }
  function pollPublishing(generation = version, failures = 0) {
    clearTimeout(pollTimer);
    if (snapshot?.job?.status !== "publishing") return;
    pollTimer = setTimeout(async () => {
      if (generation !== version) return;
      try { await refresh(false); if (snapshot?.job?.status === "published") notice.textContent = "Your new photos are live. Open View homepage to see them."; pollPublishing(generation); }
      catch (error) {
        if (generation !== version) return;
        if (failures < 2 && ![401, 403].includes(error.status)) pollPublishing(generation, failures + 1);
        else notice.textContent = "The studio connection was interrupted. Publishing may still be running. Press Check status before trying again.";
      }
    }, 2500 * (failures + 1));
  }
  form.addEventListener("input", () => { dirty = true; });
  $("#homepage-photo-file").addEventListener("change", () => {
    const file = $("#homepage-photo-file").files[0]; notice.textContent = "";
    if (!file) {
      if (localPreview) URL.revokeObjectURL(localPreview);
      localPreview = undefined;
      const photo = snapshot?.photos.find(item => item.id === selected);
      const image = $("#homepage-image-preview");
      if (photo && cachedPreviews.has(photo.image)) image.src = cachedPreviews.get(photo.image);
      else {
        image.hidden = true;
        $("#editor-placeholder").hidden = false;
      }
      return;
    }
    if (!["image/jpeg", "image/png", "image/webp"].includes(file.type) || file.size > 20 * 1024 * 1024 || !file.size) {
      $("#homepage-photo-file").value = ""; notice.textContent = "Choose a JPG, PNG or WebP photo up to 20 MB."; return;
    }
    if (localPreview) URL.revokeObjectURL(localPreview);
    localPreview = URL.createObjectURL(file); $("#homepage-image-preview").src = localPreview; $("#homepage-image-preview").hidden = false; $("#editor-placeholder").hidden = true;
  });
  $("#homepage-photo-position").addEventListener("change", () => { $("#homepage-image-preview").style.objectPosition = $("#homepage-photo-position").value; });
  form.addEventListener("submit", async event => {
    event.preventDefault(); if (busy || !snapshot || !event.target.reportValidity()) return;
    const generation = version;
    busy = true; updateControls(); notice.textContent = "Saving your homepage photo…";
    const data = new FormData(); data.set("revision", snapshot.revision);
    data.set("title", $("#homepage-photo-title").value); data.set("alt", $("#homepage-photo-alt").value);
    data.set("instagram_url", $("#homepage-photo-instagram").value.trim()); data.set("position", $("#homepage-photo-position").value);
    const file = $("#homepage-photo-file").files[0]; if (file) data.set("photo", file);
    try {
      const result = await (await request("/photos/" + selected, { method: "POST", body: data })).json();
      if (generation !== version) return;
      applyState(result); notice.textContent = "Photo draft saved. Select Publish photos when you’re ready.";
    } catch (error) { if (generation === version) notice.textContent = error.message; }
    finally { if (generation === version) { busy = false; updateControls(); } }
  });
  $("#publish-homepage").addEventListener("click", async () => {
    if (busy || !snapshot?.has_changes || (dirty && !window.confirm("Publish the saved drafts? Save your current edit first to include it."))) return;
    const generation = version;
    busy = true; updateControls(); notice.textContent = "Preparing to publish your saved photos…";
    try {
      const result = await (await request("/publish", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ revision: snapshot.revision }) })).json();
      if (generation !== version) return;
      applyState(result, false); notice.textContent = "Publishing is in progress."; pollPublishing();
    } catch (error) { if (generation === version) notice.textContent = error.message; }
    finally { if (generation === version) { busy = false; updateControls(); } }
  });
  async function load() {
    const generation = version;
    if (busy) return; busy = true; updateControls(); notice.textContent = "";
    try { await refresh(); if (generation === version) pollPublishing(); }
    catch (error) { if (generation === version) notice.textContent = error.message; }
    finally { if (generation === version) { busy = false; updateControls(); } }
  }
  $("#refresh-homepage").addEventListener("click", () => {
    if (!dirty || window.confirm("Discard the unsaved photo changes and check the latest status?")) load();
  });
  updateControls();
  return {
    open() { if (!snapshot) load(); },
    isDirty: () => dirty,
    reset() {
      version++; clearTimeout(pollTimer); clearPreviews();
      snapshot = undefined; selected = "celebration"; busy = false; dirty = false;
      form.reset(); notice.textContent = "";
      $("#homepage-slots").replaceChildren();
      $("#homepage-image-preview").removeAttribute("src");
      $("#homepage-image-preview").hidden = true;
      $("#editor-placeholder").hidden = false;
      $("#editor-placeholder").textContent = "Choose a photo position";
      $("#publish-summary").textContent = "Loading saved photos…";
      $("#publish-progress").textContent = "";
      $("#slot-description").textContent = "";
      updateControls();
    },
  };
}

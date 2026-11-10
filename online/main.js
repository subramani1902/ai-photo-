import { createPhotoArchive } from "./photo-archive.mjs";

const RELAY = "https://dtfxnuxluuwvjdcyxmnc.supabase.co/functions/v1/relay";
const MAX_SELFIE_BYTES = 10 * 1024 * 1024;
const currentUrl = new URL(location.href);
const eventToken = new URLSearchParams(currentUrl.hash.slice(1)).get("token") || currentUrl.searchParams.get("token");
if (eventToken) history.replaceState({}, "", location.pathname);
const $ = selector => document.querySelector(selector);
const consent = $("#consent"), selfie = $("#selfie"), search = $("#search"), message = $("#message");
const formSection = $("#form-section"), results = $("#results"), photos = $("#photos"), preview = $("#preview");
let objectUrls = [], downloadablePhotos = [], selectedPhoto = null, busy = false;
const selectedPhotoIndexes = new Set();
const loadingAnimation = $("#loading-animation");
let scanPlayer = null, loadingVersion = 0;
function showMessage(text) {
  message.textContent = text;
  $("#loading-status").textContent = text;
}

function setLoadingAnimation(show) {
  const version = ++loadingVersion;
  if (!show) {
    scanPlayer?.destroy(); scanPlayer = null;
    loadingAnimation.close();
    document.body.classList.remove("scanning");
    return;
  }
  document.body.classList.add("scanning");
  $("#scan-canvas").hidden = true;
  $("#scan-fallback").hidden = false;
  $("#scan-playback").hidden = true;
  loadingAnimation.showModal();
  import("https://cdn.jsdelivr.net/npm/@lottiefiles/dotlottie-web@0.80.0/+esm").then(({ DotLottie }) => {
    if (version !== loadingVersion) return;
    scanPlayer = new DotLottie({
      canvas: $("#scan-canvas"),
      src: "https://lottie.host/773e1c8a-a3d3-48a1-a32d-9cc57551b80e/dia0go5sGe.lottie",
      autoplay: true, loop: true,
    });
    scanPlayer.addEventListener("load", () => {
      if (version !== loadingVersion) return;
      $("#scan-fallback").hidden = true;
      $("#scan-canvas").hidden = false;
      scanPlayer.resize();
      scanPlayer.play();
      $("#scan-playback").hidden = false;
      $("#scan-playback").textContent = "Pause animation";
    });
  }).catch(() => { /* The local SVG remains visible if the hosted player fails. */ });
}
loadingAnimation.addEventListener("cancel", event => event.preventDefault());
$("#scan-playback").addEventListener("click", () => {
  if (!scanPlayer) return;
  if (scanPlayer.isPlaying) { scanPlayer.pause(); $("#scan-playback").textContent = "Play animation"; }
  else { scanPlayer.play(); $("#scan-playback").textContent = "Pause animation"; }
});

const menuTrigger = $("#menu-trigger"), contactMenu = $("#contact-menu");
function setMenu(open) {
  contactMenu.hidden = !open;
  menuTrigger.setAttribute("aria-expanded", String(open));
  menuTrigger.setAttribute("aria-label", open ? "Close contact menu" : "Open contact menu");
}
menuTrigger.addEventListener("click", () => setMenu(contactMenu.hidden));
document.addEventListener("click", event => {
  if (!event.target.closest(".menu-wrap")) setMenu(false);
});
document.addEventListener("keydown", event => {
  if (event.key === "Escape" && !contactMenu.hidden) {
    setMenu(false);
    menuTrigger.focus();
  }
});

function updateButton() {
  const file = selfie.files[0];
  search.disabled = busy || !consent.checked || !file || file.size > MAX_SELFIE_BYTES;
}
consent.addEventListener("change", updateButton);
selfie.addEventListener("change", () => {
  const file = selfie.files[0];
  $("#file-label").textContent = file?.name || "Choose a photo";
  showMessage(file && file.size > MAX_SELFIE_BYTES
    ? "This photo is larger than 10 MB. Please choose a smaller photo."
    : "");
  updateButton();
});

document.querySelectorAll(".view-button").forEach(button => button.addEventListener("click", () => {
  photos.dataset.view = button.dataset.view;
  document.querySelectorAll(".view-button").forEach(item => {
    const active = item === button;
    item.classList.toggle("active", active);
    item.setAttribute("aria-pressed", String(active));
  });
}));

async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(RELAY + path, options);
  } catch {
    throw new Error("The photo search service could not be reached. Check your internet connection and try again.");
  }
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.detail || "Unable to complete your request. Please try again.");
  }
  return response;
}
function downloadPhoto(photo) {
  const link = document.createElement("a");
  link.href = photo.url; link.download = photo.filename;
  document.body.append(link); link.click(); link.remove();
}
$("#download-all").addEventListener("click", async event => {
  const button = event.currentTarget;
  const items = downloadablePhotos.filter(Boolean);
  if (!items.length || button.disabled) return;
  button.disabled = true;
  button.textContent = "Preparing ZIP…";
  try {
    const archive = await createPhotoArchive(items);
    const url = URL.createObjectURL(archive);
    const link = document.createElement("a");
    link.href = url; link.download = "event-photos.zip";
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
    button.textContent = `Download all again (${items.length}) ↓`;
  } catch {
    button.textContent = "Download all (.zip) ↓";
    message.textContent = "Could not prepare the photo archive. Please try again.";
  } finally {
    button.disabled = items.length === 0;
  }
});
function updateSelectionControls() {
  const readyIndexes = downloadablePhotos.flatMap((photo, index) => photo ? [index] : []);
  const selectedCount = readyIndexes.filter(index => selectedPhotoIndexes.has(index)).length;
  const selectAll = $("#select-all");
  selectAll.disabled = readyIndexes.length === 0;
  selectAll.textContent = readyIndexes.length > 0 && selectedCount === readyIndexes.length
    ? "Deselect all"
    : "Select all";
  selectAll.setAttribute("aria-pressed", String(readyIndexes.length > 0 && selectedCount === readyIndexes.length));
  $("#download-selected").disabled = selectedCount === 0;
  $("#download-selected").textContent = selectedCount
    ? `Download selected (${selectedCount})`
    : "Download selected (.zip)";
  photos.querySelectorAll(".photo-select").forEach((checkbox, index) => {
    checkbox.checked = selectedPhotoIndexes.has(Number(checkbox.dataset.photoIndex));
  });
}
$("#select-all").addEventListener("click", () => {
  const readyIndexes = downloadablePhotos.flatMap((photo, index) => photo ? [index] : []);
  const allSelected = readyIndexes.length > 0 && readyIndexes.every(index => selectedPhotoIndexes.has(index));
  if (allSelected) readyIndexes.forEach(index => selectedPhotoIndexes.delete(index));
  else readyIndexes.forEach(index => selectedPhotoIndexes.add(index));
  updateSelectionControls();
});
$("#download-selected").addEventListener("click", async event => {
  const button = event.currentTarget;
  const items = [...selectedPhotoIndexes]
    .map(index => downloadablePhotos[index])
    .filter(Boolean);
  if (!items.length || button.disabled) return;
  button.disabled = true;
  button.textContent = "Preparing ZIP…";
  try {
    const archive = await createPhotoArchive(items);
    const url = URL.createObjectURL(archive);
    const link = document.createElement("a");
    link.href = url; link.download = "selected-event-photos.zip";
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
    button.textContent = `Downloaded ${items.length} selected`;
  } catch {
    message.textContent = "Could not prepare the selected photo archive. Please try again.";
  } finally {
    updateSelectionControls();
  }
});
function showPhoto(photo) {
  selectedPhoto = photo;
  $("#preview-image").src = photo.url;
  $("#preview-name").textContent = photo.filename;
  preview.showModal();
}
$("#close-preview").addEventListener("click", () => preview.close());
$("#preview-download").addEventListener("click", () => { if (selectedPhoto) downloadPhoto(selectedPhoto); });

async function renderPhoto(photo, job, headers, index) {
  const card = document.createElement("article"); card.className = "result";
  const open = document.createElement("button"); open.className = "photo-open"; open.disabled = true;
  open.setAttribute("aria-label", "View " + photo.filename);
  const image = document.createElement("img"); image.className = "photo"; image.alt = ""; image.loading = "lazy";
  open.append(image);
  const selectLabel = document.createElement("label");
  selectLabel.className = "photo-select-label";
  selectLabel.setAttribute("aria-label", `Select ${photo.filename}`);
  const select = document.createElement("input");
  select.className = "photo-select";
  select.type = "checkbox";
  select.setAttribute("aria-label", `Select ${photo.filename}`);
  select.disabled = true;
  select.dataset.photoIndex = index;
  select.addEventListener("change", () => {
    if (select.checked) selectedPhotoIndexes.add(index);
    else selectedPhotoIndexes.delete(index);
    updateSelectionControls();
  });
  selectLabel.append(select);
  const meta = document.createElement("div"); meta.className = "photo-meta";
  const name = document.createElement("span"); name.className = "photo-name"; name.textContent = photo.filename;
  const download = document.createElement("button"); download.className = "download"; download.textContent = "↓";
  download.setAttribute("aria-label", "Download " + photo.filename); download.disabled = true;
  meta.append(name, download); card.append(open, selectLabel, meta); photos.append(card);
  try {
    const blob = await (await request(`/guest/jobs/${job.job_id}/photos/${photo.id}`, { headers })).blob();
    const url = URL.createObjectURL(blob); objectUrls.push(url);
    const readyPhoto = { url, filename: photo.filename, blob };
    downloadablePhotos[index] = readyPhoto; image.src = url;
    open.disabled = false; download.disabled = false;
    select.disabled = false;
    open.addEventListener("click", () => showPhoto(readyPhoto));
    download.addEventListener("click", () => downloadPhoto(readyPhoto));
    updateSelectionControls();
  } catch { name.textContent = "Couldn't load photo"; open.setAttribute("aria-label", "Photo could not be loaded"); }
}

search.addEventListener("click", async () => {
  if (!consent.checked || !selfie.files.length || busy) return;
  busy = true; updateButton();
  setLoadingAnimation(true);
  search.firstChild.textContent = "Finding your photos ";
  showMessage("Sending your selfie…");
  const body = new FormData(); body.append("file", selfie.files[0]);
  try {
    const eventHeaders = eventToken ? { "X-Event-Token": eventToken } : {};
    const job = await (await request("/guest/submit", { method: "POST", headers: eventHeaders, body })).json();
    const headers = { "X-Job-Token": job.job_token };
    showMessage("Finding your photos…");
    let status;
    while (true) {
      await new Promise(resolve => setTimeout(resolve, 3000));
      status = await (await request(`/guest/jobs/${job.job_id}`, { headers })).json();
      if (status.status === "completed") break;
      if (status.status === "failed") throw new Error(status.message || "Could not process the selfie.");
      showMessage(status.status === "processing" ? "Matching your face with event photos…" : "Waiting for the event laptop…");
    }
    formSection.hidden = true; results.hidden = false; photos.replaceChildren();
    downloadablePhotos = [];
    selectedPhotoIndexes.clear();
    $("#download-all").disabled = true;
    $("#download-all").textContent = "Download all (.zip) ↓";
    updateSelectionControls();
    const count = status.photos.length;
    $("#summary").textContent = count ? `We found ${count} photo${count === 1 ? "" : "s"} of you.` : "No photos found this time.";
    $("#gallery-count").textContent = count;
    $("#empty").hidden = count !== 0;
    message.textContent = "";
    await Promise.all(status.photos.map((photo, index) => renderPhoto(photo, job, headers, index)));
    $("#download-all").disabled = downloadablePhotos.filter(Boolean).length === 0;
    updateSelectionControls();
    window.scrollTo({ top: 0, behavior: "auto" });
  } catch (error) { message.textContent = error.message; }
  finally { busy = false; setLoadingAnimation(false); search.firstChild.textContent = "Find my photos "; updateButton(); }
});

$("#again").addEventListener("click", () => {
  objectUrls.forEach(url => URL.revokeObjectURL(url)); objectUrls = [];
  downloadablePhotos = []; selectedPhotoIndexes.clear(); selectedPhoto = null; photos.replaceChildren();
  $("#download-all").disabled = true; $("#download-all").textContent = "Download all (.zip) ↓";
  updateSelectionControls();
  results.hidden = true; formSection.hidden = false; selfie.value = "";
  $("#file-label").textContent = "Choose a photo"; message.textContent = "";
  updateButton(); window.scrollTo({ top: 0, behavior: "auto" });
});

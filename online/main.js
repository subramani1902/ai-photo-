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
let selectedSelfie = null, cameraStream = null, cameraPreviewUrl = null;
const selectedPhotoIndexes = new Set();
let downloadedCount = 0;
let galleryPhotoCount = 0, settledPhotoCount = 0;
const loadingAnimation = $("#loading-animation");
let scanPlayer = null, loadingVersion = 0;
let selectedEventId = "";
let eventsLoaded = Boolean(eventToken);
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
  const file = selectedSelfie;
  const eventReady = eventToken || (eventsLoaded && selectedEventId);
  search.disabled = busy || !eventReady || !consent.checked || !file || file.size > MAX_SELFIE_BYTES;
}
consent.addEventListener("change", updateButton);
selfie.addEventListener("change", () => {
  const file = selfie.files[0];
  stopCamera();
  clearCameraPreview();
  selectedSelfie = file || null;
  $("#file-label").textContent = file?.name || "Choose a photo";
  showMessage(file && file.size > MAX_SELFIE_BYTES
    ? "This photo is larger than 10 MB. Please choose a smaller photo."
    : "");
  updateButton();
});
function stopCamera() {
  cameraStream?.getTracks().forEach(track => track.stop());
  cameraStream = null;
  $("#camera-video").srcObject = null;
}
function clearCameraPreview() {
  if (cameraPreviewUrl) URL.revokeObjectURL(cameraPreviewUrl);
  cameraPreviewUrl = null;
  $("#camera-preview").removeAttribute("src");
  $("#camera-preview").hidden = true;
  $("#retake-selfie").hidden = true;
  $("#capture-selfie").hidden = false;
}
$("#open-camera").addEventListener("click", async () => {
  if (!navigator.mediaDevices?.getUserMedia) {
    showMessage("Live camera is unavailable in this browser. Choose a photo instead.");
    return;
  }
  try {
    stopCamera();
    clearCameraPreview();
    cameraStream = await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: { facingMode: "user", width: { ideal: 1280 }, height: { ideal: 1280 } },
    });
    $("#camera-video").srcObject = cameraStream;
    $("#camera-video").hidden = false;
    $("#camera-panel").hidden = false;
    $("#open-camera").hidden = true;
    showMessage("");
  } catch (error) {
    const reason = error.name === "NotAllowedError"
      ? "Camera access was denied. Allow camera access in your browser, or choose a photo instead."
      : error.name === "NotFoundError"
        ? "No camera was found on this device. Choose a photo instead."
        : "Could not start the camera. Check camera permissions and try again.";
    stopCamera();
    showMessage(reason);
  }
});
$("#capture-selfie").addEventListener("click", async () => {
  const video = $("#camera-video");
  if (!cameraStream || !video.videoWidth || !video.videoHeight) {
    showMessage("The camera is not ready yet. Please wait a moment and try again.");
    return;
  }
  const scale = Math.min(1, 1920 / Math.max(video.videoWidth, video.videoHeight));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(video.videoWidth * scale);
  canvas.height = Math.round(video.videoHeight * scale);
  canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
  const blob = await new Promise(resolve => canvas.toBlob(resolve, "image/jpeg", 0.9));
  if (!blob) {
    showMessage("Could not capture the selfie. Please try again.");
    return;
  }
  selectedSelfie = new File([blob], `live-selfie-${Date.now()}.jpg`, { type: "image/jpeg" });
  stopCamera();
  clearCameraPreview();
  cameraPreviewUrl = URL.createObjectURL(blob);
  $("#camera-preview").src = cameraPreviewUrl;
  $("#camera-preview").hidden = false;
  $("#camera-video").hidden = true;
  $("#capture-selfie").hidden = true;
  $("#retake-selfie").hidden = false;
  $("#file-label").textContent = "Live selfie ready";
  showMessage(blob.size > MAX_SELFIE_BYTES
    ? "This selfie is larger than 10 MB. Retake it or choose a smaller photo."
    : "");
  updateButton();
});
$("#retake-selfie").addEventListener("click", async () => {
  selectedSelfie = null;
  $("#file-label").textContent = "Choose a photo";
  $("#camera-panel").hidden = true;
  $("#open-camera").hidden = false;
  updateButton();
  $("#open-camera").click();
});
$("#close-camera").addEventListener("click", () => {
  stopCamera();
  clearCameraPreview();
  $("#camera-panel").hidden = true;
  $("#camera-video").hidden = false;
  $("#open-camera").hidden = false;
  showMessage("");
});
window.addEventListener("pagehide", stopCamera);

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

function selectEvent(eventId) {
  selectedEventId = eventId;
  $("#event-select").value = eventId;
  document.querySelectorAll(".event-card-button").forEach(button => {
    const active = button.dataset.eventId === eventId;
    button.classList.toggle("selected", active);
    button.setAttribute("aria-pressed", String(active));
  });
  updateButton();
}

$("#event-select").addEventListener("change", event => selectEvent(event.target.value));

async function loadEventShowcase() {
  const headers = eventToken ? { "X-Event-Token": eventToken } : {};
  const response = await request("/guest/showcase", { headers });
  const { events } = await response.json();
  if (!Array.isArray(events)) throw new Error("The event list could not be loaded.");

  const select = $("#event-select");
  select.replaceChildren();
  if (!eventToken) {
    const prompt = document.createElement("option");
    prompt.value = "";
    prompt.textContent = events.length ? "Select an event" : "No active events";
    select.append(prompt);
    for (const item of events) {
      const option = document.createElement("option");
      option.value = item.id;
      option.textContent = item.name;
      select.append(option);
    }
    $("#event-select-wrap").hidden = false;
  }

  const eventCards = $("#showcase-events");
  eventCards.replaceChildren();
  for (const item of events) {
    const card = document.createElement("article");
    card.className = "event-card";
    const button = document.createElement("button");
    button.type = "button";
    button.className = "event-card-button";
    button.dataset.eventId = item.id;
    button.setAttribute("aria-pressed", "false");
    button.setAttribute("aria-label", `Choose ${item.name}`);
    const collage = document.createElement("span");
    collage.className = "event-card-photos";
    for (const photo of (Array.isArray(item.photos) ? item.photos : []).slice(0, 3)) {
      const image = document.createElement("img");
      image.src = photo.url;
      image.alt = "";
      image.loading = "lazy";
      image.decoding = "async";
      collage.append(image);
    }
    if (!collage.childElementCount) {
      collage.classList.add("no-event-photos");
      collage.textContent = "Photos coming soon";
    }
    const details = document.createElement("span");
    details.className = "event-card-details";
    const name = document.createElement("strong");
    name.textContent = item.name;
    const action = document.createElement("span");
    action.textContent = "Find my photos ↗";
    details.append(name, action);
    button.append(collage, details);
    button.addEventListener("click", () => {
      selectEvent(item.id);
      $("#upload-card").scrollIntoView({ behavior: "smooth", block: "center" });
    });
    card.append(button);
    eventCards.append(card);
  }

  $("#event-count").textContent = `${events.length} ${events.length === 1 ? "event" : "events"}`;
  $("#event-showcase").hidden = false;
  if (!eventToken && events.length) selectEvent(events[0].id);
  eventsLoaded = true;
  updateButton();
}

loadEventShowcase().catch(error => {
  console.error("Event collections could not be loaded:", error);
  eventsLoaded = false;
  const select = $("#event-select");
  select.replaceChildren(new Option("Events unavailable", ""));
  $("#event-select-wrap").hidden = !eventToken;
  $("#event-showcase").hidden = true;
  showMessage("Event collections are temporarily unavailable. Please try again later.");
  updateButton();
});

function downloadPhoto(photo) {
  const link = document.createElement("a");
  link.href = photo.url; link.download = photo.filename;
  document.body.append(link); link.click(); link.remove();
  updateDownloadedStatus(1);
}
function updateDownloadedStatus(count) {
  downloadedCount += count;
  $("#download-status").textContent = `Downloaded ${downloadedCount} photo${downloadedCount === 1 ? "" : "s"}`;
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
    updateDownloadedStatus(items.length);
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
  $("#download-all").disabled = readyIndexes.length === 0 || settledPhotoCount < galleryPhotoCount;
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
    updateDownloadedStatus(items.length);
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
  } catch {
    name.textContent = "Couldn't load photo";
    open.setAttribute("aria-label", "Photo could not be loaded");
  } finally {
    settledPhotoCount += 1;
    updateSelectionControls();
  }
}

search.addEventListener("click", async () => {
  if (!consent.checked || !selectedSelfie || busy) return;
  busy = true; updateButton();
  setLoadingAnimation(true);
  search.firstChild.textContent = "Finding your photos ";
  showMessage("Sending your selfie…");
  const body = new FormData(); body.append("file", selectedSelfie);
  try {
    const eventHeaders = eventToken
      ? { "X-Event-Token": eventToken }
      : { "X-Event-Id": selectedEventId };
    const job = await (await request("/guest/submit", { method: "POST", headers: eventHeaders, body })).json();
    const headers = { "X-Job-Token": job.job_token };
    showMessage("Finding your photos…");
    let status;
    while (true) {
      await new Promise(resolve => setTimeout(resolve, 1000));
      status = await (await request(`/guest/jobs/${job.job_id}`, { headers })).json();
      if (status.status === "completed") break;
      if (status.status === "failed") throw new Error(status.message || "Could not process the selfie.");
      showMessage(status.status === "processing" ? "Matching your face with event photos…" : "Waiting for the event laptop…");
    }
    setLoadingAnimation(false);
    formSection.hidden = true; results.hidden = false; photos.replaceChildren();
    $("#gallery-action-bar").hidden = false;
    document.body.classList.add("gallery-visible");
    downloadablePhotos = [];
    selectedPhotoIndexes.clear();
    downloadedCount = 0;
    galleryPhotoCount = status.photos.length;
    settledPhotoCount = 0;
    $("#download-status").textContent = "Downloaded 0 photos";
    $("#download-all").disabled = true;
    $("#download-all").textContent = "Download all (.zip) ↓";
    updateSelectionControls();
    const count = status.photos.length;
    $("#summary").textContent = count ? `We found ${count} photo${count === 1 ? "" : "s"} of you.` : "No photos found this time.";
    $("#gallery-count").textContent = count;
    $("#empty").hidden = count !== 0;
    message.textContent = "";
    status.photos.forEach((photo, index) => { void renderPhoto(photo, job, headers, index); });
    updateSelectionControls();
    window.scrollTo({ top: 0, behavior: "auto" });
  } catch (error) { message.textContent = error.message; }
  finally { busy = false; setLoadingAnimation(false); search.firstChild.textContent = "Find my photos "; updateButton(); }
});

$("#again").addEventListener("click", () => {
  objectUrls.forEach(url => URL.revokeObjectURL(url)); objectUrls = [];
  downloadablePhotos = []; selectedPhotoIndexes.clear(); selectedPhoto = null; photos.replaceChildren();
  galleryPhotoCount = 0; settledPhotoCount = 0;
  $("#download-all").disabled = true; $("#download-all").textContent = "Download all (.zip) ↓";
  updateSelectionControls();
  $("#gallery-action-bar").hidden = true;
  document.body.classList.remove("gallery-visible");
  downloadedCount = 0;
  $("#download-status").textContent = "Downloaded 0 photos";
  results.hidden = true; formSection.hidden = false; selfie.value = "";
  stopCamera(); clearCameraPreview(); selectedSelfie = null;
  $("#camera-panel").hidden = true; $("#camera-video").hidden = false; $("#open-camera").hidden = false;
  $("#file-label").textContent = "Choose a photo"; message.textContent = "";
  updateButton(); window.scrollTo({ top: 0, behavior: "auto" });
});

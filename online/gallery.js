import { createSelectionCsv, restoreSelection } from "./selection-csv.mjs";

const RELAY = "https://dtfxnuxluuwvjdcyxmnc.supabase.co/functions/v1/relay";
const $ = (selector) => document.querySelector(selector);
const loginPanel = $("#login-panel");
const galleryPanel = $("#gallery-panel");
const photoGrid = $("#photo-grid");
const loginMessage = $("#login-message");
const galleryMessage = $("#gallery-message");
const loginButton = $("#login-button");
const loadMoreButton = $("#load-more");
const exportButton = $("#export-favourites"), favouriteFilter = $("#favourite-filter");
const assetQueue = [];
const queuedPhotos = new Map();
const cards = new Map(), favourites = new Map(), previewUrls = new Set();
const folderGrids = new Map();
let activeAssets = 0;
let shareToken = location.hash.slice(1) || sessionStorage.getItem("photo-finder-gallery-token") || "";
let sessionToken = sessionStorage.getItem("photo-finder-gallery-session") || "";
let nextCursor = null;
let loadingPage = false;
let photoObserver;
let galleryContext = {}, selectionKey = "", showFavourites = false, exporting = false;
let generation = 0, requestsController = new AbortController();

class GalleryRequestError extends Error {
  constructor(message, status, retryAfter) {
    super(message);
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

if (sessionStorage.getItem("photo-finder-gallery-session-token") !== shareToken) {
  sessionToken = "";
  sessionStorage.removeItem("photo-finder-gallery-session");
}
if (/^[a-f0-9]{64}$/i.test(shareToken)) {
  sessionStorage.setItem("photo-finder-gallery-token", shareToken);
  history.replaceState({}, "", location.pathname);
} else {
  shareToken = "";
  sessionStorage.removeItem("photo-finder-gallery-token");
}
$("#gallery-link-field").hidden = Boolean(shareToken);
$("#gallery-link").required = !shareToken;

function resetAssets() {
  generation++;
  requestsController.abort(); requestsController = new AbortController();
  assetQueue.length = 0; queuedPhotos.clear(); cards.clear(); folderGrids.clear();
  photoObserver?.disconnect();
  for (const url of previewUrls) URL.revokeObjectURL(url);
  previewUrls.clear();
}

function persistFavourites() {
  if (!selectionKey) return;
  try { sessionStorage.setItem(selectionKey, JSON.stringify([...favourites.values()])); }
  catch { galleryMessage.textContent = "Download your CSV before leaving this page; your browser could not save the favourites."; }
}

function updateFavourites() {
  $("#favourite-count").textContent = String(favourites.size);
  favouriteFilter.setAttribute("aria-pressed", String(showFavourites));
  exportButton.disabled = !favourites.size || !sessionToken || exporting;
  exportButton.textContent = exporting ? "Preparing CSV…" : `Download editor CSV (${favourites.size})`;
  for (const [id, { card, button }] of cards) {
    const chosen = favourites.has(id);
    button.textContent = chosen ? "♥ Chosen for editing" : "♡ Choose favourite";
    button.setAttribute("aria-pressed", String(chosen));
    card.classList.toggle("is-favourite", chosen);
    card.hidden = showFavourites && !chosen;
  }
  loadMoreButton.hidden = showFavourites || !nextCursor;
}

function applyContext(result) {
  galleryContext = result;
  if (typeof result.event_name === "string") $("#gallery-title").textContent = result.event_name;
  if (typeof result.gallery_id === "string" && result.gallery_id !== selectionKey.replace("gallery-favourites:", "")) {
    selectionKey = "gallery-favourites:" + result.gallery_id;
    let saved;
    try { saved = sessionStorage.getItem(selectionKey); } catch { /* In-memory selection still works. */ }
    favourites.clear();
    for (const [id, photo] of restoreSelection(saved)) favourites.set(id, photo);
  }
}

function authHeaders() {
  return { "X-Gallery-Session": sessionToken };
}

function clearSession() {
  resetAssets(); favourites.clear(); selectionKey = ""; galleryContext = {};
  sessionToken = "";
  sessionStorage.removeItem("photo-finder-gallery-session");
  sessionStorage.removeItem("photo-finder-gallery-session-token");
  photoGrid.replaceChildren(); folderGrids.clear(); updateFavourites();
}

async function readResponse(response) {
  if (response.status === 401 && sessionToken) {
    clearSession();
    galleryPanel.hidden = true;
    loginPanel.hidden = false;
    loginMessage.textContent = "Your gallery session expired. Enter the password again.";
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new GalleryRequestError(
      body.detail || "The gallery service is temporarily unavailable.",
      response.status,
      Number(response.headers.get("Retry-After")) || 0,
    );
  }
  return response;
}

async function startGallery() {
  resetAssets();
  loadingPage = false;
  loginPanel.hidden = true;
  galleryPanel.hidden = false;
  galleryMessage.textContent = "";
  photoGrid.replaceChildren();
  folderGrids.clear();
  nextCursor = null;
  showFavourites = false;
  if (photoObserver) photoObserver.disconnect();
  if ("IntersectionObserver" in window) {
    photoObserver = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        photoObserver.unobserve(entry.target);
        queueThumbnail(entry.target);
      }
    }, { rootMargin: "320px 0px" });
  }
  await loadPage();
}

async function loadPage() {
  if (loadingPage) return;
  loadingPage = true;
  loadMoreButton.disabled = true;
  galleryMessage.textContent = "Loading the photo list…";
  const version = generation;
  try {
    const params = new URLSearchParams({ limit: "60" });
    if (nextCursor) params.set("after", nextCursor);
    const response = await readResponse(await fetch(`${RELAY}/guest/gallery/photos?${params}`, {
      headers: authHeaders(), signal: requestsController.signal, cache: "no-store",
    }));
    const result = await response.json();
    if (version !== generation) return;
    applyContext(result);
    for (const photo of result.photos || []) addPhoto(photo);
    nextCursor = result.next_cursor;
    updateFavourites();
    galleryMessage.textContent = photoGrid.childElementCount
      ? `${result.photo_count ?? cards.size} photos available · ${cards.size} listed. Choose favourites for your editor.`
      : "No photos are available in this gallery yet.";
  } catch (error) {
    if (version === generation) galleryMessage.textContent = error.message;
  } finally {
    if (version === generation) { loadingPage = false; loadMoreButton.disabled = false; }
  }
}

function addPhoto(photo) {
  if (cards.has(photo.id)) return;
  const folderName = typeof photo.folder_name === "string" && photo.folder_name.trim()
    ? photo.folder_name
    : galleryContext.event_name || "Photos";
  let grid = folderGrids.get(folderName);
  if (!grid) {
    const section = document.createElement("section");
    section.className = "folder-section";
    const heading = document.createElement("h2");
    heading.textContent = folderName;
    grid = document.createElement("div");
    grid.className = "photo-grid";
    section.append(heading, grid);
    photoGrid.append(section);
    folderGrids.set(folderName, grid);
  }
  const card = document.createElement("article");
  card.className = "photo-card";
  const frame = document.createElement("div");
  frame.className = "photo-frame";
  const placeholder = document.createElement("span");
  placeholder.textContent = "Preview loads when visible";
  const image = document.createElement("img");
  image.alt = photo.filename;
  image.hidden = true;
  frame.append(placeholder, image);
  const details = document.createElement("div");
  details.className = "photo-details";
  const filename = document.createElement("span");
  filename.className = "filename";
  filename.textContent = photo.filename;
  filename.title = photo.filename;
  const favourite = document.createElement("button");
  favourite.className = "favourite"; favourite.type = "button";
  favourite.setAttribute("aria-label", "Choose " + photo.filename + " for editing");
  favourite.addEventListener("click", () => {
    if (favourites.has(photo.id)) favourites.delete(photo.id);
    else favourites.set(photo.id, { id: photo.id, filename: photo.filename });
    persistFavourites(); updateFavourites();
    if (showFavourites && !favourites.size) galleryMessage.textContent = "No favourites chosen yet. Choose photos in the full gallery first.";
  });
  const download = document.createElement("button");
  download.className = "download";
  download.type = "button";
  download.textContent = "Download original";
  download.addEventListener("click", () => downloadOriginal(photo, download));
  details.append(filename, favourite, download);
  card.append(frame, details);
  grid.append(card);
  cards.set(photo.id, { card, button: favourite });
  updateFavourites();
  queuedPhotos.set(frame, { photo, placeholder });
  if (photoObserver) photoObserver.observe(frame);
  else queueThumbnail(frame);
}

function queueThumbnail(frame) {
  const item = queuedPhotos.get(frame);
  if (!item) return;
  const { photo, placeholder } = item;
  queuedPhotos.delete(frame);
  const version = generation;
  assetQueue.push(async () => {
    try {
      placeholder.textContent = "Loading preview…";
      const blob = await fetchPhoto(photo, "thumbnail");
      if (version !== generation) return;
      const image = frame.querySelector("img");
      const url = URL.createObjectURL(blob); previewUrls.add(url); image.src = url;
      image.hidden = false;
      placeholder.remove();
    } catch (error) {
      if (version === generation) placeholder.textContent = error.message || "Preview unavailable. Refresh to try again.";
    }
  });
  pumpQueue();
}

function pumpQueue() {
  while (activeAssets < 3 && assetQueue.length) {
    const operation = assetQueue.shift();
    activeAssets++;
    operation().finally(() => {
      activeAssets--;
      pumpQueue();
    });
  }
}

function waitForRetry(milliseconds, signal) {
  return new Promise((resolve, reject) => {
    const finish = () => {
      signal.removeEventListener("abort", abort);
      resolve();
    };
    const timer = setTimeout(finish, milliseconds);
    const abort = () => {
      clearTimeout(timer);
      signal.removeEventListener("abort", abort);
      reject(signal.reason);
    };
    signal.addEventListener("abort", abort, { once: true });
    if (signal.aborted) abort();
  });
}

async function createPhotoJob(photo, variant, signal) {
  for (let retry = 0; ; retry++) {
    try {
      return await readResponse(await fetch(`${RELAY}/guest/gallery/jobs`, {
        method: "POST",
        headers: { ...authHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify({ photo_id: photo.id, variant }),
        signal,
      }));
    } catch (error) {
      if (error.status !== 429 || retry >= 8) throw error;
      const delay = error.retryAfter
        ? error.retryAfter * 1000
        : Math.min(2000 * (2 ** retry), 15_000);
      await waitForRetry(delay, signal);
    }
  }
}

async function fetchPhoto(photo, variant) {
  const signal = requestsController.signal;
  for (let recovery = 0; recovery < 3; recovery++) {
    const created = await createPhotoJob(photo, variant, signal);
    const job = await created.json();
    for (let attempt = 0; attempt < 400; attempt++) {
      signal.throwIfAborted();
      if (attempt) await waitForRetry(1500, signal);
      const statusResponse = await readResponse(await fetch(
        `${RELAY}/guest/gallery/jobs/${encodeURIComponent(job.job_id)}`,
        { headers: authHeaders(), signal },
      ));
      const status = (await statusResponse.json()).status;
      if (status === "failed" || status === "expired") {
        throw new Error("This photo is temporarily unavailable. Please try again later.");
      }
      if (status !== "ready") continue;
      try {
        const fileResponse = await readResponse(await fetch(
          `${RELAY}/guest/gallery/jobs/${encodeURIComponent(job.job_id)}/file`,
          { headers: authHeaders(), signal },
        ));
        return fileResponse.blob();
      } catch (error) {
        if (error.status !== 409 || recovery === 2) throw error;
        break;
      }
    }
  }
  throw new Error("This photo is taking too long to load. Please try again.");
}

async function downloadOriginal(photo, button) {
  button.disabled = true;
  button.textContent = "Preparing download…";
  try {
    const blob = await fetchPhoto(photo, "original");
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = photo.filename;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
    button.textContent = "Download again";
  } catch (error) {
    galleryMessage.textContent = error.message;
    button.textContent = "Retry download";
  } finally {
    button.disabled = false;
  }
}

$("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  loginMessage.textContent = "";
  if (!shareToken) {
    try {
      const link = new URL($("#gallery-link").value);
      if (![location.origin, "https://sm-photo-studio.vercel.app", "https://ai-photo-k5fu.vercel.app"].includes(link.origin)
          || link.pathname !== "/gallery.html" || link.username || link.password || !/^[a-f0-9]{64}$/i.test(link.hash.slice(1))) throw new Error();
      shareToken = link.hash.slice(1);
      sessionStorage.setItem("photo-finder-gallery-token", shareToken);
    } catch {
      loginMessage.textContent = "Paste the complete private gallery link shared by your photographer.";
      return;
    }
  }
  loginButton.disabled = true;
  try {
    const response = await readResponse(await fetch(`${RELAY}/guest/gallery/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: shareToken, password: $("#gallery-password").value }),
    }));
    const result = await response.json();
    sessionToken = result.session_token;
    sessionStorage.setItem("photo-finder-gallery-session", sessionToken);
    sessionStorage.setItem("photo-finder-gallery-session-token", shareToken);
    $("#gallery-password").value = "";
    await startGallery();
  } catch (error) {
    loginMessage.textContent = error.message;
  } finally {
    loginButton.disabled = false;
  }
});

loadMoreButton.addEventListener("click", loadPage);
favouriteFilter.addEventListener("click", () => {
  showFavourites = !showFavourites;
  if (showFavourites) {
    for (const photo of favourites.values()) if (!cards.has(photo.id)) addPhoto(photo);
    galleryMessage.textContent = favourites.size ? `${favourites.size} photos chosen for editing.` : "No favourites chosen yet. Choose photos in the full gallery first.";
  }
  updateFavourites();
});
exportButton.addEventListener("click", async () => {
  if (exportButton.disabled) return;
  exporting = true; updateFavourites();
  try {
    // Recheck the private gallery before exporting a restored selection.
    await readResponse(await fetch(`${RELAY}/guest/gallery/photos?limit=1`, { headers: authHeaders(), signal: requestsController.signal, cache: "no-store" }));
    if (!favourites.size) return;
    const blob = createSelectionCsv([...favourites.values()], { eventId: galleryContext.event_id, eventName: galleryContext.event_name });
    const url = URL.createObjectURL(blob), link = document.createElement("a");
    link.href = url; link.download = "sm-photography-favourites.csv";
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
    galleryMessage.textContent = `CSV downloaded with ${favourites.size} chosen photos. Send it to your editor with the original photo folder.`;
  } catch (error) { galleryMessage.textContent = error.message; }
  finally { exporting = false; updateFavourites(); }
});
window.addEventListener("pagehide", resetAssets);

if (sessionToken && shareToken) {
  startGallery().catch((error) => {
    galleryMessage.textContent = error.message;
  });
}

const RELAY = "https://dtfxnuxluuwvjdcyxmnc.supabase.co/functions/v1/relay";
const $ = (selector) => document.querySelector(selector);
const loginPanel = $("#login-panel");
const galleryPanel = $("#gallery-panel");
const photoGrid = $("#photo-grid");
const loginMessage = $("#login-message");
const galleryMessage = $("#gallery-message");
const loginButton = $("#login-button");
const loadMoreButton = $("#load-more");
const assetQueue = [];
const queuedPhotos = new Map();
let activeAssets = 0;
let shareToken = location.hash.slice(1) || sessionStorage.getItem("photo-finder-gallery-token") || "";
let sessionToken = sessionStorage.getItem("photo-finder-gallery-session") || "";
let nextCursor = null;
let loadingPage = false;
let photoObserver;

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

function authHeaders() {
  return { "X-Gallery-Session": sessionToken };
}

function clearSession() {
  sessionToken = "";
  sessionStorage.removeItem("photo-finder-gallery-session");
  sessionStorage.removeItem("photo-finder-gallery-session-token");
}

async function readResponse(response) {
  if (response.status === 401) {
    clearSession();
    galleryPanel.hidden = true;
    loginPanel.hidden = false;
    loginMessage.textContent = "Your gallery session expired. Enter the password again.";
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || "The gallery service is temporarily unavailable.");
  }
  return response;
}

async function startGallery() {
  loginPanel.hidden = true;
  galleryPanel.hidden = false;
  galleryMessage.textContent = "";
  photoGrid.replaceChildren();
  nextCursor = null;
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
  try {
    const params = new URLSearchParams({ limit: "60" });
    if (nextCursor) params.set("after", nextCursor);
    const response = await readResponse(await fetch(`${RELAY}/guest/gallery/photos?${params}`, {
      headers: authHeaders(),
    }));
    const result = await response.json();
    for (const photo of result.photos || []) addPhoto(photo);
    nextCursor = result.next_cursor;
    loadMoreButton.hidden = !nextCursor;
    galleryMessage.textContent = photoGrid.childElementCount
      ? `${photoGrid.childElementCount} photos listed. Select a photo to download its original.`
      : "No photos are available in this gallery yet.";
  } catch (error) {
    galleryMessage.textContent = error.message;
  } finally {
    loadingPage = false;
    loadMoreButton.disabled = false;
  }
}

function addPhoto(photo) {
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
  const download = document.createElement("button");
  download.className = "download";
  download.type = "button";
  download.textContent = "Download original";
  download.addEventListener("click", () => downloadOriginal(photo, download));
  details.append(filename, download);
  card.append(frame, details);
  photoGrid.append(card);
  queuedPhotos.set(frame, { photo, placeholder });
  if (photoObserver) photoObserver.observe(frame);
  else queueThumbnail(frame);
}

function queueThumbnail(frame) {
  const item = queuedPhotos.get(frame);
  if (!item) return;
  const { photo, placeholder } = item;
  assetQueue.push(async () => {
    try {
      placeholder.textContent = "Loading preview…";
      const blob = await fetchPhoto(photo, "thumbnail");
      const image = frame.querySelector("img");
      image.src = URL.createObjectURL(blob);
      image.hidden = false;
      placeholder.remove();
    } catch (error) {
      placeholder.textContent = error.message || "Preview unavailable. Refresh to try again.";
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

async function fetchPhoto(photo, variant) {
  const created = await readResponse(await fetch(`${RELAY}/guest/gallery/jobs`, {
    method: "POST",
    headers: { ...authHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify({ photo_id: photo.id, variant }),
  }));
  const job = await created.json();
  for (let attempt = 0; attempt < 400; attempt++) {
    if (attempt) await new Promise((resolve) => setTimeout(resolve, 1500));
    const statusResponse = await readResponse(await fetch(
      `${RELAY}/guest/gallery/jobs/${encodeURIComponent(job.job_id)}`,
      { headers: authHeaders() },
    ));
    const status = (await statusResponse.json()).status;
    if (status === "failed" || status === "expired") {
      throw new Error("This photo is temporarily unavailable. Please try again later.");
    }
    if (status !== "ready") continue;
    const fileResponse = await readResponse(await fetch(
      `${RELAY}/guest/gallery/jobs/${encodeURIComponent(job.job_id)}/file`,
      { headers: authHeaders() },
    ));
    return fileResponse.blob();
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
    loginMessage.textContent = "This gallery link is incomplete. Ask the photographer for a new link.";
    return;
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

if (sessionToken && shareToken) {
  startGallery().catch((error) => {
    galleryMessage.textContent = error.message;
  });
}

import { createFirebaseClient } from "./firebase-client.js";
import { validatePublishedPortfolio } from "./homepage-content.mjs";
import configuration from "./firebase-config.js";

const client = createFirebaseClient({ context: "admin" }), $ = selector => document.querySelector(selector);
const names = { celebration: "Large cover photo", bride: "Small cover photo", wedding: "Candid portfolio photo", together: "Pre-wedding portfolio photo" };
const controls = ["#admin-email", "#admin-password", "#admin-sign-in", "#admin-google-sign-in"];
let snapshot, selected = "celebration", busy = false, version = 0, pollTimer, localPreview;
const cachedPreviews = new Map();
const login = $("#admin-login"), manager = $("#homepage-manager"), notice = $("#homepage-admin-message");
let base;
try {
  const url = new URL(configuration.photoApiBaseUrl);
  if (url.protocol !== "https:" || url.username || url.password || url.pathname !== "/" || url.search || url.hash) throw new Error();
  base = url.origin + "/api/firebase/homepage";
} catch { $("#admin-login-message").textContent = "Homepage management is not configured yet. Please check the studio connection."; }

function clearPreviews() {
  if (localPreview) URL.revokeObjectURL(localPreview);
  localPreview = undefined;
  for (const url of cachedPreviews.values()) URL.revokeObjectURL(url);
  cachedPreviews.clear();
}
function updateControls() {
  const publishing = snapshot?.job?.status === "publishing";
  for (const selector of controls) $(selector).disabled = busy;
  for (const selector of ["#save-homepage-photo", "#homepage-photo-file", "#homepage-photo-title", "#homepage-photo-alt", "#homepage-photo-instagram", "#homepage-photo-position"]) $(selector).disabled = busy || publishing || !snapshot;
  $("#publish-homepage").disabled = busy || publishing || !snapshot?.has_changes;
  $("#refresh-homepage").disabled = busy;
  for (const button of document.querySelectorAll(".homepage-slot")) button.disabled = busy || publishing;
}
async function request(path, options = {}) {
  if (!base) throw new Error("The studio connection is not configured.");
  const token = await client.getIdToken();
  const response = await fetch(base + path, { ...options, headers: { ...options.headers, Authorization: "Bearer " + token },
    cache: "no-store", signal: AbortSignal.timeout(options.body instanceof FormData ? 60000 : 20000) });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const error = new Error(typeof body.detail === "string" ? body.detail : "The studio connection is temporarily unavailable.");
    error.status = response.status;
    if (response.status === 401 || response.status === 403) {
      version++; clearTimeout(pollTimer); clearPreviews(); manager.hidden = true; login.hidden = false;
      $("#admin-login-message").textContent = response.status === 403 ? "This account does not have homepage administrator access. Use the studio administrator account." : "Your session expired. Please sign in again.";
    }
    throw error;
  }
  return response;
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
    if (selected !== id || localPreview || manager.hidden) return;
    image.src = url; image.alt = photo.alt; image.style.objectPosition = photo.position; image.hidden = false; $("#editor-placeholder").hidden = true;
  } catch { if (selected === id) $("#editor-placeholder").textContent = "Preview unavailable. Press Check status to try again."; }
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
    button.addEventListener("click", () => { if (!busy) selectPhoto(photo.id); });
    previewFor(photo).then(url => { if (version === generation) image.src = url; }).catch(() => { status.textContent += " · preview unavailable"; });
  }
  if (select) selectPhoto(selected);
  updateControls();
}
async function refresh(select = true) {
  const result = await (await request("/admin")).json();
  applyState(result, select);
  return result;
}
function pollPublishing(generation = version, failures = 0) {
  clearTimeout(pollTimer);
  if (snapshot?.job?.status !== "publishing" || manager.hidden) return;
  pollTimer = setTimeout(async () => {
    if (generation !== version) return;
    try { await refresh(false); if (snapshot.job?.status === "published") notice.textContent = "Your new photos are live. Open View homepage to see them."; pollPublishing(generation); }
    catch (error) {
      if (generation !== version) return;
      if (failures < 2 && ![401, 403].includes(error.status)) pollPublishing(generation, failures + 1);
      else notice.textContent = "The studio connection was interrupted. Publishing may still be running. Press Check status before trying again.";
    }
  }, 2500 * (failures + 1));
}
async function showManager() {
  const user = await client.getCurrentUser();
  if (!user) return false;
  if (!user.emailVerified) throw new Error("Verify your email address in Client account before managing the homepage.");
  version++; await refresh(); login.hidden = true; manager.hidden = false;
  selectPhoto(selected); pollPublishing(); return true;
}
async function signIn(action) {
  if (busy) return;
  busy = true; updateControls(); $("#admin-login-message").textContent = "Signing in securely…";
  try { await action(); await showManager(); $("#admin-login-message").textContent = ""; }
  catch (error) { $("#admin-login-message").textContent = ["auth/invalid-credential", "auth/user-not-found", "auth/wrong-password"].includes(error.code) ? "Check your email and password, or continue with Google." : error.status === 403 ? "This account does not have homepage administrator access. Use the studio administrator account." : error.message || "Sign-in is temporarily unavailable."; }
  finally { busy = false; $("#admin-password").value = ""; updateControls(); }
}
$("#admin-sign-in-form").addEventListener("submit", event => { event.preventDefault(); signIn(() => client.signInWithPassword($("#admin-email").value.trim(), $("#admin-password").value)); });
$("#admin-google-sign-in").addEventListener("click", () => signIn(() => client.signInWithGoogle()));
$("#admin-sign-out").addEventListener("click", async () => {
  version++; clearTimeout(pollTimer); clearPreviews(); await client.signOut(); snapshot = undefined;
  manager.hidden = true; login.hidden = false; $("#admin-login-message").textContent = "You’re signed out."; updateControls();
});
$("#homepage-photo-file").addEventListener("change", () => {
  const file = $("#homepage-photo-file").files[0]; notice.textContent = "";
  if (!file) { selectPhoto(selected); return; }
  if (!["image/jpeg", "image/png", "image/webp"].includes(file.type) || file.size > 20 * 1024 * 1024 || !file.size) {
    $("#homepage-photo-file").value = ""; notice.textContent = "Choose a JPG, PNG or WebP photo up to 20 MB."; return;
  }
  if (localPreview) URL.revokeObjectURL(localPreview);
  localPreview = URL.createObjectURL(file); $("#homepage-image-preview").src = localPreview; $("#homepage-image-preview").hidden = false; $("#editor-placeholder").hidden = true;
});
$("#homepage-photo-position").addEventListener("change", () => { $("#homepage-image-preview").style.objectPosition = $("#homepage-photo-position").value; });
$("#homepage-photo-form").addEventListener("submit", async event => {
  event.preventDefault(); if (busy || !snapshot || !event.target.reportValidity()) return;
  busy = true; updateControls(); notice.textContent = "Saving your homepage photo…";
  const data = new FormData(); data.set("revision", snapshot.revision);
  data.set("title", $("#homepage-photo-title").value); data.set("alt", $("#homepage-photo-alt").value);
  data.set("instagram_url", $("#homepage-photo-instagram").value.trim()); data.set("position", $("#homepage-photo-position").value);
  const file = $("#homepage-photo-file").files[0]; if (file) data.set("photo", file);
  try { applyState(await (await request("/photos/" + selected, { method: "POST", body: data })).json()); notice.textContent = "Photo saved as a draft. Replace another photo, or click Publish changes when you’re ready."; }
  catch (error) { notice.textContent = error.message || "The photo could not be saved. Check the studio connection and try again."; }
  finally { busy = false; updateControls(); }
});
$("#publish-homepage").addEventListener("click", async () => {
  if (busy || !snapshot?.has_changes) return;
  busy = true; updateControls(); notice.textContent = "Preparing to publish your saved photos…";
  try { applyState(await (await request("/publish", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ revision: snapshot.revision }) })).json(), false); notice.textContent = "Publishing is in progress. You can keep this page open to follow its status."; pollPublishing(); }
  catch (error) { notice.textContent = error.message || "Publishing could not start. Press Check status before trying again."; }
  finally { busy = false; updateControls(); }
});
$("#refresh-homepage").addEventListener("click", async () => {
  if (busy) return; busy = true; updateControls(); notice.textContent = "";
  try { await refresh(); pollPublishing(); } catch (error) { notice.textContent = error.message || "The studio computer is unavailable. Try again when it is online."; }
  finally { busy = false; updateControls(); }
});
window.addEventListener("pagehide", () => { version++; clearTimeout(pollTimer); clearPreviews(); });
try { if (!await showManager() && base) $("#admin-login-message").textContent = "Sign in with your studio administrator account."; }
catch { $("#admin-login-message").textContent = "Sign in with the studio administrator account. The studio computer must be online."; }
updateControls();

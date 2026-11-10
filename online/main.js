const RELAY = "https://ifudnqcgvqafhzazqlku.supabase.co/functions/v1/relay";
const currentUrl = new URL(location.href);
const eventToken = new URLSearchParams(currentUrl.hash.slice(1)).get("token") || currentUrl.searchParams.get("token");
if (eventToken) history.replaceState({}, "", location.pathname);
const $ = selector => document.querySelector(selector);
const consent = $("#consent"), selfie = $("#selfie"), search = $("#search"), message = $("#message");
const formSection = $("#form-section"), results = $("#results"), photos = $("#photos"), preview = $("#preview");
let objectUrls = [], selectedPhoto = null, busy = false;
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

function updateButton() { search.disabled = busy || !consent.checked || !selfie.files.length; }
consent.addEventListener("change", updateButton);
selfie.addEventListener("change", () => {
  $("#file-label").textContent = selfie.files[0]?.name || "Choose a photo";
  message.textContent = "";
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
  const response = await fetch(RELAY + path, options);
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
function showPhoto(photo) {
  selectedPhoto = photo;
  $("#preview-image").src = photo.url;
  $("#preview-name").textContent = photo.filename;
  preview.showModal();
}
$("#close-preview").addEventListener("click", () => preview.close());
$("#preview-download").addEventListener("click", () => { if (selectedPhoto) downloadPhoto(selectedPhoto); });

async function renderPhoto(photo, job, headers) {
  const card = document.createElement("article"); card.className = "result";
  const open = document.createElement("button"); open.className = "photo-open"; open.disabled = true;
  open.setAttribute("aria-label", "View " + photo.filename);
  const image = document.createElement("img"); image.className = "photo"; image.alt = ""; image.loading = "lazy";
  open.append(image);
  const meta = document.createElement("div"); meta.className = "photo-meta";
  const name = document.createElement("span"); name.className = "photo-name"; name.textContent = photo.filename;
  const download = document.createElement("button"); download.className = "download"; download.textContent = "↓";
  download.setAttribute("aria-label", "Download " + photo.filename); download.disabled = true;
  meta.append(name, download); card.append(open, meta); photos.append(card);
  try {
    const blob = await (await request(`/guest/jobs/${job.job_id}/photos/${photo.id}`, { headers })).blob();
    const url = URL.createObjectURL(blob); objectUrls.push(url);
    const readyPhoto = { url, filename: photo.filename }; image.src = url;
    open.disabled = false; download.disabled = false;
    open.addEventListener("click", () => showPhoto(readyPhoto));
    download.addEventListener("click", () => downloadPhoto(readyPhoto));
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
    const count = status.photos.length;
    $("#summary").textContent = count ? `We found ${count} photo${count === 1 ? "" : "s"} of you.` : "No photos found this time.";
    $("#gallery-count").textContent = count;
    $("#empty").hidden = count !== 0;
    message.textContent = "";
    await Promise.all(status.photos.map(photo => renderPhoto(photo, job, headers)));
    window.scrollTo({ top: 0, behavior: "auto" });
  } catch (error) { message.textContent = error.message; }
  finally { busy = false; setLoadingAnimation(false); search.firstChild.textContent = "Find my photos "; updateButton(); }
});

$("#again").addEventListener("click", () => {
  objectUrls.forEach(url => URL.revokeObjectURL(url)); objectUrls = [];
  selectedPhoto = null; photos.replaceChildren();
  results.hidden = true; formSection.hidden = false; selfie.value = "";
  $("#file-label").textContent = "Choose a photo"; message.textContent = "";
  updateButton(); window.scrollTo({ top: 0, behavior: "auto" });
});

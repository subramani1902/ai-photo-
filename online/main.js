const RELAY = "https://ifudnqcgvqafhzazqlku.supabase.co/functions/v1/relay";
const currentUrl = new URL(location.href);
const params = new URLSearchParams(currentUrl.hash.slice(1));
const eventToken = params.get("token") || currentUrl.searchParams.get("token");
if (eventToken) history.replaceState({}, "", location.pathname);
const consent = document.querySelector("#consent"), selfie = document.querySelector("#selfie");
const search = document.querySelector("#search"), message = document.querySelector("#message");
const results = document.querySelector("#results"), photos = document.querySelector("#photos");
const formSection = document.querySelector("#form-section"), summary = document.querySelector("#summary");
let objectUrls = [];
function updateButton() { search.disabled = !eventToken || !consent.checked || !selfie.files.length; }
consent.addEventListener("change", updateButton);
selfie.addEventListener("change", updateButton);
if (!eventToken) message.textContent = "Open this page by scanning your event QR code.";

async function request(path, options = {}) {
  const response = await fetch(RELAY + path, options);
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.detail || "Unable to complete your request. Please try again.");
  }
  return response;
}

search.addEventListener("click", async () => {
  search.disabled = true;
  message.textContent = "Sending your selfie…";
  const body = new FormData(); body.append("file", selfie.files[0]);
  try {
    const submitted = await (await request("/guest/submit", { method: "POST", headers: { "X-Event-Token": eventToken }, body })).json();
    const headers = { "X-Job-Token": submitted.job_token };
    message.textContent = "Finding your photos. Please keep this page open…";
    let status;
    while (true) {
      await new Promise(resolve => setTimeout(resolve, 3000));
      status = await (await request(`/guest/jobs/${submitted.job_id}`, { headers })).json();
      if (status.status === "completed") break;
      if (status.status === "failed") throw new Error(status.message || "Could not process the selfie.");
      message.textContent = status.status === "processing" ? "Matching your face with event photos…" : "Waiting for the event laptop…";
    }
    formSection.hidden = true; results.hidden = false;
    photos.replaceChildren();
    summary.textContent = `${status.photos.length} matching photos found.`;
    message.textContent = "";
    for (const photo of status.photos) {
      const card = document.createElement("div"); card.className = "result";
      const image = document.createElement("img"); image.className = "photo"; image.alt = "Matching event photo";
      const name = document.createElement("p"); name.textContent = photo.filename;
      const download = document.createElement("button"); download.textContent = "Download photo"; download.disabled = true;
      card.append(image, name, download); photos.append(card);
      try {
        const blob = await (await request(`/guest/jobs/${submitted.job_id}/photos/${photo.id}`, { headers })).blob();
        const url = URL.createObjectURL(blob); objectUrls.push(url); image.src = url;
        download.disabled = false;
        download.addEventListener("click", () => { const link = document.createElement("a"); link.href = url; link.download = photo.filename; link.click(); });
      } catch { name.textContent = `Could not load ${photo.filename}.`; }
    }
  } catch (error) { message.textContent = error.message; updateButton(); }
});

document.querySelector("#again").addEventListener("click", () => {
  objectUrls.forEach(url => URL.revokeObjectURL(url)); objectUrls = [];
  results.hidden = true; formSection.hidden = false; selfie.value = ""; updateButton();
});

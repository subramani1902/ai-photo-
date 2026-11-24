import { createFirebaseClient } from "../firebase-client.js";
import { createHomepageManager } from "../homepage-admin.js";
import { trustedPhotoApiOrigin } from "../studio-packages.mjs";
import { createClientManager } from "./clients.js";
import { createPackageManager } from "./packages.js";

const $ = selector => document.querySelector(selector);
const client = createFirebaseClient({ context: "admin" });
const form = $("#admin-sign-in-form"), login = $("#admin-login"), workspace = $("#studio-workspace");
const email = $("#admin-email"), password = $("#admin-password"), loginMessage = $("#admin-login-message");
const google = $("#admin-google-sign-in"), signOut = $("#admin-sign-out");
const nav = [...document.querySelectorAll("[data-panel]")];
const studioPath = "/api/studio", homepagePath = "/api/firebase/homepage";
let origin, generation = 0, active = false;

async function request(path, options = {}) {
  const version = generation;
  const token = await client.getIdToken();
  if (version !== generation) throw new Error("Session ended.");
  const response = await fetch(origin + path, { ...options, cache: "no-store",
    headers: { ...options.headers, Authorization: "Bearer " + token },
    signal: AbortSignal.timeout(options.body instanceof FormData ? 60000 : 20000),
  }).catch(() => { throw new Error("The studio connection is temporarily unavailable."); });
  if (version !== generation) throw new Error("Session ended.");
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const error = new Error(typeof body.detail === "string" ? body.detail : "The studio connection is temporarily unavailable.");
    error.status = response.status;
    if (active && version === generation && [401, 403].includes(response.status)) {
      reset(response.status === 401 ? "Your session expired. Please sign in again." : "Your studio access changed. Please sign in again.");
      client.signOut().catch(() => {});
    }
    throw error;
  }
  return response;
}

const studioRequest = async (path, options) => (await request(studioPath + path, options)).json();
const managers = {
  clients: createClientManager(studioRequest),
  packages: createPackageManager(studioRequest),
  "homepage-manager": createHomepageManager((path, options) => request(homepagePath + path, options)),
};

function selectPanel(panel) {
  for (const button of nav) {
    const current = button.dataset.panel === panel;
    if (current) button.setAttribute("aria-current", "true");
    else button.removeAttribute("aria-current");
    $("#" + button.dataset.panel).hidden = !current;
  }
  managers[panel].open();
}

function setBusy(busy) {
  for (const control of [email, password, form.querySelector('[type="submit"]'), google, signOut]) control.disabled = busy;
}

function reset(text) {
  generation++; active = false;
  workspace.hidden = true; login.hidden = false;
  for (const button of nav) {
    button.hidden = true;
    $("#" + button.dataset.panel).hidden = true;
  }
  for (const manager of Object.values(managers)) manager.reset();
  form.reset(); loginMessage.textContent = text;
}

async function authenticate(action) {
  setBusy(true);
  loginMessage.textContent = "Checking studio access…";
  try {
    await action();
    const user = await client.getCurrentUser();
    if (!user?.emailVerified) throw new Error("Verify your email address in Client account and sign in again.");
    const access = await studioRequest("/access");
    for (const button of nav) button.hidden = button.dataset.panel === "homepage-manager" ? !access.photos : !access.studio;
    active = true;
    workspace.hidden = false; login.hidden = true;
    selectPanel(access.studio ? "clients" : "homepage-manager");
  } catch (error) {
    await client.signOut().catch(() => {});
    loginMessage.textContent = ["auth/invalid-credential", "auth/user-not-found", "auth/wrong-password"].includes(error.code)
      ? "Check your email and password, or continue with Google."
      : error.message || "Sign-in is temporarily unavailable.";
  } finally {
    password.value = "";
    setBusy(false);
  }
}

form.addEventListener("submit", event => {
  event.preventDefault();
  authenticate(() => client.signInWithPassword(email.value.trim(), password.value));
});
google.addEventListener("click", () => authenticate(() => client.signInWithGoogle()));
signOut.addEventListener("click", async () => {
  if (Object.values(managers).some(manager => manager.isDirty()) && !window.confirm("Sign out and discard unsaved changes?")) return;
  setBusy(true);
  reset("Signing out…");
  try { await client.signOut(); loginMessage.textContent = "You’re signed out."; }
  catch { loginMessage.textContent = "The studio workspace was closed. Sign in to continue."; }
  finally { setBusy(false); }
});
for (const button of nav) {
  button.addEventListener("click", () => { if (active && !button.hidden) selectPanel(button.dataset.panel); });
}
window.addEventListener("pagehide", () => {
  if (active) {
    reset("Sign in with your studio administrator account.");
    client.signOut().catch(() => {});
  }
});

try {
  const { default: config } = await import("../firebase-config.js");
  origin = trustedPhotoApiOrigin(config.photoApiBaseUrl);
  if (!config.enabled || !origin) throw new Error();
  setBusy(false);
  loginMessage.textContent = "Sign in with your studio administrator account.";
} catch {
  loginMessage.textContent = "Studio sign-in is unavailable. Check the studio connection.";
}

import { createFirebaseClient } from "./firebase-client.js";
import { createClientGallery } from "./client-event-gallery.js";

const client = createFirebaseClient({ context: "client" });
const $ = selector => document.querySelector(selector);
const form = $("#sign-in-form"), email = $("#account-email"), password = $("#account-password");
const confirmation = $("#account-confirm-password"), message = $("#account-message");
const signIn = $("#sign-in"), signedIn = $("#signed-in"), signOut = $("#sign-out");
const modeButtons = [$("#mode-sign-in"), $("#mode-register")];
const gallery = createClientGallery({ client, onClose: () => $(".account-intro").hidden = false });
let busy = true, mode = "sign-in", verificationPending = false;
let adminProbe = 0;

async function showAdminTools(user) {
  const version = ++adminProbe, link = $("#studio-admin-tools");
  link.hidden = true;
  if (!user?.emailVerified) return;
  try {
    const { default: config } = await import("./firebase-config.js");
    const base = new URL(config.photoApiBaseUrl);
    if (base.protocol !== "https:" || base.username || base.password || base.pathname !== "/" || base.search || base.hash) return;
    const token = await client.getIdToken();
    const response = await fetch(base.origin + "/api/firebase/homepage/admin", {
      headers: { Authorization: "Bearer " + token }, cache: "no-store", signal: AbortSignal.timeout(8000),
    });
    if (version === adminProbe && response.ok) link.hidden = false;
  } catch { /* Client gallery sign-in remains usable when studio management is offline. */ }
}

function setBusy(value) {
  busy = value;
  form.setAttribute("aria-busy", String(value));
  for (const element of [email, password, confirmation, signIn, signOut, ...modeButtons,
    $("#reset-password"), $("#google-sign-in"), $("#send-verification"), $("#check-verification")]) element.disabled = value;
  $("#open-event").disabled = value || verificationPending;
}

function setMode(value) {
  if (busy) return;
  mode = value;
  const registering = mode === "register";
  $("#form-title").textContent = registering ? "Create your account" : "Sign in";
  form.setAttribute("aria-label", registering ? "Create account" : "Sign in");
  signIn.textContent = registering ? "Create account ↗" : "Sign in ↗";
  password.autocomplete = registering ? "new-password" : "current-password";
  password.minLength = registering ? 6 : 1;
  password.value = confirmation.value = "";
  confirmation.required = registering;
  confirmation.setCustomValidity("");
  $("#confirm-password-field").hidden = !registering;
  $("#registration-help").hidden = !registering;
  $("#reset-password").hidden = registering;
  modeButtons[0].setAttribute("aria-pressed", String(!registering));
  modeButtons[1].setAttribute("aria-pressed", String(registering));
  message.textContent = "";
}

async function showAccount() {
  const user = await client.getCurrentUser();
  const authenticated = Boolean(user);
  form.hidden = $("#account-modes").hidden = $("#google-option").hidden = authenticated;
  signedIn.hidden = !authenticated;
  $("#account-identity").textContent = user?.email ?? "";
  $("#account-uid").textContent = user?.uid ?? "";
  verificationPending = authenticated && !user.emailVerified;
  $("#email-verification").hidden = !verificationPending;
  $("#event-form").hidden = verificationPending;
  password.value = confirmation.value = "";
  void showAdminTools(user);
  return user;
}

function errorMessage(error) {
  if (error?.code === "auth/too-many-requests") return "Too many attempts. Please wait a while and try again.";
  if (error?.code === "auth/network-request-failed") return "Could not connect. Check your internet connection and try again.";
  if (error?.code === "auth/email-already-in-use") return "An account already uses this email. Sign in or reset your password.";
  if (["auth/weak-password", "auth/password-does-not-meet-requirements"].includes(error?.code)) return "Choose a stronger password that meets the account requirements.";
  if (error?.code === "auth/popup-blocked") return "Allow the Google sign-in popup in your browser, then try again.";
  if (["auth/popup-closed-by-user", "auth/cancelled-popup-request"].includes(error?.code)) return "Google sign-in was closed. You can try again.";
  if (error?.code === "auth/account-exists-with-different-credential") return "Use the sign-in method you previously chose for this email address.";
  if (["auth/invalid-credential", "auth/user-not-found", "auth/wrong-password", "auth/user-disabled", "auth/invalid-email"].includes(error?.code)) return "Could not sign in. Check your email address and password.";
  return "Sign-in is temporarily unavailable. Please try again later.";
}

modeButtons[0].addEventListener("click", () => setMode("sign-in"));
modeButtons[1].addEventListener("click", () => setMode("register"));
for (const input of [password, confirmation]) input.addEventListener("input", () => confirmation.setCustomValidity(""));

form.addEventListener("submit", async event => {
  event.preventDefault();
  if (busy) return;
  confirmation.setCustomValidity(mode === "register" && password.value !== confirmation.value ? "Passwords must match." : "");
  if (!form.reportValidity()) return;
  setBusy(true);
  message.textContent = mode === "register" ? "Creating your account…" : "Signing in…";
  try {
    if (mode === "register") {
      await client.createAccount(email.value.trim(), password.value);
      await showAccount();
      try {
        await client.sendVerification();
        message.textContent = "Account created. Check your email for the verification link.";
      } catch {
        message.textContent = "Account created. Use Send verification email to verify your address.";
      }
    } else {
      await client.signInWithPassword(email.value.trim(), password.value);
      await showAccount();
      message.textContent = "Signed in securely.";
    }
  } catch (error) { message.textContent = errorMessage(error); }
  finally {
    password.value = confirmation.value = "";
    setBusy(false);
    if (!signedIn.hidden && !verificationPending) $("#client-event-id").focus();
  }
});

$("#google-sign-in").addEventListener("click", async () => {
  if (busy) return;
  setBusy(true);
  message.textContent = "Opening Google sign-in…";
  try {
    await client.signInWithGoogle();
    await showAccount();
    message.textContent = "Signed in securely with Google.";
  } catch (error) { message.textContent = errorMessage(error); }
  finally { setBusy(false); }
});

$("#reset-password").addEventListener("click", async () => {
  if (busy || !email.reportValidity()) return;
  setBusy(true);
  try {
    await client.sendPasswordReset(email.value.trim());
    message.textContent = "If an account uses this email address, you’ll receive password reset instructions.";
  } catch (error) {
    message.textContent = error?.code === "auth/user-not-found" ? "If an account uses this email address, you’ll receive password reset instructions." : errorMessage(error);
  } finally { setBusy(false); }
});

$("#send-verification").addEventListener("click", async () => {
  if (busy) return;
  setBusy(true);
  try { await client.sendVerification(); message.textContent = "Check your email for the verification link."; }
  catch (error) { message.textContent = errorMessage(error); }
  finally { setBusy(false); }
});

$("#check-verification").addEventListener("click", async () => {
  if (busy) return;
  setBusy(true);
  try {
    await client.refreshUser();
    const user = await showAccount();
    message.textContent = user?.emailVerified ? "Email verified. Enter your event folder name." : "Your email is not verified yet. Open the link in your email first.";
  } catch (error) { message.textContent = errorMessage(error); }
  finally { setBusy(false); }
});

$("#event-form").addEventListener("submit", async event => {
  event.preventDefault();
  if (busy || verificationPending || !event.target.reportValidity()) return;
  setBusy(true);
  try {
    await gallery.open($("#client-event-id").value);
    $(".account-intro").hidden = true;
    message.textContent = "";
  } catch (error) { message.textContent = error.message; }
  finally { setBusy(false); }
});

signOut.addEventListener("click", async () => {
  if (busy) return;
  setBusy(true);
  try {
    gallery.close();
    await client.signOut();
    await showAccount();
    message.textContent = "You’re signed out.";
  } catch (error) { message.textContent = errorMessage(error); }
  finally { setBusy(false); }
});

try { await showAccount(); setBusy(false); message.textContent = ""; }
catch { message.textContent = "Sign-in is temporarily unavailable. Please try again later."; }

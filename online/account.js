import { createFirebaseClient } from "./firebase-client.js";

const client = createFirebaseClient({ context: "admin" });
const form = document.querySelector("#sign-in-form");
const email = document.querySelector("#account-email");
const password = document.querySelector("#account-password");
const signIn = document.querySelector("#sign-in");
const resetPassword = document.querySelector("#reset-password");
const signedIn = document.querySelector("#signed-in");
const signOut = document.querySelector("#sign-out");
const identity = document.querySelector("#account-identity");
const message = document.querySelector("#account-message");
let busy = true;

function setBusy(value) {
  busy = value;
  form.setAttribute("aria-busy", String(value));
  for (const element of [email, password, signIn, resetPassword, signOut]) element.disabled = value;
}

async function showAccount() {
  const user = await client.getCurrentUser();
  form.hidden = Boolean(user);
  signedIn.hidden = !user;
  identity.textContent = user?.email ?? "";
  password.value = "";
  return user;
}

function errorMessage(error) {
  if (error?.code === "auth/too-many-requests") return "Too many attempts. Please wait a while and try again.";
  if (error?.code === "auth/network-request-failed") return "Could not connect. Check your internet connection and try again.";
  if (["auth/invalid-credential", "auth/user-not-found", "auth/wrong-password", "auth/user-disabled", "auth/invalid-email"].includes(error?.code)) return "Could not sign in. Check your email address and password.";
  return "Sign-in is temporarily unavailable. Please try again later.";
}

form.addEventListener("submit", async event => {
  event.preventDefault();
  if (busy || !form.reportValidity()) return;
  setBusy(true);
  message.textContent = "Signing in…";
  try {
    await client.signInWithPassword(email.value.trim(), password.value);
    await showAccount();
    message.textContent = "Signed in securely.";
  } catch (error) {
    password.value = "";
    message.textContent = errorMessage(error);
  } finally {
    setBusy(false);
    if (!signedIn.hidden) signOut.focus();
  }
});

resetPassword.addEventListener("click", async () => {
  if (busy || !email.reportValidity()) return;
  setBusy(true);
  message.textContent = "Requesting password reset…";
  try {
    await client.sendPasswordReset(email.value.trim());
    message.textContent = "If an account uses this email address, you’ll receive password reset instructions.";
  } catch (error) {
    message.textContent = error?.code === "auth/user-not-found"
      ? "If an account uses this email address, you’ll receive password reset instructions."
      : errorMessage(error);
  } finally { setBusy(false); }
});

signOut.addEventListener("click", async () => {
  if (busy) return;
  setBusy(true);
  try {
    await client.signOut();
    await showAccount();
    message.textContent = "You’re signed out.";
  } catch (error) { message.textContent = errorMessage(error); }
  finally {
    setBusy(false);
    if (!form.hidden) email.focus();
  }
});

try {
  await showAccount();
  setBusy(false);
  message.textContent = "";
} catch {
  message.textContent = "Sign-in is temporarily unavailable. Please try again later.";
}

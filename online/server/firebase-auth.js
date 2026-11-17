// No public minting or account-linking endpoint. Call only after server-side authorization.
import { firebaseAdminRuntime, FirebaseConfigurationError } from "./firebase-admin.js";

export class FirebaseAuthenticationError extends Error {
  constructor(statusCode, message) { super(message); this.statusCode = statusCode; }
}

export async function verifyFirebaseIdToken(token, runtime = firebaseAdminRuntime) {
  if (typeof token !== "string" || !token || token.length > 16384 || /\s/.test(token)) {
    throw new FirebaseAuthenticationError(401, "Invalid or expired Firebase ID token.");
  }
  try {
    const { auth, settings } = await runtime.get();
    const claims = await auth.verifyIdToken(token, true);
    if (typeof claims.uid !== "string" || !claims.uid.length || claims.uid.length > 128
        || claims.sub !== claims.uid || claims.aud !== settings.projectId
        || claims.iss !== `https://securetoken.google.com/${settings.projectId}` || claims.firebase?.tenant) {
      throw new FirebaseAuthenticationError(401, "Invalid or expired Firebase ID token.");
    }
    return Object.freeze({ uid: claims.uid, projectId: settings.projectId, claims: Object.freeze({ ...claims }) });
  } catch (error) {
    if (error instanceof FirebaseAuthenticationError) throw error;
    if (["auth/id-token-expired", "auth/id-token-revoked", "auth/invalid-id-token", "auth/user-disabled", "auth/user-not-found", "auth/argument-error"].includes(error.code)) {
      throw new FirebaseAuthenticationError(401, "Invalid or expired Firebase ID token.");
    }
    throw new FirebaseAuthenticationError(503, "Firebase authentication is unavailable.");
  }
}

export async function createGallerySignInToken({ clientId, galleryId, sessionId, credentialVersion }, runtime = firebaseAdminRuntime) {
  for (const value of [clientId, galleryId, sessionId]) {
    if (typeof value !== "string" || !/^[A-Za-z0-9_-]{1,100}$/.test(value)) throw new TypeError("Invalid gallery authentication scope.");
  }
  if (!Number.isSafeInteger(credentialVersion) || credentialVersion < 1) throw new TypeError("Invalid gallery credential version.");
  try {
    const { auth, settings } = await runtime.get();
    if (settings.emulatorMode) throw new FirebaseConfigurationError("Gallery signing requires configured signing credentials.");
    return await auth.createCustomToken(`gallery-client:${clientId}`, {
      actor_type: "gallery_client", gallery_id: galleryId, gallery_session_id: sessionId, credential_version: credentialVersion,
    });
  } catch {
    throw new FirebaseAuthenticationError(503, "Unable to create a Firebase sign-in token.");
  }
}

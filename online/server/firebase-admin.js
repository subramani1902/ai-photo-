// Server-only module. Never import this file from browser code or copy credentials into online/.
import { readFileSync, realpathSync } from "node:fs";
import { dirname, isAbsolute, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const publicRoot = realpathSync(resolve(dirname(fileURLToPath(import.meta.url)), ".."));
const emulatorKeys = ["FIREBASE_AUTH_EMULATOR_HOST", "FIRESTORE_EMULATOR_HOST", "STORAGE_EMULATOR_HOST", "FIREBASE_STORAGE_EMULATOR_HOST"];

export class FirebaseConfigurationError extends Error {}

export function readBoolean(env, name) {
  const value = (env[name] ?? "false").trim().toLowerCase();
  if (!["true", "false"].includes(value)) throw new FirebaseConfigurationError(`${name} must be true or false.`);
  return value === "true";
}

function readCredentialFile(filename) {
  try {
    const path = realpathSync(filename);
    const fromPublic = relative(publicRoot, path);
    if (!fromPublic || (!isAbsolute(fromPublic) && fromPublic !== ".." && !fromPublic.startsWith(`..${sep}`))) {
      throw new Error("Public credential path");
    }
    return JSON.parse(readFileSync(path, "utf8").replace(/^\uFEFF/, ""));
  } catch {
    throw new FirebaseConfigurationError("Firebase credentials must be valid JSON stored outside the online public directory.");
  }
}

export function readServerSettings(env = process.env) {
  if (!readBoolean(env, "FIREBASE_ENABLED")) return { enabled: false };
  const projectId = env.FIREBASE_PROJECT_ID?.trim() ?? "";
  if (!/^[a-z][a-z0-9-]{4,28}[a-z0-9]$/.test(projectId)) throw new FirebaseConfigurationError("Set a valid FIREBASE_PROJECT_ID.");
  const emulatorMode = readBoolean(env, "FIREBASE_EMULATOR_MODE");
  const emulatorHosts = Object.fromEntries(emulatorKeys.filter(key => env[key]?.trim()).map(key => [key, env[key].trim()]));
  if (Object.keys(emulatorHosts).length && !emulatorMode) throw new FirebaseConfigurationError("Emulator variables are forbidden in production mode.");
  if (emulatorMode) {
    if (!projectId.startsWith("demo-") || !emulatorHosts.FIREBASE_AUTH_EMULATOR_HOST) throw new FirebaseConfigurationError("Emulators require a demo- project and an explicit Auth emulator host.");
    for (const [key, address] of Object.entries(emulatorHosts)) {
      if (key === "STORAGE_EMULATOR_HOST" && !address.startsWith("http://")) throw new FirebaseConfigurationError("STORAGE_EMULATOR_HOST requires an http:// loopback URL.");
      const host = key === "STORAGE_EMULATOR_HOST" ? address.slice(7) : address;
      if (!/^(localhost|127\.0\.0\.1|\[::1\]):[0-9]+$/.test(host)) throw new FirebaseConfigurationError("Emulators must use a loopback host:port.");
      const port = Number(host.slice(host.lastIndexOf(":") + 1));
      if (port < 1 || port > 65535) throw new FirebaseConfigurationError("Invalid emulator port.");
    }
  } else if (projectId.startsWith("demo-")) throw new FirebaseConfigurationError("A demo- project requires explicit emulator mode.");
  const filename = env.FIREBASE_CREDENTIALS_FILE?.trim() ?? "";
  const json = env.FIREBASE_ADMIN_CREDENTIALS_JSON?.trim() ?? "";
  const adcFile = env.GOOGLE_APPLICATION_CREDENTIALS?.trim() ?? "";
  if (emulatorMode && (filename || json || adcFile)) throw new FirebaseConfigurationError("Do not load production credentials in emulator mode.");
  if (filename && json) throw new FirebaseConfigurationError("Configure one explicit Firebase credential source.");
  let credential;
  if (json) {
    try { credential = JSON.parse(json); }
    catch { throw new FirebaseConfigurationError("Invalid Firebase Admin JSON credential."); }
  } else if (filename) credential = readCredentialFile(filename);
  if (adcFile) readCredentialFile(adcFile); // Reject credentials inside the static website even with ADC.
  if ((json || filename) && (!credential || typeof credential !== "object" || Array.isArray(credential)
      || credential.type !== "service_account" || credential.project_id !== projectId)) {
    throw new FirebaseConfigurationError("Firebase service-account credentials belong to another project or have an invalid type.");
  }
  const storageBucket = env.FIREBASE_STORAGE_BUCKET?.trim() ?? "";
  if (storageBucket && !/^[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]$/.test(storageBucket)) throw new FirebaseConfigurationError("Set a bucket name without a URL or path.");
  return { enabled: true, projectId, storageBucket, emulatorMode, emulatorHosts, credential };
}

async function loadSdk() {
  const [app, auth] = await Promise.all([import("firebase-admin/app"), import("firebase-admin/auth")]);
  return { ...app, ...auth };
}

export function createFirebaseAdminRuntime({ env = process.env, load = loadSdk } = {}) {
  let initialized, fingerprint;
  return {
    settings: () => readServerSettings(env),
    async get() {
      if (typeof window !== "undefined") throw new FirebaseConfigurationError("Firebase Admin is server-only.");
      const settings = readServerSettings(env);
      if (!settings.enabled) throw new FirebaseConfigurationError("Firebase is not enabled.");
      const current = JSON.stringify(settings);
      if (initialized && fingerprint !== current) throw new FirebaseConfigurationError("Firebase configuration changed; restart the process.");
      if (!initialized) {
        fingerprint = current;
        initialized = (async () => {
          const sdk = await load();
          const credential = settings.emulatorMode
            ? { getAccessToken: async () => ({ access_token: "emulator-only", expires_in: 3600 }) }
            : settings.credential ? sdk.cert(settings.credential) : sdk.applicationDefault();
          const app = sdk.initializeApp({ projectId: settings.projectId, credential,
            ...(settings.storageBucket ? { storageBucket: settings.storageBucket } : {}) }, "event-photo-finder");
          return { app, auth: sdk.getAuth(app), settings };
        })();
        initialized.catch(() => { initialized = undefined; fingerprint = undefined; });
      }
      try { return await initialized; }
      catch { throw new FirebaseConfigurationError("Unable to initialize Firebase Admin. Check server credentials."); }
    },
  };
}

export const firebaseAdminRuntime = createFirebaseAdminRuntime();

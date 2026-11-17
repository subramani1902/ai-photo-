// Explicit allowlist: this output is public and must never contain Admin credentials.
import { realpathSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

export function buildPublicConfiguration(env = process.env) {
  const enabled = (env.FIREBASE_ENABLED ?? "false").trim().toLowerCase();
  if (!["true", "false"].includes(enabled)) throw new Error("FIREBASE_ENABLED must be true or false.");
  if (enabled === "false") return { enabled: false };
  const emulatorMode = (env.FIREBASE_EMULATOR_MODE ?? "false").trim().toLowerCase();
  if (!["true", "false"].includes(emulatorMode)) throw new Error("FIREBASE_EMULATOR_MODE must be true or false.");
  const firebase = Object.fromEntries([
    ["projectId", env.FIREBASE_PROJECT_ID], ["apiKey", env.FIREBASE_WEB_API_KEY],
    ["authDomain", env.FIREBASE_WEB_AUTH_DOMAIN], ["appId", env.FIREBASE_WEB_APP_ID],
    ["storageBucket", env.FIREBASE_STORAGE_BUCKET],
  ].filter(([, value]) => value?.trim()).map(([key, value]) => [key, value.trim()]));
  for (const key of ["projectId", "apiKey", "authDomain", "appId"]) {
    if (!firebase[key]) throw new Error(`Missing public Firebase web-app setting: ${key}.`);
  }
  if (!/^[a-z][a-z0-9-]{4,28}[a-z0-9]$/.test(firebase.projectId)
      || !/^AIza[A-Za-z0-9_-]{35}$/.test(firebase.apiKey)
      || !/^[A-Za-z0-9.-]+$/.test(firebase.authDomain)
      || !/^1:[0-9]+:web:[a-f0-9]+$/.test(firebase.appId)
      || Object.values(firebase).some(value => /[\r\n]/.test(value))) throw new Error("Invalid public Firebase web-app configuration.");
  const configuration = { enabled: true, firebase, emulatorMode: emulatorMode === "true" };
  if (env.FIREBASE_PHOTO_API_URL?.trim()) {
    const base = new URL(env.FIREBASE_PHOTO_API_URL.trim());
    if (base.protocol !== "https:" || base.username || base.password || base.search || base.hash || base.pathname !== "/") {
      throw new Error("FIREBASE_PHOTO_API_URL must be a trusted HTTPS origin without a path or credentials.");
    }
    configuration.photoApiBaseUrl = base.origin;
  }
  const emulatorKeys = ["FIREBASE_AUTH_EMULATOR_HOST", "FIRESTORE_EMULATOR_HOST", "STORAGE_EMULATOR_HOST", "FIREBASE_STORAGE_EMULATOR_HOST"];
  if (configuration.emulatorMode) {
    const host = env.FIREBASE_AUTH_EMULATOR_HOST?.trim() ?? "";
    if (!firebase.projectId.startsWith("demo-") || !/^(localhost|127\.0\.0\.1|\[::1\]):[0-9]+$/.test(host)) throw new Error("Use a demo- project and loopback Auth emulator host.");
    const port = Number(host.slice(host.lastIndexOf(":") + 1));
    if (port < 1 || port > 65535) throw new Error("Invalid Auth emulator port.");
    configuration.authEmulatorUrl = `http://${host}`;
  } else if (firebase.projectId.startsWith("demo-") || emulatorKeys.some(key => env[key]?.trim())) throw new Error("Emulator configuration is forbidden in production mode.");
  return configuration;
}

// Canonical paths also handle Windows drive casing and alternate path separators.
if (process.argv[1] && realpathSync(fileURLToPath(import.meta.url)) === realpathSync(process.argv[1])) {
  try {
    const configuration = buildPublicConfiguration();
    writeFileSync(fileURLToPath(new URL("./firebase-config.js", import.meta.url)),
      `// Generated public configuration.\nexport default ${JSON.stringify(configuration, null, 2)};\n`, { encoding: "utf8" });
    console.log(`Public Firebase configuration generated (${configuration.enabled ? "enabled" : "disabled"}).`);
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}

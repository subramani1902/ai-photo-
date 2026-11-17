import assert from "node:assert/strict";
import { test } from "node:test";
import { buildPublicConfiguration } from "../build-config.mjs";
import { createFirebaseClient } from "../firebase-client.js";
import { createFirebaseAdminRuntime, readServerSettings } from "../server/firebase-admin.js";
import { createGallerySignInToken, verifyFirebaseIdToken } from "../server/firebase-auth.js";

const projectId = "unit-test-project";
const serverEnv = () => ({ FIREBASE_ENABLED: "true", FIREBASE_PROJECT_ID: projectId });
const publicEnv = () => ({ ...serverEnv(), FIREBASE_WEB_API_KEY: `AIza${"a".repeat(35)}`,
  FIREBASE_WEB_AUTH_DOMAIN: "unit-test-project.firebaseapp.com", FIREBASE_WEB_APP_ID: "1:123456789:web:abcdef" });
const claims = () => ({ uid: "test-uid", sub: "test-uid", aud: projectId,
  iss: `https://securetoken.google.com/${projectId}`, firebase: { sign_in_provider: "password" } });

test("disabled Firebase never loads the Admin SDK or validates optional secrets", async () => {
  let loaded = false;
  const runtime = createFirebaseAdminRuntime({ env: { FIREBASE_ENABLED: "false", FIREBASE_CREDENTIALS_FILE: "missing.json" }, load: async () => { loaded = true; } });
  await assert.rejects(runtime.get(), /not enabled/);
  assert.equal(loaded, false);
  assert.deepEqual(buildPublicConfiguration({ FIREBASE_ENABLED: "false" }), { enabled: false });
});

test("production rejects emulator bypasses and malformed credential sources", () => {
  for (const additions of [
    { FIREBASE_AUTH_EMULATOR_HOST: "127.0.0.1:9099" },
    { FIREBASE_EMULATOR_MODE: "true", FIREBASE_AUTH_EMULATOR_HOST: "127.0.0.1:9099" },
    { FIREBASE_ADMIN_CREDENTIALS_JSON: "not-json" },
    { FIREBASE_ADMIN_CREDENTIALS_JSON: "null" },
    { FIREBASE_ADMIN_CREDENTIALS_JSON: JSON.stringify({ type: "service_account", project_id: "other-project" }) },
  ]) assert.throws(() => readServerSettings({ ...serverEnv(), ...additions }));
  assert.throws(() => readServerSettings({ FIREBASE_ENABLED: "true" }));
  assert.throws(() => readServerSettings({ ...serverEnv(), FIREBASE_CREDENTIALS_FILE: "package.json" }));
});

test("emulators require a separate demo project and loopback host", () => {
  const env = { FIREBASE_ENABLED: "true", FIREBASE_PROJECT_ID: "demo-local", FIREBASE_EMULATOR_MODE: "true", FIREBASE_AUTH_EMULATOR_HOST: "127.0.0.1:9099" };
  assert.equal(readServerSettings(env).emulatorMode, true);
  for (const host of ["remote.test:9099", "http://127.0.0.1:9099", "localhost:99999"]) {
    assert.throws(() => readServerSettings({ ...env, FIREBASE_AUTH_EMULATOR_HOST: host }));
  }
});

test("server initialization is shared and configuration cannot change underneath it", async () => {
  const env = serverEnv();
  let initialized = 0;
  const auth = {};
  const runtime = createFirebaseAdminRuntime({ env, load: async () => ({
    applicationDefault: () => ({}), initializeApp(options, name) { initialized++; assert.equal(options.projectId, projectId); return { name }; }, getAuth: () => auth,
  }) });
  const results = await Promise.all([runtime.get(), runtime.get()]);
  assert.equal(results[0].auth, auth);
  assert.equal(initialized, 1);
  env.FIREBASE_AUTH_EMULATOR_HOST = "127.0.0.1:9099";
  await assert.rejects(runtime.get(), /forbidden/);
});

test("public configuration contains only the web allowlist and refuses missing actual values", () => {
  const config = buildPublicConfiguration({ ...publicEnv(), FIREBASE_ADMIN_CREDENTIALS_JSON: "DO-NOT-EXPOSE", PHOTO_FINDER_WORKER_SECRET: "WORKER-SECRET" });
  assert.equal(config.firebase.projectId, projectId);
  assert.equal(JSON.stringify(config).includes("DO-NOT-EXPOSE"), false);
  assert.equal(JSON.stringify(config).includes("WORKER-SECRET"), false);
  assert.throws(() => buildPublicConfiguration(serverEnv()), /Missing/);
  assert.throws(() => buildPublicConfiguration({ ...publicEnv(), FIREBASE_AUTH_EMULATOR_HOST: "127.0.0.1:9099" }), /forbidden/);
});

test("ID verification always asks the SDK to reject revoked sessions", async () => {
  let args;
  const runtime = { get: async () => ({ settings: { projectId }, auth: { verifyIdToken: async (...values) => { args = values; return claims(); } } }) };
  assert.equal((await verifyFirebaseIdToken("offline-id-token", runtime)).uid, "test-uid");
  assert.deepEqual(args, ["offline-id-token", true]);
});

test("invalid, expired, revoked, disabled, wrong-project and tenant sessions fail closed", async () => {
  for (const code of ["auth/id-token-expired", "auth/id-token-revoked", "auth/invalid-id-token", "auth/user-disabled", "auth/user-not-found"]) {
    const runtime = { get: async () => ({ settings: { projectId }, auth: { verifyIdToken: async () => { throw { code, message: "PRIVATE DETAILS" }; } } }) };
    await assert.rejects(verifyFirebaseIdToken("offline-token", runtime), error => error.statusCode === 401 && !error.message.includes("PRIVATE"));
  }
  for (const changes of [{ aud: "other-project" }, { iss: "https://example.test" }, { sub: "another-uid" }, { firebase: { tenant: "another-tenant" } }]) {
    const runtime = { get: async () => ({ settings: { projectId }, auth: { verifyIdToken: async () => ({ ...claims(), ...changes }) } }) };
    await assert.rejects(verifyFirebaseIdToken("offline-token", runtime), error => error.statusCode === 401);
  }
});

test("authentication outages are unavailable and never leak errors", async () => {
  const runtime = { get: async () => { throw new Error("PRIVATE KEY PATH"); } };
  await assert.rejects(verifyFirebaseIdToken("offline-token", runtime), error => error.statusCode === 503 && !error.message.includes("PRIVATE"));
  for (const token of ["", null, "space token", "a".repeat(16385)]) {
    await assert.rejects(verifyFirebaseIdToken(token, runtime), error => error.statusCode === 401);
  }
});

test("gallery sign-in tokens can only contain the fixed client scope", async () => {
  let args;
  const runtime = { get: async () => ({ settings: {}, auth: { createCustomToken: async (...values) => { args = values; return "offline-custom-token"; } } }) };
  assert.equal(await createGallerySignInToken({ clientId: "client_1", galleryId: "gallery_1", sessionId: "session_1", credentialVersion: 2 }, runtime), "offline-custom-token");
  assert.deepEqual(args, ["gallery-client:client_1", { actor_type: "gallery_client", gallery_id: "gallery_1", gallery_session_id: "session_1", credential_version: 2 }]);
  await assert.rejects(createGallerySignInToken({ clientId: "../escape", galleryId: "gallery", sessionId: "session", credentialVersion: 1 }, runtime));
});

function browserSdk() {
  const apps = [];
  const auth = { currentUser: null };
  const events = [];
  return { apps, auth, events, getApps: () => apps,
    initializeApp(options, name) { const app = { options, name }; apps.push(app); return app; },
    getAuth: () => auth, inMemoryPersistence: "memory",
    async setPersistence(_auth, persistence) { events.push(["persistence", persistence]); },
    async signInWithCustomToken(_auth, token) { events.push(["sign-in", token]); auth.currentUser = { getIdToken: async refresh => { events.push(["id-token", refresh]); return "offline-id-token"; } }; },
    async signInWithEmailAndPassword(_auth, email, password) { events.push(["password", email, password]); },
    async signOut() { auth.currentUser = null; },
  };
}

test("browser helper is lazy, uses memory persistence and sends exchanged ID tokens", async () => {
  const sdk = browserSdk();
  let loads = 0;
  const client = createFirebaseClient({ configuration: buildPublicConfiguration(publicEnv()), loadSdk: async () => { loads++; return sdk; } });
  assert.equal(loads, 0);
  await client.signInWithCustomToken("offline-custom-token");
  assert.equal(await client.getIdToken(true), "offline-id-token");
  assert.deepEqual(sdk.events, [["persistence", "memory"], ["sign-in", "offline-custom-token"], ["id-token", true]]);
  assert.equal(loads, 1);
  await client.signOut();
  await assert.rejects(client.getIdToken(), /Sign in/);
});

test("disabled browser helper never fetches the Firebase SDK", async () => {
  let loaded = false;
  const client = createFirebaseClient({ configuration: { enabled: false }, loadSdk: async () => { loaded = true; } });
  await assert.rejects(client.signInWithCustomToken("offline-token"), /not enabled/);
  assert.equal(loaded, false);
});

test("browser admin and gallery authentication have separate app contexts", async () => {
  const sdk = browserSdk();
  const config = buildPublicConfiguration(publicEnv());
  const admin = createFirebaseClient({ configuration: config, context: "admin", loadSdk: async () => sdk });
  const client = createFirebaseClient({ configuration: config, context: "client", loadSdk: async () => sdk });
  await admin.signInWithPassword("owner@example.test", "offline-test-password");
  await client.signInWithCustomToken("offline-custom-token");
  assert.deepEqual(sdk.apps.map(app => app.name), [`photo-finder-admin-${projectId}`, `photo-finder-client-${projectId}`]);
});

test("browser emulator configuration cannot run on a production page", async () => {
  const config = { enabled: true, firebase: buildPublicConfiguration(publicEnv()).firebase,
    emulatorMode: true, authEmulatorUrl: "http://127.0.0.1:9099" };
  let loaded = false;
  const client = createFirebaseClient({ configuration: config, loadSdk: async () => { loaded = true; } });
  await assert.rejects(client.signInWithCustomToken("offline-token"), /local demo projects/);
  assert.equal(loaded, false);
});

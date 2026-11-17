import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { stripTypeScriptTypes } from "node:module";
import { webcrypto } from "node:crypto";
import vm from "node:vm";
import test from "node:test";

// Exercise the actual Edge Function handler with synthetic private job data.
const source = await readFile(new URL("../../supabase/functions/relay/index.ts", import.meta.url), "utf8");
const code = stripTypeScriptTypes(source.replace(/^import \{ createClient \} from [^\n]+;\r?\n/m, ""));
const jobId = "10000000-0000-4000-8000-000000000001";
const eventId = "20000000-0000-4000-8000-000000000001";
const readyId = "30000000-0000-4000-8000-000000000001";
const pendingId = "30000000-0000-4000-8000-000000000002";
const otherId = "30000000-0000-4000-8000-000000000003";
const secret = "a".repeat(64);
const tokenHash = Buffer.from(await webcrypto.subtle.digest("SHA-256", new TextEncoder().encode(secret))).toString("hex");
const workerSecret = "c".repeat(64);
const workerTokenHash = Buffer.from(await webcrypto.subtle.digest("SHA-256", new TextEncoder().encode(workerSecret))).toString("hex");
const galleryId = "40000000-0000-4000-8000-000000000001";

function fixture({ status = "processing", expired = false, active = true, galleryActive = true, secondaryUploaded = false } = {}) {
  const tables = {
    photo_finder_jobs: [{ id: jobId, event_id: eventId, status, guest_token_hash: tokenHash,
      expires_at: new Date(Date.now() + (expired ? -1 : 1) * 3600000).toISOString(), error_message: null }],
    photo_finder_events: [{ id: eventId, active, name: "Test event", current_folder: "Current folder",
      worker_token_hash: workerTokenHash }],
    photo_finder_gallery_sessions: [{ id: "session", gallery_id: galleryId, token_hash: tokenHash,
      expires_at: new Date(Date.now() + (expired ? -1 : 1) * 3600000).toISOString() }],
    photo_finder_gallery_links: [{ id: galleryId, event_id: eventId, active: galleryActive,
      expires_at: new Date(Date.now() + 3600000).toISOString() }],
    photo_finder_gallery_photos: [{ event_id: eventId, photo_id: 11, filename: "a.jpg", source_path: "Wedding/a.jpg" },
      { event_id: eventId, photo_id: 12, filename: "b.jpg", source_path: "Reception/b.jpg" },
      { event_id: "different-event", photo_id: 13, filename: "private-other-event.jpg" }],
    photo_finder_gallery_jobs: [{ id: "50000000-0000-4000-8000-000000000001",
      gallery_id: galleryId, event_id: eventId, photo_id: 11, filename: "a.jpg",
      variant: "thumbnail", status: "ready", object_path: "gallery/test/a.webp",
      expires_at: new Date(Date.now() + 3600000).toISOString() }],
    photo_finder_results: [
      { id: readyId, job_id: jobId, photo_id: 11, filename: "ready.jpg", similarity: 0.8, face_count: 1, uploaded: true, object_path: "private/ready" },
      { id: pendingId, job_id: jobId, photo_id: 12, filename: "pending.jpg", similarity: 0.9, face_count: 4, uploaded: secondaryUploaded, object_path: "private/pending" },
      { id: otherId, job_id: "different-job", photo_id: 13, filename: "other.jpg", similarity: 0.99, face_count: 1, uploaded: true, object_path: "private/other" },
    ],
  };
  const downloads = [], removals = [];
  const db = {
    from(table) {
      let rows = [...tables[table]], columns, options, single = false, values, maximum, inserting = false;
      const query = {
        select(value, config) { columns = value.split(","); options = config; return query; },
        insert(value) { values = value; inserting = true; return query; },
        update(value) { values = value; return query; },
        eq(key, value) { rows = rows.filter(row => row[key] === value); return query; },
        in(key, value) { rows = rows.filter(row => value.includes(row[key])); return query; },
        gt(key, value) { rows = rows.filter(row => row[key] > value); return query; },
        order(key, { ascending = true } = {}) { rows.sort((a, b) => (a[key] - b[key]) * (ascending ? 1 : -1)); return query; },
        limit(value) { maximum = value; return query; },
        maybeSingle() { single = true; return query; },
        single() { single = true; return query; },
        then(resolve, reject) {
          if (maximum !== undefined) rows = rows.slice(0, maximum);
          if (inserting) {
            rows = [{ ...values, id: galleryId }];
            tables[table].push(rows[0]);
          } else if (values) {
            for (const row of rows) Object.assign(row, values);
          }
          const data = columns ? rows.map(row => Object.fromEntries(columns.map(column => [column, row[column]]))) : null;
          return Promise.resolve({ data: single ? data[0] || null : options?.head ? null : data,
            count: rows.length, error: null }).then(resolve, reject);
        },
      };
      return query;
    },
    storage: { from(bucket) {
      assert.equal(bucket, "photo-finder-private");
      return {
        async download(path) { downloads.push(path); return { data: new Blob(["synthetic-photo"], { type: "image/jpeg" }), error: null }; },
        async remove(paths) { removals.push(...paths); return { data: [], error: null }; },
      };
    } },
  };
  let handler;
  vm.runInNewContext(code, {
    createClient: () => db, Deno: { env: { get: () => "test-placeholder" }, serve(value) { handler = value; } },
    Response, Request, Blob, File, URL, TextEncoder, crypto: webcrypto, console: { error() {} },
  });
  return {
    downloads, removals,
    galleryRequest(token = secret, after = "") {
      return handler(new Request(`https://relay.example/functions/v1/relay/guest/gallery/photos?limit=1${after ? `&after=${after}` : ""}`, {
        headers: { "X-Gallery-Session": token },
      }));
    },
    workerRequest(payload, token = workerSecret) {
      return handler(new Request("https://relay.example/functions/v1/relay/worker/gallery/create", {
        method: "POST",
        headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
        body: JSON.stringify(payload),
      }));
    },
    galleryLinks: tables.photo_finder_gallery_links,
    fileRequest(token = secret) {
      return handler(new Request("https://relay.example/functions/v1/relay/guest/gallery/jobs/50000000-0000-4000-8000-000000000001/file", {
        headers: { "X-Gallery-Session": token },
      }));
    },
    request(path = "", token = secret) {
      return handler(new Request(`https://relay.example/functions/v1/relay/guest/jobs/${jobId}${path}`, {
        headers: { "X-Job-Token": token },
      }));
    },
  };
}

test("processing jobs expose only ready metadata, with the full matched count", async () => {
  const response = await fixture().request();
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("cache-control"), "no-store");
  const body = await response.json();
  assert.equal(body.status, "processing");
  assert.equal(body.photos_detected, 2);
  assert.equal(body.photos_ready, 1);
  assert.deepEqual(body.photos, [{ id: readyId, filename: "ready.jpg", similarity: 0.8,
    face_count: 1, folder_name: "Wedding" }]);
  assert.equal(JSON.stringify(body).includes("object_path"), false);
});

test("AI search labels matches with their separate source folders", async () => {
  const body = await (await fixture({ secondaryUploaded: true }).request()).json();
  assert.deepEqual(body.photos.map(photo => [photo.filename, photo.folder_name]), [
    ["pending.jpg", "Reception"],
    ["ready.jpg", "Wedding"],
  ]);
  assert.equal("photo_id" in body.photos[0], false);
});

test("ready photos can be viewed during processing using their job token", async () => {
  const api = fixture();
  const response = await api.request(`/photos/${readyId}`);
  assert.equal(response.status, 200);
  assert.equal(await response.text(), "synthetic-photo");
  assert.equal(response.headers.get("cache-control"), "no-store");
  assert.deepEqual(api.downloads, ["private/ready"]);
});

test("pending and other-job photos cannot be read", async () => {
  for (const id of [pendingId, otherId]) {
    const api = fixture();
    assert.equal((await api.request(`/photos/${id}`)).status, 404);
    assert.deepEqual(api.downloads, []);
  }
});

test("missing, incorrect, expired and revoked-event credentials cannot read ready photos", async () => {
  for (const [settings, token, expected] of [
    [{}, "", 401], [{}, "b".repeat(64), 404],
    [{ expired: true }, secret, 404], [{ active: false }, secret, 403],
  ]) {
    const api = fixture(settings);
    assert.equal((await api.request(`/photos/${readyId}`, token)).status, expected);
    assert.deepEqual(api.downloads, []);
  }
});

test("pending and failed jobs cannot serve ready photo bytes", async () => {
  for (const status of ["pending", "failed", "uploading"]) {
    const api = fixture({ status });
    assert.equal((await api.request(`/photos/${readyId}`)).status, 404);
    assert.deepEqual(api.downloads, []);
  }
});

test("completed jobs keep the existing photo response contract", async () => {
  const api = fixture({ status: "completed" });
  const body = await (await api.request()).json();
  assert.equal(body.photos_detected, 1);
  assert.equal(body.photos.length, 1);
  assert.equal((await api.request(`/photos/${readyId}`)).status, 200);
});

test("full-gallery CSV context and count stay within the authenticated event", async () => {
  const response = await fixture().galleryRequest();
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.gallery_id, galleryId);
  assert.equal(body.event_id, eventId);
  assert.equal(body.event_name, "Current folder");
  assert.equal(body.photo_count, 2);
  assert.equal(body.next_cursor, "11");
  assert.deepEqual(body.photos, [{ id: 11, filename: "a.jpg", folder_name: "Wedding" }]);
  const reception = await (await fixture().galleryRequest(secret, "11")).json();
  assert.deepEqual(reception.photos, [{ id: 12, filename: "b.jpg", folder_name: "Reception" }]);
  assert.equal(JSON.stringify(body).includes("private-other-event"), false);
});

test("gallery file delivery is atomically consumed to avoid concurrent 409 responses", async () => {
  const api = fixture();
  const responses = await Promise.all([api.fileRequest(), api.fileRequest()]);
  assert.deepEqual(responses.map(response => response.status).sort(), [200, 409]);
  assert.deepEqual(api.downloads, ["gallery/test/a.webp"]);
  assert.deepEqual(api.removals, ["gallery/test/a.webp"]);
});

test("gallery creation accepts a valid event date as the password", async () => {
  const api = fixture();
  const response = await api.workerRequest({ event_date: "2026-11-18", expires_days: 30 });
  assert.equal(response.status, 200);
  assert.equal(api.galleryLinks.length, 2);
  assert.notEqual(api.galleryLinks[1].password_hash, "2026-11-18");
});

test("gallery creation rejects invalid event dates and mixed password inputs", async () => {
  for (const payload of [
    { event_date: "2026-02-30", expires_days: 30 },
    { event_date: "2026-11-18", password: "a-strong-password", expires_days: 30 },
  ]) {
    const api = fixture();
    assert.equal((await api.workerRequest(payload)).status, 400);
    assert.equal(api.galleryLinks.length, 1);
  }
});

test("full-gallery metadata and CSV context require a live private session", async () => {
  for (const [settings, token, expected] of [[{}, "", 401], [{}, "b".repeat(64), 401],
    [{ expired: true }, secret, 401], [{ galleryActive: false }, secret, 403], [{ active: false }, secret, 403]]) {
    assert.equal((await fixture(settings).galleryRequest(token)).status, expected);
  }
});

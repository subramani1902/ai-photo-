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

function fixture({ status = "processing", expired = false, active = true } = {}) {
  const tables = {
    photo_finder_jobs: [{ id: jobId, event_id: eventId, status, guest_token_hash: tokenHash,
      expires_at: new Date(Date.now() + (expired ? -1 : 1) * 3600000).toISOString(), error_message: null }],
    photo_finder_events: [{ id: eventId, active }],
    photo_finder_results: [
      { id: readyId, job_id: jobId, filename: "ready.jpg", similarity: 0.8, face_count: 1, uploaded: true, object_path: "private/ready" },
      { id: pendingId, job_id: jobId, filename: "pending.jpg", similarity: 0.9, face_count: 4, uploaded: false, object_path: "private/pending" },
      { id: otherId, job_id: "different-job", filename: "other.jpg", similarity: 0.99, face_count: 1, uploaded: true, object_path: "private/other" },
    ],
  };
  const downloads = [];
  const db = {
    from(table) {
      let rows = [...tables[table]], columns, options, single = false;
      const query = {
        select(value, config) { columns = value.split(","); options = config; return query; },
        eq(key, value) { rows = rows.filter(row => row[key] === value); return query; },
        gt(key, value) { rows = rows.filter(row => row[key] > value); return query; },
        order(key, { ascending }) { rows.sort((a, b) => (a[key] - b[key]) * (ascending ? 1 : -1)); return query; },
        maybeSingle() { single = true; return query; },
        then(resolve, reject) {
          const data = rows.map(row => Object.fromEntries(columns.map(column => [column, row[column]])));
          return Promise.resolve({ data: single ? data[0] || null : options?.head ? null : data,
            count: rows.length, error: null }).then(resolve, reject);
        },
      };
      return query;
    },
    storage: { from(bucket) {
      assert.equal(bucket, "photo-finder-private");
      return { async download(path) { downloads.push(path); return { data: new Blob(["synthetic-photo"], { type: "image/jpeg" }), error: null }; } };
    } },
  };
  let handler;
  vm.runInNewContext(code, {
    createClient: () => db, Deno: { env: { get: () => "test-placeholder" }, serve(value) { handler = value; } },
    Response, Request, Blob, File, URL, TextEncoder, crypto: webcrypto, console: { error() {} },
  });
  return {
    downloads,
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
  assert.deepEqual(body.photos, [{ id: readyId, filename: "ready.jpg", similarity: 0.8, face_count: 1 }]);
  assert.equal(JSON.stringify(body).includes("object_path"), false);
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

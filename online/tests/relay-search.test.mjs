import assert from "node:assert/strict";
import test from "node:test";
import { setImmediate } from "node:timers/promises";
import { followRelaySearch, requestRelay } from "../relay-search.mjs";

const photo = (id, face_count = 1) => ({ id, filename: `${id}.jpg`, face_count });

test("shows ready photos before completion, deduplicates polling and keeps categories", async () => {
  const a = photo("a"), b = photo("b", 3), c = photo("c");
  const states = [
    { status: "processing", photos_detected: 3, photos: [a] },
    { status: "processing", photos_detected: 3, photos: [b, a] },
    { status: "completed", photos: [b, a, c] },
  ];
  const loaded = [], progress = [];
  let calls = 0;
  await followRelaySearch({
    getStatus: async () => states[calls++],
    onStatus: status => progress.push(status.status),
    loadPhoto: async (item, index) => loaded.push({ item, index, calls }),
    wait: async () => { await setImmediate(); },
  });
  assert.equal(loaded[0].calls, 1, "the first photo must appear during processing");
  assert.deepEqual(loaded.map(item => [item.item.id, item.index]), [["a", 0], ["b", 1], ["c", 2]]);
  assert.equal(loaded[1].item.face_count, 3);
  assert.deepEqual(progress, ["processing", "processing", "completed"]);
});

test("bounds downloads and waits for the final queued photos", async () => {
  let active = 0, maximum = 0, loaded = 0;
  const result = await followRelaySearch({
    getStatus: async () => ({ status: "completed", photos: Array.from({ length: 41 }, (_, i) => photo(String(i))) }),
    onStatus() {}, concurrency: 4,
    async loadPhoto() {
      maximum = Math.max(maximum, ++active);
      await setImmediate();
      active--; loaded++;
    },
  });
  assert.equal(maximum, 4);
  assert.equal(loaded, 41);
  assert.equal(active, 0);
  assert.deepEqual(result.failures, []);
});

test("supports an older relay that publishes results only on completion", async () => {
  let calls = 0;
  const loaded = [];
  await followRelaySearch({
    getStatus: async () => ++calls === 1
      ? { status: "processing", photos_detected: 1, photos: [] }
      : { status: "completed", photos: [photo("a")] },
    onStatus() {}, loadPhoto: async item => loaded.push(item.id), wait: async () => {},
  });
  assert.deepEqual(loaded, ["a"]);
});

test("a failed photo does not block other ready photos", async () => {
  const loaded = [];
  const result = await followRelaySearch({
    getStatus: async () => ({ status: "completed", photos: [photo("bad"), photo("good")] }),
    onStatus() {},
    loadPhoto: async item => { if (item.id === "bad") throw new Error("Transfer interrupted"); loaded.push(item.id); },
  });
  assert.deepEqual(loaded, ["good"]);
  assert.equal(result.failures.length, 1);
});

test("a new search cancels polling and queued downloads from the old search", async () => {
  const controller = new AbortController();
  let loads = 0;
  const run = followRelaySearch({
    signal: controller.signal, concurrency: 1,
    getStatus: async () => ({ status: "processing", photos: [photo("a"), photo("b")] }),
    onStatus() {},
    loadPhoto: async () => { loads++; controller.abort(); },
  });
  await assert.rejects(run, { name: "AbortError" });
  assert.equal(loads, 1);
});

test("failed jobs stop instead of publishing their photos", async () => {
  await assert.rejects(followRelaySearch({
    getStatus: async () => ({ status: "failed", message: "Please use one face.", photos: [photo("a")] }),
    onStatus() { assert.fail("must not publish failed jobs"); },
    loadPhoto() { assert.fail("must not fetch failed results"); },
  }), /Please use one face/);
});

test("empty matches finish without waiting for a photo", async () => {
  const result = await followRelaySearch({
    getStatus: async () => ({ status: "completed", photos: [] }),
    onStatus() {}, loadPhoto() { assert.fail("no matches"); },
  });
  assert.equal(result.status.photos.length, 0);
});

test("transient read failures retry without dropping the job authorization header", async () => {
  const headers = { "X-Job-Token": "synthetic-secret" };
  let calls = 0;
  const response = await requestRelay("https://relay.example/guest/jobs/example", { headers }, {
    async fetcher(url, options) {
      assert.equal(options.headers, headers);
      assert.equal(options.cache, "no-store");
      if (++calls === 1) throw new TypeError("Network interrupted");
      return new Response(calls === 2 ? "temporary" : "ready", { status: calls === 2 ? 503 : 200 });
    }, wait: async () => {},
  });
  assert.equal(await response.text(), "ready");
  assert.equal(calls, 3);
});

test("invalid credentials and non-idempotent selfie submissions are never retried", async () => {
  for (const [options, status] of [[{}, 403], [{ method: "POST" }, 503]]) {
    let calls = 0;
    await assert.rejects(requestRelay("https://relay.example/guest/submit", options, {
      async fetcher() { calls++; return new Response('{"detail":"Unavailable"}', { status }); },
      wait: async () => assert.fail("must not retry"),
    }), /Unavailable/);
    assert.equal(calls, 1);
  }
});

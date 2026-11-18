import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { setImmediate } from "node:timers/promises";
import test from "node:test";
import vm from "node:vm";
import { createSelectionCsv, restoreSelection } from "../selection-csv.mjs";

class Element {
  constructor() {
    this.children = []; this.dataset = {}; this.handlers = new Map(); this.attributes = new Map();
    this.hidden = false; this.disabled = false; this.className = ""; this.textContent = "";
    const classes = new Set();
    this.classList = { toggle(name, on) { if (on) classes.add(name); else classes.delete(name); } };
  }
  get childElementCount() { return this.children.length; }
  addEventListener(type, handler) { this.handlers.set(type, handler); }
  emit(type) { return this.handlers.get(type)?.({ currentTarget: this, preventDefault() {} }); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(name, value) { this.attributes.set(name, value); }
  removeAttribute(name) { this.attributes.delete(name); }
  remove() {}
  click() { this.clicked = true; }
  querySelector(tag) { return this.children.find(child => child.tagName === tag); }
}

function surface() {
  const elements = new Map(), blobs = [], observers = [], revoked = [], windowEvents = new Map();
  const folders = ["wedding", "reception"].map(folder => {
    const button = new Element(); button.dataset.galleryFolder = folder; return button;
  });
  const element = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  class BrowserURL extends URL {
    static createObjectURL(blob) { blobs.push(blob); return "blob:test-" + blobs.length; }
    static revokeObjectURL(url) { revoked.push(url); }
  }
  const document = { body: new Element(), querySelector: element,
    querySelectorAll: selector => selector === "[data-gallery-folder]" ? folders : [],
    createElement: tag => Object.assign(new Element(), { tagName: tag }), createTextNode: text => ({ textContent: text }) };
  return { element, blobs, observers, folders, revoked, windowEvents, globals: { document, URL: BrowserURL, URLSearchParams, Blob, AbortController,
    setTimeout(callback) { queueMicrotask(callback); return 0; }, clearTimeout() {}, console,
    window: { addEventListener(name, handler) { windowEvents.set(name, handler); }, IntersectionObserver: true, innerHeight: 800 },
    IntersectionObserver: class {
      constructor(callback) { this.callback = callback; this.targets = []; observers.push(this); }
      observe(target) { this.targets.push(target); }
      unobserve() {} disconnect() {}
      intersect(target) { this.callback([{ target, isIntersecting: true }]); }
    },
    createSelectionCsv, restoreSelection } };
}

const galleryCode = (await readFile(new URL("../gallery.js", import.meta.url), "utf8")).replace(/^import [^\n]+;\r?\n/gm, "");
const clientCode = (await readFile(new URL("../client-event-gallery.js", import.meta.url), "utf8")).replace(/^import [^\n]+;\r?\n/gm, "").replace("export function", "function");
const settle = async () => { await setImmediate(); await setImmediate(); };

function openGallery(fetcher) {
  const data = new Map([
    ["photo-finder-gallery-token", "a".repeat(64)], ["photo-finder-gallery-session-token", "a".repeat(64)],
    ["photo-finder-gallery-session", "b".repeat(64)],
  ]);
  const s = surface();
  s.api = vm.runInNewContext(galleryCode + "\n({ fetchPhoto, clearSession });", {
    ...s.globals, sessionStorage: { getItem: key => data.get(key) ?? null,
      setItem: (key, value) => data.set(key, value), removeItem: key => data.delete(key) },
    location: { hash: "", pathname: "/gallery.html", origin: "https://studio.example" },
    history: { replaceState() {} }, fetch: fetcher,
  });
  return s;
}

test("ready private previews need one file request; revisiting a folder reuses the image after access validation", async () => {
  const calls = [];
  const photo = { id: 101, filename: "a.jpg", folder_name: "Wedding", preview_job_id: "50000000-0000-4000-8000-000000000001" };
  const s = openGallery(async (url, options) => {
    calls.push(url);
    assert.equal(options.headers["X-Gallery-Session"], "b".repeat(64));
    if (url.includes("/photos?")) return Response.json({ gallery_id: "gallery-test", photos: url.includes("folder=wedding") ? [photo] : [], next_cursor: null });
    assert.ok(url.endsWith(photo.preview_job_id + "/file"));
    return new Response(new Blob(["private preview"]));
  });
  await settle();
  const frame = s.observers[0].targets[0];
  s.observers[0].intersect(frame); await settle();
  assert.equal(frame.dataset.previewState, "ready");
  assert.equal(calls.length, 2, "List and file only: no job POST or status polling for cached previews");
  const imageUrl = frame.querySelector("img").src;
  await s.folders[1].emit("click"); await s.folders[0].emit("click");
  const revisited = s.observers.at(-1).targets[0];
  s.observers.at(-1).intersect(revisited); await settle();
  assert.equal(revisited.querySelector("img").src, imageUrl);
  assert.equal(calls.filter(url => url.endsWith("/file")).length, 1);
  assert.equal(calls.filter(url => url.includes("/photos?")).length, 3, "Revalidate gallery access on each folder change");
  s.windowEvents.get("pagehide")();
  assert.ok(s.revoked.includes(imageUrl), "Private image memory is released when leaving");
});

test("stale cached previews regenerate, and cached originals never use the thumbnail shortcut", async () => {
  const calls = [];
  const photo = { id: 101, filename: "a.jpg", preview_job_id: "50000000-0000-4000-8000-000000000001" };
  const s = openGallery(async (url, options) => {
    if (url.includes("/photos?")) return Response.json({ gallery_id: "gallery-test", photos: [] });
    calls.push([url, options.method]);
    if (url.includes(photo.preview_job_id + "/file")) return Response.json({ detail: "Expired" }, { status: 409 });
    if (options.method === "POST") return Response.json({ job_id: "new-job", status: "ready" });
    assert.ok(url.endsWith("/new-job/file"));
    return new Response(new Blob(["preview"]));
  });
  await settle();
  await s.api.fetchPhoto({ ...photo }, "thumbnail");
  assert.equal(calls.length, 3, "A vanished cache is regenerated without status polling once ready");
  calls.length = 0;
  await s.api.fetchPhoto(photo, "original");
  assert.equal(calls[0][1], "POST", "Original downloads must be independently authorized and queued");
});

test("newly visible photos move ahead of offscreen previews in a long scrolling queue", async () => {
  const started = [], releases = new Map();
  const photos = Array.from({ length: 6 }, (_, i) => ({ id: i + 1, filename: i + ".jpg",
    preview_job_id: "50000000-0000-4000-8000-" + String(i + 1).padStart(12, "0") }));
  const s = openGallery(async url => {
    if (url.includes("/photos?")) return Response.json({ gallery_id: "gallery-test", photos });
    const id = Number(url.split("/").at(-2).slice(-12));
    started.push(id);
    await new Promise(resolve => releases.set(id, resolve));
    return new Response(new Blob(["preview"]));
  });
  await settle();
  const observer = s.observers[0];
  for (const [index, frame] of observer.targets.entries()) {
    frame.getBoundingClientRect = () => index === 5 ? { top: 100, bottom: 400 } : { top: -5000, bottom: -4700 };
    observer.intersect(frame);
  }
  assert.deepEqual(started, [1, 2, 3]);
  releases.get(1)(); await settle();
  assert.equal(started[3], 6, "The visible photo must not wait behind the offscreen queue");
  for (const release of releases.values()) release();
  await settle();
  for (const release of releases.values()) release();
  await settle();
});

test("full-gallery choices survive pagination and refresh; CSV includes only favourites", async () => {
  const data = new Map([
    ["photo-finder-gallery-token", "a".repeat(64)], ["photo-finder-gallery-session-token", "a".repeat(64)],
    ["photo-finder-gallery-session", "b".repeat(64)],
  ]);
  const storage = { getItem: key => data.get(key) ?? null, setItem: (key, value) => data.set(key, value), removeItem: key => data.delete(key) };
  const context = { gallery_id: "gallery-test", event_id: "event-test", event_name: "Synthetic event", photo_count: 3 };
  const a = { id: 101, filename: "a.jpg" }, b = { id: 102, filename: "b.jpg" }, c = { id: 103, filename: "c.jpg" };
  let expired = false;
  async function fetcher(url, options) {
    assert.equal(options.headers["X-Gallery-Session"], "b".repeat(64));
    assert.equal(url.includes("b".repeat(64)), false);
    if (expired) return new Response('{"detail":"Expired"}', { status: 401 });
    return Response.json({ ...context, photos: url.includes("after=") ? [c] : [a, b], next_cursor: url.includes("after=") ? null : "102" });
  }
  function open() {
    const s = surface();
    vm.runInNewContext(galleryCode, { ...s.globals, sessionStorage: storage,
      location: { hash: "", pathname: "/gallery.html", origin: "https://studio.example" },
      history: { replaceState() {} }, fetch: fetcher });
    return s;
  }
  const s = open(); await settle();
  const grid = s.element("#photo-grid");
  const cards = () => grid.children.flatMap(section => section.children[1].children);
  assert.equal(grid.children[0].children[0].textContent, "Synthetic event");
  await cards()[0].children[1].children[1].emit("click");
  await s.element("#load-more").emit("click");
  await cards()[2].children[1].children[1].emit("click");
  assert.equal(s.element("#favourite-count").textContent, "2");
  await s.element("#favourite-filter").emit("click");
  assert.equal(cards()[1].hidden, true);
  await s.element("#export-favourites").emit("click");
  const csv = await s.blobs[0].text();
  assert.ok(csv.includes('"a.jpg"') && csv.includes('"c.jpg"'));
  assert.equal(csv.includes('"b.jpg"'), false);
  const restored = open(); await settle();
  assert.equal(restored.element("#favourite-count").textContent, "2");
  await restored.element("#favourite-filter").emit("click");
  assert.equal(restored.element("#photo-grid").children[0].children[1].childElementCount, 3);
  expired = true;
  await restored.element("#export-favourites").emit("click");
  assert.equal(restored.blobs.length, 0, "Expired access must not export a saved selection");
  assert.equal(restored.element("#photo-grid").childElementCount, 0);
  assert.equal(restored.element("#login-panel").hidden, false);
});

test("gallery access errors keep the session and show the access message", async () => {
  const data = new Map([
    ["photo-finder-gallery-token", "a".repeat(64)], ["photo-finder-gallery-session-token", "a".repeat(64)],
    ["photo-finder-gallery-session", "b".repeat(64)],
  ]);
  const storage = { getItem: key => data.get(key) ?? null, setItem: (key, value) => data.set(key, value), removeItem: key => data.delete(key) };
  const s = surface();
  const fetcher = async () => Response.json({ detail: "Gallery link expired" }, { status: 403 });
  vm.runInNewContext(galleryCode, { ...s.globals, sessionStorage: storage,
    location: { hash: "", pathname: "/gallery.html", origin: "https://studio.example" },
    history: { replaceState() {} }, fetch: fetcher });
  await settle();
  assert.equal(s.element("#login-panel").hidden, true);
  assert.equal(s.element("#gallery-panel").hidden, false);
  assert.equal(s.element("#gallery-message").textContent, "Gallery link expired");
  assert.equal(data.get("photo-finder-gallery-session"), "b".repeat(64));
});

test("gallery retries full queues and consumed previews and groups photos by folder", async () => {
  const data = new Map([
    ["photo-finder-gallery-token", "a".repeat(64)], ["photo-finder-gallery-session-token", "a".repeat(64)],
    ["photo-finder-gallery-session", "b".repeat(64)],
  ]);
  const storage = { getItem: key => data.get(key) ?? null, setItem: (key, value) => data.set(key, value), removeItem: key => data.delete(key) };
  const s = surface();
  let jobRequests = 0, fileRequests = 0;
  const fetcher = async (url, options) => {
    const path = new URL(url).pathname;
    if (path.endsWith("/photos")) return Response.json({
      gallery_id: "gallery-test", event_id: "event-test", event_name: "Synthetic event",
      photos: [{ id: 101, filename: "a.jpg", folder_name: "Wedding" }], next_cursor: null,
    });
    if (path.endsWith("/jobs") && options.method === "POST") {
      jobRequests++;
      if (jobRequests === 1) return Response.json({ detail: "The gallery is busy." }, { status: 429 });
      return Response.json({ job_id: jobRequests === 2 ? "preview-job-1" : "preview-job-2" }, { status: 202 });
    }
    if (path.endsWith("/preview-job-1") || path.endsWith("/preview-job-2")) return Response.json({ status: "ready" });
    if (path.endsWith("/preview-job-1/file")) {
      fileRequests++;
      return Response.json({ detail: "Preview was already delivered" }, { status: 409 });
    }
    if (path.endsWith("/preview-job-2/file")) {
      fileRequests++;
      return new Response(new Blob(["preview"]));
    }
    assert.fail(`Unexpected request: ${path}`);
  };
  vm.runInNewContext(galleryCode, { ...s.globals, sessionStorage: storage,
    location: { hash: "", pathname: "/gallery.html", origin: "https://studio.example" },
    history: { replaceState() {} }, fetch: fetcher });
  await settle();
  assert.equal(s.element("#photo-grid").children[0].children[0].textContent, "Wedding");
  s.observers[0].intersect(s.observers[0].targets[0]);
  for (let attempt = 0; attempt < 20 && (jobRequests < 3 || fileRequests < 2); attempt++) await settle();
  assert.equal(jobRequests, 3);
  assert.equal(fileRequests, 2);
});

test("Firebase CSV loads favourites from every page even if their photos were never displayed", async () => {
  const s = surface();
  const calls = [];
  const fetcher = async (url, options) => {
    assert.equal(options.headers.Authorization, "Bearer verified-test-token");
    calls.push(url);
    if (url.endsWith("/events/3")) return Response.json({ event_id: 3, event_name: "Synthetic event", permissions: ["view", "favourite"], expires_at: "2027-01-01T00:00:00Z" });
    if (url.includes("category=all")) return Response.json({ photos: [], next_after: null });
    return Response.json(url.includes("after=100") ? { photos: [{ id: 200, filename: "second-page.jpg" }], next_after: null }
      : { photos: [{ id: 100, filename: "first-page.jpg" }], next_after: 100 });
  };
  const create = vm.runInNewContext(clientCode + "\ncreateClientGallery;", { ...s.globals, fetch: fetcher });
  const gallery = create({ client: { getIdToken: async () => "verified-test-token" }, configuration: { photoApiBaseUrl: "https://private-api.example" } });
  await gallery.open(3);
  await s.element("#export-client-favourites").emit("click");
  const csv = await s.blobs[0].text();
  assert.ok(csv.includes("first-page.jpg") && csv.includes("second-page.jpg"));
  assert.equal(calls.filter(url => url.includes("category=favourites")).length, 2);
  assert.equal(calls.some(url => url.includes("/preview")), false);
});

test("client gallery accepts a folder name and continues using its internal event ID", async () => {
  const s = surface();
  const calls = [];
  const fetcher = async url => {
    calls.push(url);
    if (url.endsWith("/events/Private%20wedding")) return Response.json({
      event_id: 3, event_name: "Private wedding", permissions: ["view"],
      expires_at: "2027-01-01T00:00:00Z",
    });
    if (url.endsWith("/events/3/photos?after=0&limit=24&category=all")) {
      return Response.json({ photos: [], next_after: null });
    }
    assert.fail(`Unexpected request: ${url}`);
  };
  const create = vm.runInNewContext(clientCode + "\ncreateClientGallery;", {
    ...s.globals, fetch: fetcher, window: { addEventListener() {} },
  });
  const gallery = create({
    client: { getIdToken: async () => "verified-test-token" },
    configuration: { photoApiBaseUrl: "https://private-api.example" },
  });
  await gallery.open("Private wedding");
  assert.ok(calls.some(url => url.endsWith("/events/Private%20wedding")));
  assert.ok(calls.some(url => url.endsWith("/events/3/photos?after=0&limit=24&category=all")));
  assert.equal(s.element("#client-event-name").textContent, "Private wedding");
});

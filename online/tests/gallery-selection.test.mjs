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
}

function surface() {
  const elements = new Map(), blobs = [];
  const element = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  class BrowserURL extends URL {
    static createObjectURL(blob) { blobs.push(blob); return "blob:test-" + blobs.length; }
    static revokeObjectURL() {}
  }
  const document = { body: new Element(), querySelector: element, querySelectorAll: () => [],
    createElement: () => new Element(), createTextNode: text => ({ textContent: text }) };
  return { element, blobs, globals: { document, URL: BrowserURL, URLSearchParams, Blob, AbortController,
    setTimeout() {}, console, window: { addEventListener() {}, IntersectionObserver: true },
    IntersectionObserver: class { observe() {} unobserve() {} disconnect() {} },
    createSelectionCsv, restoreSelection } };
}

const galleryCode = (await readFile(new URL("../gallery.js", import.meta.url), "utf8")).replace(/^import [^\n]+;\r?\n/gm, "");
const clientCode = (await readFile(new URL("../client-event-gallery.js", import.meta.url), "utf8")).replace(/^import [^\n]+;\r?\n/gm, "").replace("export function", "function");
const settle = async () => { await setImmediate(); await setImmediate(); };

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
  await grid.children[0].children[1].children[1].emit("click");
  await s.element("#load-more").emit("click");
  await grid.children[2].children[1].children[1].emit("click");
  assert.equal(s.element("#favourite-count").textContent, "2");
  await s.element("#favourite-filter").emit("click");
  assert.equal(grid.children[1].hidden, true);
  await s.element("#export-favourites").emit("click");
  const csv = await s.blobs[0].text();
  assert.ok(csv.includes('"a.jpg"') && csv.includes('"c.jpg"'));
  assert.equal(csv.includes('"b.jpg"'), false);
  const restored = open(); await settle();
  assert.equal(restored.element("#favourite-count").textContent, "2");
  await restored.element("#favourite-filter").emit("click");
  assert.equal(restored.element("#photo-grid").childElementCount, 3);
  expired = true;
  await restored.element("#export-favourites").emit("click");
  assert.equal(restored.blobs.length, 0, "Expired access must not export a saved selection");
  assert.equal(restored.element("#photo-grid").childElementCount, 0);
  assert.equal(restored.element("#login-panel").hidden, false);
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

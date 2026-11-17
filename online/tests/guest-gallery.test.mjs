import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { setImmediate } from "node:timers/promises";
import test from "node:test";
import vm from "node:vm";
import { followRelaySearch } from "../relay-search.mjs";

class Element {
  constructor() {
    this.children = []; this.dataset = {}; this.handlers = new Map(); this.attributes = new Map();
    this.hidden = false; this.disabled = false; this.className = ""; this.textContent = "";
    this.firstChild = { textContent: "" };
    this.classList = { add() {}, remove() {}, toggle() {} };
  }
  addEventListener(type, handler) { this.handlers.set(type, handler); }
  emit(type) { return this.handlers.get(type)?.({ currentTarget: this }); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(name, value) { this.attributes.set(name, value); }
  removeAttribute(name) { this.attributes.delete(name); }
  contains(item) { return this === item; }
  showModal() { this.open = true; }
  close() { this.open = false; }
  querySelector() { return this.counter ??= new Element(); }
  querySelectorAll(selector) {
    const name = selector.slice(1);
    return this.children.flatMap(child => [
      ...(child.className.split(" ").includes(name) ? [child] : []), ...child.querySelectorAll(selector),
    ]);
  }
}

test("actual guest page reveals partial results, keeps filters and enables downloads at completion", async () => {
  const elements = new Map();
  const element = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  const categories = ["all", "candid", "group"].map(category => {
    const button = new Element(); button.dataset.category = category; return button;
  });
  const document = {
    body: new Element(), addEventListener() {}, createElement: () => new Element(),
    querySelector: element,
    querySelectorAll: selector => selector === ".category-filter" ? categories : [],
  };
  element("#results").hidden = true;
  const a = { id: "a", filename: "candid.jpg", face_count: 1, folder_name: "Wedding" };
  const b = { id: "b", filename: "group.jpg", face_count: 4, folder_name: "Reception" };
  let polls = 0, sawPartial = false;
  const request = async (url, options = {}) => {
    const path = new URL(url).pathname.split("/relay").pop();
    if (path === "/guest/showcase") return Response.json({ events: [{ id: "event", name: "Synthetic event" }] });
    if (path === "/guest/submit") return Response.json({ job_id: "job", job_token: "private-test-token" });
    assert.equal(options.headers["X-Job-Token"], "private-test-token");
    if (path.endsWith("/photos/a") || path.endsWith("/photos/b")) return new Response(new Blob(["synthetic"], { type: "image/jpeg" }));
    polls++;
    return Response.json(polls === 1 ? { status: "pending", photos: [] }
      : polls === 2 ? { status: "processing", photos_detected: 2, photos: [a] }
        : { status: "completed", photos_detected: 2, photos: [a, b] });
  };
  const code = (await readFile(new URL("../main.js", import.meta.url), "utf8")).replace(/^import [^\n]+;\r?\n/gm, "");
  vm.runInNewContext(code, {
    document, window: { addEventListener() {}, scrollTo() {} },
    location: { href: "https://studio.example/", pathname: "/" }, history: { replaceState() {} },
    URL, URLSearchParams, File, FormData, AbortController, console, setTimeout,
    createPhotoArchive() { assert.fail("No ZIP request was made"); },
    requestRelay: request,
    followRelaySearch: options => followRelaySearch({ ...options, wait: async () => {
      await setImmediate();
      if (polls !== 2) return;
      sawPartial = true;
      assert.equal(element("#results").hidden, false);
      assert.equal(element("#form-section").hidden, true);
      assert.equal(element("#loading-animation").open, false);
      const wedding = element("#photos").children.find(section => section.dataset.folderName === "Wedding");
      assert.ok(wedding);
      const [card] = wedding.children[1].children;
      assert.equal(card.dataset.category, "candid");
      assert.equal(card.children[0].disabled, false, "Ready photos must open during processing");
      assert.equal(card.children[2].children[1].disabled, false, "Individual downloads must already work");
      assert.equal(element("#download-all").disabled, true, "Full ZIP must wait for all matches");
      await categories[2].emit("click");
      assert.equal(card.hidden, true);
    } }),
  });
  await setImmediate();
  element("#consent").checked = true;
  element("#selfie").files = [new File(["synthetic selfie"], "test.jpg", { type: "image/jpeg" })];
  await element("#selfie").emit("change");
  await element("#search").emit("click");
  assert.equal(sawPartial, true);
  assert.deepEqual(element("#photos").children.map(section => section.dataset.folderName), ["Wedding", "Reception"]);
  const reception = element("#photos").children[1];
  assert.equal(reception.hidden, false, "A newly arriving Reception photo respects the active filter");
  assert.equal(reception.children[1].children.length, 1);
  assert.equal(element("#download-all").disabled, false);
  assert.equal(element("#gallery-photos-fetched").textContent, "2");
  assert.equal(element("#gallery-load-status").textContent, "All 2 photos are ready.");
  await element("#again").emit("click");
  assert.equal(element("#photos").children.length, 0);
  assert.equal(element("#results").hidden, true);
});

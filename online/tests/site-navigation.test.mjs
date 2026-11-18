import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("public pages link to each other and identify the current destination", async () => {
  const pages = [
    ["find-photos.html", "Find event photos"],
    ["account.html", "Client account"],
    ["gallery.html", "Private gallery"],
  ];
  for (const [file, currentLabel] of pages) {
    const html = await readFile(new URL(`../${file}`, import.meta.url), "utf8");
    assert.match(html, /<nav class="site-nav" aria-label="Site navigation">/);
    assert.match(html, /<a href="\.\/">Home<\/a>/);
    assert.match(html, /<a href="\.\/find-photos\.html"(?: aria-current="page")?>Find event photos<\/a>/);
    assert.match(html, /<a href="\.\/account\.html"(?: aria-current="page")?>Client account<\/a>/);
    assert.match(html, /<a href="\.\/gallery\.html"(?: aria-current="page")?>Private gallery<\/a>/);
    assert.match(html, new RegExp(`<a href="[^"]+" aria-current="page">${currentLabel}<\\/a>`));
  }
  const home = await readFile(new URL("../index.html", import.meta.url), "utf8");
  for (const destination of ["find-photos.html", "account.html", "gallery.html"]) {
    assert.ok(home.includes(`href="./${destination}"`), `Homepage must connect to ${destination}`);
  }
});

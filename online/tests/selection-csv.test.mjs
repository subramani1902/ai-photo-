import assert from "node:assert/strict";
import test from "node:test";
import { buildSelectionCsv, createSelectionCsv, restoreSelection } from "../selection-csv.mjs";

test("editor CSV preserves filenames, order and Unicode and quotes commas and double quotes", () => {
  const csv = buildSelectionCsv([{ id: 9, filename: 'Photo, "one".jpg' }, { id: 12, filename: "தமிழ்.jpg" }, { id: 9, filename: "duplicate.jpg" }], { eventId: 3, eventName: "Wedding" });
  assert.ok(csv.startsWith('\ufeff"selection_number"'));
  assert.ok(csv.includes('"1","3","Wedding","9","","Photo, ""one"".jpg","yes"\r\n'));
  assert.ok(csv.includes('"2","3","Wedding","12","","தமிழ்.jpg","yes"\r\n'));
  assert.equal(csv.includes("duplicate.jpg"), false);
  assert.equal(csv.split("\r\n").length, 4);
});

test("CSV neutralizes spreadsheet formulas and excludes tokens, passwords and local paths", () => {
  const csv = buildSelectionCsv([{ id: 1, filename: "=SUM(1,2).jpg", path: "G:\\private\\image.jpg", password: "do-not-export", token: "secret-token" }], { eventId: 3, eventName: "  @formula" });
  assert.ok(csv.includes('"\'=SUM(1,2).jpg"'));
  assert.ok(csv.includes('"\'  @formula"'));
  for (const secret of ["G:", "private", "do-not-export", "secret-token"]) assert.equal(csv.includes(secret), false);
});

test("invalid or oversized metadata cannot produce an editor selection", () => {
  for (const photo of [{ id: 0, filename: "a.jpg" }, { id: "1", filename: "a.jpg" }, { id: 1, filename: "bad\nname.jpg" }, { id: 1, filename: "" }]) {
    assert.throws(() => buildSelectionCsv([photo]));
  }
  assert.throws(() => buildSelectionCsv(Array(10001).fill({ id: 1, filename: "a.jpg" })));
});

test("restored favourites accept only scoped photo metadata and recover from corrupt storage", () => {
  assert.equal(restoreSelection("broken JSON").size, 0);
  assert.equal(restoreSelection('{}').size, 0);
  const restored = restoreSelection(JSON.stringify([{ id: 12, filename: "a.jpg", token: "never-keep" }, { id: -1, filename: "bad" }, { id: 13, filename: "b.jpg" }]));
  assert.deepEqual([...restored.values()], [{ id: 12, filename: "a.jpg" }, { id: 13, filename: "b.jpg" }]);
});

test("CSV download has the Windows-friendly UTF-8 content type", async () => {
  const blob = createSelectionCsv([{ id: 1, filename: "a.jpg" }], { eventId: 3 });
  assert.equal(blob.type, "text/csv;charset=utf-8");
  assert.ok((await blob.text()).includes('"a.jpg"'));
});

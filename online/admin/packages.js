import { weddingPackages } from "../studio-packages.mjs";
import { putJson, readFields, setFields } from "./forms.js";

export function createPackageManager(request) {
  const form = document.querySelector("#package-form"), editors = document.querySelector("#package-editors");
  const message = document.querySelector("#package-message"), reload = document.querySelector("#reload-packages");
  const template = document.querySelector("#package-template");
  const save = form.querySelector('[type="submit"]');
  let revision, session = 0, dirty = false, busy = false;

  function setBusy(value) {
    busy = value; save.disabled = value; reload.disabled = value;
    for (const editor of editors.children) editor.disabled = value;
  }

  function showPackages(data) {
    revision = data.revision;
    editors.replaceChildren();
    for (const [key, name] of Object.entries(weddingPackages)) {
      const editor = template.content.firstElementChild.cloneNode(true);
      editor.dataset.package = key;
      editor.querySelector("legend").textContent = name;
      setFields(editor, data[key]);
      editors.append(editor);
    }
    dirty = false;
    form.hidden = false;
  }

  async function open() {
    if (busy || revision != null) return;
    const version = session;
    setBusy(true);
    message.textContent = "Loading packages…";
    try {
      const data = await request("/admin/packages");
      if (version === session) {
        showPackages(data);
        reload.hidden = true;
        message.textContent = "";
      }
    } catch (error) {
      if (version === session) {
        reload.hidden = false;
        message.textContent = error.message;
      }
    } finally {
      if (version === session) setBusy(false);
    }
  }

  reload.addEventListener("click", () => {
    if (dirty && !window.confirm("Discard the unsaved package changes and reload the latest prices?")) return;
    revision = undefined;
    open();
  });
  form.addEventListener("input", () => { dirty = true; });
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (busy || revision == null) return;
    const version = session;
    setBusy(true);
    try {
      const changes = Object.fromEntries(Array.from(editors.children, editor => [editor.dataset.package, readFields(editor)]));
      message.textContent = "Saving public packages…";
      const data = await request("/admin/packages", putJson({ revision, ...changes }));
      if (version === session) {
        revision = data.revision;
        dirty = false;
        reload.hidden = true;
        message.textContent = "Packages saved. Public pricing can take up to a minute to refresh.";
      }
    } catch (error) {
      if (version === session) {
        message.textContent = error.message;
        reload.hidden = error.status !== 409;
      }
    } finally {
      if (version === session) setBusy(false);
    }
  });

  return {
    open,
    isDirty: () => dirty,
    reset() {
      session++; revision = undefined; dirty = false; setBusy(false);
      editors.replaceChildren(); form.hidden = true; reload.hidden = true; message.textContent = "";
    },
  };
}

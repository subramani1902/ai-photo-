// Older event QR codes point at /. Preserve both query and fragment tokens.
(() => {
  const current = new URL(window.location.href);
  const fragment = new URLSearchParams(current.hash.slice(1));
  if (!current.searchParams.has("token") && !fragment.has("token")) return;
  const finder = new URL("./find-photos.html", current);
  finder.search = current.search;
  finder.hash = current.hash;
  window.location.replace(finder.href);
})();

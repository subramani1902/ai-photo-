// Keep the existing job token contract while showing each available result.
function pause(milliseconds, signal) {
  return new Promise((resolve, reject) => {
    signal?.throwIfAborted();
    const abort = () => {
      clearTimeout(timer);
      reject(signal.reason);
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", abort);
      resolve();
    }, milliseconds);
    signal?.addEventListener("abort", abort, { once: true });
  });
}

export async function requestRelay(url, options = {}, { fetcher = fetch, wait = pause } = {}) {
  const retries = !options.method || options.method.toUpperCase() === "GET" ? 2 : 0;
  for (let attempt = 0; ; attempt++) {
    options.signal?.throwIfAborted();
    let response;
    try {
      response = await fetcher(url, { ...options, cache: "no-store" });
    } catch (error) {
      options.signal?.throwIfAborted();
      if (attempt < retries) { await wait(1000 * (attempt + 1), options.signal); continue; }
      throw new Error("The photo search service could not be reached. Check your internet connection and try again.");
    }
    if (response.ok) return response;
    if (attempt < retries && (response.status === 408 || response.status === 429 || response.status >= 500)) {
      await response.body?.cancel();
      await wait(1000 * (attempt + 1), options.signal);
      continue;
    }
    const data = await response.json().catch(() => ({}));
    throw new Error(data.detail || "Unable to complete your request. Please try again.");
  }
}

export async function followRelaySearch({
  getStatus, onStatus, loadPhoto, signal, concurrency = 4,
  interval = 2000, wait = pause,
}) {
  if (!Number.isSafeInteger(concurrency) || concurrency < 1 || concurrency > 8) {
    throw new Error("Invalid photo concurrency.");
  }
  const seen = new Set(), queue = [], active = new Set();
  const failures = [];
  let stopped = false;
  function pump() {
    while (!stopped && !signal?.aborted && queue.length && active.size < concurrency) {
      const { photo, index } = queue.shift();
      const task = Promise.resolve()
        .then(() => {
          signal?.throwIfAborted();
          return loadPhoto(photo, index);
        })
        .catch(error => { failures.push(error); })
        .finally(() => { active.delete(task); pump(); });
      active.add(task);
    }
  }
  try {
    while (true) {
      signal?.throwIfAborted();
      const status = await getStatus();
      signal?.throwIfAborted();
      if (status.status === "failed") {
        throw new Error(status.message || "Could not process the selfie.");
      }
      onStatus(status);
      // Older relays return an empty list until completion and still work.
      for (const photo of status.photos || []) {
        if (seen.has(photo.id)) continue;
        const index = seen.size;
        seen.add(photo.id);
        queue.push({ photo, index });
      }
      pump();
      if (status.status === "completed") {
        while (active.size) await Promise.race(active);
        signal?.throwIfAborted();
        return { status, failures };
      }
      await wait(interval, signal);
    }
  } finally {
    stopped = true;
  }
}

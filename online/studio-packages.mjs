export const weddingPackages = Object.freeze({ silver: "Silver", gold: "Gold", platinum: "Platinum" });

const inr = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", minimumFractionDigits: 0, maximumFractionDigits: 2 });

export function formatRupees(paise) {
  return inr.format(paise / 100);
}

export function trustedPhotoApiOrigin(value) {
  try {
    const url = new URL(value);
    if (url.protocol === "https:" && !url.username && !url.password && url.pathname === "/" && !url.search && !url.hash) return url.origin;
  } catch { /* An unconfigured gateway leaves the website's default packages available. */ }
  return null;
}

export async function loadWeddingPackages(request = fetch) {
  let origin;
  try {
    const { default: config } = await import("./firebase-config.js");
    origin = trustedPhotoApiOrigin(config.photoApiBaseUrl);
    if (origin) {
      const response = await request(origin + "/api/studio/packages", { signal: AbortSignal.timeout(3500) });
      if (response.ok) return { packages: await response.json(), brochure: false };
    }
  } catch { /* Use the published brochure if the studio gateway is unavailable. */ }
  const response = await request(new URL("./studio-packages.json", import.meta.url));
  if (!response.ok) throw new Error("Wedding packages could not load.");
  return { packages: await response.json(), brochure: Boolean(origin) };
}

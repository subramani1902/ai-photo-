// Public portfolio content only. Never point these images at a private gallery.
// Replace sample files and set sample:false when adding approved studio work.
export const studio = Object.freeze({
  name: "SM Photography",
  location: "Chennai",
  instagram: "https://www.instagram.com/sm_photography_world/",
  instagramHandle: "@sm_photography_world",
  phone: "+919840108540",
  phoneLabel: "+91 98401 08540",
});

export const portfolio = Object.freeze([
  { id: "celebration", title: "The joy of being together", category: "celebrations", label: "Celebrations",
    image: "./assets/home/sample-celebration.webp", alt: "Sample wedding portrait of a couple in traditional dress",
    position: "50% 40%", sample: true },
  { id: "bride", title: "A moment, just for you", category: "weddings", label: "Wedding portraits",
    image: "./assets/home/sample-bride.webp", alt: "Sample monochrome bridal portrait with traditional jewellery",
    position: "50% 45%", sample: true },
  { id: "wedding", title: "All the beautiful chaos", category: "weddings", label: "Candid moments",
    image: "./assets/home/sample-wedding.webp", alt: "Sample candid photograph of a groom celebrating with guests",
    position: "50% 50%", sample: true },
  { id: "together", title: "Just the two of you", category: "pre-weddings", label: "Pre-weddings",
    image: "./assets/home/sample-together.webp", alt: "Sample couple portrait in warm evening sunlight",
    position: "50% 45%", sample: true },
]);

export function validatePublishedPortfolio(value) {
  if (value?.schema_version !== 1 || !Array.isArray(value.photos) || value.photos.length !== portfolio.length) throw new Error("Invalid portfolio.");
  return portfolio.map(original => {
    const photo = value.photos.find(item => item?.id === original.id);
    if (!photo || typeof photo.sample !== "boolean" || photo.category !== original.category || photo.label !== original.label
      || typeof photo.image !== "string" || !/^\.\/assets\/home\/(?:sample-[a-z]+|published\/[a-f0-9]{32})\.webp$/.test(photo.image)
      || !["title", "alt", "position"].every(key => typeof photo[key] === "string")
      || !photo.title.trim() || photo.title.length > 100 || !photo.alt.trim() || photo.alt.length > 250
      || !/^\d{1,3}% \d{1,3}%$/.test(photo.position) || photo.position.split(" ").some(part => Number(part.slice(0, -1)) > 100)) throw new Error("Invalid portfolio.");
    let instagramUrl;
    if (photo.instagramUrl) {
      const url = new URL(photo.instagramUrl);
      if (url.protocol !== "https:" || !["instagram.com", "www.instagram.com"].includes(url.hostname) || url.username || url.password) throw new Error("Invalid Instagram link.");
      instagramUrl = url.href;
    }
    return { id: original.id, category: original.category, label: original.label, title: photo.title,
      alt: photo.alt, image: photo.image, position: photo.position, sample: photo.sample, ...(instagramUrl ? { instagramUrl } : {}) };
  });
}

export async function loadPublishedPortfolio(request = fetch) {
  try {
    const response = await request("./homepage-portfolio.json", { cache: "no-cache", signal: AbortSignal.timeout(6000) });
    if (!response.ok) return portfolio;
    return validatePublishedPortfolio(await response.json());
  } catch { return portfolio; }
}

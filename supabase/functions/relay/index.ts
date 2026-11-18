// Supabase Edge Function: public guest API and authenticated laptop-worker API.
// SUPABASE_SERVICE_ROLE_KEY is injected by Supabase and never sent to clients.
import { createClient } from "npm:@supabase/supabase-js@2.49.8";

const db = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!, {
  auth: { persistSession: false, autoRefreshToken: false },
});
const bucket = "photo-finder-private";
const cors = { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "authorization, content-type, x-event-token, x-event-id, x-job-token, x-job-lease, x-gallery-session, x-gallery-job-lease, apikey", "Access-Control-Allow-Methods": "GET, POST, OPTIONS", "Cache-Control": "no-store" };
const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { ...cors, "Content-Type": "application/json" } });
const hash = async (value: string) => Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value)))).map((b) => b.toString(16).padStart(2, "0")).join("");
const token = () => Array.from(crypto.getRandomValues(new Uint8Array(32))).map((b) => b.toString(16).padStart(2, "0")).join("");
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const fail = (message: string, status = 400): never => { throw new Response(message, { status }); };
const check = <T>(result: { data: T; error: unknown }): T => { if (result.error) throw result.error; return result.data; };

async function readForm(req: Request, limit: number) {
  const declared = Number(req.headers.get("content-length") || 0);
  if (declared > limit) fail("Upload is too large", 413);
  const reader = req.body?.getReader();
  if (!reader) fail("Upload is empty");
  const chunks: Uint8Array[] = [];
  let total = 0;
  while (true) {
    const { value, done } = await reader!.read();
    if (done) break;
    total += value.length;
    if (total > limit) { await reader!.cancel(); fail("Upload is too large", 413); }
    chunks.push(value);
  }
  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.length;
  }
  const body = new Blob([bytes.buffer]);
  return new Request("https://internal.invalid", { method: "POST", headers: { "Content-Type": req.headers.get("content-type") || "" }, body }).formData();
}

async function cleanExpired() {
  // Grace permits any already-authorized in-flight file transfer to finish.
  const cutoff = new Date(Date.now() - 30 * 60_000).toISOString();
  const expired = check(await db.from("photo_finder_jobs").select("id,selfie_path").lt("expires_at", cutoff).order("expires_at").limit(200));
  let cleaned = 0;
  const jobs = expired || [];
  for (let batch = 0; batch < jobs.length; batch += 5) {
    await Promise.all(jobs.slice(batch, batch + 5).map(async (old) => {
      const paths = [old.selfie_path];
      for (let start = 0; ; start += 500) {
        const page = check(await db.from("photo_finder_results").select("object_path").eq("job_id", old.id).order("id").range(start, start + 499));
        paths.push(...(page || []).map((item) => item.object_path));
        if (!page || page.length < 500) break;
      }
      for (let offset = 0; offset < paths.length; offset += 100) {
        check(await db.storage.from(bucket).remove(paths.slice(offset, offset + 100)));
      }
      check(await db.from("photo_finder_jobs").delete().eq("id", old.id).lt("expires_at", cutoff));
      cleaned++;
    }));
  }
  const expiredGalleryJobs = check(await db.from("photo_finder_gallery_jobs")
    .select("id,object_path")
    .lt("expires_at", cutoff)
    .order("expires_at")
    .limit(200));
  for (const old of expiredGalleryJobs || []) {
    if (old.object_path) check(await db.storage.from(bucket).remove([old.object_path]));
    check(await db.from("photo_finder_gallery_jobs")
      .delete()
      .eq("id", old.id)
      .lt("expires_at", cutoff));
    cleaned++;
  }
  check(await db.from("photo_finder_gallery_sessions")
    .delete()
    .lt("expires_at", new Date().toISOString()));
  check(await db.from("photo_finder_gallery_login_attempts")
    .delete()
    .lt("attempted_at", new Date(Date.now() - 24 * 60 * 60_000).toISOString()));
  return cleaned;
}

async function getEvent(secret: string, kind: "guest" | "worker") {
  if (!/^[a-f0-9]{64}$/i.test(secret)) fail("Invalid credentials", 401);
  const column = kind === "guest" ? "guest_token_hash" : "worker_token_hash";
  const result = await db.from("photo_finder_events").select("id,name,current_folder,is_current").eq(column, await hash(secret)).eq("active", true).maybeSingle();
  const event = check(result);
  if (!event) fail("Invalid credentials", 401);
  return event!;
}

async function guestJob(id: string, secret: string) {
  if (!uuid.test(id) || !/^[a-f0-9]{64}$/i.test(secret)) fail("Invalid access", 401);
  const job = check(await db.from("photo_finder_jobs").select("id,event_id,status,error_message,expires_at").eq("id", id).eq("guest_token_hash", await hash(secret)).gt("expires_at", new Date().toISOString()).maybeSingle());
  if (!job) fail("Request expired or not found", 404);
  const event = check(await db.from("photo_finder_events").select("id").eq("id", job!.event_id).eq("active", true).maybeSingle());
  if (!event) fail("Event unavailable", 403);
  return job!;
}

async function workerJob(eventId: string, id: string, lease: string) {
  if (!uuid.test(id) || !uuid.test(lease)) fail("Invalid lease", 403);
  const job = check(await db.from("photo_finder_jobs").select("id,event_id,selfie_path,status,lease_id").eq("id", id).eq("event_id", eventId).eq("status", "processing").eq("lease_id", lease).gt("lease_expires_at", new Date().toISOString()).gt("expires_at", new Date().toISOString()).maybeSingle());
  if (!job) fail("Request lease expired", 409);
  return job!;
}

const fromHex = (value: string) => new Uint8Array(value.match(/.{2}/g)!.map((byte) => Number.parseInt(byte, 16)));
const toHex = (value: ArrayBuffer) => Array.from(new Uint8Array(value)).map((byte) => byte.toString(16).padStart(2, "0")).join("");

async function deriveGalleryPassword(password: string, salt: string, iterations: number) {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(password),
    "PBKDF2",
    false,
    ["deriveBits"],
  );
  const derived = await crypto.subtle.deriveBits({
    name: "PBKDF2",
    salt: fromHex(salt),
    iterations,
    hash: "SHA-256",
  }, key, 256);
  return toHex(derived);
}

function isEventDate(value: string) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00.000Z`);
  return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
}

function secureEqual(left: string, right: string) {
  if (left.length !== right.length) return false;
  let difference = 0;
  for (let index = 0; index < left.length; index++) {
    difference |= left.charCodeAt(index) ^ right.charCodeAt(index);
  }
  return difference === 0;
}

async function gallerySession(req: Request) {
  const secret = req.headers.get("x-gallery-session") || "";
  if (!/^[a-f0-9]{64}$/i.test(secret)) fail("Gallery session expired", 401);
  const session = check(await db.from("photo_finder_gallery_sessions")
    .select("id,gallery_id")
    .eq("token_hash", await hash(secret))
    .gt("expires_at", new Date().toISOString())
    .maybeSingle());
  if (!session) fail("Gallery session expired", 401);
  const gallery = check(await db.from("photo_finder_gallery_links")
    .select("id,event_id")
    .eq("id", session.gallery_id)
    .eq("active", true)
    .gt("expires_at", new Date().toISOString())
    .maybeSingle());
  if (!gallery) fail("Gallery link expired", 403);
  const event = check(await db.from("photo_finder_events")
    .select("id,name,current_folder")
    .eq("id", gallery.event_id)
    .eq("active", true)
    .maybeSingle());
  if (!event) fail("Gallery unavailable", 403);
  return { session, gallery, event };
}

async function galleryWorkerJob(eventId: string, id: string, lease: string) {
  if (!uuid.test(id) || !uuid.test(lease)) fail("Invalid gallery lease", 403);
  const job = check(await db.from("photo_finder_gallery_jobs")
    .select("id,gallery_id,event_id,photo_id,filename,variant,status,lease_id")
    .eq("id", id)
    .eq("event_id", eventId)
    .eq("status", "processing")
    .eq("lease_id", lease)
    .gt("lease_expires_at", new Date().toISOString())
    .gt("expires_at", new Date().toISOString())
    .maybeSingle());
  if (!job) fail("Gallery job lease expired", 409);
  const gallery = check(await db.from("photo_finder_gallery_links")
    .select("id")
    .eq("id", job.gallery_id)
    .eq("active", true)
    .gt("expires_at", new Date().toISOString())
    .maybeSingle());
  if (!gallery) fail("Gallery is no longer active", 403);
  return job!;
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
  try {
    const path = new URL(req.url).pathname.split("/relay").pop() || "/";
    const auth = req.headers.get("authorization")?.replace(/^Bearer\s+/i, "") || "";

    if (path === "/guest/submit" && req.method === "POST") {
      const eventToken = req.headers.get("x-event-token") || "";
      const eventId = req.headers.get("x-event-id") || "";
      let event: { id: string; name: string } | null;
      if (eventToken) {
        event = await getEvent(eventToken, "guest");
      } else {
        if (!uuid.test(eventId)) fail("Choose an event to search", 400);
        event = check(await db.from("photo_finder_events")
          .select("id,name")
          .eq("id", eventId)
          .eq("active", true)
          .maybeSingle());
      }
      const activeEvent = event ?? fail("Event unavailable", 404);
      const jobId = crypto.randomUUID(), jobSecret = token(), selfiePath = `selfies/${jobId}`;
      const reserved = await db.rpc("photo_finder_reserve_job", { p_event_id: activeEvent.id, p_job_id: jobId, p_guest_token_hash: await hash(jobSecret), p_selfie_path: selfiePath });
      if (reserved.error) throw reserved.error;
      if (!reserved.data) fail("Too many requests. Please try again shortly.", 429);
      try {
        const form = await readForm(req, 11 * 1024 * 1024);
        const fileEntry = form.get("file");
        const file = fileEntry instanceof File
          ? fileEntry
          : fail("Upload a JPG, PNG or WEBP image under 10 MB.");
        if (!["image/jpeg", "image/png", "image/webp"].includes(file.type) || file.size < 1 || file.size > 10 * 1024 * 1024) fail("Upload a JPG, PNG or WEBP image under 10 MB.");
        check(await db.storage.from(bucket).upload(selfiePath, file, { contentType: file.type, upsert: false }));
        check(await db.from("photo_finder_jobs").update({ status: "pending" }).eq("id", jobId).eq("status", "uploading"));
      } catch (error) {
        // Keep the tracked row for the scheduler if immediate removal fails.
        await db.from("photo_finder_jobs").update({ status: "failed", error_message: "Upload failed." }).eq("id", jobId).eq("status", "uploading");
        await db.storage.from(bucket).remove([selfiePath]);
        throw error;
      }
      return json({ job_id: jobId, job_token: jobSecret, event_name: activeEvent.name });
    }

    if (path === "/guest/showcase" && req.method === "GET") {
      const eventToken = req.headers.get("x-event-token") || "";
      const currentCutoff = new Date(Date.now() - 2 * 60_000).toISOString();
      const events = eventToken
        ? [await getEvent(eventToken, "guest")]
        : check(await db.from("photo_finder_events")
          .select("id,name,current_folder,is_current")
          .eq("active", true)
          .eq("is_current", true)
          .gt("current_updated_at", currentCutoff)
          .limit(1));
      if (!events?.length) return json({ events: [] });

      const showcaseRows = check(await db.from("photo_finder_showcases")
        .select("event_id,photo_id,filename,object_path")
        .in("event_id", events.map((event) => event.id))
        .order("photo_id", { ascending: false }));
      const rowsByEvent = new Map<string, Array<{ photo_id: number; filename: string; object_path: string }>>();
      for (const row of showcaseRows || []) {
        const rows = rowsByEvent.get(row.event_id) || [];
        if (rows.length < 3) rows.push(row);
        rowsByEvent.set(row.event_id, rows);
      }
      const supabaseUrl = Deno.env.get("SUPABASE_URL")!.replace(/\/+$/, "");
      const relayBase = `${supabaseUrl}/functions/v1/relay`;
      const publicEvents = events.map((event) => ({
        id: event.id,
        name: event.is_current && event.current_folder ? event.current_folder : event.name,
        photos: (rowsByEvent.get(event.id) || []).map((row) => ({
          id: row.photo_id,
          filename: row.filename,
          url: `${relayBase}/guest/showcase/${event.id}/${row.photo_id}`,
        })),
      }));
      return json({ events: publicEvents });
    }

    const showcasePhotoMatch = path.match(/^\/guest\/showcase\/([0-9a-f-]{36})\/(\d+)$/i);
    if (showcasePhotoMatch && req.method === "GET") {
      if (!uuid.test(showcasePhotoMatch[1])) fail("Preview unavailable", 404);
      const event = check(await db.from("photo_finder_events")
        .select("id")
        .eq("id", showcasePhotoMatch[1])
        .eq("active", true)
        .maybeSingle());
      const activeEvent = event ?? fail("Preview unavailable", 404);
      const photoId = Number(showcasePhotoMatch[2]);
      if (!Number.isSafeInteger(photoId) || photoId <= 0) fail("Preview unavailable", 404);
      const preview = check(await db.from("photo_finder_showcases")
        .select("object_path")
        .eq("event_id", activeEvent.id)
        .eq("photo_id", photoId)
        .maybeSingle());
      const activePreview = preview ?? fail("Preview unavailable", 404);
      const image = check(await db.storage.from(bucket).download(activePreview.object_path));
      if (!image) fail("Preview unavailable", 404);
      return new Response(image, {
        headers: {
          ...cors,
          "Cache-Control": "public, max-age=300",
          "Content-Type": "image/webp",
          "X-Content-Type-Options": "nosniff",
        },
      });
    }

    const guestMatch = path.match(/^\/guest\/jobs\/([0-9a-f-]+)(?:\/photos\/([0-9a-f-]+))?$/i);
    if (guestMatch && req.method === "GET") {
      const job = await guestJob(guestMatch[1], req.headers.get("x-job-token") || "");
      if (guestMatch[2]) {
        if (!["processing", "completed"].includes(job.status) || !uuid.test(guestMatch[2])) fail("Photo not available", 404);
        const photo = check(await db.from("photo_finder_results").select("object_path,filename").eq("id", guestMatch[2]).eq("job_id", job.id).eq("uploaded", true).maybeSingle());
        if (!photo) fail("Photo not found", 404);
        const blob = check(await db.storage.from(bucket).download(photo!.object_path))
          ?? fail("Photo not found", 404);
        return new Response(blob, { headers: { ...cors, "Content-Type": blob.type || "application/octet-stream", "X-Content-Type-Options": "nosniff" } });
      }
      let photos: Array<{ id: string; filename: string; similarity: number; face_count: number; folder_name: string }> = [];
      let photosDetected = 0;
      if (job.status === "processing" || job.status === "completed") {
        const matchedPhotos = check(await db.from("photo_finder_results")
          .select("id,photo_id,filename,similarity,face_count")
          .eq("job_id", job.id)
          .eq("uploaded", true)
          .order("similarity", { ascending: false })) || [];
        if (matchedPhotos.length) {
          const sources = check(await db.from("photo_finder_gallery_photos")
            .select("photo_id,source_path")
            .eq("event_id", job.event_id)
            .in("photo_id", matchedPhotos.map((photo) => photo.photo_id))) || [];
          const folderByPhoto = new Map(sources.map((source) => [
            Number(source.photo_id),
            source.source_path.includes("/") ? source.source_path.split("/")[0] : "Other photos",
          ]));
          photos = matchedPhotos.map((photo) => ({
            id: photo.id,
            filename: photo.filename,
            similarity: photo.similarity,
            face_count: photo.face_count,
            folder_name: folderByPhoto.get(Number(photo.photo_id)) || "Other photos",
          }));
        }
        photosDetected = photos.length;
      }
      if (job.status !== "completed") {
        const progress = await db.from("photo_finder_results")
          .select("id", { count: "exact", head: true })
          .eq("job_id", job.id);
        check(progress);
        photosDetected = progress.count || 0;
      }
      return json({
        status: job.status,
        message: job.error_message,
        photos,
        photos_detected: photosDetected,
        photos_ready: photos.length,
      });
    }

    if (path === "/guest/gallery/login" && req.method === "POST") {
      const payload = await req.json() as { token?: unknown; password?: unknown } | null;
      const shareToken = payload?.token;
      const password = payload?.password;
      if (typeof shareToken !== "string" || !/^[a-f0-9]{64}$/i.test(shareToken) ||
          typeof password !== "string" || password.length < 1 || password.length > 128) {
        fail("Invalid gallery link or password", 403);
      }
      const gallery = check(await db.from("photo_finder_gallery_links")
        .select("id,event_id,password_salt,password_hash,password_iterations,expires_at")
        .eq("token_hash", await hash(shareToken))
        .eq("active", true)
        .gt("expires_at", new Date().toISOString())
        .maybeSingle());
      if (!gallery) fail("Invalid gallery link or password", 403);
      const recentAttempts = await db.from("photo_finder_gallery_login_attempts")
        .select("id", { count: "exact", head: true })
        .eq("gallery_id", gallery.id)
        .gt("attempted_at", new Date(Date.now() - 15 * 60_000).toISOString());
      check(recentAttempts);
      if ((recentAttempts.count || 0) >= 10) fail("Too many attempts. Try again later.", 429);
      const suppliedHash = await deriveGalleryPassword(password, gallery.password_salt, gallery.password_iterations);
      if (!secureEqual(suppliedHash, gallery.password_hash)) {
        check(await db.from("photo_finder_gallery_login_attempts").insert({ gallery_id: gallery.id }));
        fail("Invalid gallery link or password", 403);
      }
      const activeEvent = check(await db.from("photo_finder_events")
        .select("id")
        .eq("id", gallery.event_id)
        .eq("active", true)
        .maybeSingle());
      if (!activeEvent) fail("Gallery unavailable", 403);
      check(await db.from("photo_finder_gallery_login_attempts").delete().eq("gallery_id", gallery.id));
      const sessionToken = token();
      const sessionExpiresAt = new Date(Date.now() + 12 * 60 * 60_000).toISOString();
      check(await db.from("photo_finder_gallery_sessions").insert({
        gallery_id: gallery.id,
        token_hash: await hash(sessionToken),
        expires_at: sessionExpiresAt,
      }));
      return json({ session_token: sessionToken, expires_at: sessionExpiresAt });
    }

    if (path === "/guest/gallery/photos" && req.method === "GET") {
      const { gallery, event } = await gallerySession(req);
      const url = new URL(req.url);
      const limitText = url.searchParams.get("limit") || "60";
      const afterText = url.searchParams.get("after") || "0";
      const folder = url.searchParams.get("folder") || "";
      if (folder && !["wedding", "reception"].includes(folder)) fail("Invalid folder", 400);
      if (!/^\d+$/.test(limitText) || !/^\d+$/.test(afterText)) fail("Invalid page", 400);
      const limit = Number(limitText), after = Number(afterText);
      if (!Number.isSafeInteger(limit) || limit < 1 || limit > 100 ||
          !Number.isSafeInteger(after) || after < 0) fail("Invalid page", 400);
      let query = db.from("photo_finder_gallery_photos")
        .select("photo_id,filename,source_path")
        .eq("event_id", gallery.event_id)
        .order("photo_id")
        .limit(limit);
      if (after > 0) query = query.gt("photo_id", after);
      if (folder) query = query.ilike("source_path", folder + "/%");
      const photos = check(await query);
      let totalQuery = db.from("photo_finder_gallery_photos")
        .select("photo_id", { count: "exact", head: true })
        .eq("event_id", gallery.event_id);
      if (folder) totalQuery = totalQuery.ilike("source_path", folder + "/%");
      const total = await totalQuery;
      check(total);
      const rows = photos || [];
      const cachedPreviews = rows.length ? check(await db.from("photo_finder_gallery_jobs")
        .select("id,photo_id")
        .eq("gallery_id", gallery.id)
        .eq("variant", "thumbnail")
        .eq("status", "ready")
        .gt("expires_at", new Date().toISOString())
        .in("photo_id", rows.map((photo) => photo.photo_id))) : [];
      const readyPreviews = new Map((cachedPreviews || []).map((job) => [Number(job.photo_id), job.id]));
      return json({
        gallery_id: gallery.id,
        event_id: gallery.event_id,
        event_name: event.current_folder || event.name,
        photo_count: total.count || 0,
        photos: rows.map((photo) => ({
          id: Number(photo.photo_id),
          filename: photo.filename,
          ...(readyPreviews.has(Number(photo.photo_id)) ? { preview_job_id: readyPreviews.get(Number(photo.photo_id)) } : {}),
          folder_name: photo.source_path.includes("/")
            ? photo.source_path.split("/")[0]
            : event.current_folder || event.name,
        })),
        next_cursor: rows.length === limit ? String(rows[rows.length - 1].photo_id) : null,
      });
    }

    if (path === "/guest/gallery/jobs" && req.method === "POST") {
      const { gallery } = await gallerySession(req);
      const payload = await req.json() as { photo_id?: unknown; variant?: unknown } | null;
      const photoId = payload?.photo_id;
      const variant = payload?.variant;
      if (typeof photoId !== "number" || !Number.isSafeInteger(photoId) || photoId <= 0 ||
          (variant !== "thumbnail" && variant !== "original")) fail("Invalid photo request", 400);
      const photo = check(await db.from("photo_finder_gallery_photos")
        .select("photo_id,filename")
        .eq("event_id", gallery.event_id)
        .eq("photo_id", photoId)
        .maybeSingle());
      if (!photo) fail("Photo not found", 404);
      const now = new Date().toISOString();
      const stale = check(await db.from("photo_finder_gallery_jobs")
        .select("id,object_path")
        .eq("gallery_id", gallery.id)
        .eq("photo_id", photoId)
        .eq("variant", variant)
        .lt("expires_at", now)
        .in("status", ["pending", "processing", "ready"]));
      for (const old of stale || []) {
        if (old.object_path) check(await db.storage.from(bucket).remove([old.object_path]));
        check(await db.from("photo_finder_gallery_jobs")
          .update({ status: "expired", object_path: null })
          .eq("id", old.id)
          .lt("expires_at", now));
      }
      const active = check(await db.from("photo_finder_gallery_jobs")
        .select("id,status")
        .eq("gallery_id", gallery.id)
        .eq("photo_id", photoId)
        .eq("variant", variant)
        .gt("expires_at", now)
        .in("status", ["pending", "processing", "ready"])
        .maybeSingle());
      if (active) return json({ job_id: active.id, status: active.status });
      const recentJobs = await db.from("photo_finder_gallery_jobs")
        .select("id", { count: "exact", head: true })
        .eq("gallery_id", gallery.id)
        .gt("created_at", new Date(Date.now() - 60_000).toISOString());
      check(recentJobs);
      if ((recentJobs.count || 0) >= 60) fail("Too many photo requests. Please wait a moment.", 429);
      const activeJobs = await db.from("photo_finder_gallery_jobs")
        .select("id", { count: "exact", head: true })
        .eq("gallery_id", gallery.id)
        .gt("expires_at", now)
        .or("status.in.(pending,processing),and(status.eq.ready,variant.eq.original)");
      check(activeJobs);
      if ((activeJobs.count || 0) >= 8) fail("The gallery is busy. Please wait for photos to finish loading.", 429);
      const eventJobs = await db.from("photo_finder_gallery_jobs")
        .select("id", { count: "exact", head: true })
        .eq("event_id", gallery.event_id)
        .gt("expires_at", now)
        .or("status.in.(pending,processing),and(status.eq.ready,variant.eq.original)");
      check(eventJobs);
      if ((eventJobs.count || 0) >= 12) fail("The gallery is busy. Please wait for photos to finish loading.", 429);
      const inserted = await db.from("photo_finder_gallery_jobs").insert({
        gallery_id: gallery.id,
        event_id: gallery.event_id,
        photo_id: photoId,
        filename: photo.filename,
        variant,
      }).select("id,status").single();
      if (inserted.error?.code === "23505") {
        const duplicate = check(await db.from("photo_finder_gallery_jobs")
          .select("id,status")
          .eq("gallery_id", gallery.id)
          .eq("photo_id", photoId)
          .eq("variant", variant)
          .in("status", ["pending", "processing", "ready"])
          .maybeSingle());
        if (duplicate) return json({ job_id: duplicate.id, status: duplicate.status });
      }
      const job = check(inserted);
      return json({ job_id: job.id, status: job.status }, 202);
    }

    const galleryJobMatch = path.match(/^\/guest\/gallery\/jobs\/([0-9a-f-]{36})(\/file)?$/i);
    if (galleryJobMatch && req.method === "GET") {
      const { gallery } = await gallerySession(req);
      if (!uuid.test(galleryJobMatch[1])) fail("Photo request not found", 404);
      const job = check(await db.from("photo_finder_gallery_jobs")
        .select("id,filename,variant,status,object_path")
        .eq("id", galleryJobMatch[1])
        .eq("gallery_id", gallery.id)
        .gt("expires_at", new Date().toISOString())
        .maybeSingle());
      if (!job) fail("Photo request not found", 404);
      if (!galleryJobMatch[2]) return json({ status: job.status });
      if (job.status !== "ready" || !job.object_path) fail("Photo is not ready", 409);
      if (job.variant === "original") {
      const consumed = check(await db.from("photo_finder_gallery_jobs")
        .update({ status: "expired" })
        .eq("id", job.id)
        .eq("gallery_id", gallery.id)
        .eq("status", "ready")
        .select("id")
        .maybeSingle());
      if (!consumed) fail("Photo was already delivered", 409);
      }
      const stored = await db.storage.from(bucket).download(job.object_path);
      const missing = stored.error as { statusCode?: string | number; status?: number } | null;
      if ((!stored.data && !stored.error) || Number(missing?.statusCode || missing?.status) === 404) {
        check(await db.from("photo_finder_gallery_jobs").update({ status: "expired" })
          .eq("id", job.id).eq("gallery_id", gallery.id));
        fail("The preview expired. Please retry.", 409);
      }
      const image = check(stored);
      if (!image) fail("Photo is no longer available", 404);
      if (job.variant === "original") {
      check(await db.storage.from(bucket).remove([job.object_path]));
      check(await db.from("photo_finder_gallery_jobs")
        .update({ object_path: null })
        .eq("id", job.id)
        .eq("gallery_id", gallery.id)
        .eq("status", "expired"));
      }
      const headers: Record<string, string> = {
        ...cors,
        "Cache-Control": "no-store",
        "Content-Type": job.variant === "thumbnail" ? "image/webp" : image.type || "application/octet-stream",
        "X-Content-Type-Options": "nosniff",
      };
      if (job.variant === "original") {
        headers["Content-Disposition"] = `attachment; filename*=UTF-8''${encodeURIComponent(job.filename)}`;
      }
      return new Response(image, { headers });
    }

    if (path === "/maintenance/cleanup" && req.method === "POST") {
      if (!/^[a-f0-9]{64}$/i.test(auth)) fail("Invalid credentials", 401);
      const config = check(await db.from("photo_finder_maintenance").select("secret_hash").eq("id", 1).maybeSingle());
      if (!config || config.secret_hash !== await hash(auth)) fail("Invalid credentials", 401);
      return json({ cleaned: await cleanExpired() });
    }

    if (path.startsWith("/worker/")) {
      const event = await getEvent(auth, "worker");
      if (path === "/worker/setup" && req.method === "POST") {
        const existing = await db.storage.getBucket(bucket);
        if (existing.error) {
          check(await db.storage.createBucket(bucket, { public: false, fileSizeLimit: 50 * 1024 * 1024 }));
        } else if (existing.data.public) {
          fail("Storage bucket must be private", 500);
        }
        return json({ ok: true });
      }
      if (path === "/worker/showcase" && req.method === "GET") {
        const rows = check(await db.from("photo_finder_showcases")
          .select("photo_id")
          .eq("event_id", event.id)
          .order("photo_id"));
        return json({ event_name: event.name, photo_ids: (rows || []).map((row) => row.photo_id) });
      }
      if (path === "/worker/showcase" && req.method === "POST") {
        const payload = await req.json() as {
          event_name?: unknown;
          folder_name?: unknown;
          photo_ids?: unknown;
        } | null;
        const photoIds = payload?.photo_ids;
        const folderName = payload?.folder_name;
        if (payload?.event_name !== event.name || !Array.isArray(photoIds) ||
            typeof folderName !== "string" ||
            folderName.length < 1 ||
            folderName.length > 255 ||
            folderName.trim() !== folderName ||
            folderName === "." || folderName === ".." ||
            /[/\\\u0000-\u001f\u007f]/.test(folderName) ||
            photoIds.length > 3 ||
            photoIds.some((id: unknown) =>
              typeof id !== "number" || !Number.isSafeInteger(id) || id <= 0
            ) ||
            new Set(photoIds).size !== photoIds.length) {
          fail("Invalid showcase manifest");
        }
        const keep = photoIds as number[];
        const existing = check(await db.from("photo_finder_showcases")
          .select("photo_id,object_path")
          .eq("event_id", event.id));
        const stale = (existing || []).filter((row) => !keep.includes(row.photo_id));
        if (stale.length) {
          check(await db.storage.from(bucket).remove(stale.map((row) => row.object_path)));
          check(await db.from("photo_finder_showcases")
            .delete()
            .eq("event_id", event.id)
            .in("photo_id", stale.map((row) => row.photo_id)));
        }
        const current = await db.rpc("photo_finder_set_current_folder", {
          p_event_id: event.id,
          p_folder_name: folderName,
        });
        if (current.error) throw current.error;
        if (!current.data) fail("Could not select the current photo folder", 409);
        return json({ ok: true });
      }
      const showcaseUpload = path.match(/^\/worker\/showcase\/(\d+)$/);
      if (showcaseUpload && req.method === "POST") {
        const photoId = Number(showcaseUpload[1]);
        const form = await readForm(req, 2 * 1024 * 1024);
        const photoEntry = form.get("photo");
        const eventName = form.get("event_name");
        const photo = photoEntry instanceof File ? photoEntry : fail("Invalid showcase photo");
        if (!Number.isSafeInteger(photoId) || photoId <= 0 ||
            eventName !== event.name || photo.type !== "image/webp" ||
            photo.size < 1 || photo.size > 2 * 1024 * 1024) {
          fail("Invalid showcase photo");
        }
        const objectPath = `showcases/${event.id}/${photoId}.webp`;
        check(await db.storage.from(bucket).upload(objectPath, photo, {
          contentType: "image/webp",
          upsert: true,
        }));
        check(await db.from("photo_finder_showcases").upsert({
          event_id: event.id,
          photo_id: photoId,
          object_path: objectPath,
          filename: photo.name,
          updated_at: new Date().toISOString(),
        }, { onConflict: "event_id,photo_id" }));
        return json({ ok: true });
      }
      if (path === "/worker/gallery/manifest" && req.method === "GET") {
        const url = new URL(req.url);
        const offsetText = url.searchParams.get("offset") || "0";
        const limitText = url.searchParams.get("limit") || "500";
        if (!/^\d+$/.test(offsetText) || !/^\d+$/.test(limitText)) fail("Invalid manifest page");
        const offset = Number(offsetText), limit = Number(limitText);
        if (!Number.isSafeInteger(offset) || offset < 0 ||
            !Number.isSafeInteger(limit) || limit < 1 || limit > 500) fail("Invalid manifest page");
        const manifest = await db.from("photo_finder_gallery_photos")
          .select("photo_id", { count: "exact", head: true })
          .eq("event_id", event.id);
        check(manifest);
        const photos = check(await db.from("photo_finder_gallery_photos")
          .select("photo_id,filename")
          .eq("event_id", event.id)
          .order("photo_id")
          .range(offset, offset + limit - 1));
        const rows = photos || [];
        return json({
          event_name: event.name,
          photo_count: manifest.count || 0,
          photos: rows.map((photo) => ({ photo_id: Number(photo.photo_id), filename: photo.filename })),
          next_offset: rows.length === limit ? offset + rows.length : null,
        });
      }
      if (path === "/worker/gallery/manifest" && req.method === "POST") {
        const payload = await req.json() as {
          event_name?: unknown;
          generation?: unknown;
          photos?: unknown;
          complete?: unknown;
        } | null;
        const photos = payload?.photos;
        if (payload?.event_name !== event.name || typeof payload.generation !== "string" ||
            !uuid.test(payload.generation) || !Array.isArray(photos) || photos.length > 500 ||
            typeof payload.complete !== "boolean") fail("Invalid gallery manifest");
        const seen = new Set<number>();
        const rows = photos.map((photo: unknown) => {
          const item = photo as {
            photo_id?: unknown;
            filename?: unknown;
            source_path?: unknown;
          } | null;
          if (!item || typeof item !== "object" ||
              typeof item.photo_id !== "number" || !Number.isSafeInteger(item.photo_id) ||
              item.photo_id <= 0 || typeof item.filename !== "string" ||
              item.filename.length < 1 || item.filename.length > 255 ||
              item.filename.trim() !== item.filename ||
              /[/\\\u0000-\u001f\u007f]/.test(item.filename) ||
              seen.has(item.photo_id)) fail("Invalid gallery manifest");
          const sourcePath = item.source_path ?? item.filename;
          if (typeof sourcePath !== "string" || sourcePath.length < 1 ||
              sourcePath.length > 2048 || sourcePath.trim() !== sourcePath ||
              sourcePath.includes("\\") ||
              sourcePath.split("/").some((part) => !part || part === "." || part === "..") ||
              /[\u0000-\u001f\u007f]/.test(sourcePath) ||
              sourcePath.split("/").at(-1) !== item.filename) fail("Invalid gallery manifest");
          seen.add(item.photo_id);
          return {
            event_id: event.id,
            photo_id: item.photo_id,
            filename: item.filename,
            ...(item.source_path === undefined ? {} : { source_path: sourcePath }),
            sync_generation: payload.generation,
            updated_at: new Date().toISOString(),
          };
        });
        if (rows.length) {
          check(await db.from("photo_finder_gallery_photos")
            .upsert(rows, { onConflict: "event_id,photo_id" }));
        }
        if (payload.complete) {
          while (true) {
            const stalePhotos = check(await db.from("photo_finder_gallery_photos")
              .select("photo_id")
              .eq("event_id", event.id)
              .neq("sync_generation", payload.generation)
              .order("photo_id")
              .limit(500));
            const staleIds = (stalePhotos || []).map((photo) => photo.photo_id);
            if (!staleIds.length) break;
            while (true) {
              const staleJobs = check(await db.from("photo_finder_gallery_jobs")
                .select("id,object_path")
                .eq("event_id", event.id)
                .in("photo_id", staleIds)
                .order("id")
                .limit(500));
              const jobIds = (staleJobs || []).map((job) => job.id);
              if (!jobIds.length) break;
              const paths = (staleJobs || []).flatMap((job) => job.object_path ? [job.object_path] : []);
              for (let offset = 0; offset < paths.length; offset += 100) {
                check(await db.storage.from(bucket).remove(paths.slice(offset, offset + 100)));
              }
              check(await db.from("photo_finder_gallery_jobs").delete().in("id", jobIds));
            }
            check(await db.from("photo_finder_gallery_photos")
              .delete()
              .eq("event_id", event.id)
              .in("photo_id", staleIds)
              .neq("sync_generation", payload.generation));
          }
        }
        return json({ ok: true });
      }
      if (path === "/worker/gallery/create" && req.method === "POST") {
        const payload = await req.json() as { password?: unknown; event_date?: unknown; expires_days?: unknown } | null;
        const suppliedPassword = payload?.password;
        const eventDate = payload?.event_date;
        const expiresDays = payload?.expires_days;
        let password: string;
        if (typeof eventDate === "string") {
          if (suppliedPassword !== undefined || !isEventDate(eventDate)) {
            fail("Use a real event date in YYYY-MM-DD format as the password.");
          }
          password = eventDate;
        } else {
          if (typeof suppliedPassword !== "string" || suppliedPassword.length < 12 || suppliedPassword.length > 128) {
            fail("Use a 12-128 character password.");
          }
          password = suppliedPassword;
        }
        if (typeof expiresDays !== "number" || !Number.isInteger(expiresDays) ||
            expiresDays < 1 || expiresDays > 90) fail("Gallery expiry must be 1-90 days.");
        const shareToken = token();
        const salt = token();
        const iterations = 310000;
        const passwordHash = await deriveGalleryPassword(password, salt, iterations);
        const expiresAt = new Date(Date.now() + expiresDays * 24 * 60 * 60_000).toISOString();
        const gallery = check(await db.from("photo_finder_gallery_links").insert({
          event_id: event.id,
          token_hash: await hash(shareToken),
          password_salt: salt,
          password_hash: passwordHash,
          password_iterations: iterations,
          expires_at: expiresAt,
        }).select("id,expires_at").single());
        return json({ gallery_id: gallery.id, token: shareToken, expires_at: gallery.expires_at });
      }
      if (path === "/worker/gallery/cleanup" && req.method === "POST") {
        const cutoff = new Date(Date.now() - 30 * 60_000).toISOString();
        const expired = check(await db.from("photo_finder_gallery_jobs")
          .select("id,object_path")
          .eq("event_id", event.id)
          .lt("expires_at", cutoff)
          .order("expires_at")
          .limit(200));
        for (const job of expired || []) {
          if (job.object_path) check(await db.storage.from(bucket).remove([job.object_path]));
          check(await db.from("photo_finder_gallery_jobs")
            .delete()
            .eq("id", job.id)
            .lt("expires_at", cutoff));
        }
        const galleries = check(await db.from("photo_finder_gallery_links")
          .select("id")
          .eq("event_id", event.id));
        const galleryIds = (galleries || []).map((gallery) => gallery.id);
        if (galleryIds.length) {
          check(await db.from("photo_finder_gallery_sessions")
            .delete()
            .in("gallery_id", galleryIds)
            .lt("expires_at", new Date().toISOString()));
          check(await db.from("photo_finder_gallery_login_attempts")
            .delete()
            .in("gallery_id", galleryIds)
            .lt("attempted_at", new Date(Date.now() - 24 * 60 * 60_000).toISOString()));
        }
        return json({ cleaned: (expired || []).length });
      }
      const revokeGallery = path.match(/^\/worker\/gallery\/([0-9a-f-]{36})\/revoke$/i);
      if (revokeGallery && req.method === "POST") {
        if (!uuid.test(revokeGallery[1])) fail("Gallery not found", 404);
        const gallery = check(await db.from("photo_finder_gallery_links")
          .select("id")
          .eq("id", revokeGallery[1])
          .eq("event_id", event.id)
          .maybeSingle());
        if (!gallery) fail("Gallery not found", 404);
        for (let start = 0; ; start += 500) {
          const jobs = check(await db.from("photo_finder_gallery_jobs")
            .select("id,object_path")
            .eq("gallery_id", gallery.id)
            .order("id")
            .range(start, start + 499));
          if (!jobs?.length) break;
          const paths = jobs.flatMap((job) => job.object_path ? [job.object_path] : []);
          for (let offset = 0; offset < paths.length; offset += 100) {
            check(await db.storage.from(bucket).remove(paths.slice(offset, offset + 100)));
          }
          if (jobs.length < 500) break;
        }
        check(await db.from("photo_finder_gallery_links")
          .update({ active: false })
          .eq("id", gallery.id));
        check(await db.from("photo_finder_gallery_jobs").delete().eq("gallery_id", gallery.id));
        check(await db.from("photo_finder_gallery_sessions").delete().eq("gallery_id", gallery.id));
        check(await db.from("photo_finder_gallery_login_attempts").delete().eq("gallery_id", gallery.id));
        return json({ ok: true });
      }
      if (path === "/worker/gallery/claim" && req.method === "POST") {
        const now = new Date().toISOString();
        const exhausted = check(await db.from("photo_finder_gallery_jobs")
          .select("id,status")
          .eq("event_id", event.id)
          .gte("attempt", 5)
          .or(`and(status.eq.processing,lease_expires_at.lt.${now}),status.eq.pending`)
          .limit(20));
        for (const old of exhausted || []) {
          let update = db.from("photo_finder_gallery_jobs")
            .update({ status: "failed", error_message: "Photo is temporarily unavailable." })
            .eq("id", old.id)
            .eq("status", old.status)
            .gte("attempt", 5);
          if (old.status === "processing") update = update.lt("lease_expires_at", now);
          check(await update);
        }
        const candidates = check(await db.from("photo_finder_gallery_jobs")
          .select("id,gallery_id,status,attempt,photo_id,filename,variant")
          .eq("event_id", event.id)
          .gt("expires_at", now)
          .lt("attempt", 5)
          .or(`status.eq.pending,and(status.eq.processing,lease_expires_at.lt.${now})`)
          .order("created_at")
          .limit(1));
        if (!candidates?.length) return json({ job: null });
        const candidate = candidates[0], lease = crypto.randomUUID();
        const activeGallery = check(await db.from("photo_finder_gallery_links")
          .select("id")
          .eq("id", candidate.gallery_id)
          .eq("active", true)
          .gt("expires_at", now)
          .maybeSingle());
        if (!activeGallery) {
          check(await db.from("photo_finder_gallery_jobs")
            .update({ status: "expired" })
            .eq("id", candidate.id)
            .in("status", ["pending", "processing"]));
          return json({ job: null });
        }
        let update = db.from("photo_finder_gallery_jobs")
          .update({
            status: "processing",
            lease_id: lease,
            lease_expires_at: new Date(Date.now() + 10 * 60_000).toISOString(),
            attempt: candidate.attempt + 1,
          })
          .eq("id", candidate.id)
          .eq("status", candidate.status)
          .eq("attempt", candidate.attempt);
        if (candidate.status === "processing") update = update.lt("lease_expires_at", now);
        const claimed = check(await update.select("id").maybeSingle());
        if (!claimed) return json({ job: null });
        return json({
          job: {
            id: candidate.id,
            lease,
            photo_id: candidate.photo_id,
            filename: candidate.filename,
            variant: candidate.variant,
          },
        });
      }
      const workerGalleryJob = path.match(/^\/worker\/gallery\/jobs\/([0-9a-f-]{36})\/(complete|fail)$/i);
      if (workerGalleryJob && req.method === "POST") {
        const lease = req.headers.get("x-gallery-job-lease") || "";
        const job = await galleryWorkerJob(event.id, workerGalleryJob[1], lease);
        if (workerGalleryJob[2] === "fail") {
          check(await db.from("photo_finder_gallery_jobs")
            .update({ status: "failed", error_message: "Photo is temporarily unavailable." })
            .eq("id", job.id)
            .eq("status", "processing")
            .eq("lease_id", lease));
          return json({ ok: true });
        }
        const form = await readForm(req, 51 * 1024 * 1024);
        const entry = form.get("photo");
        const photo = entry instanceof File ? entry : fail("Invalid gallery photo");
        const validType = job.variant === "thumbnail"
          ? photo.type === "image/webp" && photo.size <= 2 * 1024 * 1024
          : ["image/jpeg", "image/png", "image/webp"].includes(photo.type) &&
            photo.size <= 50 * 1024 * 1024;
        if (photo.size < 1 || !validType) fail("Invalid gallery photo");
        const objectPath = `gallery/${job.gallery_id}/${job.id}.${job.variant === "thumbnail" ? "webp" : "original"}`;
        check(await db.storage.from(bucket).upload(objectPath, photo, {
          contentType: photo.type,
          upsert: true,
        }));
        try {
          await galleryWorkerJob(event.id, job.id, lease);
          const updated = check(await db.from("photo_finder_gallery_jobs")
            .update({ status: "ready", object_path: objectPath, error_message: null })
            .eq("id", job.id)
            .eq("status", "processing")
            .eq("lease_id", lease)
            .select("id")
            .maybeSingle());
          if (!updated) fail("Gallery job lease expired", 409);
        } catch (error) {
          check(await db.storage.from(bucket).remove([objectPath]));
          throw error;
        }
        return json({ ok: true });
      }
      if (path === "/worker/claim" && req.method === "POST") {
        const now = new Date().toISOString();
        const exhausted = check(await db.from("photo_finder_jobs")
          .select("id,selfie_path,status")
          .eq("event_id", event.id)
          .gte("attempt", 5)
          .or(`and(status.eq.processing,lease_expires_at.lt.${now}),status.eq.pending`)
          .limit(20));
        for (const old of exhausted || []) {
          let update = db.from("photo_finder_jobs")
            .update({ status: "failed", error_message: "Unable to process this request. Please try again." })
            .eq("id", old.id)
            .eq("status", old.status)
            .gte("attempt", 5);
          if (old.status === "processing") update = update.lt("lease_expires_at", now);
          const failed = check(await update.select("id").maybeSingle());
          if (!failed) continue;
          await db.storage.from(bucket).remove([old.selfie_path]);
        }
        const candidates = check(await db.from("photo_finder_jobs").select("id,status,attempt,lease_id").eq("event_id", event.id).gt("expires_at", now).lt("attempt", 5).or(`status.eq.pending,and(status.eq.processing,lease_expires_at.lt.${now})`).order("created_at").limit(1));
        if (!candidates?.length) return json({ job: null });
        const candidate = candidates[0], lease = crypto.randomUUID();
        let update = db.from("photo_finder_jobs").update({ status: "processing", lease_id: lease, lease_expires_at: new Date(Date.now() + 5 * 60_000).toISOString(), attempt: candidate.attempt + 1 }).eq("id", candidate.id).eq("status", candidate.status).eq("attempt", candidate.attempt);
        const claimed = check(await update.select("id").maybeSingle());
        if (!claimed) return json({ job: null });
        const uploadedIds: number[] = [];
        for (let start = 0; ; start += 500) {
          const page = check(await db.from("photo_finder_results").select("photo_id").eq("job_id", claimed.id).eq("uploaded", true).order("photo_id").range(start, start + 499));
          uploadedIds.push(...(page || []).map((item) => item.photo_id));
          if (!page || page.length < 500) break;
        }
        return json({ job: { id: claimed.id, lease, uploaded_photo_ids: uploadedIds } });
      }
      const match = path.match(/^\/worker\/jobs\/([0-9a-f-]+)\/(selfie|matches|results|complete|fail|heartbeat|release)$/i);
      if (!match) fail("Not found", 404);
      if (req.method === "POST" && ["complete", "fail"].includes(match![2])) {
        const lease = req.headers.get("x-job-lease") || "";
        if (!uuid.test(lease)) fail("Invalid lease", 403);
        const terminal = match![2] === "complete" ? "completed" : "failed";
        const already = check(await db.from("photo_finder_jobs").select("id").eq("id", match![1]).eq("event_id", event.id).eq("status", terminal).eq("lease_id", lease).maybeSingle());
        if (already) return json({ ok: true });
      }
      const job = await workerJob(event.id, match![1], req.headers.get("x-job-lease") || "");
      if (match![2] === "matches" && req.method === "POST") {
        const payload = await req.json() as { photos?: Array<{ photo_id: number; filename: string; similarity: number; face_count: number }> } | null;
        const photos = payload?.photos ?? fail("Invalid matched photo list");
        if (!Array.isArray(photos) || photos.length > 10_000 || photos.some((photo) =>
          !photo || !Number.isSafeInteger(photo.photo_id) || photo.photo_id <= 0 ||
          typeof photo.filename !== "string" || photo.filename.length < 1 || photo.filename.length > 255 ||
          !Number.isFinite(photo.similarity) || photo.similarity < 0.45 || photo.similarity > 1.001 ||
          !Number.isSafeInteger(photo.face_count) || photo.face_count < 1
        ) || new Set(photos.map((photo) => photo.photo_id)).size !== photos.length) {
          fail("Invalid matched photo list");
        }
        await workerJob(event.id, job.id, job.lease_id);
        if (photos.length) {
          // Retrying this manifest must preserve already-uploaded photos.
          check(await db.from("photo_finder_results").upsert(photos.map((photo) => ({
            job_id: job.id,
            photo_id: photo.photo_id,
            filename: photo.filename,
            similarity: photo.similarity,
            face_count: photo.face_count,
            object_path: `results/${job.id}/${photo.photo_id}`,
            uploaded: false,
          })), { onConflict: "job_id,photo_id", ignoreDuplicates: true }));
        }
        return json({ ok: true });
      }
      if (match![2] === "heartbeat" && req.method === "POST") {
        const updated = check(await db.from("photo_finder_jobs").update({ lease_expires_at: new Date(Date.now() + 5 * 60_000).toISOString() }).eq("id", job.id).eq("status", "processing").eq("lease_id", job.lease_id).select("id").maybeSingle());
        if (!updated) fail("Request lease expired", 409);
        // A long search must not make the current event disappear from the site.
        check(await db.from("photo_finder_events")
          .update({ current_updated_at: new Date().toISOString() })
          .eq("id", event.id)
          .eq("is_current", true));
        return json({ ok: true });
      }
      if (match![2] === "release" && req.method === "POST") {
        const updated = check(await db.from("photo_finder_jobs").update({ status: "pending", lease_id: null, lease_expires_at: null }).eq("id", job.id).eq("status", "processing").eq("lease_id", job.lease_id).select("id").maybeSingle());
        if (!updated) fail("Request lease expired", 409);
        return json({ ok: true });
      }
      if (match![2] === "selfie" && req.method === "GET") {
        const blob = check(await db.storage.from(bucket).download(job.selfie_path))
          ?? fail("Selfie not found", 404);
        return new Response(blob, { headers: { ...cors, "Content-Type": blob.type || "application/octet-stream" } });
      }
      if (match![2] === "results" && req.method === "POST") {
        const form = await readForm(req, 51 * 1024 * 1024), photoEntry = form.get("photo"), photoId = form.get("photo_id"), similarity = Number(form.get("similarity"));
        const faceCount = form.has("face_count") ? Number(form.get("face_count")) : 1;
        const photo = photoEntry instanceof File ? photoEntry : fail("Invalid result");
        if (photo.size < 1 || photo.size > 50 * 1024 * 1024 || !/^\d+$/.test(String(photoId)) || !Number.isFinite(similarity) || similarity < 0.45 || similarity > 1.001 || !Number.isSafeInteger(faceCount) || faceCount < 1) fail("Invalid result");
        const objectPath = `results/${job.id}/${photoId}`;
        await workerJob(event.id, job.id, job.lease_id);
        check(await db.from("photo_finder_results").upsert({ job_id: job.id, photo_id: Number(photoId), object_path: objectPath, filename: photo.name, similarity, face_count: faceCount, uploaded: false }, { onConflict: "job_id,photo_id" }));
        check(await db.storage.from(bucket).upload(objectPath, photo, { contentType: photo.type || "application/octet-stream", upsert: true }));
        await workerJob(event.id, job.id, job.lease_id);
        check(await db.from("photo_finder_results").update({ uploaded: true }).eq("job_id", job.id).eq("photo_id", Number(photoId)));
        return json({ ok: true });
      }
      if ((match![2] === "complete" || match![2] === "fail") && req.method === "POST") {
        const completed = match![2] === "complete";
        const updated = check(await db.from("photo_finder_jobs").update({ status: completed ? "completed" : "failed", error_message: completed ? null : "Unable to process this selfie. Please try a clear selfie with one face.", completed_at: new Date().toISOString() }).eq("id", job.id).eq("status", "processing").eq("lease_id", job.lease_id).select("id").maybeSingle());
        if (!updated) fail("Request lease expired", 409);
        await db.storage.from(bucket).remove([job.selfie_path]);
        return json({ ok: true });
      }
    }
    return json({ detail: "Not found" }, 404);
  } catch (error) {
    if (error instanceof Response) return json({ detail: await error.text() }, error.status);
    console.error("Relay error", error);
    return json({ detail: "Service temporarily unavailable" }, 500);
  }
});

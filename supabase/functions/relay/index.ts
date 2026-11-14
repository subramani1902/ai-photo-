// Supabase Edge Function: public guest API and authenticated laptop-worker API.
// SUPABASE_SERVICE_ROLE_KEY is injected by Supabase and never sent to clients.
import { createClient } from "npm:@supabase/supabase-js@2.49.8";

const db = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!, {
  auth: { persistSession: false, autoRefreshToken: false },
});
const bucket = "photo-finder-private";
const cors = { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "authorization, content-type, x-event-token, x-event-id, x-job-token, x-job-lease, apikey", "Access-Control-Allow-Methods": "GET, POST, OPTIONS", "Cache-Control": "no-store" };
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
        if (job.status !== "completed" || !uuid.test(guestMatch[2])) fail("Photo not available", 404);
        const photo = check(await db.from("photo_finder_results").select("object_path,filename").eq("id", guestMatch[2]).eq("job_id", job.id).eq("uploaded", true).maybeSingle());
        if (!photo) fail("Photo not found", 404);
        const blob = check(await db.storage.from(bucket).download(photo!.object_path))
          ?? fail("Photo not found", 404);
        return new Response(blob, { headers: { ...cors, "Content-Type": blob.type || "application/octet-stream", "X-Content-Type-Options": "nosniff" } });
      }
      const photos = job.status === "completed" ? check(await db.from("photo_finder_results").select("id,filename,similarity,face_count").eq("job_id", job.id).eq("uploaded", true).order("similarity", { ascending: false })) : [];
      return json({ status: job.status, message: job.error_message, photos });
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
      if (path === "/worker/claim" && req.method === "POST") {
        const now = new Date().toISOString();
        const exhausted = check(await db.from("photo_finder_jobs").select("id,selfie_path").eq("event_id", event.id).eq("status", "processing").gte("attempt", 5).lt("lease_expires_at", now).limit(20));
        for (const old of exhausted || []) {
          check(await db.from("photo_finder_jobs").update({ status: "failed", error_message: "Unable to process this request. Please try again." }).eq("id", old.id).eq("status", "processing").lt("lease_expires_at", now));
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
      const match = path.match(/^\/worker\/jobs\/([0-9a-f-]+)\/(selfie|results|complete|fail|heartbeat|release)$/i);
      if (!match) fail("Not found", 404);
      if (req.method === "POST" && ["complete", "fail"].includes(match![2])) {
        const lease = req.headers.get("x-job-lease") || "";
        if (!uuid.test(lease)) fail("Invalid lease", 403);
        const terminal = match![2] === "complete" ? "completed" : "failed";
        const already = check(await db.from("photo_finder_jobs").select("id").eq("id", match![1]).eq("event_id", event.id).eq("status", terminal).eq("lease_id", lease).maybeSingle());
        if (already) return json({ ok: true });
      }
      const job = await workerJob(event.id, match![1], req.headers.get("x-job-lease") || "");
      if (match![2] === "heartbeat" && req.method === "POST") {
        const updated = check(await db.from("photo_finder_jobs").update({ lease_expires_at: new Date(Date.now() + 5 * 60_000).toISOString() }).eq("id", job.id).eq("status", "processing").eq("lease_id", job.lease_id).select("id").maybeSingle());
        if (!updated) fail("Request lease expired", 409);
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

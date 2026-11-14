# Online event-photo prototype

The public guest website is hosted separately from the event laptop. The
Supabase relay accepts a guest selfie, the laptop worker searches the local
event index, and matched photos are transferred to private temporary storage.
The relay Edge Function and its database objects must be deployed to the same
Supabase project configured in `main.js` before guest searches can work.

## Provision the Supabase relay database

After rotating any database password previously shared in chat, open the new
project's SQL Editor and run
[`20261110130000_create_photo_finder_relay.sql`](../supabase/migrations/20261110130000_create_photo_finder_relay.sql).
Alternatively, authenticate the Supabase CLI, then run these from the
repository root; `supabase/config.toml` targets the new project:

```powershell
npx supabase login
npx supabase link --project-ref dtfxnuxluuwvjdcyxmnc
npx supabase db push
npx supabase functions deploy relay
```

The migration creates the private relay tables, row-level security, rate-limit
reservation function, and an inactive `Candid Test` event. The relay worker
creates the private Storage bucket when it starts.

Run the showcase migration
[`20261110160000_create_private_event_showcases.sql`](../supabase/migrations/20261110160000_create_private_event_showcases.sql)
as well. Apply
[`20261114140000_follow_current_photo_folder.sql`](../supabase/migrations/20261114140000_follow_current_photo_folder.sql)
and deploy the relay function. The laptop worker synchronizes up to three
recent photos from the watched folder to the private bucket every minute. The
public website shows only the folder most recently reported by a running
worker, using the watched folder's name; the listing disappears if the worker
has not checked in for two minutes. Other events remain available through their
event-specific QR links. Restart the importer and relay worker after applying
the migration so the current watched folder is registered and published.

For each additional event, create a matching local event and folder, then add
an active `photo_finder_events` row whose `name` exactly matches that folder.
Store its worker secret in a separate ignored file (for example
`.env.wedding`) and add only its SHA-256 hash to the database. Start a worker
for each event with its local ID and secrets file:

```powershell
.\venv\Scripts\python.exe -c "from dotenv import dotenv_values; from hashlib import sha256; e=dotenv_values('.env.wedding'); print('worker hash:', sha256(e['PHOTO_FINDER_WORKER_SECRET'].encode()).hexdigest())"
```

Insert those hashes with the exact local folder name:

```sql
insert into public.photo_finder_events (name, guest_token_hash, worker_token_hash, active)
values ('Wedding', null, '<worker hash>', true);
```

Import that folder into its local event ID, then start its worker:

```powershell
.\venv\Scripts\python.exe -m app.services.camera_importer --folder .\Wedding --event-id 4
.\venv\Scripts\python.exe -m app.services.relay_worker --event-id 4 --secrets-file .env.wedding --relay-url https://dtfxnuxluuwvjdcyxmnc.supabase.co/functions/v1/relay
```

Anyone can open `https://ai-photo-k5fu.vercel.app` to see the current watched
folder and submit a selfie search. As imported photos are indexed, the worker
refreshes that folder's three most recent previews automatically. QR links
remain supported as an optional way to open one specific event.
The website shows those previews on the home page and while search results load
progressively. Public previews and selfie matches use only photos with at least
one detected face; the importer keeps all original photos locally.
Search results can be filtered into Candid (one detected face) and Group (two
or more detected faces). Photos without a detected face are not candidates for
selfie matching.

To enable these result categories on an existing relay, apply
[`20261110170000_add_result_face_count.sql`](../supabase/migrations/20261110170000_add_result_face_count.sql),
deploy the relay function, redeploy the static website, and restart the event
worker so it reports each matched photo's detected face count.

The active-search limit is 10 per event, including queued and processing
requests. The per-event intake limit is 10 submissions per minute. One worker
processes one search at a time; the remaining accepted searches wait in the
queue. To change an existing relay, apply
[`20261114203300_lower_search_rate_limits.sql`](../supabase/migrations/20261114203300_lower_search_rate_limits.sql).

Before enabling the event, set its token hashes from the local `.env.relay`
file. From the repository root, this command prints hashes only (not the
secrets):

```powershell
.\venv\Scripts\python.exe -c "from dotenv import dotenv_values; from hashlib import sha256; e=dotenv_values('.env.relay'); print('guest hash:', sha256(e['PHOTO_FINDER_EVENT_TOKEN'].encode()).hexdigest()); print('worker hash:', sha256(e['PHOTO_FINDER_WORKER_SECRET'].encode()).hexdigest())"
```

Use those two hashes in the SQL Editor:

```sql
update public.photo_finder_events
set guest_token_hash = '<guest hash>',
    worker_token_hash = '<worker hash>',
    active = true
where id = 'd3228167-5500-4a72-bda0-2f7249a938aa';
```

The CLI configuration disables platform JWT verification for this function
(the relay authenticates its worker and guest requests itself). Supabase
provides the service-role key to Edge Functions; keep it server-side only.
Verify
`https://<project-ref>.supabase.co/functions/v1/relay/guest/submit` responds to
an `OPTIONS` request before redeploying the Vercel frontend.

## Deploy the static website to Vercel

This directory is a static website. In the Vercel project settings, set
**Root Directory** to `online` and leave the Build Command empty. Redeploy after
changing the setting. `vercel.json` selects Vercel's `Other` framework and
disables the Python build commands for deployments from this directory. Do not
deploy from the repository root: its
`requirements.txt` and FastAPI app cause Vercel to build a Python function and
bundle the computer-vision dependencies, exceeding Vercel's function-size
limit. With `online` as the root, the deployment contains only this directory's
HTML, JavaScript, CSS, and image assets.

The search loading dialog reports detected matches from the relay in one wide
box. The gallery counts photos as the guest browser fetches them. After changing
the relay response, deploy the Edge Function as well as the Vercel site:

```powershell
npx supabase functions deploy relay
```

## Start the laptop services

From the repository root, run the camera importer in one PowerShell terminal.
It watches `Aathif-Sahana`, imports stable photos into local event ID 3 (`Candid
Test`), rebuilds the event's face index, and keeps that event synchronized
with the folder:

```powershell
.\venv\Scripts\python.exe -m app.services.camera_importer --folder .\Aathif-Sahana --event-id 3
```

In a second terminal, start the relay worker:

```powershell
.\venv\Scripts\python.exe -m app.services.relay_worker --event-id 3 --relay-url https://dtfxnuxluuwvjdcyxmnc.supabase.co/functions/v1/relay
```

The ignored `.env.relay` file must contain the test event and worker secrets.
Keep it private and never commit it. The worker and the laptop's internet
connection must stay on. The web page accepts uploaded or camera-captured selfies up to 10 MB; live
camera capture requires browser camera permission and a secure HTTPS connection.
Captured images are sent through the same temporary search flow as uploaded
photos. Event photo imports accept files up to 100 MB. Originals are stored unchanged, while images
over 4096 pixels on their longest edge are downscaled only in memory for face
detection. The camera importer sends up to 20 photos per batch so the face
search index is rebuilt once per batch instead of once per photo. The local
face engine uses the NVIDIA CUDA provider when available and falls back to CPU.
Install dependencies from `requirements.txt` before starting the importer.
For guest results, originals larger than 1 MB are converted to high-quality
JPEG copies up to 1600 pixels and uploaded four at a time; the local originals
are retained. A separate heartbeat keeps the search lease and current event
alive while face matching or photo uploads are in progress.

For folder-managed photos, the watched folder is the event's source of truth:
new and replaced files are re-indexed, and deleted files are removed from the
event's local search data. At startup, the importer waits until all supported
images have been successfully scanned before removing untracked legacy photos.
New files are checked every half-second and face analysis starts after their
size and modification time stay unchanged for half a second; analysis duration
depends on the photo and available hardware.
Keep every photo that belongs to the event inside its watched folder; photos
uploaded outside that folder are removed when the initial full sync completes.
Preview images refresh automatically through the relay worker.

## Replay a photo folder into the watched folder

The copy utility preserves the source files, skips destination files that
already exist, and publishes each completed copy atomically so the importer
does not see a partial image. It waits 10 seconds between successful copies by
default. Preview and selfie-match transfers to Supabase continue through the
relay worker.

From the repository root, preview the batch first, then start the copy:

```powershell
.\venv\Scripts\python.exe utilities\copy_photos_to_watched_folder.py --source D:\PUBERTY2 --destination D:\Event-AI-Photo-Finder\Aathif-Sahana --dry-run
.\venv\Scripts\python.exe utilities\copy_photos_to_watched_folder.py --source D:\PUBERTY2 --destination D:\Event-AI-Photo-Finder\Aathif-Sahana --interval 10
```

In another PowerShell terminal, monitor importer progress and local face
detection:

```powershell
.\venv\Scripts\python.exe -m utilities.check_photo_ingest --folder D:\Event-AI-Photo-Finder\Aathif-Sahana --event-id 3 --interval 5
```

`pending` should fall to zero as the importer indexes incoming photos.
`detected_faces` is the number of face records found so far; a photo with no
detectable face is still counted as indexed.

## Prototype limits

- This setup is paired to local event ID 3 (`Candid Test`) and the current Supabase event. Its intake folder is `Aathif-Sahana` at the repository root.
- Jobs expire after 24 hours. The migration does not configure a scheduled
  cleanup task; set up and test the maintenance secret and scheduler in the new
  Supabase project before relying on automatic removal of stored files.
- The relay limits each event to 20 submissions per minute and 25 active
  requests, using an atomic database reservation. One worker processes searches
  sequentially. It also limits upload size.
  These controls are not a substitute for independent security and load testing.
- Search accuracy, concurrency, internet throughput, phone downloads, and
  photo retention policies need real-event testing.
- If the guest refreshes the online page, they must scan the QR again.

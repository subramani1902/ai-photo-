# Online event-photo prototype

The studio homepage is `/`. The existing guest photo finder is now
`/find-photos.html`; client accounts and password galleries remain at
`/account.html` and `/gallery.html`. Existing event QR URLs containing `token`
in the root query string or fragment forward to the finder without losing it.

The homepage uses explicitly labelled sample photographs. To publish approved
studio photographs, add optimized public images in `assets/home/`, update
`homepage-content.mjs` (image, alt text, category, sample flag and optional
Instagram post URL), and replace the initial hero image URLs in `index.html`
with the same two first portfolio entries for fast initial rendering. Private
gallery images, originals and embeddings must never be added to public assets.
Sample attribution and source links are in `assets/home/credits.json` and the
footer. Keep credits for any sample images still in use.

The homepage shows the Silver, Gold, and Platinum wedding packages from the
studio brochure. Clients can review an enquiry and open it as a WhatsApp draft
to the studio. Links with `?utm_source=instagram` identify Instagram enquiries
in that draft.

## Studio admin

Open `/admin/index.html` and sign in with an approved, verified Firebase account.
Deploy the updated `online/` website and restart the existing studio photo
gateway with the updated application code to make the admin tools available.
The site uses the same `FIREBASE_PHOTO_API_URL` and allowed website origin as the
client account.

Homepage photo access uses the existing `manage_homepage` permission. Client
and package management uses a separate grant. From the repository root, run
the grant command for the approved staff email and add `--apply` to activate it.
Use `--studio --revoke --apply` with the same email to revoke it.

```powershell
.\venv\Scripts\python.exe -m utilities.grant_homepage_admin --studio --email STAFF_EMAIL
.\venv\Scripts\python.exe -m utilities.grant_homepage_admin --studio --email STAFF_EMAIL --apply
```

- **Clients** records contact information, Instagram leads, bookings, follow-up
  dates, agreed quotes and payments received. Add enquiries from Instagram or
  WhatsApp as client records. Booking payments are entered by staff and balances
  are calculated from the recorded amounts.
- **Packages & payments** edits the public package price, inclusions, booking
  deposit, and terms. Use the studio gateway to serve saved rates. When the site
  cannot reach it, the homepage identifies the brochure prices as a reference.
- **Homepage photos** edits the existing portfolio positions, captions,
  descriptions, framing, and Instagram post links. Save drafts, then publish
  them through the existing homepage publisher.

The public guest website is hosted separately from the event laptop. The
Supabase relay accepts a guest selfie, the laptop worker searches the local
event index, and matched photos are transferred to private temporary storage.
The relay Edge Function and its database objects must be deployed to the same
Supabase project configured in `main.js` before guest searches can work.

Matched photos appear progressively as the laptop uploads them. The guest job
token is required for every status and image request, including during processing.
Pending files are never returned. The browser fetches at most four images at a
time and retries transient GET failures without resubmitting the selfie. Candid
and Group filtering, individual downloads and selected ZIP downloads work as
photos arrive; Download all becomes available after delivery completes.

The relay worker defaults to two delivery slots so a large transfer does not
block the next selfie search. Each slot has its own HTTP session; the existing
InsightFace/FAISS call is protected by one shared lock. Use `--search-workers 1`
when starting the worker to restore one delivery slot. An existing running
worker must be restarted to pick up this change; let its current job complete
before restarting. The camera importer does not need restarting.

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
and
[`20261118120000_add_password_protected_galleries.sql`](../supabase/migrations/20261118120000_add_password_protected_galleries.sql),
and
[`20261118130000_add_gallery_source_paths.sql`](../supabase/migrations/20261118130000_add_gallery_source_paths.sql),
then deploy the relay function. The laptop worker synchronizes up to three
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

Anyone can open `https://sm-photo-studio.vercel.app` and submit a selfie search
against the current watched folder. QR links remain available for specific
events. After finding the matches, the worker publishes their metadata before
uploading any image bytes. The UI can therefore show the full Photos Matched
count immediately, while delivery continues in the background. The importer
keeps local copies as a fallback, but photo retrieval reads from
`G:\Aathif & sahana\aathif and sahana` first. The laptop relay transfers
authorized matches to private temporary storage for guest downloads; keep the
external drive connected while searches are being processed.
Search results can be filtered into Candid (one detected face) and Group (two
or more detected faces). Photos without a detected face are not candidates for
selfie matching. AI search results are also grouped into sections using each
photo's indexed source folder, such as Wedding and Reception.

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

Start missing services and check the current event with one command:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "D:\Event-AI-Photo-Finder\start-all.ps1"
```

The launcher reuses running processes and writes new service logs under the
Windows temporary folder. To keep the backend, importer, and relay monitored
and start them automatically whenever this Windows user signs in, install the
current-user scheduled task:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "D:\Event-AI-Photo-Finder\start-all.ps1" -InstallTask
```

The supervisor checks backend health every 15 seconds and relaunches missing
services. The task runs only while this user is signed in; the G: photo folder
and Firebase credential file must remain accessible. Remove it with
`Unregister-ScheduledTask -TaskName "Event AI Photo Finder Services"`.
Service logs are written under the Windows temporary folder. Individual
service commands follow below.

On this laptop, the stored copies for local events `Aathif-Sahana` (event 4)
and `wedding_001` are under `C:\Users\subra\EventAIPhotoFinderData\events`.
Their original paths under `storage/events` are junctions, so existing database
paths continue to work. Event 3 (`Candid Test`) watches
`G:\Aathif & sahana\aathif and sahana`.
The importer pauses when its storage drive has less than 256 MB free and retries
locked source files without blocking the rest of their batch.

From the repository root, run the camera importer in one PowerShell terminal.
It imports stable photos from the watched folder into local event ID 3
(`Candid Test`), rebuilds the event's face index, and keeps that event
synchronized with the folder:

```powershell
.\venv\Scripts\python.exe -m app.services.camera_importer --folder "G:\Aathif & sahana\aathif and sahana" --event-id 3
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

## Create a password-protected full-album link

The full gallery reads photos on demand from the watched drive. It does not
upload the entire album in advance: thumbnails are temporarily transferred as
the client browses, and an original is transferred only when its download
button is selected. Keep the studio computer, external drive, internet
connection, and relay worker running while the client uses the gallery.
The separate `app.services.gallery_worker` serves private gallery transfers even
when selfie searches occupy both search slots. `start-all.ps1` starts it alongside
the existing services and includes it in future supervisor checks. It reads the
existing authorized gallery jobs; it never runs face matching or changes photos.
It can also be started separately:

```powershell
.\venv\Scripts\python.exe -B -m app.services.gallery_worker --event-id 3 --secrets-file .env.relay --relay-url https://dtfxnuxluuwvjdcyxmnc.supabase.co/functions/v1/relay
```

Apply the gallery migration and deploy the relay function and Vercel website
before creating a link. The worker must be running. By default the importer must
have finished syncing the entire folder. This command checks that every supported
file in the watched folder is tracked locally and that the relay manifest is
up to date; it refuses to create a partial album. It then prompts for the
event date in `YYYY-MM-DD` format and uses that date as the gallery password.
The link expires after 30 days:

```powershell
.\venv\Scripts\python.exe -m utilities.manage_photo_gallery --event-id 3 --secrets-file .env.relay
```

To explicitly open an album while indexing continues, add `--allow-indexing`.
The link includes the current authorized manifest and gains photos as the
importer and relay synchronize. It must not be described as a completed album
until the importer reports zero pending files. To use a generated strong
password instead and save the actual link and password privately, use:

```powershell
.\venv\Scripts\python.exe -B -m utilities.manage_photo_gallery --event-id 3 --allow-indexing --generate-password --output .secrets/event-3-gallery.json
```

The output file must be new and inside the ignored `.secrets` folder. Generated
passwords are not printed to the terminal. To set the event date without a
prompt, pass `--event-date YYYY-MM-DD`. Do not publish the output file or embed
its private link into the public website. The website menu's **Private full
gallery** entry opens the login page; clients use their individual shared link
and password. Event dates are easy to guess, so only share the private gallery
link with the intended clients.

Clients can choose **♡ Choose favourite**, review **Show favourites**, then select
**Download editor CSV**. The CSV contains selection order, event ID/name, photo
ID, original filename and `selected_for_editing=yes`; it includes no image bytes,
passwords, access tokens, biometric data or absolute drive paths. It uses UTF-8
with a BOM, quotes fields and neutralizes spreadsheet formulas. Send it to the
editor with the corresponding original photo folder. Photos are not modified
by the selection or export.

Shared-password gallery favourites stay in that browser tab and survive reloads
there; export the CSV before closing the tab. The Firebase account gallery saves
favourites to the client's existing Firestore membership. Its **Download
favourites CSV** button reads every favourites page, including photos not yet
displayed, with Firebase authorization checked on each request.

Send the link and password to the client separately. The client can browse
compressed previews and download individual originals. Temporary relay copies
expire after about an hour and are cleaned up by the running worker. To revoke
a link before expiry, use its Gallery ID:

```powershell
.\venv\Scripts\python.exe -m utilities.manage_photo_gallery --event-id 3 --secrets-file .env.relay --revoke-id <gallery-id>
```

The website's `/gallery.html` page is included in the static Vercel deployment.
Gallery passwords are stored as salted, iterated hashes; the worker secret and
local drive paths are never sent to client browsers.

## Replay a photo folder into the watched folder

The copy utility preserves the source files, skips destination files that
already exist, and publishes each completed copy atomically so the importer
does not see a partial image. It waits 10 seconds between successful copies by
default. Preview and selfie-match transfers to Supabase continue through the
relay worker.

From the repository root, preview the batch first, then start the copy:

```powershell
.\venv\Scripts\python.exe utilities\copy_photos_to_watched_folder.py --source D:\PUBERTY2 --destination "G:\Aathif & sahana\aathif and sahana" --dry-run
.\venv\Scripts\python.exe utilities\copy_photos_to_watched_folder.py --source D:\PUBERTY2 --destination "G:\Aathif & sahana\aathif and sahana" --interval 10
```

In another PowerShell terminal, monitor importer progress and local face
detection:

```powershell
.\venv\Scripts\python.exe -m utilities.check_photo_ingest --folder "G:\Aathif & sahana\aathif and sahana" --event-id 3 --interval 5
```

`pending` should fall to zero as the importer indexes incoming photos.
`detected_faces` is the number of face records found so far; a photo with no
detectable face is still counted as indexed.

## Prototype limits

- This setup is paired to local event ID 3 (`Candid Test`) and the current Supabase event. Its intake folder is `G:\Aathif & sahana\aathif and sahana`.
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

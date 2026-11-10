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

## Start the laptop services

From the repository root, run the camera importer in one PowerShell terminal.
It watches `Candid`, imports stable photos into local event ID 3 (`Candid
Test`), and rebuilds the event's face index:

```powershell
.\venv\Scripts\python.exe -m app.services.camera_importer --folder .\Candid --event-id 3
```

In a second terminal, start the relay worker:

```powershell
.\venv\Scripts\python.exe -m app.services.relay_worker --event-id 3 --relay-url https://dtfxnuxluuwvjdcyxmnc.supabase.co/functions/v1/relay
```

The ignored `.env.relay` file must contain the test event and worker secrets.
Keep it private and never commit it. The worker and the laptop's internet
connection must stay on. The web page accepts selfies up to 10 MB; event photo
imports accept files up to 100 MB. Originals are stored unchanged, while images
over 4096 pixels on their longest edge are downscaled only in memory for face
detection. The camera importer sends up to 20 photos per batch so the face
search index is rebuilt once per batch instead of once per photo. The local
face engine uses the NVIDIA CUDA provider when available and falls back to CPU.
Install dependencies from `requirements.txt` before starting the importer.
For guest results, originals larger than 8 MB are converted to high-quality
JPEG copies up to 1600 pixels and uploaded four at a time; the local originals
are retained.

## Prototype limits

- This setup is paired to local event ID 3 (`Candid Test`) and the current Supabase test event. Its intake folder is `Candid` at the repository root.
- Jobs expire after 24 hours. The migration does not configure a scheduled
  cleanup task; set up and test the maintenance secret and scheduler in the new
  Supabase project before relying on automatic removal of stored files.
- The relay limits each event to 20 submissions per minute and 100 active
  requests, using an atomic database reservation. It also limits upload size.
  These controls are not a substitute for independent security and load testing.
- Search accuracy, concurrency, internet throughput, phone downloads, and
  photo retention policies need real-event testing.
- If the guest refreshes the online page, they must scan the QR again.

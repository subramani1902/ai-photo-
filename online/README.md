# Online event-photo prototype

The public guest website is hosted separately from the event laptop. The
Supabase relay accepts a guest selfie, the laptop worker searches the local
event index, and matched photos are transferred to private temporary storage.

## Start the laptop services

From the repository root, run the camera importer in one PowerShell terminal.
It watches `Candid`, imports stable photos into local event ID 3 (`Candid
Test`), and rebuilds the event's face index:

```powershell
.\venv\Scripts\python.exe -m app.services.camera_importer --folder .\Candid --event-id 3
```

In a second terminal, start the relay worker:

```powershell
.\venv\Scripts\python.exe -m app.services.relay_worker --event-id 3 --relay-url https://ifudnqcgvqafhzazqlku.supabase.co/functions/v1/relay
```

The ignored `.env.relay` file must contain the test event and worker secrets.
Keep it private and never commit it. The worker and the laptop's internet
connection must stay on. The web page accepts selfies up to 10 MB; event photos
over 50 MB are skipped by the importer and reported in its log.

## Prototype limits

- This setup is paired to local event ID 3 (`Candid Test`) and the current Supabase test event. Its intake folder is `Candid` at the repository root.
- Results expire after 24 hours. An independent Supabase task runs every five
  minutes and deletes expired files after a 30-minute grace period. If Supabase
  is unavailable or deletion fails, a later scheduled run retries; a backlog
  can also delay removal. Monitor the scheduled job before real events.
- The relay limits each event to 20 submissions per minute and 100 active
  requests, using an atomic database reservation. It also limits upload size.
  These controls are not a substitute for independent security and load testing.
- Search accuracy, concurrency, internet throughput, phone downloads, and
  photo retention policies need real-event testing.
- If the guest refreshes the online page, they must scan the QR again.

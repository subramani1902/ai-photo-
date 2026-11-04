# Online event-photo prototype

The public guest website is hosted separately from the event laptop. The
Supabase relay accepts a guest selfie, the laptop worker searches the local
event index, and matched photos are transferred to private temporary storage.

## Start the laptop worker

From the repository root, keep the camera-folder watcher running if new photos
need to be imported. In a separate PowerShell terminal run:

```powershell
.\s-venv\Scripts\Activate.ps1
python -m app.services.relay_worker --event-id 2 --relay-url https://ifudnqcgvqafhzazqlku.supabase.co/functions/v1/relay
```

The ignored `.env.relay` file contains the test event and worker secrets. Keep
it private and never commit it. The QR image also contains event access and is
ignored by Git. The worker and the laptop's internet connection must stay on.

## Prototype limits

- This setup is paired to local event ID 2 (Celebrity Five Test) and the current Supabase test event. Its intake folder is `D:\Codes\ai-photo-\sample_photos`.
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

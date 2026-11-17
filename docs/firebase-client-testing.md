# Firebase clients and the free photo API test setup

Use the existing Firebase project `smphotography-f8c83`. Email/password and
Google sign-in are configured. Clients can create their own account on
`/account.html`; email/password clients must verify their email before opening
a private event. A Google sign-in also needs a verified email in the ID token.

The event ID selects a gallery. It does not grant permission by itself. Each
client needs an explicit, expiring membership from the photographer. Copy the
client's **Account ID** from the account page and use the local event ID and its
actual local owner ID:

```powershell
.\venv\Scripts\python.exe -B -m utilities.grant_client_event --event-id <EVENT_ID> --owner-user-id <LOCAL_OWNER_ID> --firebase-uid <CLIENT_ACCOUNT_ID>
```

This is a dry run. After reviewing the account, event, permissions and expiry,
repeat it with `--apply`. The default is 30 days with viewing, favourites and
downloads. `--days`, `--no-downloads` and `--no-favourites` limit the grant.
Use `--revoke --apply` for the same account and event to revoke it. This does
not grant studio administrator access. Firebase-linked studio administrators
can also use the authenticated member-management endpoints in `client_events.py`.

Memberships and favourites live in Firestore under
`client_event_access/{eventId}/members/{sha256(uid)}`. Direct browser access is
denied by `firestore.rules`; the private API checks the Firebase ID token,
verified identity, active local event owner, membership, permissions and expiry
on every request. The UID cannot select another client's favourites.

## Running the private origin

The dedicated origin exposes health, Firebase authentication and private event
gallery routes. It excludes the legacy photo and admin routes and static files.
Do not tunnel the existing server on port 8000.

```powershell
.\venv\Scripts\python.exe -B -m uvicorn app.photo_gateway:app --host 127.0.0.1 --port 8010 --no-access-log
.\.phase2-deps\bin\cloudflared.exe tunnel --url http://127.0.0.1:8010 --no-autoupdate
```

Keep both processes running while testing. Use the HTTPS URL printed by
cloudflared as `FIREBASE_PHOTO_API_URL`. Set `FIREBASE_PHOTO_API_ORIGINS` to the
exact frontend origins, separated by commas. Wildcards are rejected. Restart
only the photo gateway when its origins or code changes.

The initial test uses `https://sm-photo-studio.vercel.app` and its verified
Vercel project address. Temporary tunnel URLs change after restart. After a
change, update the ignored local `.env` and the Vercel Production/Preview
`FIREBASE_PHOTO_API_URL`, then rebuild the frontend with `build-config.mjs`.
Never put the Firebase Admin key, local JWT secret or Supabase worker secret
in Vercel's public web configuration.

Preview images are generated locally and remain private. Individual downloads
read the authorized original from the existing external HDD. Selected ZIPs
have limits of 100 photos and 512 MiB of input data. Original files and face
embeddings are not uploaded by this gallery API.

## Verification and rollback

Verify `/health` returns 200 through the tunnel. Without an ID token,
`/api/firebase/events/<EVENT_ID>/photos` must return 401. `/docs`,
`/static/guest.html` and legacy `/api/events/<EVENT_ID>/photos` must return 404.
An account without the event membership must not receive photos. Finally test
a real, explicitly granted client account, expiry, favourites and downloads.
No client memberships are granted automatically during installation.

For rollback, remove the optional public photo API URL, rebuild the frontend
and stop only the test gateway/tunnel. Existing Supabase guest selfies, local
authentication, camera imports and face matching continue independently. The
Supabase relay is still the active transport for the public selfie search.

Domain registration and the Blaze upgrade remain pending by the user's choice.
No Firebase Storage bucket is provisioned in this free setup. A Quick Tunnel
has no uptime guarantee and is intended for testing; a permanent deployment
needs a stable tunnel hostname and Windows process supervision. The computer,
internet connection and HDD must remain available for private photo delivery.

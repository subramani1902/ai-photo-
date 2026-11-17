# Phase 2: optional Firebase initialization and authentication

The selected Firebase project is **smphotography-f8c83** (project number
271167879363). Email/password Authentication is enabled. The **SM Photography
Web** app is registered as `1:271167879363:web:2a0cf82a1268bc905a4a76`.
The Firestore **Standard** `(default)` database is provisioned in **Mumbai
(`asia-south1`)**, with deny-all client rules deployed and verified.

Firebase is enabled in the ignored local `.env`; `.env.example` remains disabled
for fresh installations. This phase keeps the existing Supabase relay, local
face engine/index, camera importer, original-photo storage and event/QR workflows.

## Current boundaries

- `app/core/firebase.py` lazily initializes a named Admin app. Importing the
  backend does not contact Firebase or require the SDK when Firebase is disabled.
- Firestore and Storage helpers return client handles; they create no documents,
  buckets or public objects. A bucket name is required only when Storage is used.
- Firebase ID tokens are verified with the Admin SDK, including signature,
  project, issuer, expiry, disabled-user and revocation checks. Verification
  failures never fall back to decoding an unverified token or accepting a local JWT.
- New read-only routes are `GET /api/firebase/auth/me` and
  `GET /api/firebase/auth/admin`. They use `Authorization: Bearer <Firebase ID token>`.
  They do not accept Firebase custom sign-in tokens.
- Existing `/api/auth/*`, event, camera-import, search, guest QR and Supabase
  endpoints continue to use their existing contracts. A Firebase token does not
  grant access to the old administrator endpoints in this phase.
- `online/account.html` provides Email/password sign-in, sign-out and password
  reset, using the existing typography, colours and card styling. The contact
  menu links to it. `online/firebase-client.js` uses named client/admin contexts
  and memory-only authentication persistence. Signing in does not grant gallery
  or administrator permissions automatically.
- `online/server/` contains server-only helpers for the metadata API in Phase 3.
  There are no new Vercel API functions. `online/vercel.json` runs the public
  configuration generator at build time. The six public Firebase web settings
  are configured for Preview and Production in `smblog/ai-photo-k5fu`, whose
  verified public alias is `sm-photo-studio.vercel.app`. The Admin key has not
  been uploaded to Vercel.
- Firestore rules deny direct reads/writes for everyone, including authenticated
  browsers. There are no client collections yet. Admin SDK access uses server
  IAM and must also enforce the API's explicit authentication and authorization.
- Firebase startup configuration failures are logged without secret details;
  local/Supabase services stay available and Firebase endpoints fail closed.

## Configure the actual project securely

Use the root `.env` locally and server environment settings when deployed:

| Variable | Requirement |
| --- | --- |
| `FIREBASE_ENABLED` | `false` until matching credentials are configured; explicit `true` activates only the new authentication helpers |
| `FIREBASE_PROJECT_ID` | `smphotography-f8c83` |
| `FIREBASE_CREDENTIALS_FILE` | Absolute path to this project's service-account JSON, outside `online/` |
| `FIREBASE_STORAGE_BUCKET` | Actual bucket name from Firebase; not needed for Phase 2 authentication |
| `FIREBASE_EMULATOR_MODE` | `false` for the actual project |
| `FIREBASE_WEB_API_KEY` | Public web-app configuration copied from the selected Firebase web app |
| `FIREBASE_WEB_AUTH_DOMAIN` | Actual web-app configuration; do not infer a domain |
| `FIREBASE_WEB_APP_ID` | Actual web-app ID; the project number is not a substitute |

Python supports Application Default Credentials when no explicit file is configured.
The future Vercel server helper also supports `FIREBASE_ADMIN_CREDENTIALS_JSON`
as a server-only environment variable instead of a file. Never put this JSON in
a browser configuration, `NEXT_PUBLIC_*` variable, or the static website.
Explicit service-account files/JSON must belong to the configured project.

The matching service-account file supplied for **smphotography-f8c83** is
configured by absolute path in the ignored root `.env`. It remains outside the
repository and the static website. The earlier **smphotography-f680c** key is not
used. Do not copy either key into `online/`, source control, or public configuration.

The actual public configuration was retrieved from the registered Firebase web
app, saved in the ignored root `.env`, and generated into the ignored
`online/firebase-config.js`. Storage is not configured by this phase: a bucket
name appearing in Firebase's web configuration does not prove it is provisioned.
No Firebase users have been automatically created or linked to local accounts.

The Firebase Python dependency is pinned in `requirements.txt` and installed in
the repository virtual environment. The installation retained the previously
installed dependency versions. Node dependencies are pinned by `online/package-lock.json`.

## Explicitly link an administrator

Create/identify an enabled Firebase Auth user in the selected project, with a
verified email matching the existing local administrator. Obtain its actual UID.
Account creation and email verification use Firebase's normal workflow; this
repository exposes no public administrator-linking endpoint.

From the repository root, with matching credentials and Firebase enabled:

```powershell
python -m utilities.link_firebase_admin --firebase-uid ACTUAL_FIREBASE_UID --local-user-id 1
```

This is a dry run. It verifies the Firebase user and checks the local identity
without changing the schema or records. Apply the reviewed binding explicitly:

```powershell
python -m utilities.link_firebase_admin --firebase-uid ACTUAL_FIREBASE_UID --local-user-id 1 --apply
```

Only the additive `firebase_admin_links` table is created. Existing user IDs,
event ownership, passwords, photographs, embeddings and indexes are preserved.
Linking is idempotent. Conflicting identities are rejected. Matching email alone
or a Firebase `admin` custom claim never grants administrator access.

Deactivate a binding locally (no cloud write):

```powershell
python -m utilities.link_firebase_admin --firebase-uid ACTUAL_FIREBASE_UID --local-user-id 1 --deactivate
python -m utilities.link_firebase_admin --firebase-uid ACTUAL_FIREBASE_UID --local-user-id 1 --deactivate --apply
```

The new admin endpoint checks the active local user and binding on every request.
If the mapping table has not been initialized, it fails closed; startup never
creates or modifies it automatically.

## Custom gallery authentication foundation

Python `create_gallery_sign_in_token` and JavaScript `createGallerySignInToken`
are internal building blocks for Phase 5, not public endpoints. They use the
`gallery-client:` UID namespace and a fixed gallery/session/credential-version
claim set. They cannot mint arbitrary administrator claims.

The future password endpoint must check the password, gallery expiry and live
membership **before** invoking either helper. Every subsequent gallery/photo
request must still check live permissions and credential version. Custom-token
expiration alone does not end a Firebase user session. No gallery access is
enabled by Phase 2.

## Public configuration generation

From `online/`, run `npm ci --ignore-scripts` to install the pinned JavaScript
dependencies. To generate the optional, ignored browser config from root `.env`,
run this from the repository root with Node 22:

```powershell
node --env-file=.env online/build-config.mjs
```

The generator copies only explicitly allowlisted public web-app values. Disabled
mode generates `{ "enabled": false }`; enabled mode rejects incomplete values.
Only the new account page loads this generated configuration. The existing guest
page keeps its Supabase search transport. Vercel's build command generates the
file from the deployment's public Firebase environment values; it must never
receive the service-account JSON as a public value. No Vercel production release
is made by this setup step.

Verified protected Preview:
https://ai-photo-k5fu-ag4azrv17-smblog.vercel.app/account.html

This Preview contains only the public frontend files and deployment configuration.
Authenticated Vercel checks confirmed the account page and generated Firebase
configuration return HTTP 200, use the selected project and keep the existing
guest Supabase relay URL. Production has not been promoted. The six public web
settings are configured in both Vercel Preview and Production for the next build.

## Offline tests and emulator isolation

```powershell
.\venv\Scripts\python.exe -B -m unittest discover -s tests -v
npm.cmd --prefix online test
```

Python tests verify actual RSA-signed ID tokens through the real Admin SDK.
Certificate transport and Firebase user-directory reads are replaced with local
fixtures, so no real account, private key, or cloud project is used. SQLite tests
use an isolated in-memory database. Browser tests inject a local SDK stub.

For future Auth emulator tests use a **separate** `demo-` project, explicit
`FIREBASE_EMULATOR_MODE=true`, and `FIREBASE_AUTH_EMULATOR_HOST=127.0.0.1:9099`.
No production credentials may be loaded in emulator mode. The optional Firestore
helper requires its loopback `FIRESTORE_EMULATOR_HOST`; the Python Storage helper
requires the Google SDK's URL format `STORAGE_EMULATOR_HOST=http://127.0.0.1:9199`.
Production mode rejects emulator variables, including ones introduced after app
initialization. Browser emulators also require a page served on localhost.

## Rollback and remaining verification

Set `FIREBASE_ENABLED=false` and restart only a service that was deliberately
activated for Firebase. Existing Supabase and local authentication need no data
rollback. An unused additive identity table may remain in place.

Verified live setup:

- The credential and web app belong to `smphotography-f8c83`.
- Email/password is enabled, with password required.
- Firestore is Standard, native mode, `(default)`, `asia-south1`.
- Server-side create/read/delete passed with a temporary diagnostic document
  containing no user/photo data; the document was removed.
- The deployed rules match `firestore.rules`; an unauthenticated REST read
  returns `403 PERMISSION_DENIED`.
- The actual browser Auth SDK loads and the sign-in page renders correctly.

Successful sign-in with a real user and the existing administrator UID binding
still require that user's account. Do not infer a UID from an email address.
The optional test emulator uses separate `demo-` projects and no production key.

For deployment commands, authenticate the Firebase CLI as the project owner.
The runtime service-account key has data-access permissions, but cannot create
databases, enable APIs or publish rules. Provisioning does not require expanding
the runtime key's privileges:

```powershell
npx -y firebase-tools@latest deploy --only auth --project smphotography-f8c83
npx -y firebase-tools@latest deploy --only firestore --project smphotography-f8c83
```

Keep the Windows service supervisor running for backend/importer/worker recovery.
Firebase manages Auth/Firestore availability; local face matching and HDD access
require this PC, its external disk and its internet connection to remain online.
The supervisor in `start-all.ps1` checks every 15 seconds. Use its existing
event/folder settings when installing or running it; do not start a second importer.

Full guest-search and private-gallery cutover remains separate: temporary Firebase
Storage, a permanent authenticated Cloudflare photo API, relay adaptation, private
gallery APIs/UI, expiry/rate limits and end-to-end matching/download tests are
still required. Keep Supabase enabled until those checks pass.

The supplied Cloudflare Account ID and Zone ID currently identify a pending zone
for `sm-photo-studio.vercel.app`, a Vercel-provided hostname. A permanent tunnel
needs a domain whose DNS the user controls. `smphotography.in` is a proposed name,
not yet purchased. The website can remain on Vercel; a future owned-domain API
hostname must be configured from verified DNS/tunnel values. No tunnel or DNS
change has been made.

The official .in registry lookup found `smphotography.in` already registered.
`smphotographyworld.in` and `smphotographystudio.in` returned no registration
record at the time of checking; confirm availability and price with the registrar.
No domain purchase is authorized or performed. The Firebase project currently
has no billing linked. Provisioning Firebase Storage requires the owner to enable
the Blaze plan first; no billing upgrade or bucket creation has been performed.

References: [Admin setup](https://firebase.google.com/docs/admin/setup),
[ID-token verification](https://firebase.google.com/docs/auth/admin/verify-id-tokens),
[revocation checks](https://firebase.google.com/docs/auth/admin/manage-sessions),
[custom sign-in tokens](https://firebase.google.com/docs/auth/admin/create-custom-tokens),
[Auth emulator isolation](https://firebase.google.com/docs/emulator-suite/connect_auth).

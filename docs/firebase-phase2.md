# Phase 2: optional Firebase initialization and authentication

The selected Firebase project is **smphotography-f8c83** (project number
271167879363). Firebase is disabled by default. This phase does not migrate the
relay, modify the face engine/index, change the website layout, deploy cloud
resources, or change existing login/QR behaviour.

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
- `online/firebase-client.js` is a dormant helper for later integration. It uses
  named client/admin contexts and memory-only authentication persistence.
- `online/server/` contains server-only helpers for the metadata API in Phase 3.
  There are no new Vercel API functions or changes to the current deployment settings.

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

The supplied service-account key for **smphotography-f680c** does not match the
selected project and must not be configured here. No key has been copied or
activated. Configure the matching key securely when it is available.

Firebase web configuration is intentionally incomplete until the actual values
are supplied. There are no invented API keys, bucket names, auth domains, UIDs,
or web-app IDs in the application configuration.

The Firebase Python dependency is pinned in `requirements.txt`. During Phase 2,
testing uses an isolated `.phase2-deps/python` directory to avoid changing the
running importer environment. Install the updated requirements into the intended
service environment when preparing activation; no service restart is performed
by these modules.

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
The existing page does not load this generated file yet. The current Vercel
build/install settings remain unchanged; production integration belongs to Phase 3.

## Offline tests and emulator isolation

```powershell
$env:PYTHONPATH = Join-Path (Get-Location) '.phase2-deps/python'
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

Live sign-in, IAM permissions, selected-project credential verification and the
Firebase web app remain unverified until the matching credentials/configuration
are available. No Supabase decommissioning or production deployment is part of
Phase 2.

References: [Admin setup](https://firebase.google.com/docs/admin/setup),
[ID-token verification](https://firebase.google.com/docs/auth/admin/verify-id-tokens),
[revocation checks](https://firebase.google.com/docs/auth/admin/manage-sessions),
[custom sign-in tokens](https://firebase.google.com/docs/auth/admin/create-custom-tokens),
[Auth emulator isolation](https://firebase.google.com/docs/emulator-suite/connect_auth).

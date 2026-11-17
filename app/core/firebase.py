"""Lazy Firebase Admin clients. Importing this module never contacts Firebase."""

import importlib
import json
from pathlib import Path
from threading import RLock

from app.core.firebase_config import FirebaseConfigurationError, get_firebase_settings

_lock = RLock()
_initialized_settings = None
_app = None


def load_admin_sdk():
    try:
        return importlib.import_module("firebase_admin")
    except ImportError:
        raise FirebaseConfigurationError("Install the pinned firebase-admin dependency before enabling Firebase.") from None


def _credential(sdk, settings):
    credentials = importlib.import_module("firebase_admin.credentials")
    if settings.emulator_mode:
        from google.auth.credentials import AnonymousCredentials

        class EmulatorCredential(credentials.Base):
            def get_credential(self):
                return AnonymousCredentials()

        return EmulatorCredential()
    if settings.credentials_file:
        try:
            data = json.loads(Path(settings.credentials_file).read_text(encoding="utf-8-sig"))
            if data.get("type") != "service_account" or data.get("project_id") != settings.project_id:
                raise ValueError("Credential project mismatch")
            return credentials.Certificate(data)
        except (OSError, ValueError, TypeError, AttributeError):
            raise FirebaseConfigurationError("The Firebase service-account file is invalid or belongs to another project.") from None
    return credentials.ApplicationDefault()


def get_firebase_app():
    global _app, _initialized_settings
    settings = get_firebase_settings()
    settings.require_enabled()
    # Check mode on every access: an emulator variable added after startup must
    # never silently turn production signature verification into emulator mode.
    with _lock:
        if _app is not None:
            if _initialized_settings != settings:
                raise FirebaseConfigurationError("Firebase configuration changed; restart the process before continuing.")
            return _app
        sdk = load_admin_sdk()
        options = {"projectId": settings.project_id, "httpTimeout": 15}
        if settings.storage_bucket:
            options["storageBucket"] = settings.storage_bucket
        try:
            _app = sdk.initialize_app(_credential(sdk, settings), options, name="event-photo-finder")
        except FirebaseConfigurationError:
            raise
        except (ValueError, OSError):
            raise FirebaseConfigurationError("Unable to initialize Firebase Admin. Check server credentials.") from None
        _initialized_settings = settings
        return _app


def get_firebase_auth():
    get_firebase_app()
    return importlib.import_module("firebase_admin.auth")


def get_firestore_client():
    settings = get_firebase_settings()
    settings.require_enabled()
    if settings.emulator_mode and not dict(settings.emulator_hosts).get("FIRESTORE_EMULATOR_HOST"):
        raise FirebaseConfigurationError("Firestore emulator access requires FIRESTORE_EMULATOR_HOST.")
    app = get_firebase_app()
    return importlib.import_module("firebase_admin.firestore").client(app=app)


def get_storage_bucket():
    settings = get_firebase_settings()
    settings.require_enabled()
    if not settings.storage_bucket:
        raise FirebaseConfigurationError("Set FIREBASE_STORAGE_BUCKET before using temporary Storage.")
    if settings.emulator_mode and not dict(settings.emulator_hosts).get("STORAGE_EMULATOR_HOST"):
        raise FirebaseConfigurationError("Storage emulator access requires STORAGE_EMULATOR_HOST.")
    app = get_firebase_app()
    return importlib.import_module("firebase_admin.storage").bucket(settings.storage_bucket, app=app)

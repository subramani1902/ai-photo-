"""Opt-in Firebase configuration, independent of the existing relay/JWT settings."""

import ipaddress
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PUBLIC_ROOT = PROJECT_ROOT / "online"
EMULATOR_VARIABLES = (
    "FIREBASE_AUTH_EMULATOR_HOST",
    "FIRESTORE_EMULATOR_HOST",
    "STORAGE_EMULATOR_HOST",
    "FIREBASE_STORAGE_EMULATOR_HOST",
)


class FirebaseConfigurationError(RuntimeError):
    """Firebase is disabled or its configuration is unsafe/incomplete."""


def env_bool(environment: Mapping[str, str], name: str) -> bool:
    value = environment.get(name, "false").strip().lower()
    if value not in {"true", "false"}:
        raise FirebaseConfigurationError(f"{name} must be true or false.")
    return value == "true"


def validate_emulator_host(value: str, *, storage_url=False) -> None:
    try:
        parsed = urlsplit(value if storage_url else "http://" + value)
        port = parsed.port
        host = parsed.hostname
        loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except (TypeError, ValueError):
        raise FirebaseConfigurationError("Invalid loopback emulator address.") from None
    if (parsed.scheme != "http" or not loopback or not port or parsed.username or parsed.password
            or parsed.path or parsed.query or parsed.fragment):
        raise FirebaseConfigurationError("Use a loopback host:port; STORAGE_EMULATOR_HOST requires an http:// prefix.")


@dataclass(frozen=True)
class FirebaseSettings:
    enabled: bool = False
    project_id: str = ""
    storage_bucket: str = ""
    credentials_file: str = ""
    emulator_mode: bool = False
    emulator_hosts: tuple[tuple[str, str], ...] = ()

    @classmethod
    def from_env(cls, environment: Mapping[str, str]):
        enabled = env_bool(environment, "FIREBASE_ENABLED")
        if not enabled:
            return cls()  # Optional settings cannot break the existing Supabase/local path.
        project_id = environment.get("FIREBASE_PROJECT_ID", "").strip()
        if not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", project_id):
            raise FirebaseConfigurationError("Set a valid FIREBASE_PROJECT_ID before enabling Firebase.")
        emulator_mode = env_bool(environment, "FIREBASE_EMULATOR_MODE")
        hosts = tuple((key, environment[key].strip()) for key in EMULATOR_VARIABLES
                      if environment.get(key, "").strip())
        if hosts and not emulator_mode:
            raise FirebaseConfigurationError("Emulator environment variables are forbidden in production mode.")
        if emulator_mode:
            if not project_id.startswith("demo-"):
                raise FirebaseConfigurationError("Emulators require a separate demo- project ID.")
            if not dict(hosts).get("FIREBASE_AUTH_EMULATOR_HOST"):
                raise FirebaseConfigurationError("Set FIREBASE_AUTH_EMULATOR_HOST for emulator mode.")
            for key, host in hosts:
                validate_emulator_host(host, storage_url=key == "STORAGE_EMULATOR_HOST")
        elif project_id.startswith("demo-"):
            raise FirebaseConfigurationError("A demo- project requires explicit emulator mode.")
        credential = environment.get("FIREBASE_CREDENTIALS_FILE", "").strip()
        adc_file = environment.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
        if emulator_mode and (credential or adc_file):
            raise FirebaseConfigurationError("Do not load production credentials in emulator mode.")
        for value in (credential, adc_file):
            if value:
                path = Path(value).expanduser().resolve()
                if path.is_relative_to(PUBLIC_ROOT.resolve()):
                    raise FirebaseConfigurationError("Admin credentials must be outside the online public directory.")
                if not path.is_file():
                    raise FirebaseConfigurationError("The configured Firebase credential file is unavailable.")
        bucket = environment.get("FIREBASE_STORAGE_BUCKET", "").strip()
        if bucket and not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]", bucket):
            raise FirebaseConfigurationError("FIREBASE_STORAGE_BUCKET must be a bucket name, without a URL or path.")
        return cls(enabled, project_id, bucket, credential, emulator_mode, hosts)

    def require_enabled(self):
        if not self.enabled:
            raise FirebaseConfigurationError("Firebase is not enabled.")


def get_firebase_settings() -> FirebaseSettings:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    return FirebaseSettings.from_env(os.environ)

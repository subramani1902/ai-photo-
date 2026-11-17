"""Firebase token boundaries; no public token-minting endpoint is exposed."""

import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from app.core.firebase import get_firebase_app, get_firebase_auth
from app.core.firebase_config import FirebaseConfigurationError, get_firebase_settings


class InvalidFirebaseToken(ValueError):
    pass


class FirebaseAuthUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class FirebasePrincipal:
    uid: str
    project_id: str
    claims: Mapping = field(repr=False)


def verify_firebase_id_token(token: str) -> FirebasePrincipal:
    if not isinstance(token, str) or not token or len(token) > 16384 or any(c.isspace() for c in token):
        raise InvalidFirebaseToken("Invalid or expired Firebase ID token.")
    settings = get_firebase_settings()
    settings.require_enabled()
    app = get_firebase_app()
    auth = get_firebase_auth()
    try:
        # Custom sign-in tokens are NOT API bearer tokens. The SDK enforces the
        # ID-token signature, audience, issuer and expiry. Revocation is mandatory.
        claims = auth.verify_id_token(token, app=app, check_revoked=True, clock_skew_seconds=0)
    except auth.CertificateFetchError:
        raise FirebaseAuthUnavailable("Firebase authentication is temporarily unavailable.") from None
    except (auth.InvalidIdTokenError, auth.RevokedIdTokenError, auth.UserDisabledError,
            auth.UserNotFoundError, ValueError):
        raise InvalidFirebaseToken("Invalid or expired Firebase ID token.") from None
    except Exception:
        # Credential/transport failures never downgrade to unverified or local JWTs.
        raise FirebaseAuthUnavailable("Firebase authentication is temporarily unavailable.") from None
    if not isinstance(claims, dict) or not isinstance(claims.get("firebase", {}), dict):
        raise InvalidFirebaseToken("Invalid or expired Firebase ID token.")
    uid = claims.get("uid")
    if (not isinstance(uid, str) or not 1 <= len(uid) <= 128
            or claims.get("sub") != uid or claims.get("aud") != settings.project_id
            or claims.get("iss") != f"https://securetoken.google.com/{settings.project_id}"
            or claims.get("firebase", {}).get("tenant")):
        raise InvalidFirebaseToken("Invalid or expired Firebase ID token.")
    return FirebasePrincipal(uid, settings.project_id, MappingProxyType(dict(claims)))


def create_gallery_sign_in_token(*, client_id: str, gallery_id: str,
                                session_id: str, credential_version: int) -> str:
    """Internal Phase 5 building block: caller must first authorize the gallery session.

    These claims identify a scope, not photo permissions. Future gallery APIs must
    still check the live membership, password version and expiry on every request.
    """
    for value in (client_id, gallery_id, session_id):
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value):
            raise ValueError("Invalid gallery authentication scope.")
    if type(credential_version) is not int or credential_version < 1:
        raise ValueError("Invalid gallery credential version.")
    app = get_firebase_app()
    settings = get_firebase_settings()
    if settings.emulator_mode:
        raise FirebaseConfigurationError("Gallery token signing requires configured signing credentials.")
    try:
        token = get_firebase_auth().create_custom_token(
            f"gallery-client:{client_id}",
            {"actor_type": "gallery_client", "gallery_id": gallery_id,
             "gallery_session_id": session_id, "credential_version": credential_version},
            app=app,
        )
        return token.decode("utf-8") if isinstance(token, bytes) else token
    except Exception:
        raise FirebaseAuthUnavailable("Unable to create a Firebase sign-in token.") from None

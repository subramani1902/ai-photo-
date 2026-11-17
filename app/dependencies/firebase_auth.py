"""New Firebase dependencies; legacy administrator and QR dependencies are unchanged."""

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.firebase_config import FirebaseConfigurationError
from app.core.firebase_tokens import (
    FirebaseAuthUnavailable, FirebasePrincipal, InvalidFirebaseToken, verify_firebase_id_token,
)
from app.database.cloud_models import FirebaseAdminLink
from app.database.db import get_db
from app.database.models import User

firebase_bearer = HTTPBearer(auto_error=False, scheme_name="FirebaseIDToken")


def get_current_firebase_principal(
    credentials: HTTPAuthorizationCredentials = Depends(firebase_bearer),
) -> FirebasePrincipal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(401, "A Firebase ID token is required.", headers={"WWW-Authenticate": "Bearer"})
    try:
        return verify_firebase_id_token(credentials.credentials)
    except InvalidFirebaseToken:
        raise HTTPException(401, "Invalid or expired Firebase ID token.",
                            headers={"WWW-Authenticate": "Bearer"}) from None
    except (FirebaseConfigurationError, FirebaseAuthUnavailable):
        raise HTTPException(503, "Firebase authentication is unavailable.") from None


def get_current_firebase_admin(
    principal: FirebasePrincipal = Depends(get_current_firebase_principal),
    db: Session = Depends(get_db),
) -> User:
    if principal.uid.startswith("gallery-client:") or principal.claims.get("actor_type") == "gallery_client":
        raise HTTPException(403, "Administrator access denied.")
    try:
        user = (db.query(User).join(FirebaseAdminLink, FirebaseAdminLink.user_id == User.id)
                .filter(FirebaseAdminLink.project_id == principal.project_id,
                        FirebaseAdminLink.firebase_uid == principal.uid,
                        FirebaseAdminLink.is_active.is_(True), User.is_active.is_(True))
                .one_or_none())
    except SQLAlchemyError:
        raise HTTPException(503, "Firebase administrator mapping is unavailable.") from None
    # Neither an email match nor an admin custom claim grants administrator access.
    if user is None or principal.claims.get("email_verified") is not True:
        raise HTTPException(403, "Administrator access denied.")
    email = principal.claims.get("email")
    if not isinstance(email, str) or email.strip().lower() != user.email.strip().lower():
        raise HTTPException(403, "Administrator access denied.")
    return user

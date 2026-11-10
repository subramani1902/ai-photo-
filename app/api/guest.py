
import base64
import hashlib
import io
import os
import secrets
import uuid

from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from fastapi.responses import FileResponse
from urllib.parse import urlencode

import qrcode

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    UploadFile,
    File,
    Query,
    Header
)
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from jose import jwt, JWTError
from fastapi.security import HTTPAuthorizationCredentials

from app.database.db import get_db
from app.database.models import (
    Event,
    Face,
    Photo,
    User,
    GuestAccess
)
from app.dependencies.auth import get_current_admin
from app.dependencies.guest import get_current_guest, guest_bearer
from app.core.config import (
    JWT_SECRET_KEY,
    JWT_ALGORITHM
)
from app.core.security import decode_access_token
from app.ai.face_engine import FaceEngine
from app.services.persistent_face_index import PersistentFaceIndex


router = APIRouter(tags=["Guest Photo Finder"])


# --------------------------------------------------
# CONFIGURATION
# --------------------------------------------------

GUEST_QR_BASE_URL = os.getenv(
    "GUEST_QR_BASE_URL",
    "http://127.0.0.1:8000/guest"
).strip()

GUEST_QR_EXPIRE_DAYS = int(
    os.getenv("GUEST_QR_EXPIRE_DAYS", "30")
)

GUEST_SESSION_EXPIRE_MINUTES = int(
    os.getenv("GUEST_SESSION_EXPIRE_MINUTES", "30")
)

MAX_FILE_SIZE = 50 * 1024 * 1024

ALLOWED_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp"
}

STRONG_MATCH_THRESHOLD = 0.60
POSSIBLE_MATCH_THRESHOLD = 0.45

UPLOAD_DIR = Path("storage/private_uploads")


# --------------------------------------------------
# REQUEST MODELS
# --------------------------------------------------

class GuestSessionRequest(BaseModel):
    qr_token: str = Field(min_length=20, max_length=200)


# --------------------------------------------------
# FACE ENGINE
# --------------------------------------------------

@lru_cache(maxsize=1)
def get_face_engine():
    return FaceEngine()


# --------------------------------------------------
# HELPERS
# --------------------------------------------------

def hash_guest_token(token: str) -> str:
    return hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()


def get_owned_event(
    db: Session,
    event_id: int,
    admin_id: int
):
    event = (
        db.query(Event)
        .filter(
            Event.id == event_id,
            Event.owner_id == admin_id
        )
        .first()
    )

    if event is None:
        raise HTTPException(
            status_code=404,
            detail="Event not found."
        )

    return event


def classify_match(similarity: float) -> str:

    if similarity >= STRONG_MATCH_THRESHOLD:
        return "strong_match"

    if similarity >= POSSIBLE_MATCH_THRESHOLD:
        return "possible_match"

    return "low_match"


def make_guest_qr(url: str) -> str:

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4
    )

    qr.add_data(url)
    qr.make(fit=True)

    image = qr.make_image(
        fill_color="black",
        back_color="white"
    )

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")

    return base64.b64encode(
        buffer.getvalue()
    ).decode("utf-8")


# --------------------------------------------------
# 1. ADMIN: GENERATE EVENT QR CODE
# --------------------------------------------------

@router.post("/api/events/{event_id}/guest-access")
def create_guest_access(
    event_id: int,
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin)
):

    if not GUEST_QR_BASE_URL.startswith(("http://", "https://")):
        raise HTTPException(
            status_code=500,
            detail="Invalid GUEST_QR_BASE_URL configuration."
        )

    if GUEST_QR_EXPIRE_DAYS < 1:
        raise HTTPException(
            status_code=500,
            detail="GUEST_QR_EXPIRE_DAYS must be at least 1."
        )

    event = get_owned_event(
        db,
        event_id,
        current_admin.id
    )

    # Revoke previous QR credentials for this event.
    db.query(GuestAccess).filter(
        GuestAccess.event_id == event_id,
        GuestAccess.is_active.is_(True)
    ).update(
        {GuestAccess.is_active: False},
        synchronize_session=False
    )

    raw_token = secrets.token_urlsafe(32)

    expires_at = (
        datetime.now(timezone.utc)
        + timedelta(days=GUEST_QR_EXPIRE_DAYS)
    )

    access = GuestAccess(
        event_id=event_id,
        token_hash=hash_guest_token(raw_token),
        is_active=True,
        expires_at=expires_at
    )

    try:
        db.add(access)
        db.commit()
        db.refresh(access)

    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Unable to create guest access."
        )

    separator = "&" if "?" in GUEST_QR_BASE_URL else "?"

    guest_url = (
        GUEST_QR_BASE_URL
        + separator
        + urlencode({"token": raw_token})
    )

    try:
        qr_base64 = make_guest_qr(guest_url)

    except Exception:
        # Revoke access if QR generation fails.
        access.is_active = False
        db.commit()

        raise HTTPException(
            status_code=500,
            detail="Unable to generate guest QR code."
        )

    return {
        "status": "success",
        "message": "Guest QR code generated successfully.",
        "event_id": event.id,
        "event_name": event.event_name,
        "guest_url": guest_url,
        "qr_code_base64": qr_base64,
        "expires_at": expires_at.isoformat(),
        "expires_in_days": GUEST_QR_EXPIRE_DAYS,
        "instruction": (
            "Keep the guest URL and QR code private. "
            "Anyone possessing them can request guest access."
        )
    }


# --------------------------------------------------
# 2. ADMIN: REVOKE EVENT QR ACCESS
# --------------------------------------------------

@router.delete("/api/events/{event_id}/guest-access")
def revoke_guest_access(
    event_id: int,
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin)
):

    get_owned_event(
        db,
        event_id,
        current_admin.id
    )

    updated = (
        db.query(GuestAccess)
        .filter(
            GuestAccess.event_id == event_id,
            GuestAccess.is_active.is_(True)
        )
        .update(
            {GuestAccess.is_active: False},
            synchronize_session=False
        )
    )

    db.commit()

    return {
        "status": "success",
        "event_id": event_id,
        "revoked_access_records": updated,
        "message": "Guest access revoked."
    }


# --------------------------------------------------
# 3. GUEST: EXCHANGE QR TOKEN FOR SESSION JWT
# --------------------------------------------------

@router.post("/api/guest/session")
def create_guest_session(
    data: GuestSessionRequest,
    db: Session = Depends(get_db)
):

    token_hash = hash_guest_token(
        data.qr_token
    )

    access = (
        db.query(GuestAccess)
        .filter(
            GuestAccess.token_hash == token_hash,
            GuestAccess.is_active.is_(True)
        )
        .first()
    )

    if access is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired event QR code."
        )

    if access.expires_at is not None:

        expiry = access.expires_at

        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)

        if expiry <= datetime.now(timezone.utc):
            raise HTTPException(
                status_code=401,
                detail="Event QR code has expired."
            )

    event = db.query(Event).filter(
        Event.id == access.event_id
    ).first()

    if event is None:
        raise HTTPException(
            status_code=404,
            detail="Event not found."
        )

    expires_at = (
        datetime.now(timezone.utc)
        + timedelta(minutes=GUEST_SESSION_EXPIRE_MINUTES)
    )

    payload = {
        "sub": "guest",
        "type": "guest",
        "event_id": access.event_id,
        "guest_access_id": access.id,
        "jti": secrets.token_urlsafe(24),
        "exp": expires_at
    }

    guest_token = jwt.encode(
        payload,
        JWT_SECRET_KEY,
        algorithm=JWT_ALGORITHM
    )

    return {
        "status": "success",
        "token_type": "bearer",
        "access_token": guest_token,
        "expires_at": expires_at.isoformat(),
        "event": {
            "id": event.id,
            "name": event.event_name
        }
    }


# --------------------------------------------------
# 4. GUEST: SEARCH EVENT PHOTOS USING SELFIE
# --------------------------------------------------

@router.post("/api/guest/search")
async def guest_photo_search(
    file: UploadFile = File(...),

    top_k: int = Query(
        default=10,
        ge=1,
        le=20
    ),

    min_similarity: float = Query(
        default=POSSIBLE_MATCH_THRESHOLD,
        ge=POSSIBLE_MATCH_THRESHOLD,
        le=1.0
    ),

    guest_access: GuestAccess = Depends(get_current_guest),

    credentials: HTTPAuthorizationCredentials = Depends(guest_bearer),

    db: Session = Depends(get_db)
):

    extension = Path(
        file.filename or ""
    ).suffix.lower()

    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail="Unsupported image format."
        )

    content = await file.read(MAX_FILE_SIZE + 1)

    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail="Maximum upload size is 50 MB."
        )

    if not content:
        raise HTTPException(
            status_code=400,
            detail="Uploaded file is empty."
        )

    UPLOAD_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    temp_path = (
        UPLOAD_DIR /
        f"{uuid.uuid4().hex}{extension}"
    )

    try:

        temp_path.write_bytes(content)

        engine = get_face_engine()

        detection = engine.process_image(
            temp_path
        )

        total_faces = detection["total_faces"]

        if total_faces == 0:
            raise HTTPException(
                status_code=422,
                detail="No face detected in uploaded image."
            )

        if total_faces != 1:
            raise HTTPException(status_code=422, detail="Please use a selfie with exactly one face.")

        event_id = guest_access.event_id

        search_engine = PersistentFaceIndex()

        try:
            search_engine.load_event_index(event_id)

        except FileNotFoundError:
            raise HTTPException(
                status_code=404,
                detail="Event search index not found."
            )

        matched_photos = {}

        for uploaded_face in detection["faces"]:

            matches = search_engine.search(
                uploaded_face["embedding"],
                top_k=top_k
            )

            for match in matches:

                similarity = float(
                    match["similarity"]
                )

                if similarity < min_similarity:
                    continue

                record = (
                    db.query(Face, Photo)
                    .join(
                        Photo,
                        Face.photo_id == Photo.id
                    )
                    .filter(
                        Face.id == match["face_id"],
                        Photo.event_id == event_id
                    )
                    .first()
                )

                if record is None:
                    continue

                face, photo = record

                photo_id = photo.id

                if (
                    photo_id not in matched_photos
                    or similarity > matched_photos[photo_id]["similarity"]
                ):

                    matched_photos[photo_id] = {
                        "photo_id": photo.id,
                        "filename": photo.filename,
                        "similarity": similarity,
                        "match_quality": classify_match(similarity)
                    }

        results = sorted(
            matched_photos.values(),
            key=lambda item: item["similarity"],
            reverse=True
        )[:top_k]

        for result in results:
            result["similarity"] = round(
                result["similarity"],
                4
            )
            session = decode_access_token(credentials.credentials)
            result["photo_token"] = jwt.encode({
                "type": "matched_photo", "photo_id": result["photo_id"],
                "event_id": event_id, "guest_access_id": guest_access.id,
                "session_id": session.get("jti"), "exp": session["exp"]
            }, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)

        return {
            "event_id": event_id,
            "faces_detected": total_faces,
            "total_matches": len(results),
            "matches": results,
            "message": "Guest photo search completed."
        }

    finally:
        await file.close()

        if temp_path.exists():
            temp_path.unlink()


@router.get("/api/guest/photos/{photo_id}")
def get_guest_photo(
    photo_id: int,
    photo_token: str = Header(..., alias="X-Photo-Token"),
    download: bool = False,
    guest_access: GuestAccess = Depends(get_current_guest),
    credentials: HTTPAuthorizationCredentials = Depends(guest_bearer),
    db: Session = Depends(get_db),
):
    try:
        grant = decode_access_token(photo_token)
        session = decode_access_token(credentials.credentials)
        if (grant.get("type") != "matched_photo" or grant.get("photo_id") != photo_id
                or grant.get("event_id") != guest_access.event_id
                or grant.get("guest_access_id") != guest_access.id
                or not session.get("jti") or grant.get("session_id") != session["jti"]):
            raise ValueError("Invalid photo grant")
    except (JWTError, ValueError, KeyError):
        raise HTTPException(status_code=403, detail="Photo access denied.")

    photo = db.query(Photo).filter(Photo.id == photo_id, Photo.event_id == guest_access.event_id).first()
    event = db.query(Event).filter(Event.id == guest_access.event_id).first()
    if photo is None or event is None:
        raise HTTPException(status_code=404, detail="Photo not found.")
    path = Path(photo.file_path).resolve()
    root = (Path("storage/events") / event.event_name).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(status_code=404, detail="Photo not found.")
    return FileResponse(path, filename=photo.filename,
                        content_disposition_type="attachment" if download else "inline",
                        headers={"Cache-Control": "private, no-store"})


@router.get("/guest", include_in_schema=False)
def guest_frontend():
    guest_page = (
        Path(__file__).resolve().parents[1]
        / "static"
        / "guest.html"
    )

    if not guest_page.exists():
        raise HTTPException(
            status_code=404,
            detail="Guest frontend not found."
        )

    return FileResponse(guest_page)

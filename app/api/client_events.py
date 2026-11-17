"""Firebase-authenticated event galleries, backed by existing local photos."""

import re
import tempfile
import time
import zipfile
from collections import OrderedDict, deque
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from threading import Lock
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path as ApiPath, Query, Response
from fastapi.responses import FileResponse, StreamingResponse
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.firebase_tokens import FirebasePrincipal
from app.database.db import get_db
from app.database.models import Event, Face, Photo, User
from app.dependencies.firebase_auth import get_current_firebase_admin, get_current_firebase_principal
from app.services.client_event_access import (
    ClientEventStore, EventAccessDenied, EventAccessUnavailable, get_client_event_store,
)
from app.services.photo_sources import resolve_photo_file

router = APIRouter(prefix="/api/firebase", tags=["Private Client Events"])
PRIVATE_HEADERS = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}
PREVIEW_ROOT = Path(__file__).resolve().parents[2] / "storage" / "private_client_previews"
MAX_ZIP_PHOTOS = 100
MAX_ZIP_BYTES = 512 * 1024 * 1024


class RequestLimits:
    def __init__(self):
        self.lock = Lock()
        self.windows = OrderedDict()

    def check(self, uid, operation, limit):
        now = time.monotonic()
        with self.lock:
            key = (uid, operation)
            if len(self.windows) >= 10000 and key not in self.windows:
                stale = [k for k, v in self.windows.items() if not v or v[-1] <= now - 60]
                for item in stale:
                    self.windows.pop(item, None)
                if len(self.windows) >= 10000:
                    raise HTTPException(429, "Please try again later.", headers={"Retry-After": "60"})
            window = self.windows.setdefault(key, deque())
            while window and window[0] <= now - 60:
                window.popleft()
            if len(window) >= limit:
                raise HTTPException(429, "Please wait before trying again.", headers={"Retry-After": "60"})
            window.append(now)
            self.windows.move_to_end(key)


limits = RequestLimits()


def client_account(principal: FirebasePrincipal = Depends(get_current_firebase_principal)):
    if (principal.claims.get("email_verified") is not True or principal.uid.startswith("gallery-client:")
            or principal.claims.get("actor_type") == "gallery_client"):
        raise HTTPException(403, "Verify your email before opening event photos.")
    return principal


def checked_event(event_id, principal, db, store, *, operation="browse"):
    limits.check(principal.uid, operation, {"browse": 120, "photo": 600, "favourite": 120, "zip": 5}[operation])
    event = (db.query(Event).join(User, User.id == Event.owner_id)
             .filter(Event.id == event_id, User.is_active.is_(True)).one_or_none())
    if event is None:
        raise HTTPException(403, "This event is not available to your account.")
    try:
        permission = store.authorize(principal.uid, event_id, event.owner_id)
    except EventAccessDenied:
        raise HTTPException(403, "This event is not available to your account.") from None
    except EventAccessUnavailable:
        raise HTTPException(503, "The gallery is temporarily unavailable.") from None
    return event, permission


def photo_for_event(db, event, photo_id):
    photo = db.query(Photo).filter_by(id=photo_id, event_id=event.id).one_or_none()
    if photo is None:
        raise HTTPException(404, "Photo not found.")
    try:
        return photo, resolve_photo_file(db, event, photo)
    except (OSError, RuntimeError, ValueError):
        raise HTTPException(404, "Photo is currently unavailable.") from None


def clean_filename(value):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value))[:180] or "photo.jpg"


def preview_file(path, photo_id):
    stat = path.stat()
    digest = sha256(f"{path}:{stat.st_size}:{stat.st_mtime_ns}".encode()).hexdigest()
    PREVIEW_ROOT.mkdir(parents=True, exist_ok=True)
    destination = PREVIEW_ROOT / f"{photo_id}-{digest}.webp"
    if not destination.is_file():
        try:
            with Image.open(path) as source:
                image = ImageOps.exif_transpose(source)
                image.thumbnail((1600, 1600))
                with tempfile.NamedTemporaryFile(dir=PREVIEW_ROOT, suffix=".webp", delete=False) as output:
                    temporary = Path(output.name)
                try:
                    image.convert("RGB").save(temporary, "WEBP", quality=82)
                    temporary.replace(destination)
                finally:
                    temporary.unlink(missing_ok=True)
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError):
            raise HTTPException(404, "Preview is currently unavailable.") from None
    return destination


@router.get("/events/{event_id}")
def event_gallery(response: Response, event_id: int = ApiPath(gt=0, le=2147483647),
                  principal: FirebasePrincipal = Depends(client_account), db: Session = Depends(get_db),
                  store: ClientEventStore = Depends(get_client_event_store)):
    event, permission = checked_event(event_id, principal, db, store)
    response.headers.update(PRIVATE_HEADERS)
    return {"event_id": event.id, "event_name": event.event_name,
            "permissions": sorted(permission.permissions), "expires_at": permission.expires_at.isoformat(),
            "zip_max_photos": MAX_ZIP_PHOTOS, "zip_max_bytes": MAX_ZIP_BYTES}


@router.get("/events/{event_id}/photos")
def event_photos(event_id: int, response: Response, after: int = Query(0, ge=0),
                 limit: int = Query(40, ge=1, le=100),
                 category: Literal["all", "candid", "group", "favourites"] = "all",
                 principal: FirebasePrincipal = Depends(client_account), db: Session = Depends(get_db),
                 store: ClientEventStore = Depends(get_client_event_store)):
    event, permission = checked_event(event_id, principal, db, store)
    try:
        favourites = store.favourites(principal.uid, event.id) if "favourite" in permission.permissions else set()
    except EventAccessUnavailable:
        raise HTTPException(503, "Favourites are temporarily unavailable.") from None
    face_count = func.count(Face.id)
    query = (db.query(Photo, face_count.label("face_count")).outerjoin(Face, Face.photo_id == Photo.id)
             .filter(Photo.event_id == event.id, Photo.id > after).group_by(Photo.id))
    if category == "candid":
        query = query.having(face_count == 1)
    elif category == "group":
        query = query.having(face_count > 1)
    elif category == "favourites":
        query = query.filter(Photo.id.in_(favourites))
    rows = query.order_by(Photo.id).limit(limit + 1).all()
    more = len(rows) > limit
    rows = rows[:limit]
    response.headers.update(PRIVATE_HEADERS)
    return {"photos": [{"id": photo.id, "filename": clean_filename(photo.filename),
                        "face_count": count, "favourite": photo.id in favourites} for photo, count in rows],
            "next_after": rows[-1][0].id if more and rows else None}


@router.get("/events/{event_id}/photos/{photo_id}/preview")
def event_photo_preview(event_id: int, photo_id: int,
                        principal: FirebasePrincipal = Depends(client_account), db: Session = Depends(get_db),
                        store: ClientEventStore = Depends(get_client_event_store)):
    event, _ = checked_event(event_id, principal, db, store, operation="photo")
    photo, path = photo_for_event(db, event, photo_id)
    return FileResponse(preview_file(path, photo.id), media_type="image/webp", headers=PRIVATE_HEADERS)


@router.get("/events/{event_id}/photos/{photo_id}/download")
def event_photo_download(event_id: int, photo_id: int,
                         principal: FirebasePrincipal = Depends(client_account), db: Session = Depends(get_db),
                         store: ClientEventStore = Depends(get_client_event_store)):
    event, permission = checked_event(event_id, principal, db, store, operation="photo")
    if "download" not in permission.permissions:
        raise HTTPException(403, "Downloads are not enabled for your account.")
    photo, path = photo_for_event(db, event, photo_id)
    return FileResponse(path, filename=clean_filename(photo.filename), media_type="application/octet-stream",
                        content_disposition_type="attachment", headers=PRIVATE_HEADERS)


class FavouriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selected: StrictBool


@router.put("/events/{event_id}/photos/{photo_id}/favourite")
def event_photo_favourite(event_id: int, photo_id: int, body: FavouriteRequest, response: Response,
                          principal: FirebasePrincipal = Depends(client_account), db: Session = Depends(get_db),
                          store: ClientEventStore = Depends(get_client_event_store)):
    event, permission = checked_event(event_id, principal, db, store, operation="favourite")
    if "favourite" not in permission.permissions:
        raise HTTPException(403, "Favourites are not enabled for your account.")
    if db.query(Photo.id).filter_by(id=photo_id, event_id=event.id).first() is None:
        raise HTTPException(404, "Photo not found.")
    try:
        store.set_favourite(principal.uid, event.id, event.owner_id, photo_id, body.selected)
    except EventAccessDenied:
        raise HTTPException(403, "This event is not available to your account.") from None
    except EventAccessUnavailable:
        raise HTTPException(503, "Unable to save your favourite.") from None
    response.headers.update(PRIVATE_HEADERS)
    return {"photo_id": photo_id, "selected": body.selected}


class ZipRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    photo_ids: list[StrictInt] = Field(min_length=1, max_length=MAX_ZIP_PHOTOS)


@router.post("/events/{event_id}/download-zip")
def event_zip(event_id: int, body: ZipRequest,
              principal: FirebasePrincipal = Depends(client_account), db: Session = Depends(get_db),
              store: ClientEventStore = Depends(get_client_event_store)):
    event, permission = checked_event(event_id, principal, db, store, operation="zip")
    if "download" not in permission.permissions:
        raise HTTPException(403, "Downloads are not enabled for your account.")
    if any(type(photo_id) is not int or photo_id < 1 for photo_id in body.photo_ids):
        raise HTTPException(400, "Invalid photo selection.")
    files, total = [], 0
    for photo_id in sorted(set(body.photo_ids)):
        photo, path = photo_for_event(db, event, photo_id)
        total += path.stat().st_size
        if total > MAX_ZIP_BYTES:
            raise HTTPException(413, "Choose fewer photos. A ZIP download is limited to 512 MB.")
        files.append((photo, path))
    archive = tempfile.TemporaryFile()
    try:
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as output:
            used = set()
            for photo, path in files:
                name = clean_filename(photo.filename)
                basename, counter = name, 1
                while name.lower() in used:
                    name = f"{photo.id}-{counter}-{basename}"
                    counter += 1
                used.add(name.lower())
                output.write(path, arcname=name)
        size = archive.tell()
        archive.seek(0)
    except Exception:
        archive.close()
        raise HTTPException(503, "The ZIP download is temporarily unavailable.") from None

    def stream():
        try:
            while chunk := archive.read(1024 * 1024):
                yield chunk
        finally:
            archive.close()

    return StreamingResponse(stream(), media_type="application/zip", headers={
        **PRIVATE_HEADERS, "Content-Length": str(size),
        "Content-Disposition": f'attachment; filename="event-{event.id}-photos.zip"',
    })


class AccessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expires_at: datetime
    downloads: StrictBool = True
    favourites: StrictBool = True


@router.put("/admin/events/{event_id}/members/{uid}")
def grant_client_event(event_id: int, uid: str, body: AccessRequest,
                        admin: User = Depends(get_current_firebase_admin), db: Session = Depends(get_db),
                        store: ClientEventStore = Depends(get_client_event_store)):
    event = db.query(Event).filter_by(id=event_id, owner_id=admin.id).one_or_none()
    if event is None:
        raise HTTPException(404, "Event not found.")
    from app.core.firebase import get_firebase_app, get_firebase_auth

    try:
        account = get_firebase_auth().get_user(uid, app=get_firebase_app())
        if account.disabled or uid.startswith("gallery-client:"):
            raise ValueError("Invalid client account")
    except Exception:
        raise HTTPException(400, "Use an existing active client account ID.") from None
    permissions = ["view"] + (["download"] if body.downloads else []) + (["favourite"] if body.favourites else [])
    try:
        store.grant(uid, event.id, admin.id, body.expires_at, permissions)
    except (EventAccessDenied, ValueError):
        raise HTTPException(400, "Choose a future expiry with a timezone.") from None
    except EventAccessUnavailable:
        raise HTTPException(503, "Unable to save event access.") from None
    return {"event_id": event.id, "firebase_uid": uid, "permissions": permissions,
            "expires_at": body.expires_at.isoformat()}


@router.delete("/admin/events/{event_id}/members/{uid}")
def revoke_client_event(event_id: int, uid: str,
                         admin: User = Depends(get_current_firebase_admin), db: Session = Depends(get_db),
                         store: ClientEventStore = Depends(get_client_event_store)):
    if db.query(Event.id).filter_by(id=event_id, owner_id=admin.id).first() is None:
        raise HTTPException(404, "Event not found.")
    try:
        store.revoke(uid, event_id)
    except EventAccessUnavailable:
        raise HTTPException(503, "Unable to revoke event access.") from None
    return {"event_id": event_id, "firebase_uid": uid, "active": False}


import hashlib
import json
import uuid

from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

from fastapi import (
    APIRouter,
    UploadFile,
    File,
    HTTPException,
    Query,
    Depends
)

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database.db import get_db
from app.database.models import Event, Photo, Face, User
from app.dependencies.auth import get_current_admin
from app.ai.face_engine import FaceEngine
from app.services.persistent_face_index import PersistentFaceIndex
from app.services.person_groups import sync_person_groups


# --------------------------------------------------
# ROUTER CONFIGURATION
# --------------------------------------------------

router = APIRouter(
    prefix="/api/events",
    tags=["Photo Management"]
)


# --------------------------------------------------
# CONFIGURATION
# --------------------------------------------------

ALLOWED_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp"
}

MAX_FILE_SIZE = 100 * 1024 * 1024
MAX_FILES_PER_UPLOAD = 100

EVENT_STORAGE = Path("storage/events")
PRIVATE_EMBEDDINGS = Path("storage/private_embeddings")


# --------------------------------------------------
# INITIALIZE FACE ENGINE
# --------------------------------------------------

@lru_cache(maxsize=1)
def get_face_engine():
    return FaceEngine()


# --------------------------------------------------
# VERIFY EVENT OWNERSHIP
# --------------------------------------------------

def get_event_or_404(
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

    if (
        not event.event_name
        or Path(event.event_name).name != event.event_name
        or event.event_name in {".", ".."}
    ):
        raise HTTPException(
            status_code=400,
            detail="Invalid event folder name."
        )

    return event


# --------------------------------------------------
# SHA256 HASH
# --------------------------------------------------

def calculate_hash(content: bytes):

    return hashlib.sha256(content).hexdigest()


# --------------------------------------------------
# SAVE FACE QUALITY METADATA
# --------------------------------------------------

def save_quality_metadata(
    embedding_path,
    face_data
):

    quality = face_data.get("quality", {})

    if not isinstance(quality, dict):
        quality = {}

    quality_status = quality.get(
        "quality_status",
        "unknown"
    )

    if quality_status not in ("good", "poor"):
        quality_status = "unknown"

    quality_issues = quality.get(
        "quality_issues",
        []
    )

    if not isinstance(quality_issues, list):
        quality_issues = []

    metadata = {
        "quality": {
            "quality_status": quality_status,
            "quality_issues": quality_issues,
            "face_width": quality.get("face_width"),
            "face_height": quality.get("face_height"),
            "blur_score": quality.get("blur_score"),
            "brightness": quality.get("brightness")
        }
    }

    metadata_path = Path(
        embedding_path
    ).with_suffix(".json")

    temp_path = Path(
        str(metadata_path) + ".tmp"
    )

    try:

        with temp_path.open(
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                metadata,
                file,
                indent=4
            )

        temp_path.replace(metadata_path)

    finally:

        if temp_path.exists():
            temp_path.unlink()

    return metadata_path, quality_status


# --------------------------------------------------
# DUPLICATE CHECK
# --------------------------------------------------

@lru_cache(maxsize=10000)
def cached_file_hash(path, size, modified_ns):
    # Stat values invalidate the cached digest when an original changes.
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def is_duplicate(
    db: Session,
    event_id: int,
    file_hash: str
):

    photos = (
        db.query(Photo)
        .filter(Photo.event_id == event_id)
        .all()
    )

    for photo in photos:

        existing_path = Path(
            photo.file_path
        )

        if not existing_path.is_absolute():
            existing_path = Path.cwd() / existing_path

        if not existing_path.is_file():
            continue

        try:

            stat = existing_path.stat()
            existing_hash = cached_file_hash(
                str(existing_path.resolve()), stat.st_size, stat.st_mtime_ns
            )

            if existing_hash == file_hash:
                return True

        except OSError:
            continue

    return False


# --------------------------------------------------
# 1. BULK PHOTO UPLOAD
# ADMIN AUTHENTICATION REQUIRED
# --------------------------------------------------

@router.post("/{event_id}/upload")
async def upload_event_photos(
    event_id: int,

    files: list[UploadFile] = File(...),

    db: Session = Depends(get_db),

    current_admin: User = Depends(get_current_admin)
):

    if not files:

        raise HTTPException(
            status_code=400,
            detail="Please select at least one photo."
        )

    if len(files) > MAX_FILES_PER_UPLOAD:

        raise HTTPException(
            status_code=400,
            detail=f"Maximum {MAX_FILES_PER_UPLOAD} photos per upload."
        )

    uploaded_results = []

    total_faces_detected = 0

    successful_uploads = 0
    skipped_duplicates = 0
    failed_uploads = 0

    good_quality_faces = 0
    poor_quality_faces = 0
    unknown_quality_faces = 0

    index_rebuild_message = None

    event = get_event_or_404(
        db,
        event_id,
        current_admin.id
    )

    original_dir = (
        EVENT_STORAGE /
        event.event_name /
        "originals"
    )

    original_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    embedding_dir = (
        PRIVATE_EMBEDDINGS /
        f"event_{event_id}"
    )

    embedding_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    face_engine = get_face_engine()

    try:

        # ------------------------------------------
        # PROCESS EACH UPLOADED PHOTO
        # ------------------------------------------

        for upload in files:

            saved_path = None

            embedding_paths = []
            metadata_paths = []

            photo_good_faces = 0
            photo_poor_faces = 0
            photo_unknown_faces = 0

            original_filename = Path(
                upload.filename or ""
            ).name

            try:

                extension = Path(
                    original_filename
                ).suffix.lower()

                # Validate extension

                if extension not in ALLOWED_EXTENSIONS:

                    failed_uploads += 1

                    uploaded_results.append({
                        "filename": original_filename,
                        "status": "failed",
                        "message": "Unsupported image format."
                    })

                    continue

                # Read maximum 100 MB + 1 byte

                content = await upload.read(
                    MAX_FILE_SIZE + 1
                )

                if not content:

                    failed_uploads += 1

                    uploaded_results.append({
                        "filename": original_filename,
                        "status": "failed",
                        "message": "Empty file."
                    })

                    continue

                # Validate size

                if len(content) > MAX_FILE_SIZE:

                    failed_uploads += 1

                    uploaded_results.append({
                        "filename": original_filename,
                        "status": "failed",
                        "message": "File exceeds 100 MB."
                    })

                    continue

                # Validate actual image content

                image_array = np.frombuffer(
                    content,
                    dtype=np.uint8
                )

                image = cv2.imdecode(
                    image_array,
                    cv2.IMREAD_COLOR
                )

                if image is None:

                    failed_uploads += 1

                    uploaded_results.append({
                        "filename": original_filename,
                        "status": "failed",
                        "message": "Invalid or unreadable image."
                    })

                    continue

                # Check duplicate content

                file_hash = calculate_hash(content)

                if is_duplicate(
                    db,
                    event_id,
                    file_hash
                ):

                    skipped_duplicates += 1

                    uploaded_results.append({
                        "filename": original_filename,
                        "status": "skipped",
                        "message": "Duplicate photograph."
                    })

                    continue

                # Generate unique filename

                stored_filename = (
                    f"{uuid.uuid4().hex}{extension}"
                )

                saved_path = (
                    original_dir /
                    stored_filename
                )

                # Save original photograph

                saved_path.write_bytes(content)

                # Detect faces

                detection = face_engine.process_image(
                    saved_path
                )

                # Create photo database record

                photo = Photo(
                    filename=original_filename,
                    file_path=str(saved_path),
                    event_id=event_id
                )

                db.add(photo)
                db.flush()

                # ----------------------------------
                # SAVE FACE EMBEDDINGS
                # ----------------------------------

                for position, face_data in enumerate(
                    detection["faces"],
                    start=1
                ):

                    embedding = np.asarray(
                        face_data["embedding"],
                        dtype=np.float32
                    )

                    # Validate embedding

                    if (
                        embedding.ndim != 1
                        or embedding.shape[0] != 512
                        or not np.isfinite(embedding).all()
                        or np.linalg.norm(embedding) == 0
                    ):

                        raise ValueError(
                            "Invalid face embedding."
                        )

                    embedding_path = (
                        embedding_dir /
                        f"photo_{photo.id}_face_{position}.npy"
                    )

                    # Save embedding

                    np.save(
                        embedding_path,
                        embedding
                    )

                    embedding_paths.append(
                        embedding_path
                    )

                    # Save quality metadata

                    metadata_path, quality_status = (
                        save_quality_metadata(
                            embedding_path,
                            face_data
                        )
                    )

                    metadata_paths.append(
                        metadata_path
                    )

                    # Count quality categories locally

                    if quality_status == "good":

                        photo_good_faces += 1

                    elif quality_status == "poor":

                        photo_poor_faces += 1

                    else:

                        photo_unknown_faces += 1

                    # Create database face record

                    face = Face(
                        photo_id=photo.id,
                        confidence=float(
                            face_data["confidence"]
                        ),
                        embedding_path=str(embedding_path)
                    )

                    db.add(face)

                # ----------------------------------
                # COMMIT PHOTO
                # ----------------------------------

                sync_person_groups(db, event_id)
                db.commit()
                db.refresh(photo)

                successful_uploads += 1

                total_faces_detected += (
                    detection["total_faces"]
                )

                good_quality_faces += photo_good_faces
                poor_quality_faces += photo_poor_faces
                unknown_quality_faces += photo_unknown_faces

                uploaded_results.append({

                    "photo_id": photo.id,

                    "filename": original_filename,

                    "status": "success",

                    "faces_detected": detection["total_faces"],

                    "image_url": (
                        f"/api/events/{event_id}/photos/{photo.id}"
                    )

                })

            except Exception:

                db.rollback()

                # Remove original image if processing failed

                if saved_path and saved_path.exists():

                    try:
                        saved_path.unlink()
                    except OSError:
                        pass

                # Remove embedding files

                for path in embedding_paths:

                    try:

                        if path.exists():
                            path.unlink()

                    except OSError:
                        pass

                # Remove quality metadata files

                for path in metadata_paths:

                    try:

                        if path.exists():
                            path.unlink()

                    except OSError:
                        pass

                failed_uploads += 1

                uploaded_results.append({

                    "filename": original_filename,

                    "status": "failed",

                    "message": (
                        "Photo processing failed. "
                        "Check server logs for details."
                    )

                })

            finally:

                try:
                    await upload.close()
                except Exception:
                    pass

        # ------------------------------------------
        # REBUILD FAISS INDEX
        # ------------------------------------------

        if total_faces_detected > 0:

            try:

                index_engine = PersistentFaceIndex()

                index_engine.build_event_index(
                    event_id
                )

                index_rebuild_message = (
                    "Index rebuilt successfully."
                )

            except Exception:

                index_rebuild_message = (
                    "Index rebuild failed. "
                    "Check server logs."
                )

        else:

            index_rebuild_message = (
                "No new faces detected. Index unchanged."
            )

        # ------------------------------------------
        # RESPONSE
        # ------------------------------------------

        return {

            "event_id": event_id,

            "total_files_received": len(files),

            "successful_uploads": successful_uploads,

            "skipped_duplicates": skipped_duplicates,

            "failed_uploads": failed_uploads,

            "total_faces_detected": total_faces_detected,

            "face_quality_summary": {

                "good_quality_faces": good_quality_faces,

                "poor_quality_faces": poor_quality_faces,

                "unknown_quality_faces": unknown_quality_faces

            },

            "index_status": index_rebuild_message,

            "results": uploaded_results

        }

    finally:

        for upload in files:

            try:
                await upload.close()
            except Exception:
                pass


# --------------------------------------------------
# 2. LIST EVENT PHOTOGRAPHS
# ADMIN AUTHENTICATION REQUIRED
# --------------------------------------------------

@router.get("/{event_id}/photos")
def get_event_photos(
    event_id: int,

    skip: int = Query(default=0, ge=0),

    limit: int = Query(default=50, ge=1, le=200),

    db: Session = Depends(get_db),

    current_admin: User = Depends(get_current_admin)
):

    get_event_or_404(
        db,
        event_id,
        current_admin.id
    )

    total_photos = (
        db.query(func.count(Photo.id))
        .filter(Photo.event_id == event_id)
        .scalar()
    )

    photos = (
        db.query(Photo)
        .filter(Photo.event_id == event_id)
        .order_by(Photo.id.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )

    results = []

    for photo in photos:

        face_count = (
            db.query(func.count(Face.id))
            .filter(Face.photo_id == photo.id)
            .scalar()
        )

        results.append({

            "photo_id": photo.id,

            "filename": photo.filename,

            "faces_detected": face_count,

            "image_url": (
                f"/api/events/{event_id}/photos/{photo.id}"
            )

        })

    return {

        "event_id": event_id,

        "total_photos": total_photos,

        "skip": skip,

        "limit": limit,

        "photos": results

    }


# --------------------------------------------------
# 3. EVENT STATISTICS
# ADMIN AUTHENTICATION REQUIRED
# --------------------------------------------------

@router.get("/{event_id}/stats")
def get_event_stats(
    event_id: int,

    db: Session = Depends(get_db),

    current_admin: User = Depends(get_current_admin)
):

    event = get_event_or_404(
        db,
        event_id,
        current_admin.id
    )

    total_photos = (
        db.query(func.count(Photo.id))
        .filter(Photo.event_id == event_id)
        .scalar()
    )

    total_faces = (
        db.query(func.count(Face.id))
        .join(
            Photo,
            Face.photo_id == Photo.id
        )
        .filter(Photo.event_id == event_id)
        .scalar()
    )

    photos_with_faces = (
        db.query(func.count(func.distinct(Photo.id)))
        .join(
            Face,
            Face.photo_id == Photo.id
        )
        .filter(Photo.event_id == event_id)
        .scalar()
    )

    photos_without_faces = (
        total_photos - photos_with_faces
    )

    return {

        "event_id": event.id,

        "event_name": event.event_name,

        "total_photos": total_photos,

        "total_faces": total_faces,

        "photos_with_faces": photos_with_faces,

        "photos_without_faces": photos_without_faces

    }


import json
import mimetypes
import uuid

from functools import lru_cache
from pathlib import Path

from fastapi import (
    APIRouter,
    UploadFile,
    File,
    HTTPException,
    Query,
    Depends,
)
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.ai.face_engine import FaceEngine
from app.database.db import get_db
from app.database.models import Event, Face, Photo, User
from app.dependencies.auth import get_current_admin
from app.services.persistent_face_index import PersistentFaceIndex
from app.services.photo_sources import resolve_photo_file


# --------------------------------------------------
# ROUTER CONFIGURATION
# --------------------------------------------------

router = APIRouter(
    prefix="/api",
    tags=["Photo Search"]
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

MAX_FILE_SIZE = 50 * 1024 * 1024

UPLOAD_DIR = Path("storage/private_uploads")

STRONG_MATCH_THRESHOLD = 0.60
POSSIBLE_MATCH_THRESHOLD = 0.45


# --------------------------------------------------
# EVENT OWNERSHIP CHECK
# --------------------------------------------------

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


# --------------------------------------------------
# INITIALIZE FACE ENGINE
# --------------------------------------------------

@lru_cache(maxsize=1)
def get_face_engine():
    return FaceEngine()


# --------------------------------------------------
# CLASSIFY SIMILARITY
# --------------------------------------------------

def classify_match(similarity: float) -> str:

    if similarity >= STRONG_MATCH_THRESHOLD:
        return "strong_match"

    if similarity >= POSSIBLE_MATCH_THRESHOLD:
        return "possible_match"

    return "low_match"


# --------------------------------------------------
# 1. CHECK EVENT INDEX STATUS
# --------------------------------------------------

@router.get("/events/{event_id}/index-status")
def index_status(
    event_id: int,
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin)
):

    get_owned_event(
        db,
        event_id,
        current_admin.id
    )

    index_path = Path(
        f"indexes/event_{event_id}.faiss"
    )

    metadata_path = Path(
        f"indexes/event_{event_id}.json"
    )

    if not index_path.exists() or not metadata_path.exists():

        return {
            "event_id": event_id,
            "index_exists": False,
            "message": "Build the event index first."
        }

    try:

        with metadata_path.open(
            "r",
            encoding="utf-8"
        ) as file:

            metadata = json.load(file)

    except (json.JSONDecodeError, OSError):

        raise HTTPException(
            status_code=500,
            detail="Unable to read event index metadata."
        )

    return {
        "event_id": event_id,
        "index_exists": True,
        "total_embeddings": metadata.get(
            "total_embeddings",
            0
        )
    }


# --------------------------------------------------
# 2. GUEST PHOTO SEARCH
# --------------------------------------------------

@router.post("/events/{event_id}/search")
async def search_photos(
    event_id: int,

    file: UploadFile = File(...),

    top_k: int = Query(
        default=10,
        ge=1,
        le=20
    ),

    min_similarity: float = Query(
        default=POSSIBLE_MATCH_THRESHOLD,
        ge=-1.0,
        le=1.0,
        description="Minimum cosine similarity required for a result."
    ),

    db: Session = Depends(get_db),

    current_admin: User = Depends(get_current_admin)
):

    # ------------------------------------------
    # VERIFY EVENT OWNERSHIP
    # ------------------------------------------

    get_owned_event(
        db,
        event_id,
        current_admin.id
    )

    # ------------------------------------------
    # VALIDATE FILE
    # ------------------------------------------

    extension = Path(
        file.filename or ""
    ).suffix.lower()

    if extension not in ALLOWED_EXTENSIONS:

        raise HTTPException(
            status_code=400,
            detail="Unsupported image format."
        )

    content = await file.read(
        MAX_FILE_SIZE + 1
    )

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

        # ------------------------------------------
        # DETECT FACES
        # ------------------------------------------

        engine = get_face_engine()

        detection = engine.process_image(
            temp_path
        )

        total_faces = detection["total_faces"]

        if total_faces == 0:

            raise HTTPException(
                status_code=422,
                detail=(
                    "No face detected. "
                    "Please upload a photograph containing a visible face."
                )
            )

        # ------------------------------------------
        # LOAD EVENT INDEX
        # ------------------------------------------

        search_engine = PersistentFaceIndex()

        try:

            search_engine.load_event_index(
                event_id
            )

        except FileNotFoundError:

            raise HTTPException(
                status_code=404,
                detail="Event search index not found."
            )

        # ------------------------------------------
        # SEARCH DETECTED FACES
        # ------------------------------------------

        matched_photos = {}

        for uploaded_face in detection["faces"]:

            query_embedding = (
                uploaded_face["embedding"]
            )

            matches = search_engine.search(
                query_embedding,
                top_k=top_k
            )

            for match in matches:

                similarity = float(
                    match["similarity"]
                )

                # ----------------------------------
                # FILTER LOW SIMILARITY
                # ----------------------------------

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

                # ----------------------------------
                # REMOVE DUPLICATE PHOTO RESULTS
                # Keep strongest matching face
                # ----------------------------------

                if (
                    photo_id not in matched_photos
                    or similarity > matched_photos[photo_id]["similarity"]
                ):

                    matched_photos[photo_id] = {

                        "photo_id": photo.id,

                        "face_id": face.id,

                        "filename": photo.filename,

                        "similarity": similarity,

                        "match_quality": classify_match(
                            similarity
                        ),

                        "image_url": (
                            f"/api/events/{event_id}/photos/{photo.id}"
                        )

                    }

        # ------------------------------------------
        # SORT MATCHES
        # ------------------------------------------

        results = sorted(
            matched_photos.values(),
            key=lambda item: item["similarity"],
            reverse=True
        )

        results = results[:top_k]

        # ------------------------------------------
        # ROUND SIMILARITY VALUES
        # ------------------------------------------

        for result in results:

            result["similarity"] = round(
                result["similarity"],
                4
            )

        # ------------------------------------------
        # RESPONSE
        # ------------------------------------------

        return {

            "event_id": event_id,

            "faces_detected": total_faces,

            "minimum_similarity": min_similarity,

            "total_matches": len(results),

            "matches": results

        }

    finally:

        await file.close()

        if temp_path.exists():
            temp_path.unlink()


# --------------------------------------------------
# 3. RETRIEVE ORIGINAL EVENT PHOTOGRAPH
# --------------------------------------------------

@router.get(
    "/events/{event_id}/photos/{photo_id}"
)
def get_event_photo(
    event_id: int,
    photo_id: int,
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin)
):

    # ------------------------------------------
    # VERIFY EVENT OWNERSHIP
    # ------------------------------------------

    event = get_owned_event(
        db,
        event_id,
        current_admin.id
    )

    # ------------------------------------------
    # FIND PHOTO
    # ------------------------------------------

    photo = (
        db.query(Photo)
        .filter(
            Photo.id == photo_id,
            Photo.event_id == event_id
        )
        .first()
    )

    if photo is None:

        raise HTTPException(
            status_code=404,
            detail="Photo not found."
        )

    # ------------------------------------------
    # VALIDATE IMAGE PATH
    # ------------------------------------------

    try:
        image_path = resolve_photo_file(db, event, photo)
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail="Photo access denied."
        )
    except (OSError, RuntimeError):
        raise HTTPException(
            status_code=404,
            detail="Image file not found."
        )

    # ------------------------------------------
    # DETECT MIME TYPE
    # ------------------------------------------

    media_type, _ = mimetypes.guess_type(
        str(image_path)
    )

    # ------------------------------------------
    # RETURN ORIGINAL PHOTO
    # ------------------------------------------

    return FileResponse(
        path=str(image_path),
        media_type=media_type or "application/octet-stream",
        filename=photo.filename,
        content_disposition_type="inline"
    )

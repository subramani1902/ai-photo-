"""Local matching for an online request processed by the event laptop.

This module does not upload images or expose local file paths to guests.
The relay worker is responsible for authorizing requests and transferring files.
"""

from pathlib import Path

from app.database.db import SessionLocal
from app.database.models import Event, Face, Photo
from app.ai.face_engine import FaceEngine
from app.services.persistent_face_index import PersistentFaceIndex


def find_event_matches(event_id, selfie_path, *, engine=None, min_similarity=0.60):
    """Return local photo records matching one face in an event's current index."""
    if not 0.45 <= min_similarity <= 1:
        raise ValueError("Similarity threshold must be between 0.45 and 1.")
    engine = engine or FaceEngine()
    detection = engine.process_image(Path(selfie_path))
    if detection["total_faces"] != 1:
        raise ValueError("Please use a clear selfie with exactly one face.")

    index = PersistentFaceIndex()
    index.load_event_index(event_id)
    matches = index.search(detection["faces"][0]["embedding"], top_k=index.index.ntotal)
    db = SessionLocal()
    try:
        event = db.query(Event).filter(Event.id == event_id).first()
        if event is None:
            raise ValueError("Event not found.")
        if not event.event_name or Path(event.event_name).name != event.event_name or event.event_name in {".", ".."}:
            raise ValueError("Invalid event folder name.")
        root = (Path("storage/events") / event.event_name).resolve()
        results = {}
        for match in matches:
            if match["similarity"] < min_similarity:
                continue
            record = (db.query(Face, Photo)
                      .join(Photo, Face.photo_id == Photo.id)
                      .filter(Face.id == match["face_id"], Photo.event_id == event_id)
                      .first())
            if record is None:
                continue
            _, photo = record
            path = Path(photo.file_path).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                continue
            if photo.id not in results:
                results[photo.id] = {
                    "photo_id": photo.id,
                    "filename": photo.filename,
                    "path": path,
                    "similarity": float(match["similarity"]),
                }
        return list(results.values())
    finally:
        db.close()

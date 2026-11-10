import json
from pathlib import Path

import numpy as np
from sqlalchemy.orm import Session

from app.database.db import SessionLocal
from app.database.models import Event, Photo, Face


REPORT_PATH = Path(
    "storage/events/wedding_001/processed/batch_results.json"
)

EMBEDDING_ROOT = Path("storage/private_embeddings")


def save_quality_metadata(embedding_path, face_data):
    """
    Save quality information beside the embedding.

    Example:
        photo_1_face_1.npy
        photo_1_face_1.json
    """

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

    metadata_path = Path(embedding_path).with_suffix(".json")

    temp_path = Path(str(metadata_path) + ".tmp")

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

    return metadata_path, quality_status


def import_results():

    if not REPORT_PATH.exists():
        raise FileNotFoundError(REPORT_PATH)

    with REPORT_PATH.open(
        "r",
        encoding="utf-8"
    ) as file:

        report = json.load(file)

    event_name = report.get(
        "event",
        "wedding_001"
    )

    db: Session = SessionLocal()

    created_photos = 0
    updated_photos = 0
    created_faces = 0
    updated_faces = 0
    deleted_faces = 0

    good_quality_faces = 0
    poor_quality_faces = 0
    unknown_quality_faces = 0

    obsolete_embedding_files = []
    active_embedding_files = set()

    try:

        # Find existing event or create one
        event = db.query(Event).filter(
            Event.event_name == event_name
        ).first()

        if event is None:

            event = Event(
                event_name=event_name
            )

            db.add(event)
            db.flush()

        embedding_dir = (
            EMBEDDING_ROOT /
            f"event_{event.id}"
        )

        embedding_dir.mkdir(
            parents=True,
            exist_ok=True
        )

        for item in report.get("photos", []):

            if item.get("status") != "success":
                continue

            source_path = str(
                item["source_path"]
            )

            # Find existing photo
            photo = db.query(Photo).filter(
                Photo.event_id == event.id,
                Photo.file_path == source_path
            ).first()

            if photo is None:

                photo = Photo(
                    filename=item["filename"],
                    file_path=source_path,
                    event_id=event.id
                )

                db.add(photo)
                db.flush()

                created_photos += 1

            else:

                photo.filename = item["filename"]

                updated_photos += 1

            # Get existing face records
            existing_faces = (
                db.query(Face)
                .filter(Face.photo_id == photo.id)
                .order_by(Face.id)
                .all()
            )

            new_face_data = sorted(
                item.get("faces", []),
                key=lambda face: face["face_number"]
            )

            for position, face_data in enumerate(new_face_data):

                embedding = np.asarray(
                    face_data["embedding"],
                    dtype=np.float32
                )

                if embedding.shape != (512,):

                    raise ValueError(
                        f"Invalid embedding for {item['filename']}: "
                        f"{embedding.shape}"
                    )

                if not np.isfinite(embedding).all():

                    raise ValueError(
                        "Embedding contains invalid values"
                    )

                if np.linalg.norm(embedding) == 0:

                    raise ValueError(
                        "Embedding has zero magnitude"
                    )

                embedding_path = (
                    embedding_dir /
                    f"photo_{photo.id}_face_{position + 1}.npy"
                )

                # Save embedding
                np.save(
                    embedding_path,
                    embedding
                )

                # Save quality metadata beside embedding
                metadata_path, quality_status = save_quality_metadata(
                    embedding_path,
                    face_data
                )

                active_embedding_files.add(
                    str(embedding_path.resolve())
                )

                active_embedding_files.add(
                    str(metadata_path.resolve())
                )

                if quality_status == "good":

                    good_quality_faces += 1

                elif quality_status == "poor":

                    poor_quality_faces += 1

                else:

                    unknown_quality_faces += 1

                if position < len(existing_faces):

                    face = existing_faces[position]

                    if face.embedding_path:

                        old_embedding = Path(
                            face.embedding_path
                        )

                        if (
                            old_embedding.resolve()
                            != embedding_path.resolve()
                        ):

                            obsolete_embedding_files.append(
                                old_embedding
                            )

                            obsolete_embedding_files.append(
                                old_embedding.with_suffix(".json")
                            )

                    face.confidence = face_data["confidence"]

                    face.embedding_path = str(
                        embedding_path
                    )

                    updated_faces += 1

                else:

                    face = Face(
                        photo_id=photo.id,
                        confidence=face_data["confidence"],
                        embedding_path=str(embedding_path)
                    )

                    db.add(face)

                    created_faces += 1

            # Remove obsolete face records if the new image
            # contains fewer faces than the previous version.
            for old_face in existing_faces[len(new_face_data):]:

                if old_face.embedding_path:

                    old_embedding = Path(
                        old_face.embedding_path
                    )

                    obsolete_embedding_files.append(
                        old_embedding
                    )

                    obsolete_embedding_files.append(
                        old_embedding.with_suffix(".json")
                    )

                db.delete(old_face)

                deleted_faces += 1

        # Commit database changes
        db.commit()

        # Delete obsolete files only after successful DB commit
        for old_path in set(obsolete_embedding_files):

            resolved_path = str(
                Path(old_path).resolve()
            )

            if resolved_path not in active_embedding_files:

                path = Path(old_path)

                if path.exists():
                    path.unlink()

        print("\n========== IMPORT COMPLETE ==========")

        print("Event:", event_name)

        print("Event ID:", event.id)

        print("New photos:", created_photos)

        print("Updated photos:", updated_photos)

        print("New faces:", created_faces)

        print("Updated faces:", updated_faces)

        print("Deleted faces:", deleted_faces)

        print("\n========== FACE QUALITY SUMMARY ==========")

        print("Good-quality faces:", good_quality_faces)

        print("Poor-quality faces:", poor_quality_faces)

        print("Unknown-quality faces:", unknown_quality_faces)

        print("\nQuality metadata saved beside embeddings.")

    except Exception:

        db.rollback()

        raise

    finally:

        db.close()


if __name__ == "__main__":

    import_results()
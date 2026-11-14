"""Report photos arriving in the watched folder and their local face analysis."""

import argparse
import time
from datetime import datetime
from pathlib import Path

from sqlalchemy import func

from app.database.db import SessionLocal
from app.database.models import Face, Photo
from app.services.person_groups import EventSourceFolder, PhotoSource
from app.services.photo_sources import source_paths_in


DEFAULT_FOLDER = Path(r"D:\Event-AI-Photo-Finder\Aathif-Sahana")


def report_status(folder, event_id):
    folder = folder.resolve(strict=True)
    current_paths = set(source_paths_in(folder))

    with SessionLocal() as db:
        registration = (
            db.query(EventSourceFolder)
            .filter_by(event_id=event_id)
            .one_or_none()
        )
        if registration is None:
            raise RuntimeError(
                f"No watched folder is registered for event {event_id}."
            )
        registered_folder = Path(registration.folder_path).resolve(strict=True)
        if registered_folder != folder:
            raise RuntimeError(
                f"Importer watches {registered_folder}, not {folder}."
            )

        tracked_paths = {
            row.relative_path
            for row in db.query(PhotoSource.relative_path)
            .filter_by(event_id=event_id)
            .all()
        }
        indexed_photos = db.query(Photo).filter_by(event_id=event_id).count()
        detected_faces = (
            db.query(func.count(Face.id))
            .join(Photo, Face.photo_id == Photo.id)
            .filter(Photo.event_id == event_id)
            .scalar()
        )

    pending = current_paths - tracked_paths
    stale = tracked_paths - current_paths
    print(
        f"{datetime.now().astimezone().isoformat(timespec='seconds')} "
        f"folder={folder} files={len(current_paths)} "
        f"tracked={len(current_paths & tracked_paths)} pending={len(pending)} "
        f"stale={len(stale)} indexed_photos={indexed_photos} "
        f"detected_faces={detected_faces}",
        flush=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", type=Path, default=DEFAULT_FOLDER)
    parser.add_argument("--event-id", type=int, default=3)
    parser.add_argument("--interval", type=float, default=5)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.event_id < 1:
        parser.error("--event-id must be positive.")
    if args.interval <= 0:
        parser.error("--interval must be positive.")
    if not args.folder.is_dir():
        parser.error(f"Watched folder does not exist: {args.folder}")

    try:
        while True:
            report_status(args.folder, args.event_id)
            if args.once:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped monitoring.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

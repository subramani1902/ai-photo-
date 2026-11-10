"""Local operator command: ingest a camera folder without an HTTP login."""
import argparse
import asyncio
import logging
import time
from contextlib import ExitStack
from pathlib import Path

from fastapi import UploadFile
from app.api.photos import upload_event_photos
from app.database.db import SessionLocal
from app.database.models import Event, User
from app.services.person_groups import init_person_groups
from watch_camera_folder import scan_ready

LOG = logging.getLogger(__name__)
MAX_FILE_SIZE = 100 * 1024 * 1024
MAX_FILES_PER_BATCH = 10


async def run(folder, event_id):
    init_person_groups()
    with SessionLocal() as db:
        event = db.query(Event).filter_by(id=event_id).first()
        if event is None or not db.query(User).filter_by(id=event.owner_id, is_active=True).first():
            raise ValueError("Choose an event with an active owner.")
    observed, processed = {}, {}
    LOG.info("Watching %s for event %s", folder, event_id)
    while True:
        ready = scan_ready(folder, observed, processed, time.monotonic(), 5)
        importable = []
        for path, signature in ready:
            if signature[0] > MAX_FILE_SIZE:
                processed[path] = signature
                LOG.warning("Skipping %s: file exceeds the 100 MB upload limit", path.name)
            else:
                importable.append((path, signature))

        for start in range(0, len(importable), MAX_FILES_PER_BATCH):
            batch = importable[start:start + MAX_FILES_PER_BATCH]
            try:
                with SessionLocal() as db, ExitStack() as stack:
                    event = db.query(Event).filter_by(id=event_id).one()
                    owner = db.query(User).filter_by(id=event.owner_id, is_active=True).one()
                    uploads = [
                        UploadFile(
                            filename=path.name,
                            file=stack.enter_context(path.open("rb"))
                        )
                        for path, _ in batch
                    ]
                    result = await upload_event_photos(event_id,
                        files=uploads,
                        db=db, current_admin=owner)
                items = result["results"]
                if len(items) != len(batch):
                    raise ValueError("Unexpected photo import result count.")
                for (path, signature), item in zip(batch, items):
                    if item["status"] in ("success", "skipped"):
                        processed[path] = signature
                        LOG.info("%s: %s", item["status"], path.name)
                    else:
                        LOG.warning("Import failed: %s; retrying", path.name)
                LOG.info("Index: %s", result["index_status"])
            except Exception:
                LOG.exception("Import batch failed; retrying from %s", batch[0][0].name)
        await asyncio.sleep(2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", type=Path, required=True)
    parser.add_argument("--event-id", type=int, required=True)
    args = parser.parse_args()
    if not args.folder.is_dir():
        parser.error("Camera folder must exist.")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        asyncio.run(run(args.folder.resolve(), args.event_id))
    except KeyboardInterrupt:
        LOG.info("Camera importer stopped")


if __name__ == "__main__":
    main()

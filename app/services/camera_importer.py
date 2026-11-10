"""Local operator command: ingest a camera folder without an HTTP login."""
import argparse
import asyncio
import logging
import time
from pathlib import Path

from fastapi import UploadFile
from app.api.photos import upload_event_photos
from app.database.db import SessionLocal
from app.database.models import Event, User
from app.services.person_groups import init_person_groups
from watch_camera_folder import scan_ready

LOG = logging.getLogger(__name__)


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
        for path, signature in ready:
            try:
                with SessionLocal() as db, path.open("rb") as stream:
                    event = db.query(Event).filter_by(id=event_id).one()
                    owner = db.query(User).filter_by(id=event.owner_id, is_active=True).one()
                    result = await upload_event_photos(event_id,
                        files=[UploadFile(filename=path.name, file=stream)],
                        db=db, current_admin=owner)
                item = result["results"][0]
                if item["status"] in ("success", "skipped"):
                    processed[path] = signature
                    LOG.info("%s: %s; %s", item["status"], path.name, result["index_status"])
                else:
                    LOG.warning("Import failed: %s; retrying", path.name)
            except Exception:
                LOG.exception("Import failed: %s; retrying", path.name)
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

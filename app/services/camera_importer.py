"""Local operator command: ingest a camera folder without an HTTP login."""
import argparse
import asyncio
import logging
import shutil
import time
from contextlib import ExitStack
from pathlib import Path

from fastapi import UploadFile
from app.api.photos import upload_event_photos
from app.database.db import SessionLocal
from app.database.models import Event, User
from app.services.person_groups import EventSourceFolder, init_person_groups
from app.services.photo_sources import (
    folder_is_processed,
    reconcile_photo_sources,
    source_paths_in,
)
from watch_camera_folder import (
    DEFAULT_SCAN_INTERVAL_SECONDS,
    scan_ready,
)

LOG = logging.getLogger(__name__)
MAX_FILE_SIZE = 100 * 1024 * 1024
MAX_FILES_PER_BATCH = 20
MIN_FREE_DISK_BYTES = 256 * 1024 * 1024


def open_import_batch(batch, stack):
    uploads, readable = [], []
    for path, signature in batch:
        try:
            file = stack.enter_context(path.open("rb"))
        except OSError as exc:
            LOG.warning("Cannot read %s (%s); retrying later", path.name, type(exc).__name__)
            continue
        uploads.append(UploadFile(filename=path.name, file=file))
        readable.append((path, signature))
    return uploads, readable


async def run(folder, event_id):
    folder = Path(folder).resolve(strict=True)
    init_person_groups()
    with SessionLocal() as db:
        event = db.query(Event).filter_by(id=event_id).first()
        if event is None or not db.query(User).filter_by(id=event.owner_id, is_active=True).first():
            raise ValueError("Choose an event with an active owner.")
        db.merge(EventSourceFolder(event_id=event_id, folder_path=str(folder)))
        db.commit()
    observed, processed, blocked = {}, {}, {}
    LOG.info("Watching %s for event %s", folder, event_id)
    while True:
        free_bytes = shutil.disk_usage(Path.cwd()).free
        if free_bytes < MIN_FREE_DISK_BYTES:
            LOG.error("Storage drive is nearly full (%d MB free); pausing imports for 30 seconds",
                      free_bytes // (1024 * 1024))
            await asyncio.sleep(30)
            continue
        try:
            present_paths = source_paths_in(folder)
            ready = scan_ready(folder, observed, processed, time.monotonic())
        except OSError:
            LOG.exception("Could not scan event folder; retrying without reconciling")
            await asyncio.sleep(DEFAULT_SCAN_INTERVAL_SECONDS)
            continue
        importable = []
        for path, signature in ready:
            if signature[0] > MAX_FILE_SIZE:
                processed[path] = signature
                blocked[path] = signature
                LOG.warning("Skipping %s: file exceeds the 100 MB upload limit", path.name)
            else:
                importable.append((path, signature))

        for start in range(0, len(importable), MAX_FILES_PER_BATCH):
            batch = importable[start:start + MAX_FILES_PER_BATCH]
            try:
                with SessionLocal() as db, ExitStack() as stack:
                    event = db.query(Event).filter_by(id=event_id).one()
                    owner = db.query(User).filter_by(id=event.owner_id, is_active=True).one()
                    uploads, readable_batch = open_import_batch(batch, stack)
                    if not uploads:
                        continue
                    result = await upload_event_photos(event_id,
                        files=uploads,
                        source_paths=[
                            path.relative_to(folder).as_posix()
                            for path, _ in readable_batch
                        ],
                        db=db, current_admin=owner)
                items = result["results"]
                if len(items) != len(readable_batch):
                    raise ValueError("Unexpected photo import result count.")
                for (path, signature), item in zip(readable_batch, items):
                    if item["status"] in ("success", "skipped"):
                        processed[path] = signature
                        blocked.pop(path, None)
                        LOG.info("%s: %s", item["status"], path.name)
                    else:
                        LOG.warning("Import failed: %s (%s); retrying", path.name,
                                    item.get("message", "Unknown error"))
                LOG.info("Index: %s", result["index_status"])
            except Exception:
                LOG.exception("Import batch failed; retrying from %s", batch[0][0].name)
        try:
            with SessionLocal() as db:
                reconcile_photo_sources(
                    db,
                    event_id,
                    present_paths,
                    complete=folder_is_processed(folder, processed, blocked),
                )
        except Exception:
            LOG.exception("Could not reconcile removed event photos; retrying")
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

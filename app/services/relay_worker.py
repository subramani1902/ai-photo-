"""Laptop-side worker for a photo-finder relay.

The relay URL must point to a trusted HTTPS service implementing the worker
endpoints. The worker secret is never sent to guest browsers.
"""

import logging
import mimetypes
import os
import tempfile
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from pathlib import Path
from threading import Event, Lock, Thread
from urllib.parse import urlparse
from uuid import UUID, uuid4

import cv2
import numpy as np
import requests
from dotenv import load_dotenv

from app.ai.face_engine import FaceEngine
from app.services.remote_matching import find_event_matches

LOG = logging.getLogger(__name__)
MAX_SELFIE_BYTES = 20 * 1024 * 1024
MAX_RESULT_SOURCE_BYTES = 1024 * 1024
MAX_RESULT_DIMENSION = 1600
RESULT_JPEG_QUALITY = 85
RESULT_UPLOAD_WORKERS = 4
HEARTBEAT_INTERVAL_SECONDS = 30
SHOWCASE_PHOTO_COUNT = 3
SHOWCASE_MAX_DIMENSION = 640
SHOWCASE_WEBP_QUALITY = 78
MAX_GALLERY_ORIGINAL_BYTES = 50 * 1024 * 1024
GALLERY_MANIFEST_BATCH_SIZE = 500
GALLERY_CLEANUP_INTERVAL_SECONDS = 300


def prepare_photo_for_transfer(source_path, output_path):
    image = cv2.imread(str(source_path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Unable to prepare a matched photo for delivery.")

    height, width = image.shape[:2]
    largest_dimension = max(height, width)
    if largest_dimension > MAX_RESULT_DIMENSION:
        scale = MAX_RESULT_DIMENSION / largest_dimension
        image = cv2.resize(
            image,
            (round(width * scale), round(height * scale)),
            interpolation=cv2.INTER_AREA
        )

    success, encoded = cv2.imencode(
        ".jpg",
        image,
        [cv2.IMWRITE_JPEG_QUALITY, RESULT_JPEG_QUALITY]
    )
    if not success:
        raise ValueError("Unable to prepare a matched photo for delivery.")
    Path(output_path).write_bytes(encoded.tobytes())
    return Path(output_path)


def prepare_photo_for_showcase(source_path, output_path):
    image = cv2.imread(str(source_path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Unable to prepare showcase photo: {Path(source_path).name}")

    height, width = image.shape[:2]
    largest_dimension = max(height, width)
    if largest_dimension > SHOWCASE_MAX_DIMENSION:
        scale = SHOWCASE_MAX_DIMENSION / largest_dimension
        image = cv2.resize(
            image,
            (round(width * scale), round(height * scale)),
            interpolation=cv2.INTER_AREA
        )

    success, encoded = cv2.imencode(
        ".webp",
        image,
        [cv2.IMWRITE_WEBP_QUALITY, SHOWCASE_WEBP_QUALITY]
    )
    if not success:
        raise ValueError(f"Unable to prepare showcase photo: {Path(source_path).name}")
    Path(output_path).write_bytes(encoded.tobytes())
    return Path(output_path)


class RelayWorker:
    def __init__(self, relay_url, worker_secret, event_id, *, session=None, engine=None, matching_lock=None):
        parsed = urlparse(relay_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Relay URL must be a trusted HTTPS URL.")
        if not worker_secret or not event_id:
            raise ValueError("A worker secret and local event ID are required.")
        self.relay_url = relay_url.rstrip("/")
        self.event_id = int(event_id)
        self.session = session or requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {worker_secret}"})
        self.engine = engine
        self.matching_lock = matching_lock or Lock()

    def sync_showcase(self):
        from app.database.db import SessionLocal
        from app.database.models import Event, Photo
        from app.services.person_groups import EventSourceFolder, init_person_groups
        from app.services.photo_sources import resolve_photo_file

        init_person_groups()
        with SessionLocal() as db:
            event = db.query(Event).filter_by(id=self.event_id).one_or_none()
            if event is None:
                raise ValueError(f"Local event {self.event_id} does not exist.")
            source_folder = (
                db.query(EventSourceFolder)
                .filter_by(event_id=self.event_id)
                .one_or_none()
            )
            if source_folder is None:
                raise ValueError(
                    f"No watched folder is registered for local event {self.event_id}."
                )
            folder_path = Path(source_folder.folder_path).resolve()
            if not folder_path.is_dir():
                LOG.warning("External photo folder is unavailable; using local copies where possible")
            folder_name = folder_path.name
            if not folder_name or folder_name in {".", ".."}:
                raise ValueError("Invalid watched folder name.")
            event_name = event.event_name
            if not event_name or Path(event_name).name != event_name or event_name in {".", ".."}:
                raise ValueError("Invalid local event folder name.")
            photos = (
                db.query(Photo)
                .filter_by(event_id=self.event_id)
                .filter(Photo.faces.any())
                .order_by(Photo.id.desc())
                .limit(SHOWCASE_PHOTO_COUNT)
                .all()
            )
            photo_rows = []
            for photo in photos:
                try:
                    photo_path = resolve_photo_file(db, event, photo)
                except (OSError, RuntimeError, ValueError) as exc:
                    LOG.warning("Skipping showcase photo %s (%s)", photo.id, type(exc).__name__)
                    continue
                photo_rows.append((photo.id, photo.filename, photo_path))

        current = self.request("GET", "/worker/showcase").json()
        if current.get("event_name") != event_name:
            raise ValueError("The local event folder name does not match its QR event.")
        current_ids = set(current.get("photo_ids", []))
        keep_ids = []

        with tempfile.TemporaryDirectory(prefix="photo-finder-showcase-") as directory:
            event_root = (Path("storage/events") / event_name).resolve()
            for photo_id, filename, file_path in photo_rows:
                if photo_id in current_ids:
                    keep_ids.append(photo_id)
                    continue
                try:
                    source_path = Path(file_path).resolve(strict=True)
                    if not source_path.is_relative_to(folder_path) and not source_path.is_relative_to(event_root):
                        raise ValueError("Photo path is outside approved storage.")
                    preview_path = prepare_photo_for_showcase(
                        source_path,
                        Path(directory) / f"{photo_id}.webp"
                    )
                    with preview_path.open("rb") as photo:
                        self.request(
                            "POST",
                            f"/worker/showcase/{photo_id}",
                            data={"event_name": event_name},
                            files={"photo": (f"{Path(filename).stem}.webp", photo, "image/webp")},
                        )
                except (OSError, ValueError) as exc:
                    LOG.warning("Skipping showcase photo %s (%s)", photo_id, type(exc).__name__)
                    continue
                keep_ids.append(photo_id)

        self.request(
            "POST",
            "/worker/showcase",
            json={
                "event_name": event_name,
                "folder_name": folder_name,
                "photo_ids": keep_ids,
            },
        )
        LOG.info(
            "Synchronized %d showcase photos for current folder %s",
            len(keep_ids),
            folder_name,
        )

    def sync_gallery_manifest(self):
        from app.database.db import SessionLocal
        from app.database.models import Event, Photo
        from app.services.person_groups import PhotoSource

        with SessionLocal() as db:
            event = db.query(Event).filter_by(id=self.event_id).one_or_none()
            if event is None:
                raise ValueError(f"Local event {self.event_id} does not exist.")
            if not event.event_name or Path(event.event_name).name != event.event_name:
                raise ValueError("Invalid local event folder name.")
            photos = (
                db.query(Photo.id, Photo.filename)
                .filter_by(event_id=self.event_id)
                .order_by(Photo.id)
                .all()
            )
            sources = (
                db.query(PhotoSource)
                .filter_by(event_id=self.event_id)
                .order_by(PhotoSource.id)
                .all()
            )
        source_paths = {}
        for source in sources:
            source_paths.setdefault(source.photo_id, source.relative_path)
        generation = str(uuid4())
        rows = [
            {
                "photo_id": photo_id,
                "filename": filename,
                "source_path": source_paths.get(photo_id, filename),
            }
            for photo_id, filename in photos
        ]
        if not rows:
            self.request("POST", "/worker/gallery/manifest", json={
                "event_name": event.event_name,
                "generation": generation,
                "photos": [],
                "complete": True,
            })
        else:
            for start in range(0, len(rows), GALLERY_MANIFEST_BATCH_SIZE):
                batch = rows[start:start + GALLERY_MANIFEST_BATCH_SIZE]
                self.request("POST", "/worker/gallery/manifest", json={
                    "event_name": event.event_name,
                    "generation": generation,
                    "photos": batch,
                    "complete": start + len(batch) == len(rows),
                })
        LOG.info("Synchronized %d gallery photos", len(rows))

    def claim_gallery_job(self):
        job = self.request("POST", "/worker/gallery/claim").json().get("job")
        return job

    def process_gallery_once(self):
        job = self.claim_gallery_job()
        if not job:
            return False
        return self.process_gallery_job(job)

    def process_gallery_job(self, job):
        job_id = str(UUID(str(job["id"])))
        lease = str(UUID(str(job["lease"])))
        photo_id = job.get("photo_id")
        variant = job.get("variant")
        headers = {"X-Gallery-Job-Lease": lease}
        try:
            if not isinstance(photo_id, int) or photo_id < 1 or variant not in {"thumbnail", "original"}:
                raise ValueError("Invalid gallery request.")
            from app.database.db import SessionLocal
            from app.database.models import Event, Photo
            from app.services.photo_sources import resolve_photo_file

            with SessionLocal() as db:
                event = db.query(Event).filter_by(id=self.event_id).one_or_none()
                photo = db.query(Photo).filter_by(id=photo_id, event_id=self.event_id).one_or_none()
                if event is None or photo is None:
                    raise FileNotFoundError("Gallery photo is unavailable.")
                source_path = resolve_photo_file(db, event, photo)
            source_path = Path(source_path).resolve(strict=True)
            with tempfile.TemporaryDirectory(prefix="photo-finder-gallery-") as directory:
                transfer_path = source_path
                if variant == "thumbnail":
                    transfer_path = prepare_photo_for_showcase(
                        source_path,
                        Path(directory) / f"{photo_id}.webp",
                    )
                    content_type = "image/webp"
                else:
                    if source_path.stat().st_size > MAX_GALLERY_ORIGINAL_BYTES:
                        raise ValueError("Original photo exceeds the relay size limit.")
                    content_type = mimetypes.guess_type(source_path.name)[0]
                    if content_type not in {"image/jpeg", "image/png", "image/webp"}:
                        raise ValueError("Unsupported gallery photo format.")
                with transfer_path.open("rb") as photo_file:
                    self.request(
                        "POST",
                        f"/worker/gallery/jobs/{job_id}/complete",
                        headers=headers,
                        retry_safe=True,
                        files={"photo": (transfer_path.name, photo_file, content_type)},
                    )
            LOG.info("Delivered gallery %s photo %s", variant, photo_id)
        except (OSError, ValueError) as exc:
            LOG.warning("Gallery photo %s unavailable (%s)", photo_id, type(exc).__name__)
            self.request(
                "POST",
                f"/worker/gallery/jobs/{job_id}/fail",
                headers=headers,
                retry_safe=True,
            )
        return True

    def cleanup_gallery(self):
        response = self.request("POST", "/worker/gallery/cleanup")
        cleaned = response.json().get("cleaned", 0)
        if cleaned:
            LOG.info("Cleaned %d expired gallery photo transfers", cleaned)

    def request(self, method, path, *, retry_safe=False, session=None, timeout=(10, 120), **kwargs):
        # Multipart uploads consume their file objects; rewind them on each retry.
        client = session or self.session
        files = kwargs.get("files", {})
        positions = [(value[1], value[1].tell()) for value in files.values() if hasattr(value[1], "tell")]
        attempts = 5 if retry_safe else 1
        for attempt in range(attempts):
            for file, position in positions:
                file.seek(position)
            try:
                response = client.request(method, self.relay_url + path,
                                          timeout=timeout, allow_redirects=False, **kwargs)
                if 300 <= response.status_code < 400:
                    raise requests.HTTPError("Relay redirects are not allowed.")
                if response.status_code in (429, 500, 502, 503, 504) and attempt + 1 < attempts:
                    response.close()
                    LOG.warning("Relay returned %s; retrying", response.status_code)
                    time.sleep(min(2 ** attempt, 8))
                    continue
                if not response.ok:
                    LOG.warning("Relay %s %s returned HTTP %s", method, path, response.status_code)
                response.raise_for_status()
                return response
            except (requests.ConnectionError, requests.Timeout) as exc:
                LOG.warning("Relay %s %s failed (%s)", method, path, type(exc).__name__)
                if attempt + 1 == attempts:
                    raise
                LOG.warning("Relay connection interrupted; retrying")
                time.sleep(min(2 ** attempt, 8))

    @contextmanager
    def keep_job_alive(self, job_id, headers):
        # Matching and an upload batch can both outlast a lease. Use a separate
        # session so heartbeats continue while the main thread is blocked.
        stop = Event()
        errors = []

        def heartbeat():
            failures = 0
            with requests.Session() as session:
                session.headers.update(self.session.headers)
                while not stop.wait(HEARTBEAT_INTERVAL_SECONDS):
                    try:
                        self.request("POST", f"/worker/jobs/{job_id}/heartbeat",
                                     headers=headers, session=session, timeout=(5, 15))
                        failures = 0
                    except requests.RequestException as exc:
                        failures += 1
                        status = exc.response.status_code if exc.response is not None else None
                        if status in (401, 403, 409) or failures >= 3:
                            errors.append(exc)
                            return
                        LOG.warning("Job heartbeat interrupted; retrying")

        def check_lease():
            if errors:
                raise errors[0]

        thread = Thread(target=heartbeat, name="relay-job-heartbeat", daemon=True)
        thread.start()
        try:
            yield check_lease
        finally:
            stop.set()
            thread.join()

    def process_once(self):
        response = self.request("POST", "/worker/claim")
        job = response.json().get("job")
        if not job:
            return False
        # Job IDs and leases are opaque, server-generated UUIDs. Never use them as paths.
        from uuid import UUID
        job_id = str(UUID(str(job["id"])))
        lease = str(UUID(str(job["lease"])))
        already_uploaded = set(job.get("uploaded_photo_ids", []))
        headers = {"X-Job-Lease": lease}
        started = time.monotonic()
        LOG.info("Started request %s (resuming %d uploaded photos)", job_id, len(already_uploaded))
        try:
            with self.keep_job_alive(job_id, headers) as check_lease, \
                    tempfile.TemporaryDirectory(prefix="photo-finder-") as directory:
                selfie = Path(directory) / "selfie.jpg"
                with self.request("GET", f"/worker/jobs/{job_id}/selfie", headers=headers, retry_safe=True,
                                  stream=True) as download, selfie.open("wb") as output:
                    total = 0
                    for chunk in download.iter_content(64 * 1024):
                        total += len(chunk)
                        if total > MAX_SELFIE_BYTES:
                            raise ValueError("Selfie exceeds the size limit.")
                        output.write(chunk)
                if not total:
                    raise ValueError("Selfie is empty.")
                if self.engine is None:
                    self.engine = FaceEngine()
                # Serialize the existing InsightFace/FAISS call when another
                # job is transferring photos. Neither algorithm is changed.
                with self.matching_lock:
                    matches = find_event_matches(self.event_id, selfie, engine=self.engine)
                matches = [
                    match for match in matches
                    if match["photo_id"] not in already_uploaded
                ]
                check_lease()
                LOG.info("Matched %d new photos in %.1fs", len(matches), time.monotonic() - started)
                # Publish every match before transferring image bytes, so the
                # guest sees the full count while the uploads are in progress.
                try:
                    self.request(
                        "POST", f"/worker/jobs/{job_id}/matches",
                        headers=headers, retry_safe=True,
                        json={"photos": [
                            {key: match[key] for key in ("photo_id", "filename", "similarity", "face_count")}
                            for match in matches
                        ]},
                    )
                except requests.HTTPError as exc:
                    if exc.response is None or exc.response.status_code != 404:
                        raise
                    LOG.warning("Relay does not support live match counts yet; continuing photo delivery")
                check_lease()

                def upload_match(match):
                    check_lease()
                    source_path = Path(match["path"])
                    if source_path.stat().st_size > MAX_RESULT_SOURCE_BYTES:
                        transfer_path = prepare_photo_for_transfer(
                            source_path,
                            Path(directory) / f"{match['photo_id']}.jpg"
                        )
                    else:
                        transfer_path = source_path
                    with requests.Session() as upload_session:
                        upload_session.headers.update(self.session.headers)
                        with transfer_path.open("rb") as photo:
                            self.request(
                                "POST",
                                f"/worker/jobs/{job_id}/results",
                                headers=headers,
                                retry_safe=True,
                                session=upload_session,
                                data={
                                    "photo_id": str(match["photo_id"]),
                                    "similarity": str(match["similarity"]),
                                    "face_count": str(match["face_count"]),
                                },
                                files={
                                    "photo": (
                                        transfer_path.name if transfer_path != source_path else match["filename"],
                                        photo,
                                        mimetypes.guess_type(transfer_path.name)[0]
                                        or "application/octet-stream",
                                    )
                                },
                            )

                match_iter = iter(matches)
                uploaded_count = 0
                with ThreadPoolExecutor(max_workers=RESULT_UPLOAD_WORKERS) as executor:
                    pending = {
                        executor.submit(upload_match, match)
                        for _, match in zip(range(RESULT_UPLOAD_WORKERS), match_iter)
                    }
                    while pending:
                        completed, pending = wait(pending, return_when=FIRST_COMPLETED)
                        for future in completed:
                            future.result()
                            check_lease()
                            uploaded_count += 1
                            try:
                                next_match = next(match_iter)
                            except StopIteration:
                                continue
                            pending.add(executor.submit(upload_match, next_match))
                        if uploaded_count % RESULT_UPLOAD_WORKERS == 0 or uploaded_count == len(matches):
                            LOG.info(
                                "Uploaded %d/%d matched photos",
                                uploaded_count,
                                len(matches),
                            )
                check_lease()
                self.request("POST", f"/worker/jobs/{job_id}/complete", headers=headers, retry_safe=True)
                LOG.info("Finished request %s with %d new matches in %.1fs",
                         job_id, len(matches), time.monotonic() - started)
        except ValueError as exc:
            # Validation errors are safe to show; internal errors must not disclose local paths.
            message = str(exc) if str(exc) in {
                "Please use a clear selfie with exactly one face.",
                "Selfie exceeds the size limit.", "Selfie is empty."
            } else "Unable to process this selfie."
            self.request("POST", f"/worker/jobs/{job_id}/fail", headers=headers,
                         json={"message": message}, retry_safe=True)
        except (requests.RequestException, OSError):
            # Return the lease promptly instead of leaving a guest waiting for it to expire.
            try:
                self.request("POST", f"/worker/jobs/{job_id}/release", headers=headers, retry_safe=True)
            except requests.RequestException:
                LOG.warning("Could not release request; it will be retried after its lease expires")
            raise
        return True


def process_search_queue(worker, stop, poll_seconds):
    """One extra delivery slot, with its own HTTP session and the shared AI lock."""
    try:
        while not stop.is_set():
            try:
                if not worker.process_once():
                    stop.wait(poll_seconds)
            except (requests.RequestException, OSError, KeyError) as exc:
                if isinstance(exc, requests.HTTPError) and exc.response is not None and exc.response.status_code == 401:
                    LOG.error("Additional search slot credentials were rejected; stopping that slot")
                    return
                LOG.warning("Additional search slot interrupted (%s); retrying", type(exc).__name__)
                stop.wait(poll_seconds)
    finally:
        worker.session.close()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Process online photo requests on this laptop.")
    parser.add_argument("--event-id", type=int, required=True, help="Local event ID")
    parser.add_argument(
        "--secrets-file",
        type=Path,
        default=Path(".env.relay"),
        help="Event-specific file containing PHOTO_FINDER_WORKER_SECRET",
    )
    parser.add_argument("--relay-url", required=True, help="Trusted HTTPS relay URL")
    parser.add_argument("--poll-seconds", type=float, default=3)
    parser.add_argument("--search-workers", type=int, choices=(1, 2), default=2,
                        help="Concurrent photo deliveries; face matching remains sequential (default: 2)")
    args = parser.parse_args()
    if args.poll_seconds <= 0:
        parser.error("--poll-seconds must be positive")
    load_dotenv(args.secrets_file, override=True)
    secret = os.environ.get("PHOTO_FINDER_WORKER_SECRET")
    if not secret:
        parser.error("Set PHOTO_FINDER_WORKER_SECRET in the environment first.")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    worker = RelayWorker(args.relay_url, secret, args.event_id)
    stop = Event()
    extra_thread = None
    try:
        initialized = False
        next_showcase_sync = 0.0
        next_gallery_cleanup = 0.0
        while True:
            try:
                if not initialized:
                    worker.request("POST", "/worker/setup")
                    initialized = True
                    next_showcase_sync = 0
                    if args.search_workers == 2 and extra_thread is None:
                        extra = RelayWorker(args.relay_url, secret, args.event_id,
                                            matching_lock=worker.matching_lock)
                        extra_thread = Thread(target=process_search_queue,
                                              args=(extra, stop, args.poll_seconds),
                                              name="relay-search-slot-2", daemon=True)
                        extra_thread.start()
                        LOG.info("Two search delivery slots active; face matching remains sequential")
                if time.monotonic() >= next_showcase_sync:
                    try:
                        worker.sync_showcase()
                    except (requests.RequestException, OSError, ValueError, KeyError) as exc:
                        LOG.warning("Showcase sync failed (%s); retrying in one minute", type(exc).__name__)
                    try:
                        worker.sync_gallery_manifest()
                    except (requests.RequestException, OSError, ValueError, KeyError) as exc:
                        LOG.warning("Gallery manifest sync failed (%s); retrying in one minute", type(exc).__name__)
                    next_showcase_sync = time.monotonic() + 60
                if time.monotonic() >= next_gallery_cleanup:
                    try:
                        worker.cleanup_gallery()
                    except (requests.RequestException, ValueError, KeyError) as exc:
                        LOG.warning("Gallery cleanup failed (%s); retrying in five minutes", type(exc).__name__)
                    next_gallery_cleanup = time.monotonic() + GALLERY_CLEANUP_INTERVAL_SECONDS
                processed = worker.process_once()
                if not processed:
                    processed = worker.process_gallery_once()
                if not processed:
                    time.sleep(args.poll_seconds)
            except (requests.RequestException, OSError, KeyError) as exc:
                if isinstance(exc, requests.HTTPError) and exc.response is not None and exc.response.status_code == 401:
                    LOG.error("Worker credentials were rejected. Check %s before restarting.", args.secrets_file)
                    return 1
                LOG.warning("Relay request failed (%s); retrying", type(exc).__name__)
                time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        LOG.info("Worker stopped")
    finally:
        stop.set()
        if extra_thread is not None:
            extra_thread.join(timeout=2)


if __name__ == "__main__":
    raise SystemExit(main())

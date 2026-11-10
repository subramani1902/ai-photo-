"""Laptop-side worker for a photo-finder relay.

The relay URL must point to a trusted HTTPS service implementing the worker
endpoints. The worker secret is never sent to guest browsers.
"""

import logging
import mimetypes
import os
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

import cv2
import numpy as np
import requests
from dotenv import load_dotenv

from app.ai.face_engine import FaceEngine
from app.services.remote_matching import find_event_matches

LOG = logging.getLogger(__name__)
MAX_SELFIE_BYTES = 20 * 1024 * 1024
MAX_RESULT_SOURCE_BYTES = 8 * 1024 * 1024
MAX_RESULT_DIMENSION = 1600
RESULT_JPEG_QUALITY = 85
RESULT_UPLOAD_WORKERS = 4
SHOWCASE_PHOTO_COUNT = 3
SHOWCASE_MAX_DIMENSION = 640
SHOWCASE_WEBP_QUALITY = 78


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
    def __init__(self, relay_url, worker_secret, event_id, *, session=None, engine=None):
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

    def sync_showcase(self):
        from app.database.db import SessionLocal
        from app.database.models import Event, Photo

        with SessionLocal() as db:
            event = db.query(Event).filter_by(id=self.event_id).one_or_none()
            if event is None:
                raise ValueError(f"Local event {self.event_id} does not exist.")
            event_name = event.event_name
            if not event_name or Path(event_name).name != event_name or event_name in {".", ".."}:
                raise ValueError("Invalid local event folder name.")
            photos = (
                db.query(Photo)
                .filter_by(event_id=self.event_id)
                .order_by(Photo.id.desc())
                .limit(SHOWCASE_PHOTO_COUNT)
                .all()
            )
            photo_rows = [(photo.id, photo.filename, photo.file_path) for photo in photos]

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
                    source_path.relative_to(event_root)
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
            json={"event_name": event_name, "photo_ids": keep_ids},
        )
        LOG.info("Synchronized %d showcase photos for event %s", len(keep_ids), event_name)

    def request(self, method, path, *, retry_safe=False, session=None, **kwargs):
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
                                          timeout=(10, 120), allow_redirects=False, **kwargs)
                if 300 <= response.status_code < 400:
                    raise requests.HTTPError("Relay redirects are not allowed.")
                if response.status_code in (429, 500, 502, 503, 504) and attempt + 1 < attempts:
                    response.close()
                    LOG.warning("Relay returned %s; retrying", response.status_code)
                    time.sleep(min(2 ** attempt, 8))
                    continue
                response.raise_for_status()
                return response
            except (requests.ConnectionError, requests.Timeout):
                if attempt + 1 == attempts:
                    raise
                LOG.warning("Relay connection interrupted; retrying")
                time.sleep(min(2 ** attempt, 8))

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
        try:
            with tempfile.TemporaryDirectory(prefix="photo-finder-") as directory:
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
                matches = find_event_matches(self.event_id, selfie, engine=self.engine)
                matches = [
                    match for match in matches
                    if match["photo_id"] not in already_uploaded
                ]

                def upload_match(match):
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

                last_heartbeat = time.monotonic()
                for start in range(0, len(matches), RESULT_UPLOAD_WORKERS):
                    if time.monotonic() - last_heartbeat >= 30:
                        self.request(
                            "POST",
                            f"/worker/jobs/{job_id}/heartbeat",
                            headers=headers,
                            retry_safe=True,
                        )
                        last_heartbeat = time.monotonic()
                    batch = matches[start:start + RESULT_UPLOAD_WORKERS]
                    with ThreadPoolExecutor(max_workers=RESULT_UPLOAD_WORKERS) as executor:
                        futures = [executor.submit(upload_match, match) for match in batch]
                        for future in as_completed(futures):
                            future.result()
                    LOG.info(
                        "Uploaded %d/%d matched photos",
                        min(start + len(batch), len(matches)),
                        len(matches),
                    )
                self.request("POST", f"/worker/jobs/{job_id}/complete", headers=headers, retry_safe=True)
                LOG.info("Finished request %s with %d matches", job_id, len(matches))
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
    args = parser.parse_args()
    if args.poll_seconds <= 0:
        parser.error("--poll-seconds must be positive")
    load_dotenv(args.secrets_file, override=True)
    secret = os.environ.get("PHOTO_FINDER_WORKER_SECRET")
    if not secret:
        parser.error("Set PHOTO_FINDER_WORKER_SECRET in the environment first.")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    worker = RelayWorker(args.relay_url, secret, args.event_id)
    try:
        initialized = False
        next_showcase_sync = 0.0
        while True:
            try:
                if not initialized:
                    worker.request("POST", "/worker/setup")
                    initialized = True
                    next_showcase_sync = 0
                if time.monotonic() >= next_showcase_sync:
                    try:
                        worker.sync_showcase()
                    except (requests.RequestException, OSError, ValueError, KeyError) as exc:
                        LOG.warning("Showcase sync failed (%s); retrying in one minute", type(exc).__name__)
                    next_showcase_sync = time.monotonic() + 60
                if not worker.process_once():
                    time.sleep(args.poll_seconds)
            except (requests.RequestException, OSError, KeyError) as exc:
                LOG.warning("Relay request failed (%s); retrying", type(exc).__name__)
                time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        LOG.info("Worker stopped")


if __name__ == "__main__":
    main()

"""Laptop-side worker for a photo-finder relay.

The relay URL must point to a trusted HTTPS service implementing the worker
endpoints. The worker secret is never sent to guest browsers.
"""

import logging
import mimetypes
import os
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

from app.ai.face_engine import FaceEngine
from app.services.remote_matching import find_event_matches

LOG = logging.getLogger(__name__)
MAX_SELFIE_BYTES = 20 * 1024 * 1024


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

    def request(self, method, path, *, retry_safe=False, **kwargs):
        # Multipart uploads consume their file objects; rewind them on each retry.
        files = kwargs.get("files", {})
        positions = [(value[1], value[1].tell()) for value in files.values() if hasattr(value[1], "tell")]
        attempts = 5 if retry_safe else 1
        for attempt in range(attempts):
            for file, position in positions:
                file.seek(position)
            try:
                response = self.session.request(method, self.relay_url + path,
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
                last_heartbeat = time.monotonic()
                for match in matches:
                    if match["photo_id"] in already_uploaded:
                        continue
                    if time.monotonic() - last_heartbeat >= 30:
                        self.request("POST", f"/worker/jobs/{job_id}/heartbeat", headers=headers, retry_safe=True)
                        last_heartbeat = time.monotonic()
                    with match["path"].open("rb") as photo:
                        self.request("POST", f"/worker/jobs/{job_id}/results", headers=headers, retry_safe=True,
                                     data={"photo_id": str(match["photo_id"]),
                                           "similarity": str(match["similarity"])},
                                     files={"photo": (match["filename"], photo,
                                                      mimetypes.guess_type(match["filename"])[0] or "application/octet-stream")})
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
    parser.add_argument("--relay-url", required=True, help="Trusted HTTPS relay URL")
    parser.add_argument("--poll-seconds", type=float, default=3)
    args = parser.parse_args()
    if args.poll_seconds <= 0:
        parser.error("--poll-seconds must be positive")
    load_dotenv(".env.relay")
    secret = os.environ.get("PHOTO_FINDER_WORKER_SECRET")
    if not secret:
        parser.error("Set PHOTO_FINDER_WORKER_SECRET in the environment first.")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    worker = RelayWorker(args.relay_url, secret, args.event_id)
    try:
        initialized = False
        while True:
            try:
                if not initialized:
                    worker.request("POST", "/worker/setup")
                    initialized = True
                if not worker.process_once():
                    time.sleep(args.poll_seconds)
            except (requests.RequestException, OSError, KeyError) as exc:
                LOG.warning("Relay request failed (%s); retrying", type(exc).__name__)
                time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        LOG.info("Worker stopped")


if __name__ == "__main__":
    main()

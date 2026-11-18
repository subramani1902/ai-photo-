"""Deliver authorized private-gallery photos without waiting for selfie searches."""

import argparse
import logging
import os
from pathlib import Path
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from threading import Event as StopEvent, local

from dotenv import load_dotenv
import requests

from app.database.db import SessionLocal
from app.database.models import Event, User
from app.services.relay_worker import RelayWorker

LOG = logging.getLogger(__name__)


def deliver_parallel(dispatcher, factory, workers, poll_seconds, stop=None):
    """One idle poller; each busy delivery thread keeps its own HTTP session."""
    stop = stop or StopEvent()
    thread_state, sessions = local(), []

    def deliver(job):
        if not hasattr(thread_state, "worker"):
            thread_state.worker = factory()
            sessions.append(thread_state.worker.session)
        return thread_state.worker.process_gallery_job(job)

    try:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="gallery-preview") as pool:
            pending = set()
            while not stop.is_set():
                for completed in [future for future in pending if future.done()]:
                    pending.remove(completed)
                    try:
                        completed.result()
                    except (requests.RequestException, OSError, ValueError, KeyError) as error:
                        LOG.warning("Gallery transfer interrupted (%s); its lease permits retry", type(error).__name__)
                try:
                    while len(pending) < workers and not stop.is_set():
                        job = dispatcher.claim_gallery_job()
                        if not job:
                            break
                        pending.add(pool.submit(deliver, job))
                except (requests.RequestException, OSError, KeyError) as error:
                    if isinstance(error, requests.HTTPError) and error.response is not None and error.response.status_code == 401:
                        raise
                    LOG.warning("Gallery queue interrupted (%s); retrying", type(error).__name__)
                if pending:
                    wait(pending, timeout=poll_seconds, return_when=FIRST_COMPLETED)
                else:
                    stop.wait(poll_seconds)
    finally:
        for session in sessions:
            session.close()


def validate_event(worker):
    with SessionLocal() as db:
        event = (db.query(Event).join(User, User.id == Event.owner_id)
                 .filter(Event.id == worker.event_id, User.is_active.is_(True)).one_or_none())
        if event is None:
            raise ValueError("Choose an existing event belonging to an active local owner.")
        event_name = event.event_name
    remote = worker.request("GET", "/worker/showcase", retry_safe=True).json()
    if remote.get("event_name") != event_name:
        raise ValueError("The gallery worker credentials belong to a different event.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-id", type=int, required=True)
    parser.add_argument("--relay-url", required=True)
    parser.add_argument("--secrets-file", type=Path, default=Path(".env.relay"))
    parser.add_argument("--poll-seconds", type=float, default=2)
    parser.add_argument("--workers", type=int, choices=(1, 2, 3, 4), default=3,
                        help="Parallel private-gallery deliveries (default: 3)")
    args = parser.parse_args()
    if args.event_id < 1 or args.poll_seconds <= 0:
        parser.error("Use a positive event ID and polling interval.")
    load_dotenv(args.secrets_file, override=True)
    secret = os.environ.get("PHOTO_FINDER_WORKER_SECRET")
    if not secret:
        parser.error("The gallery worker secret is missing.")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    worker = RelayWorker(args.relay_url, secret, args.event_id)
    initialized = False
    try:
        while True:
            try:
                if not initialized:
                    validate_event(worker)
                    worker.request("POST", "/worker/setup", retry_safe=True)
                    initialized = True
                    LOG.info("Private gallery delivery is active independently of selfie searches")
                    if args.workers > 1:
                        LOG.info("Preparing up to %d private gallery previews in parallel", args.workers)
                        deliver_parallel(worker, lambda: RelayWorker(args.relay_url, secret, args.event_id),
                                         args.workers, args.poll_seconds)
                        return 0
                if not worker.process_gallery_once():
                    time.sleep(args.poll_seconds)
            except (requests.RequestException, OSError, KeyError) as error:
                status = error.response.status_code if isinstance(error, requests.HTTPError) and error.response is not None else None
                if status == 401:
                    LOG.error("Gallery worker credentials were rejected; check the selected settings file")
                    return 1
                LOG.warning("Gallery delivery interrupted (%s); retrying", type(error).__name__)
                time.sleep(args.poll_seconds)
    except ValueError as error:
        LOG.error("%s", error)
        return 1
    except KeyboardInterrupt:
        LOG.info("Gallery worker stopped")
        return 0
    finally:
        worker.session.close()


if __name__ == "__main__":
    raise SystemExit(main())

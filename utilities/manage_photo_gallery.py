"""Create or revoke a password-protected gallery link for a local photo event."""

import argparse
from getpass import getpass
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from dotenv import dotenv_values

from app.database.db import SessionLocal
from app.database.models import Event, Photo
from app.services.person_groups import EventSourceFolder, PhotoSource
from app.services.photo_sources import source_paths_in

DEFAULT_RELAY_URL = "https://dtfxnuxluuwvjdcyxmnc.supabase.co/functions/v1/relay"
DEFAULT_SITE_URL = "https://sm-photo-studio.vercel.app"


def local_manifest(event_id):
    with SessionLocal() as db:
        event = db.query(Event).filter_by(id=event_id).one_or_none()
        if event is None:
            raise RuntimeError(f"Local event {event_id} does not exist.")
        registration = (
            db.query(EventSourceFolder)
            .filter_by(event_id=event_id)
            .one_or_none()
        )
        if registration is None:
            raise RuntimeError(f"No watched photo folder is registered for event {event_id}.")
        folder = Path(registration.folder_path).resolve(strict=True)
        expected_paths = set(source_paths_in(folder))
        tracked_paths = {
            row.relative_path
            for row in db.query(PhotoSource.relative_path)
            .filter_by(event_id=event_id)
            .all()
        }
        photos = (
            db.query(Photo.id, Photo.filename)
            .filter_by(event_id=event_id)
            .order_by(Photo.id)
            .all()
        )
        manifest = [
            {"photo_id": photo_id, "filename": filename}
            for photo_id, filename in photos
        ]
        return event.event_name, expected_paths, tracked_paths, manifest


def remote_manifest(relay_url, headers, event_name):
    offset = 0
    expected_count = None
    photos = []
    while True:
        response = requests.get(
            f"{relay_url.rstrip('/')}/worker/gallery/manifest",
            headers=headers,
            params={"limit": 500, "offset": offset},
            timeout=(10, 30),
            allow_redirects=False,
        )
        response.raise_for_status()
        page = response.json()
        if page.get("event_name") != event_name:
            raise RuntimeError("The local event does not match the relay event.")
        if expected_count is None:
            expected_count = page.get("photo_count")
        elif expected_count != page.get("photo_count"):
            return None
        if not isinstance(expected_count, int):
            raise RuntimeError("Relay returned an invalid gallery manifest.")
        photos.extend(page.get("photos", []))
        offset = page.get("next_offset")
        if offset is None:
            break
    if len(photos) != expected_count:
        return None
    return photos


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-id", type=int, default=3, help="Local event ID")
    parser.add_argument("--relay-url", default=DEFAULT_RELAY_URL, help="Trusted HTTPS relay URL")
    parser.add_argument("--site-url", default=DEFAULT_SITE_URL, help="Public website base URL")
    parser.add_argument("--secrets-file", type=Path, default=Path(".env.relay"))
    parser.add_argument("--expires-days", type=int, default=30)
    parser.add_argument("--wait-seconds", type=int, default=180)
    parser.add_argument("--revoke-id", help="Revoke an existing gallery ID instead of creating one")
    args = parser.parse_args()

    relay = urlparse(args.relay_url)
    site = urlparse(args.site_url)
    if relay.scheme != "https" or not relay.hostname or relay.username or relay.password:
        parser.error("--relay-url must be a trusted HTTPS URL.")
    if site.scheme != "https" or not site.hostname or site.username or site.password:
        parser.error("--site-url must be a trusted HTTPS URL.")
    if args.event_id < 1:
        parser.error("--event-id must be positive.")
    if args.wait_seconds < 1 or args.wait_seconds > 900:
        parser.error("--wait-seconds must be between 1 and 900.")
    if not args.revoke_id and not 1 <= args.expires_days <= 90:
        parser.error("--expires-days must be between 1 and 90.")

    if args.revoke_id:
        try:
            from uuid import UUID
            gallery_id = str(UUID(args.revoke_id))
        except ValueError:
            parser.error("--revoke-id must be a gallery UUID.")

    secret = dotenv_values(args.secrets_file).get("PHOTO_FINDER_WORKER_SECRET")
    if not secret:
        parser.error(f"PHOTO_FINDER_WORKER_SECRET is missing from {args.secrets_file}.")
    headers = {"Authorization": f"Bearer {secret}"}

    if args.revoke_id:
        response = requests.post(
            f"{args.relay_url.rstrip('/')}/worker/gallery/{gallery_id}/revoke",
            headers=headers,
            timeout=(10, 60),
            allow_redirects=False,
        )
        response.raise_for_status()
        print(f"Revoked gallery {gallery_id}.")
        return 0

    event_name, expected_paths, tracked_paths, expected_manifest = local_manifest(args.event_id)
    pending = expected_paths - tracked_paths
    stale = tracked_paths - expected_paths
    if pending or stale:
        print(
            f"Gallery not created: the importer has not finished syncing "
            f"({len(pending)} files pending, {len(stale)} stale). Keep it running and retry.",
            file=sys.stderr,
        )
        return 1

    deadline = time.monotonic() + args.wait_seconds
    while True:
        remote_photos = remote_manifest(args.relay_url, headers, event_name)
        if remote_photos == expected_manifest:
            break
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"The relay gallery manifest does not match the {len(expected_manifest)} "
                "local photos. Make sure the relay worker is running, then retry."
            )
        time.sleep(10)

    password = getpass("Set a gallery password (12-128 characters): ")
    if len(password) < 12 or len(password) > 128:
        parser.error("Gallery password must be 12-128 characters.")
    if password != getpass("Re-enter the gallery password: "):
        parser.error("Passwords do not match.")

    response = requests.post(
        f"{args.relay_url.rstrip('/')}/worker/gallery/create",
        headers=headers,
        json={"password": password, "expires_days": args.expires_days},
        timeout=(10, 60),
        allow_redirects=False,
    )
    response.raise_for_status()
    result = response.json()
    link = f"{args.site_url.rstrip('/')}/gallery.html#{result['token']}"
    print(f"\nGallery link: {link}")
    print(f"Gallery ID (keep this to revoke): {result['gallery_id']}")
    print(f"Expires: {result['expires_at']}")
    print("Share the password you entered separately from the link.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

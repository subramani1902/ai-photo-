"""Copy supported photos into the watched event folder at a controlled pace."""

import argparse
import os
import shutil
import time
import uuid
from pathlib import Path


EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_IMPORT_BYTES = 100 * 1024 * 1024
DEFAULT_SOURCE = Path(r"D:\PUBERTY2")
DEFAULT_DESTINATION = Path(r"D:\Event-AI-Photo-Finder\Aathif-Sahana")


def discover_photos(root):
    photos = []

    def raise_walk_error(error):
        raise error

    for directory, names, filenames in os.walk(root, onerror=raise_walk_error):
        directory_path = Path(directory)
        names[:] = [
            name for name in names
            if not (directory_path / name).is_symlink()
        ]
        for filename in filenames:
            path = directory_path / filename
            if path.is_symlink() or path.suffix.lower() not in EXTENSIONS:
                continue
            if path.is_file():
                photos.append(path)
    return sorted(photos)


def copy_photo(source, source_root, destination_root):
    relative_path = source.relative_to(source_root)
    destination = destination_root / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        return False

    temporary = destination.with_name(
        f".{destination.name}.{uuid.uuid4().hex}.part"
    )
    before = source.stat()
    try:
        with source.open("rb") as input_file, temporary.open("xb") as output_file:
            shutil.copyfileobj(input_file, output_file, length=1024 * 1024)
        after = source.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise RuntimeError(f"Source changed while copying: {source}")
        shutil.copystat(source, temporary)
        temporary.rename(destination)
        return True
    finally:
        if temporary.exists():
            temporary.unlink()


def copy_photos(source_root, destination_root, photos, interval):
    copied = skipped = 0
    for source in photos:
        relative_path = source.relative_to(source_root)
        destination = destination_root / relative_path
        if destination.exists():
            skipped += 1
            print(f"Skipped existing: {relative_path}", flush=True)
            continue
        if copied:
            time.sleep(interval)
        if copy_photo(source, source_root, destination_root):
            copied += 1
            print(f"Copied {copied}/{len(photos)}: {relative_path}", flush=True)
        else:
            skipped += 1
            print(f"Skipped existing: {relative_path}", flush=True)
    return copied, skipped


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    parser.add_argument("--interval", type=float, default=10)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.interval <= 0:
        parser.error("--interval must be positive.")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive.")
    if not args.source.is_dir():
        parser.error(f"Source folder does not exist: {args.source}")

    source_root = args.source.resolve(strict=True)
    destination_root = args.destination.resolve()
    if (source_root == destination_root or
            source_root in destination_root.parents or
            destination_root in source_root.parents):
        parser.error("Source and destination folders must not overlap.")

    photos = discover_photos(source_root)
    if args.limit is not None:
        photos = photos[:args.limit]
    if not photos:
        parser.error("No supported photos were found in the source folder.")

    oversized = [path for path in photos if path.stat().st_size > MAX_IMPORT_BYTES]
    if oversized:
        parser.error(
            f"{len(oversized)} photo(s) exceed the importer's 100 MB limit; "
            f"first: {oversized[0].relative_to(source_root)}"
        )

    total_bytes = sum(path.stat().st_size for path in photos)
    wait_seconds = max(0, len(photos) - 1) * args.interval
    print(
        f"Photos: {len(photos)}; size: {total_bytes / (1024 ** 3):.2f} GiB; "
        f"scheduled waits: {wait_seconds / 60:.1f} minutes.",
        flush=True,
    )
    if args.dry_run:
        return 0

    destination_root.mkdir(parents=True, exist_ok=True)
    copied, skipped = copy_photos(
        source_root,
        destination_root,
        photos,
        args.interval,
    )
    print(f"Finished. Copied: {copied}; skipped: {skipped}.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

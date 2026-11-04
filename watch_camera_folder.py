"""Import camera transfers into an event through the running local API."""

import argparse
from contextlib import ExitStack
import getpass
import time
from pathlib import Path

import requests


EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def scan_ready(folder, observed, processed, now, stable_seconds):
    """Return files whose size and modification time have stopped changing."""
    ready = []
    for path in folder.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in EXTENSIONS:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        signature = (stat.st_size, stat.st_mtime_ns)
        if processed.get(path) == signature:
            continue
        previous = observed.get(path)
        if previous is None or previous[0] != signature:
            observed[path] = (signature, now)
        elif stat.st_size and now - previous[1] >= stable_seconds:
            ready.append((path, signature))
    return ready


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path, help="Folder used by the camera transfer app")
    parser.add_argument("--event-id", type=int, required=True)
    parser.add_argument("--server", default="http://127.0.0.1:8000")
    parser.add_argument("--interval", type=float, default=2)
    parser.add_argument("--stable-seconds", type=float, default=5)
    args = parser.parse_args()
    folder = args.folder.resolve()
    if not folder.is_dir():
        parser.error("The camera folder must already exist.")
    if args.interval <= 0 or args.stable_seconds <= 0:
        parser.error("Interval and stable-seconds must be positive.")
    if args.server.rstrip("/") not in ("http://127.0.0.1:8000", "http://localhost:8000") and not args.server.startswith("https://"):
        parser.error("Use HTTPS for servers other than the default local server.")

    email = input("Admin email: ").strip()
    password = getpass.getpass("Admin password: ")
    server = args.server.rstrip("/")
    session = requests.Session()

    def login():
        response = session.post(f"{server}/api/auth/login", data={"username": email, "password": password}, timeout=30)
        response.raise_for_status()
        session.headers["Authorization"] = "Bearer " + response.json()["access_token"]

    try:
        login()
        response = session.get(f"{server}/api/events/{args.event_id}", timeout=30)
        response.raise_for_status()
    except (requests.RequestException, KeyError, ValueError) as error:
        print(f"Could not authenticate or access event: {error}")
        return 1

    print(f"Watching {folder} for event {args.event_id}. Press Ctrl+C to stop.", flush=True)
    observed, processed = {}, {}
    try:
        while True:
            ready = scan_ready(folder, observed, processed, time.monotonic(), args.stable_seconds)
            for start in range(0, len(ready), 20):
                batch = ready[start:start + 20]
                try:
                    with ExitStack() as stack:
                        uploads = [("files", (path.name, stack.enter_context(path.open("rb")))) for path, _ in batch]
                        response = session.post(f"{server}/api/events/{args.event_id}/upload", files=uploads, timeout=(10, 600))
                    if response.status_code == 401:
                        login()
                        continue
                    response.raise_for_status()
                    result = response.json()
                    items = result["results"]
                    if len(items) != len(batch):
                        raise ValueError("Unexpected upload response length")
                    for (path, signature), item in zip(batch, items):
                        if item["status"] in ("success", "skipped"):
                            processed[path] = signature
                            print(f"{item['status']}: {path.name}", flush=True)
                        else:
                            print(f"Failed: {path.name}: {item.get('message', 'unknown error')}; retrying.", flush=True)
                    print(f"Index: {result['index_status']}", flush=True)
                    if "failed" in result["index_status"].lower():
                        print("WARNING: Search index rebuild failed; an operator must rebuild it.", flush=True)
                except (OSError, requests.RequestException, ValueError, KeyError, IndexError) as error:
                    print(f"Could not import batch starting with {batch[0][0].name}: {error}; retrying.", flush=True)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped watching.")
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

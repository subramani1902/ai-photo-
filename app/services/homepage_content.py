"""Public homepage renditions and private, server-only Firestore publishing state."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import io
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
from uuid import uuid4
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.firebase import get_firestore_client
from app.core.firebase_config import PROJECT_ROOT

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 80_000_000
SLOTS = ("celebration", "bride", "wedding", "together")
PUBLIC_ROOT = PROJECT_ROOT / "online"
MANIFEST = PUBLIC_ROOT / "homepage-portfolio.json"
RENDITION_ROOT = PROJECT_ROOT / "storage/homepage-renditions"
IMAGE_PATTERN = re.compile(r"^\./assets/home/(?:sample-[a-z]+|published/[a-f0-9]{32})\.webp$")


class HomepageError(ValueError):
    def __init__(self, message, status=422):
        super().__init__(message)
        self.status = status


def admin_document_id(uid):
    return sha256(uid.encode()).hexdigest()


def has_website_permission(client, principal, collection, permission):
    claims = principal.claims
    email = claims.get("email")
    if (claims.get("email_verified") is not True or not isinstance(email, str)
            or not email.strip() or principal.uid.startswith("gallery-client:")
            or claims.get("actor_type") == "gallery_client"):
        return False
    role = client.document(f"{collection}/{admin_document_id(principal.uid)}").get(timeout=10).to_dict()
    return bool(role and role.get("firebase_uid") == principal.uid and role.get("enabled") is True
                and role.get("permission") == permission and role.get("email") == email.strip().lower())


def clean_photo(current, *, title, alt, instagram_url="", position="50% 50%"):
    def text(value, maximum):
        if not isinstance(value, str) or not value.strip() or len(value) > maximum or any(ord(c) < 32 for c in value):
            raise HomepageError("Enter a title and image description within the displayed limits.")
        return value.strip()
    photo = deepcopy(current)
    photo.update(title=text(title, 100), alt=text(alt, 250))
    if not re.fullmatch(r"\d{1,3}% \d{1,3}%", position) or any(int(x[:-1]) > 100 for x in position.split()):
        raise HomepageError("Choose a valid image position.")
    photo["position"] = position
    photo.pop("instagramUrl", None)
    if instagram_url:
        if len(instagram_url) > 500:
            raise HomepageError("The Instagram link is too long.")
        url = urlsplit(instagram_url)
        if (url.scheme != "https" or url.netloc not in {"instagram.com", "www.instagram.com"}
                or url.username or url.password or not re.fullmatch(r"/(?:p|reel)/[A-Za-z0-9_-]+/?", url.path)):
            raise HomepageError("Use a public Instagram post or reel link.")
        photo["instagramUrl"] = f"https://www.instagram.com{url.path}"
    return photo


def prepare_homepage_image(data):
    if not data or len(data) > MAX_UPLOAD_BYTES:
        raise HomepageError("Choose a JPG, PNG or WebP image up to 20 MB.", 413)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as source:
                if source.format not in {"JPEG", "PNG", "WEBP"} or getattr(source, "is_animated", False):
                    raise HomepageError("Choose a still JPG, PNG or WebP image.")
                if source.width * source.height > MAX_PIXELS:
                    raise HomepageError("Choose an image smaller than 80 megapixels.")
                source.load()
                image = ImageOps.exif_transpose(source).convert("RGB")
                image.thumbnail((1800, 1800), Image.Resampling.LANCZOS)
                output = io.BytesIO()
                # A new encoder output deliberately omits EXIF, GPS, comments and original bytes.
                image.save(output, "WEBP", quality=82, method=4, exif=b"", icc_profile=b"")
                encoded = output.getvalue()
                if len(encoded) > 2 * 1024 * 1024:
                    raise HomepageError("This image is too detailed for a homepage preview. Try a smaller copy.")
                return encoded
    except HomepageError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise HomepageError("The image could not be read. Choose a valid JPG, PNG or WebP file.") from None


def public_configuration(value):
    if value.get("schema_version") != 1 or len(value.get("photos", [])) != len(SLOTS):
        raise HomepageError("The homepage configuration is invalid.", 503)
    photos = []
    for slot in SLOTS:
        matches = [photo for photo in value["photos"] if photo.get("id") == slot]
        if len(matches) != 1:
            raise HomepageError("The homepage configuration is invalid.", 503)
        photo = matches[0]
        if (not isinstance(photo.get("image"), str) or not IMAGE_PATTERN.fullmatch(photo["image"])
                or type(photo.get("sample")) is not bool):
            raise HomepageError("A homepage image is invalid.", 503)
        safe = {key: photo[key] for key in ("id", "category", "label", "image", "sample")}
        safe = clean_photo(safe, title=photo.get("title"), alt=photo.get("alt"),
                           position=photo.get("position", "50% 50%"), instagram_url=photo.get("instagramUrl", ""))
        photos.append(safe)
    result = {"schema_version": 1, "photos": photos}
    if type(value.get("revision")) is int and value["revision"] > 0:
        result["revision"] = value["revision"]
    if isinstance(value.get("publication_id"), str) and re.fullmatch(r"[a-f0-9]{32}", value["publication_id"]):
        result["publication_id"] = value["publication_id"]
    return result


def initial_state():
    config = public_configuration(json.loads(MANIFEST.read_text(encoding="utf-8")))
    if [p.get("id") for p in config.get("photos", [])] != list(SLOTS):
        raise HomepageError("The homepage configuration needs attention on the studio computer.", 503)
    revision = config.get("revision", 1)
    return {"draft": config, "published": deepcopy(config), "revision": revision,
            "published_revision": revision, "job": None}


def resolve_homepage_image(relative):
    if not IMAGE_PATTERN.fullmatch(relative):
        raise HomepageError("The saved photo is unavailable.", 404)
    root = RENDITION_ROOT if relative.startswith("./assets/home/published/") else PUBLIC_ROOT / "assets/home"
    path = root / Path(relative).name
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
        raise HomepageError("The saved photo is unavailable on the studio computer.", 404)
    return path


def check_window(state, key, *, seconds, maximum):
    now = datetime.now(timezone.utc)
    window = state.get(key, {})
    if not window or (now - window["started_at"]).total_seconds() >= seconds:
        window = {"started_at": now, "count": 0}
    if window["count"] >= maximum:
        raise HomepageError("Too many homepage changes. Please wait before trying again.", 429)
    window["count"] += 1
    state[key] = window


class HomepageStore:
    def __init__(self, client=None):
        self.client = client or get_firestore_client()
        self.reference = self.client.document("website_content/homepage")

    def authorize(self, principal):
        if not has_website_permission(self.client, principal, "website_homepage_admins", "manage_homepage"):
            raise HomepageError("This account cannot manage the studio homepage.", 403)

    def mutate(self, action):
        from google.cloud import firestore

        @firestore.transactional
        def apply(transaction):
            snapshot = self.reference.get(transaction=transaction)
            state = snapshot.to_dict() if snapshot.exists else initial_state()
            result = action(state)
            transaction.set(self.reference, state)
            return result
        return apply(self.client.transaction())

    def read(self):
        snapshot = self.reference.get(timeout=10)
        state = snapshot.to_dict() if snapshot.exists else initial_state()
        job = state.get("job")
        if job and job.get("status") == "publishing" and job["lease_until"] <= datetime.now(timezone.utc):
            def expire(latest):
                active = latest.get("job")
                if active and active["id"] == job["id"] and active["status"] == "publishing" and active["lease_until"] <= datetime.now(timezone.utc):
                    active.update(status="failed", stage="Publishing was interrupted. Your saved photos are ready to publish again.")
                return deepcopy(latest)
            return self.mutate(expire)
        return state

    @staticmethod
    def check_writable(state, revision):
        if revision != state["revision"]:
            raise HomepageError("The saved photos changed. Reload them before saving again.", 409)
        job = state.get("job")
        if job and job["status"] == "publishing" and job["lease_until"] > datetime.now(timezone.utc):
            raise HomepageError("The homepage is publishing. Wait for it to finish before making another change.", 409)

    def save(self, slot_id, fields, image, revision, uid):
        if slot_id not in SLOTS:
            raise HomepageError("Choose one of the four homepage photo positions.", 404)
        image_path = None
        if image is not None:
            encoded = prepare_homepage_image(image)
            folder = RENDITION_ROOT
            folder.mkdir(parents=True, exist_ok=True)
            filename = uuid4().hex + ".webp"
            (folder / filename).write_bytes(encoded)
            image_path = "./assets/home/published/" + filename

        def update(state):
            self.check_writable(state, revision)
            check_window(state, "save_window", seconds=60, maximum=12)
            current = next(p for p in state["draft"]["photos"] if p["id"] == slot_id)
            replacement = clean_photo(current, **fields)
            if image_path:
                replacement.update(image=image_path, sample=False)
            state["draft"]["photos"] = [replacement if p["id"] == slot_id else p for p in state["draft"]["photos"]]
            state["revision"] += 1
            state.update(updated_by=uid, updated_at=datetime.now(timezone.utc))
            return deepcopy(state)
        return self.mutate(update)

    def begin_publish(self, revision, uid):
        def begin(state):
            self.check_writable(state, revision)
            if state["revision"] == state["published_revision"]:
                raise HomepageError("Save a photo or change before publishing.", 409)
            check_window(state, "publish_window", seconds=3600, maximum=6)
            now = datetime.now(timezone.utc)
            job = {"id": uuid4().hex, "revision": revision, "status": "publishing", "stage": "Preparing your homepage…",
                   "started_at": now, "lease_until": now + timedelta(minutes=5), "requested_by": uid}
            state["job"] = job
            config = public_configuration(state["draft"])
            config.update(revision=revision, publication_id=job["id"])
            return job["id"], config
        return self.mutate(begin)

    def progress(self, job_id, message, *, status="publishing", config=None):
        def update(state):
            job = state.get("job")
            if not job or job["id"] != job_id or job["status"] != "publishing":
                raise HomepageError("This publishing request is no longer active.", 409)
            job.update(status=status, stage=message, lease_until=datetime.now(timezone.utc) + timedelta(minutes=5))
            if status == "published":
                state.update(published=deepcopy(config), published_revision=job["revision"], draft=deepcopy(config))
            return None
        self.mutate(update)


def public_admin_state(state):
    job = state.get("job")
    return {"photos": public_configuration(state["draft"])["photos"], "revision": state["revision"],
            "published_revision": state["published_revision"],
            "has_changes": state["revision"] != state["published_revision"],
            "job": {key: job[key] for key in ("id", "status", "stage")} if job else None,
            "homepage_url": "https://sm-photo-studio.vercel.app/"}

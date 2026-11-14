"""Reconcile watched event folders with photos managed by the camera importer."""

import logging
import os
from pathlib import Path, PurePosixPath

from app.database.models import Event, Face, Photo
from app.services.person_groups import PersonFace, PersonGroup, PhotoSource

LOG = logging.getLogger(__name__)


def normalize_source_path(value):
    if not isinstance(value, str) or not value:
        raise ValueError("Photo source path is required.")
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("Invalid photo source path.")
    if path.parts and ":" in path.parts[0]:
        raise ValueError("Invalid photo source path.")
    return path.as_posix()


def _delete_managed_photo(db, event_id, photo_id):
    photo = db.query(Photo).filter_by(id=photo_id, event_id=event_id).one_or_none()
    if photo is None:
        return False, []

    faces = db.query(Face).filter_by(photo_id=photo_id).all()
    paths = [Path(photo.file_path)]
    for face in faces:
        embedding = Path(face.embedding_path)
        paths.extend((embedding, embedding.with_suffix(".json")))

    db.query(PersonFace).filter_by(photo_id=photo_id).delete(synchronize_session=False)
    db.query(Face).filter_by(photo_id=photo_id).delete(synchronize_session=False)
    db.delete(photo)

    for group in db.query(PersonGroup).filter_by(event_id=event_id).all():
        if not db.query(PersonFace).filter_by(person_id=group.id).first():
            db.delete(group)

    db.flush()
    event = db.query(Event).filter_by(id=event_id).one()
    return bool(faces), [(path, event.event_name, event_id) for path in paths]


def remove_source_files(paths):
    for path, event_name, event_id in paths:
        try:
            resolved = Path(path).resolve()
            event_root = (Path("storage/events") / event_name).resolve()
            embedding_root = (Path("storage/private_embeddings") / f"event_{event_id}").resolve()
            if resolved.is_relative_to(event_root) or resolved.is_relative_to(embedding_root):
                resolved.unlink(missing_ok=True)
        except OSError:
            LOG.warning("Could not remove stale event photo file", exc_info=True)


def _release_source(db, event_id, source):
    photo_id = source.photo_id
    was_managed = source.managed
    db.delete(source)
    db.flush()

    remaining = db.query(PhotoSource).filter_by(photo_id=photo_id).order_by(PhotoSource.id).all()
    if not remaining:
        return _delete_managed_photo(db, event_id, photo_id) if was_managed else (False, [])
    if was_managed and not any(item.managed for item in remaining):
        remaining[0].managed = True
    return False, []


def assign_photo_source(db, event_id, relative_path, photo_id, *, managed):
    relative_path = normalize_source_path(relative_path)
    source = db.query(PhotoSource).filter_by(
        event_id=event_id,
        relative_path=relative_path,
    ).one_or_none()
    if source and source.photo_id == photo_id:
        source.managed = source.managed or managed
        return False, []

    index_changed, cleanup_paths = _release_source(db, event_id, source) if source else (False, [])
    db.add(PhotoSource(
        event_id=event_id,
        relative_path=relative_path,
        photo_id=photo_id,
        managed=managed,
    ))
    db.flush()
    return index_changed, cleanup_paths


def reconcile_photo_sources(db, event_id, relative_paths, *, complete=False):
    present = {normalize_source_path(path) for path in relative_paths}
    index_changed = False
    cleanup_paths = []
    sources = db.query(PhotoSource).filter_by(event_id=event_id).all()
    for source in sources:
        if source.relative_path not in present:
            changed, cleanup = _release_source(db, event_id, source)
            index_changed |= changed
            cleanup_paths.extend(cleanup)
    if complete:
        tracked_ids = db.query(PhotoSource.photo_id).filter_by(event_id=event_id)
        orphans = db.query(Photo).filter(
            Photo.event_id == event_id,
            ~Photo.id.in_(tracked_ids),
        ).all()
        for photo in orphans:
            changed, cleanup = _delete_managed_photo(db, event_id, photo.id)
            index_changed |= changed
            cleanup_paths.extend(cleanup)
    db.commit()

    remove_source_files(cleanup_paths)
    if index_changed:
        from app.services.persistent_face_index import PersistentFaceIndex

        PersistentFaceIndex().build_event_index(event_id)
    return index_changed


def source_paths_in(folder):
    root = Path(folder).resolve(strict=True)
    if not root.is_dir():
        raise NotADirectoryError(root)
    extensions = {".jpg", ".jpeg", ".png", ".webp"}
    paths = []

    def raise_walk_error(error):
        raise error

    for directory, _, filenames in os.walk(root, onerror=raise_walk_error):
        for filename in filenames:
            path = Path(directory) / filename
            if path.is_symlink() or path.suffix.lower() not in extensions:
                continue
            if path.is_file():
                paths.append(path.relative_to(root).as_posix())
    return sorted(paths)


def folder_is_processed(folder, processed, blocked=None):
    root = Path(folder).resolve(strict=True)
    blocked = blocked or {}
    for relative_path in source_paths_in(root):
        path = root / relative_path
        try:
            stat = path.stat()
        except OSError:
            return False
        signature = (stat.st_size, stat.st_mtime_ns)
        if processed.get(path) != signature or blocked.get(path) == signature:
            return False
    return True

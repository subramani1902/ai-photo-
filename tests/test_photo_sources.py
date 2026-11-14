import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.db import Base
from app.database.models import Event, Face, Photo
from app.services.person_groups import PersonFace, PersonGroup, PhotoSource
from app.services.photo_sources import (
    assign_photo_source,
    folder_is_processed,
    normalize_source_path,
    reconcile_photo_sources,
    remove_source_files,
    source_paths_in,
)


class PhotoSourceTests(unittest.TestCase):
    def setUp(self):
        self.original_directory = Path.cwd()
        self.temporary_directory = TemporaryDirectory()
        os.chdir(self.temporary_directory.name)
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()
        os.chdir(self.original_directory)
        self.temporary_directory.cleanup()

    def test_source_paths_are_normalized_and_reject_traversal(self):
        self.assertEqual(normalize_source_path(r"ceremony\bride.jpg"), "ceremony/bride.jpg")
        with self.assertRaises(ValueError):
            normalize_source_path("../outside.jpg")
        with self.assertRaises(ValueError):
            normalize_source_path("C:/outside.jpg")

    def test_source_manifest_lists_nested_images_and_rejects_missing_folder(self):
        root = Path("event")
        (root / "ceremony").mkdir(parents=True)
        (root / "ceremony" / "photo.JPG").write_bytes(b"photo")
        (root / "notes.txt").write_text("not a photo", encoding="utf-8")

        self.assertEqual(source_paths_in(root), ["ceremony/photo.JPG"])
        with self.assertRaises(FileNotFoundError):
            source_paths_in(root / "missing")

    def test_folder_is_not_complete_if_a_photo_disappears_during_scan(self):
        root = Path("event")
        root.mkdir()
        photo = root / "photo.jpg"
        photo.write_bytes(b"photo")
        missing_photo = root.resolve() / photo.name
        original_stat = Path.stat

        def stat(path, *args, **kwargs):
            if path == missing_photo:
                raise FileNotFoundError(path)
            return original_stat(path, *args, **kwargs)

        with patch(
            "app.services.photo_sources.source_paths_in",
            return_value=["photo.jpg"],
        ), patch.object(Path, "stat", new=stat):
            self.assertFalse(folder_is_processed(root, {}))

    def test_replacing_a_source_removes_the_old_managed_photo_and_files(self):
        event_root = Path("storage/events/wedding/originals")
        embedding_root = Path("storage/private_embeddings/event_1")
        event_root.mkdir(parents=True)
        embedding_root.mkdir(parents=True)
        original = event_root / "old.jpg"
        embedding = embedding_root / "photo_1_face_1.npy"
        metadata = embedding.with_suffix(".json")
        original.write_bytes(b"old photo")
        embedding.write_bytes(b"embedding")
        metadata.write_text("{}", encoding="utf-8")

        self.session.add_all([
            Event(id=1, event_name="wedding"),
            Photo(id=1, event_id=1, filename="old.jpg", file_path=str(original)),
            Photo(id=2, event_id=1, filename="new.jpg", file_path=str(event_root / "new.jpg")),
            Face(id=1, photo_id=1, confidence=0.9, embedding_path=str(embedding)),
            PersonGroup(id=1, event_id=1, exemplar="[]"),
            PhotoSource(
                event_id=1,
                relative_path="ceremony/photo.jpg",
                photo_id=1,
                managed=True,
            ),
        ])
        self.session.flush()
        self.session.add(PersonFace(face_id=1, person_id=1, photo_id=1))
        self.session.commit()

        changed, cleanup = assign_photo_source(
            self.session,
            1,
            "ceremony/photo.jpg",
            2,
            managed=True,
        )
        self.session.commit()
        remove_source_files(cleanup)

        self.assertTrue(changed)
        self.assertIsNone(self.session.query(Photo).filter_by(id=1).one_or_none())
        self.assertEqual(
            self.session.query(PhotoSource).one().photo_id,
            2,
        )
        self.assertFalse(original.exists())
        self.assertFalse(embedding.exists())
        self.assertFalse(metadata.exists())
        self.assertEqual(self.session.query(Face).count(), 0)
        self.assertEqual(self.session.query(PersonFace).count(), 0)

    def test_removing_last_managed_source_rebuilds_index(self):
        root = Path("storage/events/wedding/originals")
        root.mkdir(parents=True)
        original = root / "photo.jpg"
        original.write_bytes(b"photo")
        self.session.add_all([
            Event(id=1, event_name="wedding"),
            Photo(id=1, event_id=1, filename="photo.jpg", file_path=str(original)),
            Face(id=1, photo_id=1, confidence=0.9, embedding_path="missing.npy"),
            PhotoSource(
                event_id=1,
                relative_path="photo.jpg",
                photo_id=1,
                managed=True,
            ),
        ])
        self.session.commit()

        with patch("app.services.persistent_face_index.PersistentFaceIndex") as index:
            changed = reconcile_photo_sources(self.session, 1, [])

        self.assertTrue(changed)
        self.assertIsNone(self.session.query(Photo).filter_by(id=1).one_or_none())
        self.assertFalse(original.exists())
        index.return_value.build_event_index.assert_called_once_with(1)

    def test_unmanaged_duplicate_is_not_deleted_when_source_is_removed(self):
        self.session.add_all([
            Event(id=1, event_name="wedding"),
            Photo(id=1, event_id=1, filename="manual.jpg", file_path="missing.jpg"),
            PhotoSource(
                event_id=1,
                relative_path="copy.jpg",
                photo_id=1,
                managed=False,
            ),
        ])
        self.session.commit()

        self.assertFalse(reconcile_photo_sources(self.session, 1, []))
        self.assertIsNotNone(self.session.query(Photo).filter_by(id=1).one_or_none())

    def test_complete_folder_sync_removes_untracked_legacy_photos(self):
        self.session.add_all([
            Event(id=1, event_name="wedding"),
            Photo(id=1, event_id=1, filename="old.jpg", file_path="missing.jpg"),
        ])
        self.session.commit()

        self.assertFalse(reconcile_photo_sources(self.session, 1, [], complete=False))
        self.assertIsNotNone(self.session.query(Photo).filter_by(id=1).one_or_none())
        self.assertFalse(reconcile_photo_sources(self.session, 1, [], complete=True))
        self.assertIsNone(self.session.query(Photo).filter_by(id=1).one_or_none())

    def test_empty_event_index_can_be_rebuilt_and_searched(self):
        from app.services.persistent_face_index import PersistentFaceIndex

        query = self.session.query(Face, Photo)
        with patch("app.services.persistent_face_index.SessionLocal", return_value=self.session):
            built = PersistentFaceIndex()
            self.assertEqual(built.build_event_index(1), 0)

        loaded = PersistentFaceIndex()
        loaded.load_event_index(1)
        self.assertEqual(loaded.index.ntotal, 0)
        self.assertEqual(loaded.search([1.0] * 512), [])


if __name__ == "__main__":
    unittest.main()

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app.database.models import Face, Photo
from app.services.remote_matching import find_event_matches


class RemoteMatchingTests(unittest.TestCase):
    def test_matches_include_the_number_of_detected_faces(self):
        original_directory = Path.cwd()
        with TemporaryDirectory() as directory:
            os.chdir(directory)
            try:
                event_root = Path("storage/events/demo")
                event_root.mkdir(parents=True)
                first_photo_path = event_root / "first.jpg"
                second_photo_path = event_root / "second.jpg"
                first_photo_path.write_bytes(b"first")
                second_photo_path.write_bytes(b"second")

                event = SimpleNamespace(event_name="demo")
                first_photo = SimpleNamespace(
                    id=1, filename="first.jpg", file_path=str(first_photo_path)
                )
                second_photo = SimpleNamespace(
                    id=2, filename="second.jpg", file_path=str(second_photo_path)
                )
                event_query = Mock()
                event_query.filter.return_value.first.return_value = event
                photo_query = Mock()
                photo_query.join.return_value.filter.return_value.all.return_value = [
                    (SimpleNamespace(id=11), first_photo),
                    (SimpleNamespace(id=12), first_photo),
                    (SimpleNamespace(id=13), second_photo),
                ]
                face_count_query = Mock()
                face_count_query.filter.return_value.group_by.return_value.all.return_value = [
                    (1, 3),
                    (2, 1),
                ]
                db = Mock()
                db.query.side_effect = [event_query, photo_query, face_count_query]

                index = Mock()
                index.index.ntotal = 3
                index.search.return_value = [
                    {"face_id": 11, "similarity": 0.86},
                    {"face_id": 12, "similarity": 0.80},
                    {"face_id": 13, "similarity": 0.62},
                ]
                engine = Mock()
                engine.process_image.return_value = {
                    "total_faces": 1,
                    "faces": [{"embedding": [0.1] * 512}],
                }

                selfie_path = Path("selfie.jpg")
                selfie_path.write_bytes(b"selfie")
                with (
                    patch("app.services.remote_matching.PersistentFaceIndex", return_value=index),
                    patch("app.services.remote_matching.SessionLocal", return_value=db),
                ):
                    matches = find_event_matches(3, selfie_path, engine=engine)

                self.assertEqual(len(matches), 2)
                self.assertEqual(
                    [(match["photo_id"], match["similarity"]) for match in matches],
                    [(1, 0.86), (2, 0.62)],
                )
                self.assertEqual([match["face_count"] for match in matches], [3, 1])
                self.assertEqual(db.query.call_count, 3)
                photo_query.join.return_value.filter.return_value.all.assert_called_once()
            finally:
                os.chdir(original_directory)


if __name__ == "__main__":
    unittest.main()

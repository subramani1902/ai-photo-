import tempfile
import unittest
from pathlib import Path

import numpy as np
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.database.db import Base
from app.database.models import Event, Face, Photo
from app.services.person_groups import PersonGroup, PersonFace, sync_person_groups


class GroupingTests(unittest.TestCase):
    def test_recurring_faces_group_photos_and_event_isolation(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = create_engine("sqlite://")
            Base.metadata.create_all(engine)
            with Session(engine) as db:
                a = np.zeros(512); a[0] = 1
                b = np.zeros(512); b[1] = 1
                db.add_all([Event(id=1, event_name="first"), Event(id=2, event_name="second")])
                db.flush()
                face_number = 0
                for event, vectors in [(1, [a]), (1, [b]), (1, [a, b]), (2, [a])]:
                    photo = Photo(event_id=event, filename="test.jpg", file_path="test.jpg")
                    db.add(photo); db.flush()
                    for vector in vectors:
                        face_number += 1
                        path = Path(folder) / f"{face_number}.npy"
                        np.save(path, vector)
                        db.add(Face(photo_id=photo.id, confidence=1, embedding_path=str(path)))
                db.flush()
                first = sync_person_groups(db, 1)
                self.assertEqual(first["faces_grouped"], 4)
                self.assertEqual(first["total_people"], 2)
                self.assertEqual(sync_person_groups(db, 1)["faces_grouped"], 0)
                self.assertEqual(sync_person_groups(db, 2)["total_people"], 1)
                links = db.query(PersonFace).all()
                counts = {}
                for link in links:
                    counts[link.person_id] = counts.get(link.person_id, 0) + 1
                self.assertEqual(sorted(counts.values()), [1, 2, 2])
                self.assertEqual(db.query(PersonGroup).count(), 3)


if __name__ == "__main__":
    unittest.main()

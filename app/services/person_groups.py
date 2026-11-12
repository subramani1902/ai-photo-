"""Persistent, event-local face groups. These are estimates, not identities."""
import json
from pathlib import Path

import numpy as np
from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, UniqueConstraint

from app.database.db import Base, engine
from app.database.models import Face, Photo


class PersonGroup(Base):
    __tablename__ = "person_groups"
    id = Column(Integer, primary_key=True)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=False, index=True)
    # A fixed exemplar avoids gradual drift from repeated automatic updates.
    exemplar = Column(String, nullable=False)


class PersonFace(Base):
    __tablename__ = "person_faces"
    face_id = Column(Integer, ForeignKey("faces.id"), primary_key=True)
    person_id = Column(Integer, ForeignKey("person_groups.id"), nullable=False, index=True)
    photo_id = Column(Integer, ForeignKey("photos.id"), nullable=False, index=True)
    __table_args__ = (UniqueConstraint("person_id", "photo_id"),)


class PhotoSource(Base):
    __tablename__ = "photo_sources"
    id = Column(Integer, primary_key=True)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=False, index=True)
    relative_path = Column(String, nullable=False)
    photo_id = Column(Integer, ForeignKey("photos.id"), nullable=False, index=True)
    managed = Column(Boolean, nullable=False, default=False)
    __table_args__ = (UniqueConstraint("event_id", "relative_path"),)


def init_person_groups():
    Base.metadata.create_all(
        engine,
        tables=[PersonGroup.__table__, PersonFace.__table__, PhotoSource.__table__],
    )


def normalized(value):
    vector = np.asarray(value, dtype=np.float32)
    if vector.shape != (512,) or not np.isfinite(vector).all():
        raise ValueError("Invalid face vector")
    norm = np.linalg.norm(vector)
    if norm <= 0:
        raise ValueError("Empty face vector")
    return vector / norm


def sync_person_groups(db, event_id, threshold=0.65, margin=0.05):
    """Assign only ungrouped faces; caller commits the transaction.

    A face must match one exemplar clearly. Ambiguous faces form separate
    groups, and two faces in one photograph cannot join the same group.
    """
    db.flush()
    groups = db.query(PersonGroup).filter_by(event_id=event_id).order_by(PersonGroup.id).all()
    exemplars = {group.id: normalized(json.loads(group.exemplar)) for group in groups}
    links = (db.query(PersonFace).join(Photo, Photo.id == PersonFace.photo_id)
             .filter(Photo.event_id == event_id).all())
    occupied = {(link.person_id, link.photo_id) for link in links}
    faces = (db.query(Face).join(Photo).outerjoin(PersonFace, Face.id == PersonFace.face_id)
             .filter(Photo.event_id == event_id, PersonFace.face_id.is_(None))
             .order_by(Face.photo_id, Face.id).all())
    assigned = skipped = created = 0
    for face in faces:
        try:
            vector = normalized(np.load(Path(face.embedding_path), allow_pickle=False))
        except (OSError, ValueError):
            skipped += 1
            continue
        scores = sorted(((float(vector @ exemplar), person_id)
                         for person_id, exemplar in exemplars.items()), reverse=True)
        person_id = None
        if scores and scores[0][0] >= threshold:
            score, candidate = scores[0]
            clear = len(scores) == 1 or score - scores[1][0] >= margin
            if clear and (candidate, face.photo_id) not in occupied:
                person_id = candidate
        if person_id is None:
            group = PersonGroup(event_id=event_id, exemplar=json.dumps(vector.tolist()))
            db.add(group)
            db.flush()
            person_id = group.id
            exemplars[person_id] = vector
            created += 1
        db.add(PersonFace(face_id=face.id, person_id=person_id, photo_id=face.photo_id))
        occupied.add((person_id, face.photo_id))
        assigned += 1
    db.flush()
    return {"faces_grouped": assigned, "new_people": created, "skipped_faces": skipped,
            "total_people": len(exemplars)}

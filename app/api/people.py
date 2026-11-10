"""Admin-only inspection of automatic event person groups."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.database.models import Photo, User
from app.dependencies.auth import get_current_admin
from app.api.photos import get_event_or_404
from app.services.person_groups import PersonGroup, PersonFace, sync_person_groups

router = APIRouter(prefix="/api/events", tags=["People"])


@router.post("/{event_id}/people/sync")
def sync_people(event_id: int, db: Session = Depends(get_db),
                admin: User = Depends(get_current_admin)):
    get_event_or_404(db, event_id, admin.id)
    try:
        result = sync_person_groups(db, event_id)
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise


@router.get("/{event_id}/people")
def list_people(event_id: int, db: Session = Depends(get_db),
                admin: User = Depends(get_current_admin)):
    get_event_or_404(db, event_id, admin.id)
    rows = (db.query(PersonGroup.id, func.count(PersonFace.face_id))
            .join(PersonFace, PersonFace.person_id == PersonGroup.id)
            .join(Photo, Photo.id == PersonFace.photo_id)
            .filter(PersonGroup.event_id == event_id, Photo.event_id == event_id)
            .group_by(PersonGroup.id).order_by(PersonGroup.id).all())
    return {"people": [{"person_id": pid, "label": f"Person {pid}", "photo_count": count}
                       for pid, count in rows]}


@router.get("/{event_id}/people/{person_id}/photos")
def person_photos(event_id: int, person_id: int, db: Session = Depends(get_db),
                  admin: User = Depends(get_current_admin)):
    get_event_or_404(db, event_id, admin.id)
    person = db.query(PersonGroup).filter_by(id=person_id, event_id=event_id).first()
    if person is None:
        raise HTTPException(404, "Person group not found.")
    photos = (db.query(Photo).join(PersonFace, Photo.id == PersonFace.photo_id)
              .filter(PersonFace.person_id == person_id, Photo.event_id == event_id)
              .order_by(Photo.id).all())
    return {"person_id": person_id, "photos": [
        {"photo_id": photo.id, "filename": photo.filename,
         "image_url": f"/api/events/{event_id}/photos/{photo.id}"} for photo in photos]}

"""Explicit local-operator linking; dry-run by default, never a public API."""

import argparse
from pathlib import Path

from sqlalchemy import inspect
from sqlalchemy.exc import SQLAlchemyError

from app.core.firebase import get_firebase_app, get_firebase_auth
from app.core.firebase_config import FirebaseConfigurationError, get_firebase_settings
from app.database.cloud_models import FirebaseAdminLink
from app.database.db import SessionLocal
from app.database.models import User


def link_admin(db, project_id, firebase_user, user_id, *, apply=False):
    uid = firebase_user.uid
    user = db.query(User).filter(User.id == user_id, User.is_active.is_(True)).one_or_none()
    if user is None:
        raise ValueError("Choose an existing active local administrator.")
    if (not isinstance(uid, str) or not 1 <= len(uid) <= 128 or uid.startswith("gallery-client:")
            or firebase_user.disabled or firebase_user.email_verified is not True
            or not isinstance(firebase_user.email, str)
            or firebase_user.email.strip().lower() != user.email.strip().lower()):
        raise ValueError("Use an enabled Firebase user with the same verified email as the local administrator.")
    exists = inspect(db.get_bind()).has_table(FirebaseAdminLink.__tablename__)
    link = None
    if exists:
        link = db.get(FirebaseAdminLink, (project_id, uid))
        owner_link = db.query(FirebaseAdminLink).filter_by(project_id=project_id, user_id=user_id).one_or_none()
        if ((link and link.user_id != user_id)
                or (owner_link and owner_link.firebase_uid != uid)):
            raise ValueError("A different identity is already linked; revoke and review the mapping manually.")
    if apply:
        if not exists:
            # Only the additive identity table; never create/rebuild core AI tables.
            FirebaseAdminLink.__table__.create(db.get_bind(), checkfirst=True)
        if link is None:
            db.add(FirebaseAdminLink(project_id=project_id, firebase_uid=uid, user_id=user_id, is_active=True))
        else:
            link.is_active = True
        db.commit()
    return {"project_id": project_id, "firebase_uid": uid, "local_user_id": user_id, "applied": apply}


def deactivate_admin(db, project_id, uid, user_id, *, apply=False):
    if not inspect(db.get_bind()).has_table(FirebaseAdminLink.__tablename__):
        raise ValueError("No Firebase administrator mapping exists.")
    link = db.get(FirebaseAdminLink, (project_id, uid))
    if link is None or link.user_id != user_id:
        raise ValueError("No matching Firebase administrator mapping exists.")
    if apply:
        link.is_active = False
        db.commit()
    return {"project_id": project_id, "firebase_uid": uid, "local_user_id": user_id, "applied": apply}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--firebase-uid", required=True)
    parser.add_argument("--local-user-id", type=int, required=True)
    parser.add_argument("--apply", action="store_true", help="Apply the reviewed local mapping")
    parser.add_argument("--deactivate", action="store_true", help="Revoke only this local binding; no cloud write")
    args = parser.parse_args()
    if not Path("event_photos.db").is_file():
        parser.error("Run from the existing repository root; the local database must already exist.")
    try:
        settings = get_firebase_settings()
        settings.require_enabled()
        with SessionLocal() as db:
            if args.deactivate:
                result = deactivate_admin(db, settings.project_id, args.firebase_uid, args.local_user_id, apply=args.apply)
            else:
                auth = get_firebase_auth()
                try:
                    firebase_user = auth.get_user(args.firebase_uid, app=get_firebase_app())
                except Exception:
                    raise ValueError("Unable to verify the Firebase user. Check the UID and server credentials.") from None
                result = link_admin(db, settings.project_id, firebase_user, args.local_user_id, apply=args.apply)
        action = "Deactivation" if args.deactivate else "Link"
        print(f"{action} {'applied' if result['applied'] else 'dry run; no changes made'}.")
        print(f"Project: {result['project_id']}; local administrator ID: {result['local_user_id']}")
        return 0
    except (FirebaseConfigurationError, ValueError) as error:
        parser.error(str(error))
    except SQLAlchemyError:
        parser.error("Unable to access the local administrator mapping. No cloud changes were made.")


if __name__ == "__main__":
    raise SystemExit(main())

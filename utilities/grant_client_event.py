"""Grant one Firebase account access to one local event; dry-run by default."""

import argparse
from datetime import datetime, timedelta, timezone

from app.core.firebase import get_firebase_app, get_firebase_auth
from app.core.firebase_config import FirebaseConfigurationError, get_firebase_settings
from app.database.db import SessionLocal
from app.database.models import Event, User
from app.services.client_event_access import ClientEventStore, EventAccessUnavailable


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-id", type=int, required=True)
    parser.add_argument("--firebase-uid", required=True)
    parser.add_argument("--owner-user-id", type=int, required=True)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--no-downloads", action="store_true")
    parser.add_argument("--no-favourites", action="store_true")
    parser.add_argument("--revoke", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.days <= 366:
        parser.error("Choose an expiry from 1 to 366 days.")
    try:
        settings = get_firebase_settings()
        settings.require_enabled()
        with SessionLocal() as db:
            event = (db.query(Event).join(User, User.id == Event.owner_id)
                     .filter(Event.id == args.event_id, Event.owner_id == args.owner_user_id,
                             User.is_active.is_(True)).one_or_none())
            if event is None:
                parser.error("Choose an existing event belonging to the specified active local owner.")
        if args.firebase_uid.startswith("gallery-client:"):
            parser.error("Choose the client's registered Email/password or Google account ID.")
        try:
            user = get_firebase_auth().get_user(args.firebase_uid, app=get_firebase_app())
        except Exception:
            parser.error("Unable to verify the Firebase account ID.")
        if user.disabled:
            parser.error("The Firebase account is disabled.")
        permissions = ["view"]
        if not args.no_downloads:
            permissions.append("download")
        if not args.no_favourites:
            permissions.append("favourite")
        expiry = datetime.now(timezone.utc) + timedelta(days=args.days)
        print(f"Event: {args.event_id}; local owner: {args.owner_user_id}")
        print(f"Operation: {'revoke' if args.revoke else 'grant'}; permissions: {', '.join(permissions)}")
        print(f"Expires: {expiry.isoformat()}")
        if not args.apply:
            print("Dry run: no permissions were changed. Use --apply after reviewing the selected account/event.")
            return
        store = ClientEventStore()
        if args.revoke:
            store.revoke(args.firebase_uid, args.event_id)
        else:
            store.grant(args.firebase_uid, args.event_id, args.owner_user_id, expiry, permissions)
        print("Event permission updated. Other accounts and events are unchanged.")
    except (FirebaseConfigurationError, EventAccessUnavailable, ValueError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()

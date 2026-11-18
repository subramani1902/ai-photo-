"""Operator-only homepage permission; does not grant legacy/event administrator access."""

import argparse
from datetime import datetime, timezone

from app.core.firebase import get_firebase_app, get_firebase_auth, get_firestore_client
from app.core.firebase_config import get_firebase_settings
from app.services.homepage_content import admin_document_id


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--revoke", action="store_true")
    args = parser.parse_args()
    email = args.email.strip().lower()
    user = get_firebase_auth().get_user_by_email(email, app=get_firebase_app())
    if user.uid.startswith("gallery-client:") or (not args.revoke and (user.disabled or not user.email_verified)):
        parser.error("Use an existing enabled Firebase account with a verified email address.")
    reference = get_firestore_client().document("website_homepage_admins/" + admin_document_id(user.uid))
    previous = reference.get(timeout=10).to_dict()
    if previous and (previous.get("firebase_uid") != user.uid or previous.get("email") != email):
        parser.error("A conflicting homepage administrator record needs manual review.")
    if args.apply:
        reference.set({"firebase_uid": user.uid, "email": email, "enabled": not args.revoke,
                       "permission": "manage_homepage", "updated_at": datetime.now(timezone.utc)})
    print(("Revocation" if args.revoke else "Homepage permission") + (" applied." if args.apply else " dry run; no changes made."))
    print("Project:", get_firebase_settings().project_id, "Account:", email)


if __name__ == "__main__":
    main()

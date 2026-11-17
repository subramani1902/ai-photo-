"""Private event permissions in Firestore. No photos or face vectors leave the PC."""

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone

from app.core.firebase import get_firestore_client

PERMISSIONS = frozenset({"view", "download", "favourite"})


class EventAccessDenied(ValueError):
    pass


class EventAccessUnavailable(RuntimeError):
    pass


def member_document_id(uid):
    if not isinstance(uid, str) or not 1 <= len(uid) <= 128:
        raise ValueError("Invalid Firebase account ID.")
    return hashlib.sha256(uid.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EventPermission:
    uid: str
    event_id: int
    owner_id: int
    permissions: frozenset
    expires_at: datetime


def verify_event_permission(data, uid, event_id, owner_id, *, now=None):
    now = now or datetime.now(timezone.utc)
    if not isinstance(data, dict):
        raise EventAccessDenied("Event access denied.")
    expiry = data.get("expires_at")
    permissions = data.get("permissions")
    if (data.get("active") is not True or data.get("firebase_uid") != uid
            or type(data.get("event_id")) is not int or data["event_id"] != event_id
            or type(data.get("owner_id")) is not int or data["owner_id"] != owner_id
            or not isinstance(expiry, datetime) or expiry.tzinfo is None or expiry <= now
            or not isinstance(permissions, list) or not permissions
            or not all(isinstance(value, str) and value in PERMISSIONS for value in permissions)
            or "view" not in permissions):
        raise EventAccessDenied("Event access denied.")
    return EventPermission(uid, event_id, owner_id, frozenset(permissions), expiry)


class ClientEventStore:
    def __init__(self, client=None):
        self._client = client

    @property
    def client(self):
        return self._client if self._client is not None else get_firestore_client()

    def member(self, event_id, uid):
        return (self.client.collection("client_event_access").document(str(event_id))
                .collection("members").document(member_document_id(uid)))

    def authorize(self, uid, event_id, owner_id):
        try:
            snapshot = self.member(event_id, uid).get(timeout=10)
            return verify_event_permission(snapshot.to_dict() if snapshot.exists else None,
                                           uid, event_id, owner_id)
        except EventAccessDenied:
            raise
        except Exception:
            raise EventAccessUnavailable("Event access is temporarily unavailable.") from None

    def grant(self, uid, event_id, owner_id, expires_at, permissions):
        from google.cloud import firestore

        data = {"firebase_uid": uid, "event_id": event_id, "owner_id": owner_id,
                "expires_at": expires_at, "permissions": sorted(set(permissions)), "active": True}
        verify_event_permission(data, uid, event_id, owner_id)
        try:
            self.member(event_id, uid).set({**data, "updated_at": firestore.SERVER_TIMESTAMP}, timeout=10)
        except Exception:
            raise EventAccessUnavailable("Unable to save event access.") from None

    def revoke(self, uid, event_id):
        try:
            self.member(event_id, uid).set({"active": False}, merge=True, timeout=10)
        except Exception:
            raise EventAccessUnavailable("Unable to revoke event access.") from None

    def favourites(self, uid, event_id):
        try:
            documents = self.member(event_id, uid).collection("favourites").limit(10000).stream(timeout=10)
            return {int(snapshot.id) for snapshot in documents if snapshot.id.isdecimal() and int(snapshot.id) > 0}
        except Exception:
            raise EventAccessUnavailable("Unable to load favourites.") from None

    def set_favourite(self, uid, event_id, owner_id, photo_id, selected):
        from google.cloud import firestore

        try:
            member = self.member(event_id, uid)
            favourite = member.collection("favourites").document(str(photo_id))

            @firestore.transactional
            def apply(transaction):
                snapshot = member.get(transaction=transaction)
                permission = verify_event_permission(snapshot.to_dict() if snapshot.exists else None,
                                                     uid, event_id, owner_id)
                if "favourite" not in permission.permissions:
                    raise EventAccessDenied("Event access denied.")
                if selected:
                    transaction.set(favourite, {"photo_id": photo_id, "selected_at": firestore.SERVER_TIMESTAMP})
                else:
                    transaction.delete(favourite)

            apply(self.client.transaction(max_attempts=3))
        except EventAccessDenied:
            raise
        except Exception:
            raise EventAccessUnavailable("Unable to save favourite.") from None


def get_client_event_store():
    return ClientEventStore()

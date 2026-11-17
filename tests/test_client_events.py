import io
import tempfile
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import client_events
from app.core.firebase_tokens import FirebasePrincipal
from app.database.db import Base, get_db
from app.database.models import Event, Face, Photo, User
from app.dependencies.firebase_auth import get_current_firebase_principal
from app.services.client_event_access import (
    EventAccessDenied, EventAccessUnavailable, get_client_event_store, member_document_id, verify_event_permission,
)
from app.services.person_groups import EventSourceFolder, PhotoSource


def permission_data(uid="alice", event_id=1, owner_id=1):
    return {"firebase_uid": uid, "event_id": event_id, "owner_id": owner_id, "active": True,
            "permissions": ["view", "download", "favourite"],
            "expires_at": datetime.now(timezone.utc) + timedelta(days=1)}


class MemoryPermissions:
    def __init__(self):
        self.data = {(1, "alice"): permission_data()}
        self.selected = {}

    def authorize(self, uid, event_id, owner_id):
        return verify_event_permission(self.data.get((event_id, uid)), uid, event_id, owner_id)

    def favourites(self, uid, event_id):
        return self.selected.get((event_id, uid), set()).copy()

    def set_favourite(self, uid, event_id, owner_id, photo_id, selected):
        access = self.authorize(uid, event_id, owner_id)
        if "favourite" not in access.permissions:
            raise EventAccessDenied()
        favourites = self.selected.setdefault((event_id, uid), set())
        favourites.add(photo_id) if selected else favourites.discard(photo_id)


class PermissionTests(unittest.TestCase):
    def test_membership_is_scoped_to_account_event_owner_and_live_expiry(self):
        self.assertEqual(verify_event_permission(permission_data(), "alice", 1, 1).event_id, 1)
        for change in [{"firebase_uid": "bob"}, {"event_id": 2}, {"owner_id": 2}, {"owner_id": True},
                       {"active": False}, {"permissions": ["view", "admin"]}, {"permissions": "view"},
                       {"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)},
                       {"expires_at": datetime.now()}, {"permissions": ["download"]}]:
            with self.subTest(change=change), self.assertRaises(EventAccessDenied):
                verify_event_permission({**permission_data(), **change}, "alice", 1, 1)

    def test_firebase_account_ids_cannot_become_firestore_paths(self):
        self.assertNotIn("/", member_document_id("user/../other"))
        self.assertNotEqual(member_document_id("alice"), member_document_id("bob"))
        self.assertEqual(len(member_document_id("alice")), 64)


class ClientGalleryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.db.add_all([User(id=1, email="owner@example.test", password_hash="fixture", is_active=True),
                         User(id=2, email="other@example.test", password_hash="fixture", is_active=True),
                         Event(id=1, event_name="Private wedding", owner_id=1),
                         Event(id=2, event_name="Other wedding", owner_id=2)])
        for event_id, photo_ids in [(1, [101, 102, 103]), (2, [201])]:
            folder = self.root / ("external-" + str(event_id))
            folder.mkdir()
            self.db.add(EventSourceFolder(event_id=event_id, folder_path=str(folder)))
            for photo_id in photo_ids:
                source = folder / (str(photo_id) + ".jpg")
                Image.new("RGB", (80, 60), (110, photo_id % 255, 90)).save(source)
                self.db.add(Photo(id=photo_id, filename=source.name, file_path="unavailable-local-copy",
                                  event_id=event_id))
                self.db.add(PhotoSource(event_id=event_id, photo_id=photo_id,
                                        relative_path=source.name, managed=True))
        self.db.add_all([Face(photo_id=101, confidence=0.9), Face(photo_id=102, confidence=0.9),
                         Face(photo_id=102, confidence=0.8)])
        self.db.commit()
        self.store = MemoryPermissions()
        self.principal = FirebasePrincipal("alice", "demo-gallery", {"email_verified": True})
        self.app = FastAPI()
        self.app.include_router(client_events.router)
        self.app.dependency_overrides[get_db] = lambda: self.db
        self.app.dependency_overrides[get_current_firebase_principal] = lambda: self.principal
        self.app.dependency_overrides[get_client_event_store] = lambda: self.store
        self.cache = patch.object(client_events, "PREVIEW_ROOT", self.root / "previews")
        self.cache.start()
        self.rate = patch.object(client_events, "limits", client_events.RequestLimits())
        self.rate.start()
        self.http = TestClient(self.app)
        self.url = "/api/firebase/events/1"

    def tearDown(self):
        self.http.close()
        self.rate.stop()
        self.cache.stop()
        self.db.close()
        self.engine.dispose()
        self.temp.cleanup()

    def test_account_id_and_event_id_do_not_grant_access_without_membership(self):
        self.assertEqual(self.http.get(self.url).status_code, 200)
        self.principal = FirebasePrincipal("bob", "demo-gallery", {"email_verified": True})
        for suffix in ["", "/photos", "/photos/101/preview", "/photos/101/download"]:
            self.assertEqual(self.http.get(self.url + suffix).status_code, 403)
        self.assertEqual(self.http.post(self.url + "/download-zip", json={"photo_ids": [101]}).status_code, 403)

    def test_missing_identity_unverified_identity_and_disabled_owner_are_denied(self):
        self.principal = FirebasePrincipal("alice", "demo-gallery", {"email_verified": False})
        self.assertEqual(self.http.get(self.url).status_code, 403)
        self.app.dependency_overrides.pop(get_current_firebase_principal)
        self.assertEqual(self.http.get(self.url).status_code, 401)
        self.app.dependency_overrides[get_current_firebase_principal] = lambda: FirebasePrincipal("alice", "demo-gallery", {"email_verified": True})
        self.db.get(User, 1).is_active = False
        self.db.commit()
        self.assertEqual(self.http.get(self.url).status_code, 403)

    def test_other_event_photo_ids_are_never_served(self):
        self.assertEqual(self.http.get("/api/firebase/events/2").status_code, 403)
        for suffix in ["/photos/201/preview", "/photos/201/download"]:
            self.assertEqual(self.http.get(self.url + suffix).status_code, 404)
        self.assertEqual(self.http.post(self.url + "/download-zip", json={"photo_ids": [101, 201]}).status_code, 404)

    def test_revocation_and_expiry_apply_to_each_new_request(self):
        self.assertEqual(self.http.get(self.url + "/photos").status_code, 200)
        self.store.data[(1, "alice")]["active"] = False
        self.assertEqual(self.http.get(self.url + "/photos/101/download").status_code, 403)
        self.store.data[(1, "alice")] = {**permission_data(), "expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}
        self.assertEqual(self.http.get(self.url + "/photos/101/preview").status_code, 403)

    def test_candid_group_filters_and_pagination_use_existing_face_counts(self):
        candid = self.http.get(self.url + "/photos?category=candid").json()["photos"]
        group = self.http.get(self.url + "/photos?category=group").json()["photos"]
        self.assertEqual([p["id"] for p in candid], [101])
        self.assertEqual([p["id"] for p in group], [102])
        first = self.http.get(self.url + "/photos?limit=2").json()
        self.assertEqual(first["next_after"], 102)
        last = self.http.get(self.url + "/photos?limit=2&after=102").json()
        self.assertEqual([p["id"] for p in last["photos"]], [103])
        self.assertIsNone(last["next_after"])

    def test_previews_and_downloads_preserve_hdd_originals_and_hide_paths(self):
        source = self.root / "external-1" / "101.jpg"
        original = source.read_bytes()
        response = self.http.get(self.url + "/photos/101/preview")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/webp")
        self.assertEqual(response.headers["cache-control"], "private, no-store")
        self.assertEqual(self.http.get(self.url + "/photos/101/download").content, original)
        self.assertEqual(source.read_bytes(), original)
        metadata = self.http.get(self.url + "/photos").text
        self.assertNotIn(str(self.root), metadata)
        self.assertNotIn("embedding", metadata)

    def test_permissions_separate_view_download_and_favourite(self):
        self.store.data[(1, "alice")]["permissions"] = ["view"]
        self.assertEqual(self.http.get(self.url + "/photos/101/preview").status_code, 200)
        self.assertEqual(self.http.get(self.url + "/photos/101/download").status_code, 403)
        self.assertEqual(self.http.put(self.url + "/photos/101/favourite", json={"selected": True}).status_code, 403)

    def test_favourites_are_scoped_and_do_not_accept_identity_in_body(self):
        self.assertEqual(self.http.put(self.url + "/photos/101/favourite", json={"selected": True, "uid": "bob"}).status_code, 422)
        self.assertEqual(self.http.put(self.url + "/photos/101/favourite", json={"selected": True}).status_code, 200)
        photos = self.http.get(self.url + "/photos?category=favourites").json()["photos"]
        self.assertEqual([p["id"] for p in photos], [101])
        self.assertEqual(self.store.favourites("bob", 1), set())

    def test_zip_contains_only_selected_originals_with_flat_safe_names(self):
        self.db.get(Photo, 101).filename = "../private.jpg"
        self.db.commit()
        response = self.http.post(self.url + "/download-zip", json={"photo_ids": [101, 102]})
        self.assertEqual(response.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertEqual(len(archive.namelist()), 2)
            self.assertTrue(all("/" not in name and "\\" not in name for name in archive.namelist()))
            self.assertEqual(archive.read("102.jpg"), (self.root / "external-1/102.jpg").read_bytes())
        self.assertEqual(self.http.post(self.url + "/download-zip", json={"photo_ids": [True]}).status_code, 422)
        with patch.object(client_events, "MAX_ZIP_BYTES", 1):
            self.assertEqual(self.http.post(self.url + "/download-zip", json={"photo_ids": [101]}).status_code, 413)

    def test_cloud_outage_returns_unavailable_without_private_details(self):
        with patch.object(self.store, "authorize", side_effect=EventAccessUnavailable("PRIVATE CREDENTIAL PATH")):
            response = self.http.get(self.url)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("PRIVATE", response.text)


if __name__ == "__main__":
    unittest.main()

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.api import studio
from app.core.firebase_tokens import FirebasePrincipal
from app.dependencies.firebase_auth import get_current_firebase_principal
from app.photo_gateway import app
from app.services.homepage_content import admin_document_id
from app.services.studio_management import (
    ClientUpdate, PackageUpdate, StudioError, StudioStore, initial_packages, search_term,
)


class StudioManagementTests(unittest.TestCase):
    def setUp(self):
        self.firestore = Mock()
        self.store = StudioStore(self.firestore)
        self.actor = FirebasePrincipal("approved-staff", "demo-studio",
                                       {"email": "staff@example.test", "email_verified": True})
        self.roles = {}
        self.firestore.document = lambda path: SimpleNamespace(
            get=lambda **kwargs: SimpleNamespace(to_dict=lambda: self.roles.get(path)))
        self.client = TestClient(app)
        self.patch = patch.object(studio, "store_for_request", return_value=self.store)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.addCleanup(self.client.close)
        self.addCleanup(app.dependency_overrides.clear)
        patcher = patch.object(studio, "_packages_expires", 0)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(studio, "_public_packages", None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def sign_in(self, *, photos=False, studio_access=False):
        app.dependency_overrides[get_current_firebase_principal] = lambda: self.actor
        for collection, permission, enabled in (
            ("website_homepage_admins", "manage_homepage", photos),
            ("website_studio_admins", "manage_studio", studio_access),
        ):
            self.roles[f"{collection}/{admin_document_id(self.actor.uid)}"] = {
                "enabled": enabled, "permission": permission, "email": "staff@example.test", "firebase_uid": self.actor.uid,
            }

    def test_anonymous_and_photo_only_accounts_cannot_read_client_records(self):
        endpoint = "/api/studio/admin/clients/search"
        self.assertEqual(self.client.post(endpoint, json={}).status_code, 401)
        self.sign_in(photos=True)
        self.assertEqual(self.client.get("/api/studio/access").json(), {"photos": True, "studio": False})
        denied = self.client.post(endpoint, json={"search": "staff@example.test"})
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.headers["cache-control"], "private, no-store")
        self.store.clients.get.assert_not_called()

    def test_studio_role_cannot_be_used_by_gallery_token_or_mismatched_email(self):
        self.sign_in(studio_access=True)
        for extra in ({"actor_type": "gallery_client"}, {"email_verified": False}, {"email": "other@example.test"}):
            actor = FirebasePrincipal(self.actor.uid, self.actor.project_id, {**self.actor.claims, **extra})
            app.dependency_overrides[get_current_firebase_principal] = lambda: actor
            self.assertEqual(self.client.get("/api/studio/access").status_code, 403)

    def test_public_endpoint_exposes_only_brochure_details_and_actual_prices(self):
        config = initial_packages().model_dump()
        with patch.object(self.store, "read_packages", return_value={**config, "revision": 3, "updated_by": "staff"}):
            result = self.client.get("/api/studio/packages")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.headers["cache-control"], "public, max-age=30")
        self.assertEqual(result.json(), config)
        self.assertEqual([config[key]["starting_paise"] for key in ("silver", "gold", "platinum")],
                         [4_500_000, 7_500_000, 10_000_000])
        self.assertIn("Drone video", config["platinum"]["inclusions"])
        self.assertNotIn("Drone video", config["gold"]["inclusions"])
        self.assertTrue(all(value["deposit_paise"] is None for value in config.values()))

    def test_staff_can_save_and_search_clients_with_private_responses(self):
        self.sign_in(studio_access=True)
        record_id = "1" * 32
        data = {"name": "Test client", "revision": 0, "instagram": "@test.client", "source": "instagram",
                "package": "Gold", "quote_paise": 7_500_000, "status": "booked",
                "payments": [{"date": "2026-11-24", "amount_paise": 2_000_000, "method": "upi", "reference": "test-ref"}]}
        with patch.object(self.store, "save_client", return_value={"id": record_id, "revision": 1}) as save, \
                patch.object(self.store, "list_clients", return_value={"clients": [], "next": None}) as list_clients:
            result = self.client.put(f"/api/studio/admin/clients/{record_id}", json=data)
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.headers["cache-control"], "private, no-store")
            self.assertEqual(save.call_args.args, (record_id, ClientUpdate.model_validate(data), self.actor.uid))
            self.assertEqual(save.call_args.args[1].instagram, "test.client")
            self.assertEqual(self.client.post("/api/studio/admin/clients/search", json={"search": "@TEST"}).status_code, 200)
            list_clients.assert_called_once_with("test", None)
            self.assertEqual(search_term("+91 (90000) 00000"), "919000000000")
            invalid = self.client.put(f"/api/studio/admin/clients/{record_id}", json={**data, "quote_paise": 2.5})
            self.assertEqual(invalid.status_code, 422)
            self.assertNotIn("test-ref", invalid.text)

    def test_staff_package_edits_validate_and_refresh_public_prices(self):
        self.sign_in(studio_access=True)
        config = initial_packages().model_dump()
        ok = {**config, "revision": 0, "silver": {**config["silver"], "deposit_paise": 500_000}}
        with patch.object(self.store, "save_packages", return_value={**ok, "revision": 1}) as save, \
                patch.object(self.store, "read_packages", side_effect=[config, ok]):
            self.assertIsNone(self.client.get("/api/studio/packages").json()["silver"]["deposit_paise"])
            result = self.client.put("/api/studio/admin/packages", json=ok)
            self.assertEqual(result.status_code, 200)
            save.assert_called_once_with(PackageUpdate.model_validate(ok), self.actor.uid)
            self.assertEqual(self.client.get("/api/studio/packages").json()["silver"]["deposit_paise"], 500_000)
            invalid = {**ok, "silver": {**ok["silver"], "deposit_paise": 9_000_000}}
            self.assertEqual(self.client.put("/api/studio/admin/packages", json=invalid).status_code, 422)

    def test_transactions_recover_retries_and_reject_stale_writes(self):
        reference, transaction = Mock(), Mock()
        self.firestore.transaction.return_value = transaction
        reference.get.return_value.to_dict.return_value = None
        with patch("google.cloud.firestore.transactional", side_effect=lambda function: function):
            details = {"name": "Client"}
            self.assertEqual(self.store.save(reference, details, 0, self.actor.uid), 1)
            first = transaction.set.call_args.args[1]
            self.assertEqual(first["details"], details)
            reference.get.return_value.to_dict.return_value = first
            self.assertEqual(self.store.save(reference, details, 0, self.actor.uid), 1)
            transaction.set.assert_called_once()
            with self.assertRaises(StudioError) as conflict:
                self.store.save(reference, {"name": "Stale change"}, 0, self.actor.uid)
            self.assertEqual(conflict.exception.status, 409)
            self.assertEqual(self.store.save(reference, {"name": "Current change"}, 1, self.actor.uid), 2)
            self.assertEqual(transaction.set.call_args.args[1]["created_at"], first["created_at"])

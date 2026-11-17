"""Offline security tests: real SDK signatures, isolated SQLite, no cloud accounts."""

import json
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

import firebase_admin
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from firebase_admin import auth, credentials
from google.auth.credentials import AnonymousCredentials
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.auth import router as legacy_auth_router
from app.api.firebase_auth import router as firebase_auth_router
from app import main as backend_main
from app.core import firebase as firebase_core
from app.core.firebase_config import FirebaseConfigurationError, FirebaseSettings
from app.core.firebase_tokens import (
    FirebaseAuthUnavailable, FirebasePrincipal, InvalidFirebaseToken,
    create_gallery_sign_in_token, verify_firebase_id_token,
)
from app.core.security import hash_password
from app.database.cloud_models import FirebaseAdminLink
from app.database.db import Base, get_db
from app.database.models import Event, User
from utilities.link_firebase_admin import deactivate_admin, link_admin

PROJECT = "unit-test-project"
EMAIL = "owner@example.test"


def claims_for(uid="admin-test-uid", **overrides):
    now = int(time.time()) - 5
    return {"sub": uid, "aud": PROJECT, "iss": f"https://securetoken.google.com/{PROJECT}",
            "iat": now, "exp": now + 600, "auth_time": now, "email": EMAIL,
            "email_verified": True, "firebase": {"sign_in_provider": "password"}, **overrides}


class FirebaseConfigurationTests(unittest.TestCase):
    def test_backend_initializes_firebase_only_when_enabled(self):
        with patch.object(backend_main, "get_firebase_settings", return_value=FirebaseSettings()), \
                patch.object(backend_main, "get_firebase_app") as initialize:
            backend_main.initialize_firebase()
        initialize.assert_not_called()

        with patch.object(backend_main, "get_firebase_settings", return_value=FirebaseSettings(True, PROJECT)), \
                patch.object(backend_main, "get_firebase_app") as initialize:
            backend_main.initialize_firebase()
        initialize.assert_called_once_with()

    def test_firebase_startup_configuration_failure_preserves_local_services(self):
        with patch.object(backend_main, "get_firebase_settings", return_value=FirebaseSettings(True, PROJECT)), \
                patch.object(backend_main, "get_firebase_app", side_effect=FirebaseConfigurationError("credential unavailable")), \
                self.assertLogs("app.main", level="WARNING") as log:
            backend_main.initialize_firebase()
        self.assertIn("existing local services remain active", log.output[0])
        self.assertNotIn("credential unavailable", log.output[0])

    def test_disabled_configuration_does_not_require_project_credentials_or_sdk(self):
        settings = FirebaseSettings.from_env({"FIREBASE_ENABLED": "false", "FIREBASE_PROJECT_ID": "invalid",
                                              "FIREBASE_CREDENTIALS_FILE": "missing.json"})
        with patch.object(firebase_core, "get_firebase_settings", return_value=settings), \
                patch.object(firebase_core, "load_admin_sdk") as load:
            with self.assertRaises(FirebaseConfigurationError):
                firebase_core.get_firebase_app()
        load.assert_not_called()

    def test_enabled_configuration_requires_real_project_and_blocks_emulator_bypass(self):
        for values in [
            {"FIREBASE_ENABLED": "yes"},
            {"FIREBASE_ENABLED": "true"},
            {"FIREBASE_ENABLED": "true", "FIREBASE_PROJECT_ID": PROJECT, "FIREBASE_AUTH_EMULATOR_HOST": "127.0.0.1:9099"},
            {"FIREBASE_ENABLED": "true", "FIREBASE_PROJECT_ID": PROJECT, "FIREBASE_EMULATOR_MODE": "true", "FIREBASE_AUTH_EMULATOR_HOST": "127.0.0.1:9099"},
            {"FIREBASE_ENABLED": "true", "FIREBASE_PROJECT_ID": "demo-local", "FIREBASE_EMULATOR_MODE": "true", "FIREBASE_AUTH_EMULATOR_HOST": "example.com:9099"},
        ]:
            with self.subTest(values=values), self.assertRaises(FirebaseConfigurationError):
                FirebaseSettings.from_env(values)

    def test_emulator_requires_all_accessed_services_to_be_local(self):
        settings = FirebaseSettings.from_env({"FIREBASE_ENABLED": "true", "FIREBASE_PROJECT_ID": "demo-local",
                                              "FIREBASE_EMULATOR_MODE": "true", "FIREBASE_AUTH_EMULATOR_HOST": "127.0.0.1:9099"})
        with patch.object(firebase_core, "get_firebase_settings", return_value=settings):
            with self.assertRaises(FirebaseConfigurationError):
                firebase_core.get_firestore_client()
            with self.assertRaises(FirebaseConfigurationError):
                firebase_core.get_storage_bucket()

    def test_storage_emulator_uses_the_google_sdk_url_format(self):
        environment = {"FIREBASE_ENABLED": "true", "FIREBASE_PROJECT_ID": "demo-local",
                       "FIREBASE_EMULATOR_MODE": "true", "FIREBASE_AUTH_EMULATOR_HOST": "127.0.0.1:9099",
                       "STORAGE_EMULATOR_HOST": "http://127.0.0.1:9199"}
        self.assertIn(("STORAGE_EMULATOR_HOST", "http://127.0.0.1:9199"), FirebaseSettings.from_env(environment).emulator_hosts)
        for address in ["127.0.0.1:9199", "http://external.test:9199", "http://127.0.0.1:9199/path", "http://[invalid"]:
            with self.subTest(address=address), self.assertRaises(FirebaseConfigurationError):
                FirebaseSettings.from_env({**environment, "STORAGE_EMULATOR_HOST": address})

    def test_credentials_in_public_directory_and_other_projects_are_rejected(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            public = root / "online"
            public.mkdir()
            key = public / "credential.json"
            key.write_text("{}", encoding="utf-8")
            with patch("app.core.firebase_config.PUBLIC_ROOT", public):
                with self.assertRaises(FirebaseConfigurationError):
                    FirebaseSettings.from_env({"FIREBASE_ENABLED": "true", "FIREBASE_PROJECT_ID": PROJECT,
                                               "FIREBASE_CREDENTIALS_FILE": str(key)})
            key = root / "private.json"
            key.write_text(json.dumps({"type": "service_account", "project_id": "different-project"}), encoding="utf-8")
            with self.assertRaises(FirebaseConfigurationError):
                firebase_core._credential(firebase_admin, FirebaseSettings(True, PROJECT, credentials_file=str(key)))

    def test_initializer_is_lazy_singleton_and_rejects_runtime_mode_changes(self):
        settings = FirebaseSettings(True, PROJECT)
        sdk = Mock()
        with patch.object(firebase_core, "_app", None), patch.object(firebase_core, "_initialized_settings", None), \
                patch.object(firebase_core, "get_firebase_settings", return_value=settings) as read, \
                patch.object(firebase_core, "load_admin_sdk", return_value=sdk), \
                patch.object(firebase_core, "_credential", return_value="test-credential"):
            self.assertIs(firebase_core.get_firebase_app(), sdk.initialize_app.return_value)
            self.assertIs(firebase_core.get_firebase_app(), sdk.initialize_app.return_value)
            sdk.initialize_app.assert_called_once_with("test-credential", {"projectId": PROJECT, "httpTimeout": 15}, name="event-photo-finder")
            read.return_value = FirebaseSettings(True, "another-project")
            with self.assertRaises(FirebaseConfigurationError):
                firebase_core.get_firebase_app()


class TestCredential(credentials.Base):
    def get_credential(self):
        return AnonymousCredentials()


class FirebaseSignatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.public_key = cls.private_key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()

    def setUp(self):
        self.app = firebase_admin.initialize_app(TestCredential(), {"projectId": PROJECT}, name=self.id())
        self.client = auth._get_client(self.app)
        # Only certificate transport and user-directory reads are stubbed. The
        # SDK still performs its real RSA signature/audience/issuer/expiry checks.
        self.client._token_verifier.request = Mock(return_value=SimpleNamespace(
            status=200, data=json.dumps({"offline-test-key": self.public_key}).encode()))
        self.user = SimpleNamespace(disabled=False, tokens_valid_after_timestamp=0)
        self.patches = [patch("app.core.firebase_tokens.get_firebase_app", return_value=self.app),
                        patch("app.core.firebase_tokens.get_firebase_auth", return_value=auth),
                        patch("app.core.firebase_tokens.get_firebase_settings", return_value=FirebaseSettings(True, PROJECT)),
                        patch.object(self.client, "get_user", return_value=self.user)]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        firebase_admin.delete_app(self.app)

    def token(self, *, key=None, **claims):
        return jwt.encode(claims_for(**claims), key or self.private_key, algorithm="RS256", headers={"kid": "offline-test-key"})

    def test_valid_signed_id_token_is_verified_and_revocation_is_checked(self):
        principal = verify_firebase_id_token(self.token())
        self.assertEqual((principal.uid, principal.project_id), ("admin-test-uid", PROJECT))
        self.client.get_user.assert_called_once_with("admin-test-uid")
        self.assertNotIn("email", repr(principal))

    def test_wrong_signature_project_issuer_expiry_and_unsigned_tokens_are_rejected(self):
        tokens = [self.token(key=self.other_key), self.token(aud="other-project"), self.token(iss="https://example.test"),
                  self.token(iat=int(time.time()) - 900, exp=int(time.time()) - 300),
                  jwt.encode(claims_for(), "", algorithm="none")]
        for token in tokens:
            with self.subTest(case=tokens.index(token)), self.assertRaises(InvalidFirebaseToken):
                verify_firebase_id_token(token)

    def test_revoked_disabled_and_tenant_tokens_are_rejected(self):
        self.user.tokens_valid_after_timestamp = int(time.time()) * 1000
        with self.assertRaises(InvalidFirebaseToken):
            verify_firebase_id_token(self.token())
        self.user.tokens_valid_after_timestamp = 0
        self.user.disabled = True
        with self.assertRaises(InvalidFirebaseToken):
            verify_firebase_id_token(self.token())
        self.user.disabled = False
        with self.assertRaises(InvalidFirebaseToken):
            verify_firebase_id_token(self.token(firebase={"tenant": "unconfigured-tenant"}))

    def test_certificate_outage_fails_closed_as_unavailable(self):
        self.client._token_verifier.request.return_value = SimpleNamespace(status=503, data=b"unavailable")
        with self.assertRaises(FirebaseAuthUnavailable):
            verify_firebase_id_token(self.token())

    def test_custom_sign_in_token_is_not_accepted_as_an_api_id_token(self):
        custom = jwt.encode({"uid": "admin-test-uid", "aud": "https://identitytoolkit.googleapis.com/google.identity.identitytoolkit.v1.IdentityToolkit",
                             "iss": "service@example.test", "sub": "service@example.test", "iat": int(time.time()), "exp": int(time.time()) + 300},
                            self.private_key, algorithm="RS256", headers={"kid": "offline-test-key"})
        with self.assertRaises(InvalidFirebaseToken):
            verify_firebase_id_token(custom)


class FirebaseAdminBindingTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine, tables=[User.__table__, Event.__table__])
        self.db = sessionmaker(bind=self.engine)()
        self.db.add_all([User(id=1, email=EMAIL, password_hash=hash_password("local-test-password-123"), is_active=True),
                         User(id=2, email="other@example.test", password_hash="unused", is_active=True)])
        self.db.commit()
        self.remote = SimpleNamespace(uid="admin-test-uid", email=EMAIL, email_verified=True, disabled=False)
        app = FastAPI()
        app.include_router(firebase_auth_router)
        app.include_router(legacy_auth_router)
        app.dependency_overrides[get_db] = lambda: self.db
        self.http = TestClient(app)

    def tearDown(self):
        self.http.close()
        self.db.close()
        self.engine.dispose()

    def principal(self, **overrides):
        claims = {"uid": "admin-test-uid", **claims_for(), **overrides}
        return FirebasePrincipal(claims["uid"], PROJECT, claims)

    def request_admin(self, principal=None):
        with patch("app.dependencies.firebase_auth.verify_firebase_id_token", return_value=principal or self.principal()):
            return self.http.get("/api/firebase/auth/admin", headers={"Authorization": "Bearer offline-test-token"})

    def test_linking_dry_run_does_not_create_schema_then_apply_is_idempotent(self):
        result = link_admin(self.db, PROJECT, self.remote, 1)
        self.assertFalse(result["applied"])
        self.assertFalse(inspect(self.engine).has_table("firebase_admin_links"))
        link_admin(self.db, PROJECT, self.remote, 1, apply=True)
        link_admin(self.db, PROJECT, self.remote, 1, apply=True)
        self.assertEqual(self.db.query(FirebaseAdminLink).count(), 1)
        response = self.request_admin()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], 1)
        self.assertEqual(response.headers["Cache-Control"], "private, no-store")

    def test_email_or_admin_claim_alone_never_grants_local_admin(self):
        FirebaseAdminLink.__table__.create(self.engine)
        self.assertEqual(self.request_admin(self.principal(admin=True)).status_code, 403)
        self.assertEqual(self.request_admin(self.principal(email_verified=False)).status_code, 403)
        self.assertEqual(self.http.get("/api/firebase/auth/admin").status_code, 401)

    def test_missing_mapping_schema_fails_closed_without_creating_it(self):
        self.assertEqual(self.request_admin().status_code, 503)
        self.assertFalse(inspect(self.engine).has_table("firebase_admin_links"))

    def test_disabled_local_user_revoked_link_wrong_project_and_client_token_are_denied(self):
        link_admin(self.db, PROJECT, self.remote, 1, apply=True)
        for principal in [FirebasePrincipal("admin-test-uid", "other-project", self.principal().claims),
                          self.principal(actor_type="gallery_client"), self.principal(email="other@example.test"),
                          self.principal(email_verified=False)]:
            self.assertEqual(self.request_admin(principal).status_code, 403)
        deactivate_admin(self.db, PROJECT, self.remote.uid, 1, apply=True)
        self.assertEqual(self.request_admin().status_code, 403)
        link_admin(self.db, PROJECT, self.remote, 1, apply=True)
        self.db.get(User, 1).is_active = False
        self.db.commit()
        self.assertEqual(self.request_admin().status_code, 403)

    def test_mismatched_unverified_disabled_and_duplicate_identities_cannot_be_linked(self):
        for changes in [{"email": "other@example.test"}, {"email_verified": False}, {"disabled": True}, {"uid": "gallery-client:test"}]:
            remote = SimpleNamespace(**{**vars(self.remote), **changes})
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                link_admin(self.db, PROJECT, remote, 1, apply=True)
        self.assertFalse(inspect(self.engine).has_table("firebase_admin_links"))
        link_admin(self.db, PROJECT, self.remote, 1, apply=True)
        remote = SimpleNamespace(**{**vars(self.remote), "uid": "another-uid"})
        with self.assertRaises(ValueError):
            link_admin(self.db, PROJECT, remote, 1, apply=True)

    def test_sdk_failure_responses_never_leak_tokens_or_credentials(self):
        for error, status in [(InvalidFirebaseToken("secret-token"), 401),
                              (FirebaseAuthUnavailable("private-key-details"), 503),
                              (FirebaseConfigurationError("credential-path"), 503)]:
            with patch("app.dependencies.firebase_auth.verify_firebase_id_token", side_effect=error):
                response = self.http.get("/api/firebase/auth/me", headers={"Authorization": "Bearer offline-test-token"})
            self.assertEqual(response.status_code, status)
            self.assertNotIn(str(error), response.text)

    def test_existing_local_login_and_admin_contract_still_work_with_firebase_unconfigured(self):
        response = self.http.post("/api/auth/login", data={"username": EMAIL, "password": "local-test-password-123"})
        self.assertEqual(response.status_code, 200)
        token = response.json()["access_token"]
        response = self.http.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], 1)
        self.assertEqual(self.http.get("/api/auth/me", headers={"Authorization": "Bearer offline-firebase-token"}).status_code, 401)


class GalleryTokenTests(unittest.TestCase):
    def test_gallery_token_has_fixed_client_scope_and_no_admin_claim(self):
        sdk = Mock()
        sdk.create_custom_token.return_value = b"offline-custom-token"
        with patch("app.core.firebase_tokens.get_firebase_app", return_value="test-app"), \
                patch("app.core.firebase_tokens.get_firebase_auth", return_value=sdk), \
                patch("app.core.firebase_tokens.get_firebase_settings", return_value=FirebaseSettings(True, PROJECT)):
            token = create_gallery_sign_in_token(client_id="client_1", gallery_id="gallery_1", session_id="session_1", credential_version=2)
        self.assertEqual(token, "offline-custom-token")
        sdk.create_custom_token.assert_called_once_with("gallery-client:client_1", {
            "actor_type": "gallery_client", "gallery_id": "gallery_1", "gallery_session_id": "session_1", "credential_version": 2}, app="test-app")

    def test_invalid_scopes_cannot_reach_signer(self):
        for changes in [{"client_id": "../escape"}, {"gallery_id": ""}, {"credential_version": True}, {"credential_version": 0}]:
            with patch("app.core.firebase_tokens.get_firebase_app") as app, self.assertRaises(ValueError):
                create_gallery_sign_in_token(**{"client_id": "client", "gallery_id": "gallery", "session_id": "session", "credential_version": 1, **changes})
            app.assert_not_called()


if __name__ == "__main__":
    unittest.main()

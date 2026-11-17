import contextlib
import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from utilities.manage_photo_gallery import main


class GalleryInviteTests(unittest.TestCase):
    manifest = ("Synthetic event", {"a.jpg", "b.jpg"}, {"a.jpg"}, [{"photo_id": 11, "filename": "a.jpg"}])

    def test_complete_album_remains_the_default(self):
        with patch("sys.argv", ["gallery", "--event-id", "3"]), \
                patch("utilities.manage_photo_gallery.dotenv_values", return_value={"PHOTO_FINDER_WORKER_SECRET": "synthetic"}), \
                patch("utilities.manage_photo_gallery.local_manifest", return_value=self.manifest), \
                patch("utilities.manage_photo_gallery.requests.post") as create, \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(), 1)
            create.assert_not_called()

    def test_explicit_growing_album_saves_a_generated_password_without_printing_it(self):
        with TemporaryDirectory() as directory, contextlib.chdir(directory):
            output = Path(directory) / ".secrets" / "invite.json"
            password = "generated-strong-test-password-123"
            response = Mock()
            response.json.return_value = {"token": "a" * 64, "gallery_id": "test-gallery", "expires_at": "2026-12-18T00:00:00Z"}
            printed = io.StringIO()
            with patch("sys.argv", ["gallery", "--event-id", "3", "--allow-indexing", "--generate-password", "--output", str(output)]), \
                    patch("utilities.manage_photo_gallery.dotenv_values", return_value={"PHOTO_FINDER_WORKER_SECRET": "synthetic"}), \
                    patch("utilities.manage_photo_gallery.local_manifest", return_value=self.manifest), \
                    patch("utilities.manage_photo_gallery.remote_manifest", return_value=self.manifest[3]), \
                    patch("utilities.manage_photo_gallery.secrets.token_urlsafe", return_value=password), \
                    patch("utilities.manage_photo_gallery.requests.post", return_value=response) as create, \
                    contextlib.redirect_stdout(printed):
                self.assertEqual(main(), 0)
            saved = json.loads(output.read_text())
            self.assertEqual(saved["password"], password)
            self.assertEqual(saved["photos_expected_at_creation"], 2)
            self.assertEqual(saved["photos_ready_at_creation"], 1)
            self.assertEqual(create.call_args.kwargs["json"]["password"], password)
            self.assertNotIn(password, printed.getvalue())

    def test_event_date_is_used_as_the_gallery_password(self):
        response = Mock()
        response.json.return_value = {"token": "a" * 64, "gallery_id": "test-gallery", "expires_at": "2026-12-18T00:00:00Z"}
        with patch("sys.argv", ["gallery", "--event-id", "3", "--allow-indexing", "--event-date", "2026-11-18"]), \
                patch("utilities.manage_photo_gallery.dotenv_values", return_value={"PHOTO_FINDER_WORKER_SECRET": "synthetic"}), \
                patch("utilities.manage_photo_gallery.local_manifest", return_value=self.manifest), \
                patch("utilities.manage_photo_gallery.remote_manifest", return_value=self.manifest[3]), \
                patch("utilities.manage_photo_gallery.requests.post", return_value=response) as create, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(), 0)
        self.assertEqual(create.call_args.kwargs["json"], {
            "event_date": "2026-11-18", "expires_days": 30,
        })

    def test_invalid_event_date_is_rejected(self):
        with patch("sys.argv", ["gallery", "--event-id", "3", "--event-date", "2026-02-30"]), \
                contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                main()
        self.assertEqual(error.exception.code, 2)

    def test_growing_album_rejects_a_foreign_remote_manifest(self):
        with patch("sys.argv", ["gallery", "--event-id", "3", "--allow-indexing", "--wait-seconds", "1"]), \
                patch("utilities.manage_photo_gallery.dotenv_values", return_value={"PHOTO_FINDER_WORKER_SECRET": "synthetic"}), \
                patch("utilities.manage_photo_gallery.local_manifest", return_value=self.manifest), \
                patch("utilities.manage_photo_gallery.remote_manifest", return_value=[{"photo_id": 99, "filename": "foreign.jpg"}]), \
                patch("utilities.manage_photo_gallery.time.monotonic", side_effect=[0, 2]), \
                patch("utilities.manage_photo_gallery.requests.post") as create, \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(RuntimeError):
                main()
            create.assert_not_called()


if __name__ == "__main__":
    unittest.main()

from pathlib import Path
from tempfile import TemporaryDirectory
from contextlib import ExitStack
import unittest
from unittest.mock import patch

from watch_camera_folder import scan_ready
from app.services.camera_importer import open_import_batch


class CameraImporterTests(unittest.TestCase):
    def test_locked_file_does_not_block_readable_photos_in_the_batch(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            locked, readable = root / "locked.jpg", root / "ready.jpg"
            locked.write_bytes(b"locked")
            readable.write_bytes(b"ready")
            original_open = Path.open

            def open_file(path, *args, **kwargs):
                if path == locked:
                    raise PermissionError("Camera is still writing this file")
                return original_open(path, *args, **kwargs)

            with ExitStack() as stack, patch.object(Path, "open", open_file), \
                    self.assertLogs("app.services.camera_importer", level="WARNING"):
                uploads, batch = open_import_batch([(locked, (6, 1)), (readable, (5, 1))], stack)
                self.assertEqual([upload.filename for upload in uploads], ["ready.jpg"])
                self.assertEqual(batch, [(readable, (5, 1))])
                self.assertEqual(uploads[0].file.read(), b"ready")

    def test_new_photo_becomes_ready_after_short_stability_window(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            photo = root / "photo.jpg"
            photo.write_bytes(b"complete photo")
            observed = {}

            self.assertEqual(scan_ready(root, observed, {}, 10.0), [])
            ready = scan_ready(root, observed, {}, 10.5)

            self.assertEqual(ready, [(photo, (len(b"complete photo"), photo.stat().st_mtime_ns))])

    def test_photo_write_restarts_stability_window(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            photo = root / "photo.jpg"
            photo.write_bytes(b"partial")
            observed = {}
            scan_ready(root, observed, {}, 10.0)

            photo.write_bytes(b"completed photo")
            self.assertEqual(scan_ready(root, observed, {}, 10.5), [])
            self.assertEqual(len(scan_ready(root, observed, {}, 11.0)), 1)


if __name__ == "__main__":
    unittest.main()

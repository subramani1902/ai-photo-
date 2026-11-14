from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from watch_camera_folder import scan_ready


class CameraImporterTests(unittest.TestCase):
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

import contextlib
from pathlib import Path
from tempfile import TemporaryDirectory
import io
import unittest
from unittest.mock import patch

from utilities.copy_photos_to_watched_folder import (
    copy_photo,
    copy_photos,
    discover_photos,
)


class PhotoTransferTests(unittest.TestCase):
    def test_discovers_supported_images_recursively(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "nested").mkdir()
            (root / "nested" / "photo.JPG").write_bytes(b"photo")
            (root / "notes.txt").write_text("not a photo", encoding="utf-8")

            self.assertEqual(
                [path.relative_to(root).as_posix() for path in discover_photos(root)],
                ["nested/photo.JPG"],
            )

    def test_copy_preserves_source_and_does_not_replace_existing_destination(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source_root = root / "source"
            destination_root = root / "destination"
            source_root.mkdir()
            destination_root.mkdir()
            source = source_root / "photo.jpg"
            destination = destination_root / "photo.jpg"
            source.write_bytes(b"source")
            destination.write_bytes(b"existing")

            self.assertFalse(copy_photo(source, source_root, destination_root))
            self.assertEqual(source.read_bytes(), b"source")
            self.assertEqual(destination.read_bytes(), b"existing")

    def test_waits_exact_interval_between_successful_copies(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source_root = root / "source"
            destination_root = root / "destination"
            source_root.mkdir()
            destination_root.mkdir()
            first = source_root / "first.jpg"
            second = source_root / "second.jpg"
            first.write_bytes(b"first")
            second.write_bytes(b"second")

            with patch(
                "utilities.copy_photos_to_watched_folder.time.sleep"
            ) as sleep, contextlib.redirect_stdout(io.StringIO()):
                copied, skipped = copy_photos(
                    source_root,
                    destination_root,
                    [first, second],
                    interval=10,
                )

            self.assertEqual((copied, skipped), (2, 0))
            sleep.assert_called_once_with(10)


if __name__ == "__main__":
    unittest.main()

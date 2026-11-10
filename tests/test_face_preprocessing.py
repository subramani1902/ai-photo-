from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

from app.api.photos import is_duplicate
from app.ai.face_engine import (
    prepare_image_for_processing,
)
from app.services.relay_worker import prepare_photo_for_transfer


class FacePreprocessingTests(unittest.TestCase):
    def test_large_image_is_downscaled_without_changing_aspect_ratio(self):
        image = np.zeros((1200, 1800, 3), dtype=np.uint8)

        processed = prepare_image_for_processing(image, max_dimension=600)

        self.assertEqual(processed.shape[:2], (400, 600))

    def test_small_image_is_not_resized(self):
        image = np.zeros((120, 180, 3), dtype=np.uint8)

        self.assertIs(prepare_image_for_processing(image), image)

    def test_duplicate_check_does_not_hash_different_sized_files(self):
        db = Mock()
        query = db.query.return_value
        query.filter.return_value = query
        with TemporaryDirectory() as directory:
            path = Path(directory) / "existing.jpg"
            path.write_bytes(b"existing photo")
            query.all.return_value = [Mock(file_path=str(path))]

            with patch("app.api.photos.cached_file_hash") as cached_hash:
                self.assertFalse(is_duplicate(db, 1, "new photo hash", 8))

            cached_hash.assert_not_called()
            cached_hash.assert_not_called()
    def test_guest_transfer_uses_compressed_copy_and_keeps_original(self):
        image = np.full((1600, 2400, 3), 120, dtype=np.uint8)
        with TemporaryDirectory() as directory:
            source = Path(directory) / "original.jpg"
            transfer = Path(directory) / "transfer.jpg"
            self.assertTrue(
                cv2.imwrite(
                    str(source),
                    image,
                    [cv2.IMWRITE_JPEG_QUALITY, 100]
                )
            )
            original = source.read_bytes()

            prepare_photo_for_transfer(source, transfer)

            self.assertEqual(source.read_bytes(), original)
            self.assertTrue(transfer.is_file())
            self.assertLess(transfer.stat().st_size, source.stat().st_size)
            processed = cv2.imread(str(transfer))
            self.assertEqual(processed.shape[:2], (1067, 1600))

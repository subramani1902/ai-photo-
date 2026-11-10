from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, Mock, patch
from types import SimpleNamespace

import cv2
import numpy as np

from app.api.photos import is_duplicate
from app.ai.face_engine import (
    prepare_image_for_processing,
)
from app.services.relay_worker import (
    RelayWorker,
    prepare_photo_for_showcase,
    prepare_photo_for_transfer,
)


class FacePreprocessingTests(unittest.TestCase):
    def test_relay_worker_uses_the_worker_secret_for_authorization(self):
        worker = RelayWorker("https://relay.example", "worker-secret", 5)

        self.assertEqual(
            worker.session.headers["Authorization"],
            "Bearer worker-secret"
        )

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

    def test_event_showcase_preview_is_small_webp_and_keeps_original(self):
        image = np.full((1200, 1800, 3), 120, dtype=np.uint8)
        with TemporaryDirectory() as directory:
            source = Path(directory) / "original.jpg"
            preview = Path(directory) / "preview.webp"
            self.assertTrue(cv2.imwrite(str(source), image))
            original = source.read_bytes()

            prepare_photo_for_showcase(source, preview)

            self.assertEqual(source.read_bytes(), original)
            self.assertLessEqual(preview.stat().st_size, 2 * 1024 * 1024)
            processed = cv2.imread(str(preview))
            self.assertEqual(processed.shape[:2], (427, 640))

    def test_event_showcase_sync_keeps_only_latest_local_photos(self):
        event_query, photo_query, db = Mock(), Mock(), MagicMock()
        db.__enter__.return_value = db
        db.query.side_effect = [event_query, photo_query]
        event_query.filter_by.return_value.one_or_none.return_value = SimpleNamespace(
            event_name="Sample event"
        )
        photo_query.filter_by.return_value.order_by.return_value.limit.return_value.all.return_value = [
            SimpleNamespace(id=3, filename="three.jpg", file_path="unused"),
            SimpleNamespace(id=2, filename="two.jpg", file_path="unused"),
            SimpleNamespace(id=1, filename="one.jpg", file_path="unused"),
        ]
        worker = RelayWorker("https://relay.example", "secret", 5)
        current = Mock()
        current.json.return_value = {"event_name": "Sample event", "photo_ids": [1, 2, 3]}

        with patch("app.database.db.SessionLocal", return_value=db), \
                patch.object(worker, "request", return_value=current) as request:
            worker.sync_showcase()

        self.assertEqual(request.call_args_list[0].args, ("GET", "/worker/showcase"))
        self.assertEqual(
            request.call_args_list[1].kwargs["json"],
            {"event_name": "Sample event", "photo_ids": [3, 2, 1]}
        )

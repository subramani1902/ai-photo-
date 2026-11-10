import unittest

import numpy as np

from app.ai.face_engine import (
    prepare_image_for_processing,
)


class FacePreprocessingTests(unittest.TestCase):
    def test_large_image_is_downscaled_without_changing_aspect_ratio(self):
        image = np.zeros((1200, 1800, 3), dtype=np.uint8)

        processed = prepare_image_for_processing(image, max_dimension=600)

        self.assertEqual(processed.shape[:2], (400, 600))

    def test_small_image_is_not_resized(self):
        image = np.zeros((120, 180, 3), dtype=np.uint8)

        self.assertIs(prepare_image_for_processing(image), image)

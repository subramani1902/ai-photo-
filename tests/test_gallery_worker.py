import os
import unittest
from unittest.mock import Mock, patch

import requests

from app.services.gallery_worker import main


class GalleryWorkerTests(unittest.TestCase):
    def run_worker(self, worker):
        with patch("sys.argv", ["gallery_worker", "--event-id", "3", "--relay-url", "https://relay.example"]), \
                patch("app.services.gallery_worker.load_dotenv"), \
                patch.dict(os.environ, {"PHOTO_FINDER_WORKER_SECRET": "synthetic"}), \
                patch("app.services.gallery_worker.validate_event"), \
                patch("app.services.gallery_worker.RelayWorker", return_value=worker), \
                patch("app.services.gallery_worker.time.sleep"):
            return main()

    def test_gallery_worker_delivers_without_claiming_or_matching_selfies(self):
        worker = Mock()
        worker.process_gallery_once.side_effect = [True, KeyboardInterrupt()]
        self.assertEqual(self.run_worker(worker), 0)
        self.assertEqual(worker.process_gallery_once.call_count, 2)
        worker.process_once.assert_not_called()
        worker.sync_showcase.assert_not_called()
        worker.session.close.assert_called_once()

    def test_transient_gallery_failure_recovers_without_stopping_the_search_worker(self):
        worker = Mock()
        worker.process_gallery_once.side_effect = [requests.ConnectionError(), True, KeyboardInterrupt()]
        with self.assertLogs("app.services.gallery_worker", level="WARNING"):
            self.assertEqual(self.run_worker(worker), 0)
        self.assertEqual(worker.process_gallery_once.call_count, 3)

    def test_rejected_gallery_credentials_fail_closed(self):
        worker, response = Mock(), requests.Response()
        response.status_code = 401
        worker.request.side_effect = requests.HTTPError(response=response)
        with self.assertLogs("app.services.gallery_worker", level="ERROR"):
            self.assertEqual(self.run_worker(worker), 1)
        worker.process_gallery_once.assert_not_called()


if __name__ == "__main__":
    unittest.main()

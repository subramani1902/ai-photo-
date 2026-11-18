import os
import unittest
from threading import Barrier, Event, Lock, Thread, get_ident
from unittest.mock import Mock, patch

import requests

from app.services.gallery_worker import deliver_parallel, main


class GalleryWorkerTests(unittest.TestCase):
    def run_worker(self, worker):
        with patch("sys.argv", ["gallery_worker", "--event-id", "3", "--relay-url", "https://relay.example", "--workers", "1"]), \
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

    def test_parallel_previews_use_separate_reused_sessions_and_one_dispatcher(self):
        stop, release_first, wave = Event(), Event(), Barrier(3)
        lock, deliveries, owners, sessions = Lock(), [], {}, []
        jobs = iter(range(1, 7))
        dispatcher = Mock()
        dispatcher.claim_gallery_job.side_effect = lambda: next(jobs, None)

        def factory():
            session = Mock()
            sessions.append(session)
            owner = get_ident()

            def deliver(job):
                self.assertEqual(get_ident(), owner)
                if job <= 3:
                    wave.wait(timeout=5)
                if job == 1:
                    self.assertTrue(release_first.wait(5))
                with lock:
                    owners.setdefault(owner, []).append(job)
                    deliveries.append(job)
                    if len(deliveries) >= 4:
                        release_first.set()
                    if len(deliveries) == 6:
                        stop.set()
            return Mock(session=session, process_gallery_job=deliver)

        runner = Thread(target=deliver_parallel, args=(dispatcher, factory, 3, .01, stop), daemon=True)
        runner.start()
        runner.join(timeout=8)
        self.assertFalse(runner.is_alive(), "Parallel delivery must stop and close its sessions")
        self.assertEqual(sorted(deliveries), list(range(1, 7)))
        self.assertNotIn(1, deliveries[:3], "A slow preview must not block the next previews")
        self.assertEqual(len(owners), 3)
        self.assertTrue(any(len(jobs) > 1 for jobs in owners.values()))
        self.assertEqual(len(sessions), 3)
        for session in sessions:
            session.close.assert_called_once()
        dispatcher.process_once.assert_not_called()

    def test_idle_parallel_delivery_uses_one_poller_and_no_transfer_sessions(self):
        stop, dispatcher, factory = Event(), Mock(), Mock()
        def empty_queue():
            stop.set()
            return None
        dispatcher.claim_gallery_job.side_effect = empty_queue
        deliver_parallel(dispatcher, factory, 3, .01, stop)
        dispatcher.claim_gallery_job.assert_called_once()
        factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()

from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import MagicMock, Mock, patch

import requests

from app.services.relay_worker import RelayWorker, main


JOB_ID = "00000000-0000-4000-8000-000000000001"
LEASE_ID = "00000000-0000-4000-8000-000000000002"


class RelayWorkerTests(unittest.TestCase):
    def setUp(self):
        self.worker = RelayWorker("https://relay.example", "worker-secret", 3, engine=object())
        self.download = MagicMock()
        self.download.__enter__.return_value = self.download
        self.download.iter_content.return_value = [b"selfie"]
        self.claim = Mock()
        self.claim.json.return_value = {"job": {"id": JOB_ID, "lease": LEASE_ID}}

    def respond(self, method, path, **kwargs):
        if path == "/worker/claim":
            return self.claim
        if path.endswith("/selfie"):
            return self.download
        return Mock()

    def test_medium_original_is_compressed_before_guest_upload(self):
        with TemporaryDirectory() as folder:
            source = Path(folder) / "original.jpg"
            original = b"x" * (2 * 1024 * 1024)
            source.write_bytes(original)
            match = {"photo_id": 12, "path": source, "filename": source.name,
                     "similarity": 0.9, "face_count": 1}
            uploaded = []

            def prepare(source_path, target):
                target.write_bytes(b"small guest copy")
                return target

            def request(method, path, **kwargs):
                if path.endswith("/results"):
                    uploaded.append(kwargs["files"]["photo"][1].read())
                return self.respond(method, path, **kwargs)

            with patch.object(self.worker, "request", side_effect=request), \
                    patch("app.services.relay_worker.find_event_matches", return_value=[match]), \
                    patch("app.services.relay_worker.prepare_photo_for_transfer", side_effect=prepare):
                self.assertTrue(self.worker.process_once())

            self.assertEqual(uploaded, [b"small guest copy"])
            self.assertEqual(source.read_bytes(), original)

    def test_full_match_list_is_published_before_uploads_and_skips_resumed_photos(self):
        self.claim.json.return_value["job"]["uploaded_photo_ids"] = [11]
        with TemporaryDirectory() as folder:
            source = Path(folder) / "photo.jpg"
            source.write_bytes(b"guest photo")
            matches = [
                {"photo_id": photo_id, "filename": source.name, "path": source,
                 "similarity": 0.9, "face_count": 1}
                for photo_id in (11, 12, 13)
            ]
            requests_sent = []

            def request(method, path, **kwargs):
                if path.endswith("/matches"):
                    requests_sent.append(("matches", kwargs["json"]))
                elif path.endswith("/results"):
                    requests_sent.append(("upload", kwargs["data"]["photo_id"]))
                return self.respond(method, path, **kwargs)

            with patch.object(self.worker, "request", side_effect=request), \
                    patch("app.services.relay_worker.find_event_matches", return_value=matches):
                self.assertTrue(self.worker.process_once())

        self.assertEqual(requests_sent[0][0], "matches")
        manifest = requests_sent[0][1]["photos"]
        self.assertEqual([photo["photo_id"] for photo in manifest], [12, 13])
        self.assertTrue(all(set(photo) == {"photo_id", "filename", "similarity", "face_count"}
                            for photo in manifest))
        self.assertEqual(sorted(value for kind, value in requests_sent if kind == "upload"), ["12", "13"])

    def test_zero_matches_are_reported_before_completion(self):
        with patch.object(self.worker, "request", side_effect=self.respond) as send, \
                patch("app.services.relay_worker.find_event_matches", return_value=[]):
            self.assertTrue(self.worker.process_once())
        manifest_call = next(call for call in send.call_args_list if call.args[1].endswith("/matches"))
        self.assertEqual(manifest_call.kwargs["json"], {"photos": []})
        paths = [call.args[1] for call in send.call_args_list]
        self.assertLess(paths.index(f"/worker/jobs/{JOB_ID}/matches"),
                        paths.index(f"/worker/jobs/{JOB_ID}/complete"))

    def test_legacy_relay_without_match_reporting_still_delivers_photos(self):
        with TemporaryDirectory() as folder:
            source = Path(folder) / "photo.jpg"
            source.write_bytes(b"guest photo")
            match = {"photo_id": 12, "filename": source.name, "path": source,
                     "similarity": 0.9, "face_count": 1}
            response = requests.Response()
            response.status_code = 404

            def request(method, path, **kwargs):
                if path.endswith("/matches"):
                    raise requests.HTTPError(response=response)
                return self.respond(method, path, **kwargs)

            with patch.object(self.worker, "request", side_effect=request) as send, \
                    patch("app.services.relay_worker.find_event_matches", return_value=[match]), \
                    self.assertLogs("app.services.relay_worker", level="WARNING"):
                self.assertTrue(self.worker.process_once())
            paths = [call.args[1] for call in send.call_args_list]
            self.assertIn(f"/worker/jobs/{JOB_ID}/results", paths)
            self.assertIn(f"/worker/jobs/{JOB_ID}/complete", paths)
            self.assertNotIn(f"/worker/jobs/{JOB_ID}/release", paths)

    def test_invalid_worker_credentials_exit_instead_of_retrying_forever(self):
        response = requests.Response()
        response.status_code = 401
        worker = Mock()
        worker.request.side_effect = requests.HTTPError(response=response)
        with patch("sys.argv", ["relay_worker", "--event-id", "3", "--relay-url", "https://relay.example"]), \
                patch("app.services.relay_worker.load_dotenv"), \
                patch.dict("os.environ", {"PHOTO_FINDER_WORKER_SECRET": "test-secret"}), \
                patch("app.services.relay_worker.RelayWorker", return_value=worker), \
                patch("app.services.relay_worker.time.sleep") as sleep, \
                self.assertLogs("app.services.relay_worker", level="ERROR"):
            self.assertEqual(main(), 1)
        sleep.assert_not_called()

    def test_heartbeat_runs_while_face_matching_is_blocked(self):
        heartbeat = Event()

        def request(method, path, **kwargs):
            if path.endswith("/heartbeat"):
                self.assertIsNot(kwargs["session"], self.worker.session)
                heartbeat.set()
            return self.respond(method, path, **kwargs)

        def match(*args, **kwargs):
            self.assertTrue(heartbeat.wait(2), "Matching prevented the lease heartbeat")
            return []

        with patch("app.services.relay_worker.HEARTBEAT_INTERVAL_SECONDS", 0.01), \
                patch.object(self.worker, "request", side_effect=request), \
                patch("app.services.relay_worker.find_event_matches", side_effect=match):
            self.assertTrue(self.worker.process_once())

    def test_lost_lease_stops_uploads_after_matching(self):
        heartbeat_finished = Event()
        session = MagicMock()
        session.__enter__.return_value = session
        session.__exit__.side_effect = lambda *args: heartbeat_finished.set()
        response = requests.Response()
        response.status_code = 409
        lost_lease = requests.HTTPError(response=response)

        def request(method, path, **kwargs):
            if path.endswith("/heartbeat"):
                raise lost_lease
            return self.respond(method, path, **kwargs)

        def match(*args, **kwargs):
            self.assertTrue(heartbeat_finished.wait(2))
            return [{"photo_id": 12}]

        with patch("app.services.relay_worker.HEARTBEAT_INTERVAL_SECONDS", 0.01), \
                patch("app.services.relay_worker.requests.Session", return_value=session), \
                patch.object(self.worker, "request", side_effect=request) as send, \
                patch("app.services.relay_worker.find_event_matches", side_effect=match):
            with self.assertRaises(requests.HTTPError):
                self.worker.process_once()

        paths = [call.args[1] for call in send.call_args_list]
        self.assertFalse(any(path.endswith(("/results", "/complete")) for path in paths))
        self.assertIn(f"/worker/jobs/{JOB_ID}/release", paths)


if __name__ == "__main__":
    unittest.main()

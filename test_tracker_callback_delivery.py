import contextlib
import io
import json
import os
import tempfile
import unittest

import requests

with contextlib.redirect_stdout(io.StringIO()):
    from creator_api_client import (
        CALLBACK_RETRY_DELAYS_SECONDS,
        TERMINAL_MAX_REQUEST_BYTES,
        TrackerAPIClient,
    )
from tracker_reporting import MAX_ERROR_LOG_BYTES, MAX_LOG_EXCERPT_BYTES, SCHEMA_VERSION


class FakeResponse:
    def __init__(self, status_code, body="ok"):
        self.status_code = status_code
        self.text = body if isinstance(body, str) else json.dumps(body)

    def json(self):
        return json.loads(self.text)


class FakeSession:
    def __init__(self, responses=None, exceptions=None):
        self.responses = list(responses or [])
        self.exceptions = list(exceptions or [])
        self.calls = []

    def put(self, url, json, timeout):
        self.calls.append(("PUT", url, json, timeout))
        if self.exceptions:
            raise self.exceptions.pop(0)
        return self.responses.pop(0) if self.responses else FakeResponse(200)

    def post(self, url, json, timeout):
        self.calls.append(("POST", url, json, timeout))
        if self.exceptions:
            raise self.exceptions.pop(0)
        return self.responses.pop(0) if self.responses else FakeResponse(200)


def make_client(session, outbox_directory):
    client = TrackerAPIClient.__new__(TrackerAPIClient)
    client.tracker_url = "http://tracker.test"
    client.timeout = 3
    client.service_name = "creator-callback-test"
    client.session = session
    client.tracker_outbox_directory = outbox_directory
    client._sleep = lambda _delay: None
    client.callback_max_retries = 3
    client.callback_retry_delays = CALLBACK_RETRY_DELAYS_SECONDS
    return client


def diagnostic(queue_id=3118, report_id="report-3118", attempt_nonce="nonce-3118"):
    return {
        "schema_version": SCHEMA_VERSION,
        "report_id": report_id,
        "attempt_nonce": attempt_nonce,
        "status": "failed",
        "job": {"queue_id": queue_id, "mod_id": 123, "mode": "master"},
        "mod": {
            "source_url": "https://cocrea.world/@author/mod",
            "layout": {"icon_count": 46, "slot_count": 9},
            "content": "should-not-be-forwarded",
        },
        "summary": {"failure_code": "remix_threshold_not_met"},
        "timing": {"duration_ms": 1000},
        "phases": {"statuses": {"normal_gameplay": "FAIL"}},
        "quality_gate": {
            "passed": False,
            "checks": [{"name": "normal_icons", "status": "FAIL"}],
            "evidence": {"raw_log": "x" * 1000},
        },
        "outputs": {
            "video_files": {"showcase": {"file_name": "showcase.mp4"}},
            "manifest_snapshot": {"stdout": "x" * 1000},
        },
        "logs": {
            "excerpt": "😀" * 100000,
            "line_count": 5000,
            "file_name": "job-3118.log",
            "relative_path": "logs/video_jobs/job-3118.log",
            "size_bytes": 4_000_000,
            "sha256": "a" * 64,
            "full_log_ref": {
                "path": r"C:\secrets\logs\video_jobs\job-3118.log",
                "filename": "job-3118.log",
                "exists": True,
                "size_bytes": 4_000_000,
                "sha256": "a" * 64,
            },
        },
        "workflow_report": {"traceback": "x" * 1_000_000},
    }


class TrackerCallbackDeliveryTests(unittest.TestCase):
    def test_terminal_payload_is_compact_and_keeps_required_sections(self):
        with tempfile.TemporaryDirectory() as outbox:
            session = FakeSession([FakeResponse(201)])
            client = make_client(session, outbox)
            with contextlib.redirect_stdout(io.StringIO()):
                result = client.update_job_status(
                    3118,
                    "failed",
                    details={"stage": "quality_gate"},
                    error_log="error\n" * 100000,
                    workflow_report={"stdout": "x" * 1_000_000},
                    diagnostic_report=diagnostic(),
                    log_ref=diagnostic()["logs"],
                    report_id="report-3118",
                    attempt_nonce="nonce-3118",
                    sequence=10,
                    final=True,
                )

            self.assertTrue(result)
            payload = session.calls[-1][2]
            encoded = json.dumps(payload, ensure_ascii=True).encode("utf-8")
            self.assertLessEqual(len(encoded), TERMINAL_MAX_REQUEST_BYTES)
            self.assertEqual(payload["status"], "failed")
            self.assertTrue(payload["final"])
            self.assertEqual(payload["sequence"], 10)
            self.assertEqual(payload["report_id"], "report-3118")
            self.assertEqual(payload["attempt_nonce"], "nonce-3118")
            report = payload["diagnostic_report"]
            for section in ("mod", "summary", "timing", "phases", "quality_gate", "outputs", "logs"):
                self.assertIn(section, report)
            self.assertNotIn("workflow_report", payload)
            serialized = json.dumps(payload, ensure_ascii=False)
            for forbidden in ("raw_log", "stdout", "stderr", "traceback", "content"):
                self.assertNotIn(forbidden, serialized)
            self.assertNotIn(r"C:\secrets\logs", serialized)
            self.assertLessEqual(
                len(report["logs"]["excerpt"].encode("utf-8")),
                MAX_LOG_EXCERPT_BYTES,
            )
            self.assertLessEqual(
                len(payload["error_log"].encode("utf-8")),
                MAX_ERROR_LOG_BYTES,
            )
            self.assertEqual(client.last_callback_metrics["http_status"], 201)
            self.assertEqual(client.last_callback_metrics["retry_count"], 0)

    def test_http_413_compacts_again_and_retries_with_same_identity_and_sequence(self):
        with tempfile.TemporaryDirectory() as outbox:
            session = FakeSession(
                [
                    FakeResponse(413, {"error_code": "payload_too_large", "message": "too large"}),
                    FakeResponse(202, {"accepted": True}),
                ]
            )
            client = make_client(session, outbox)
            with contextlib.redirect_stdout(io.StringIO()):
                result = client.update_job_status(
                    3118,
                    "completed",
                    diagnostic_report=diagnostic(),
                    report_id="report-3118",
                    attempt_nonce="nonce-3118",
                    sequence=10,
                    final=True,
                )

            self.assertTrue(result)
            self.assertEqual(len(session.calls), 2)
            first = session.calls[0][2]
            second = session.calls[1][2]
            self.assertEqual(first["report_id"], second["report_id"])
            self.assertEqual(first["attempt_nonce"], second["attempt_nonce"])
            self.assertEqual(first["sequence"], second["sequence"])
            self.assertGreaterEqual(
                len(first["diagnostic_report"]["logs"]["excerpt"].encode("utf-8")),
                len(second["diagnostic_report"]["logs"]["excerpt"].encode("utf-8")),
            )
            self.assertLessEqual(
                len(second["diagnostic_report"]["logs"]["excerpt"].encode("utf-8")),
                48 * 1024,
            )
            self.assertEqual(client.last_callback_metrics["http_status"], 202)
            self.assertEqual(client.last_callback_metrics["retry_count"], 1)
            self.assertFalse(os.path.exists(client._tracker_outbox_path(3118)))

    def test_network_error_retries_1_2_4_and_accepts_any_2xx(self):
        with tempfile.TemporaryDirectory() as outbox:
            session = FakeSession(
                [FakeResponse(204)],
                exceptions=[
                    requests.ConnectionError("connection reset"),
                    requests.Timeout("timeout"),
                    requests.ConnectionError("connection reset"),
                ],
            )
            client = make_client(session, outbox)
            delays = []
            client._sleep = delays.append
            with contextlib.redirect_stdout(io.StringIO()):
                result = client.update_job_status(
                    3118,
                    "failed",
                    diagnostic_report=diagnostic(),
                    report_id="report-3118",
                    attempt_nonce="nonce-3118",
                    sequence=10,
                    final=True,
                )
            self.assertTrue(result)
            self.assertEqual(delays, [1.0, 2.0, 4.0])
            self.assertEqual(len(session.calls), 4)
            self.assertEqual(client.last_callback_metrics["http_status"], 204)
            self.assertEqual(client.last_callback_metrics["retry_count"], 3)

    def test_failed_terminal_delivery_is_saved_and_job_3118_can_be_replayed(self):
        with tempfile.TemporaryDirectory() as outbox:
            failing_session = FakeSession([FakeResponse(422, {"error_code": "invalid"})])
            client = make_client(failing_session, outbox)
            with contextlib.redirect_stdout(io.StringIO()):
                result = client.update_job_status(
                    3118,
                    "cancelled",
                    diagnostic_report=diagnostic(),
                    report_id="report-3118",
                    attempt_nonce="nonce-3118",
                    sequence=10,
                    final=True,
                )
            self.assertFalse(result)
            outbox_path = client._tracker_outbox_path(3118)
            self.assertTrue(os.path.exists(outbox_path))
            archive_path = client._tracker_terminal_archive_path(3118)
            self.assertTrue(os.path.exists(archive_path))
            with open(outbox_path, "r", encoding="utf-8") as handle:
                stored = json.load(handle)
            self.assertNotIn("workflow_report", stored)
            self.assertNotIn(r"C:\secrets\logs", json.dumps(stored))
            # Exercise the retained-report fallback, not only the pending
            # outbox path.
            os.remove(outbox_path)

            replay_session = FakeSession([FakeResponse(200, {"accepted": True})])
            replay_client = make_client(replay_session, outbox)
            with contextlib.redirect_stdout(io.StringIO()):
                replayed = replay_client.resend_terminal_callback(3118)
            self.assertTrue(replayed)
            replay_payload = replay_session.calls[0][2]
            self.assertEqual(replay_payload["report_id"], "report-3118")
            self.assertEqual(replay_payload["attempt_nonce"], "nonce-3118")
            self.assertEqual(replay_payload["sequence"], 10)
            self.assertTrue(replay_payload["final"])
            self.assertFalse(os.path.exists(outbox_path))
            self.assertTrue(os.path.exists(archive_path))


if __name__ == "__main__":
    unittest.main()

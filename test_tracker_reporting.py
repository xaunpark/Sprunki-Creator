import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from tracker_reporting import (
    SCHEMA_VERSION,
    build_log_snapshot,
    build_tracker_diagnostic_report,
)
from workflow_quality import PASS, QualityGateResult, WorkflowReport


def passing_report() -> WorkflowReport:
    report = WorkflowReport(
        mode="master",
        runtime_state=PASS,
        runtime_verified=True,
        layout_icons=10,
        layout_slots=3,
        placement_before=0,
        placement_after=1,
        placement_verified=True,
        cleanup_verified=True,
        identity_status="verified",
        normal_icons_showcased=8,
        normal_icons_total=10,
        normal_remixes_played=2,
        normal_remixes_total=4,
    )
    report.apply_quality_gate(QualityGateResult.pass_result())
    report.evidence["retryAttempts"] = [
        {
            "operation": "runtime_entry",
            "attempt": 1,
            "status": "PASS",
            "retryable": False,
            "started_at": "2026-08-20T10:00:00",
            "ended_at": "2026-08-20T10:00:01",
            "duration_ms": 1000,
        }
    ]
    return report


class TrackerReportingTests(unittest.TestCase):
    def test_report_contains_structured_failure_and_full_input_snapshot(self):
        item = SimpleNamespace(
            source_url="https://www.cocrea.world/@author/mod",
            project_slug="author-mod",
            mode="master",
            json_data={
                "currentUrl": "https://www.cocrea.world/@author/mod",
                "pageTitle": "Test mod",
                "layout": {"icons": [], "slots": []},
                "gameSettings": {"hasHorror": False, "password": "hidden"},
                "start_time": datetime.now(),
            },
            failure_code="no_icons",
            error_message="Layout contains no playable icons.",
            start_time=datetime(2026, 8, 20, 10, 0, 0),
            end_time=datetime(2026, 8, 20, 10, 0, 2),
            output_files=[],
            recorded_identity_status="not_started",
            recorded_identity_reason=None,
            recorded_identity_retryable=False,
            recorded_identity_files=[],
        )
        report = WorkflowReport(mode="master")
        report.apply_quality_gate(
            QualityGateResult.failure(
                "no_icons",
                evidence={"layoutIcons": 0, "layoutSlots": 0},
                message="Layout contains no playable icons.",
            )
        )

        diagnostic = build_tracker_diagnostic_report(
            {"id": 3024, "mod_id": 44, "production_mode": "master"},
            item,
            report,
            report_id="report-3024",
            terminal_status="failed",
            manifest_data={
                "projectInfo": {"outputDirectory": tempfile.gettempdir()},
                "qualityGates": {"failureCode": "no_icons"},
            },
        )

        self.assertEqual(diagnostic["schema_version"], SCHEMA_VERSION)
        self.assertEqual(diagnostic["report_id"], "report-3024")
        self.assertEqual(diagnostic["summary"]["failure_code"], "no_icons")
        self.assertEqual(diagnostic["summary"]["blocking_stage"], "layout")
        self.assertFalse(diagnostic["quality_gate"]["passed"])
        self.assertEqual(diagnostic["mod"]["layout"]["icon_count"], 0)
        self.assertEqual(diagnostic["mod"]["game_settings"]["password"], "[REDACTED]")
        self.assertEqual(diagnostic["tracker_ui"]["badge"], "FAILED")
        json.dumps(diagnostic)

    def test_report_contains_placement_transition_retry_and_policy_coverage(self):
        item = SimpleNamespace(
            source_url="https://cocrea.world/@author/mod",
            project_slug="author-mod",
            mode="master",
            json_data={
                "currentUrl": "https://cocrea.world/@author/mod",
                "layout": {
                    "icons": [{"index": 0}],
                    "slots": [{"index": 0}],
                },
                "gameSettings": {"hasHorror": True},
            },
            failure_code=None,
            error_message=None,
            start_time=datetime(2026, 8, 20, 10, 0, 0),
            end_time=datetime(2026, 8, 20, 10, 0, 3),
            output_files=[],
            recorded_identity_status="verified",
            recorded_identity_reason=None,
            recorded_identity_retryable=False,
            recorded_identity_files=["identity.json"],
        )
        report = passing_report()
        report.evidence["placementTransactions"] = [
            {
                "status": "VERIFIED",
                "targetVerified": True,
                "before": 0,
                "after": 1,
                "cleanupVerified": True,
            }
        ]
        diagnostic = build_tracker_diagnostic_report(
            {"id": 3028, "production_mode": "master"},
            item,
            report,
            terminal_status="completed",
        )

        self.assertTrue(diagnostic["quality_gate"]["passed"])
        self.assertEqual(diagnostic["phases"]["placement"]["delta"], 1)
        self.assertEqual(diagnostic["phases"]["horror"]["status"], "NOT_CHECKED_BY_POLICY")
        self.assertEqual(diagnostic["attempts"][0]["duration_ms"], 1000)
        self.assertEqual(diagnostic["tracker_ui"]["badge"], "COMPLETED")

    def test_log_snapshot_has_reference_excerpt_and_redaction(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "job-1.log"
            path.write_text(
                "password=top-secret\n" + "important line\n" * 20,
                encoding="utf-8",
            )
            snapshot = build_log_snapshot(str(path), excerpt_chars=80)

        self.assertTrue(snapshot["full_log_ref"]["exists"])
        self.assertTrue(snapshot["full_log_ref"]["sha256"])
        self.assertTrue(snapshot["truncated"])
        self.assertNotIn("top-secret", snapshot["excerpt"])
        self.assertTrue(snapshot["redactions"])

    def test_tracker_status_payload_contains_diagnostic_contract(self):
        with contextlib.redirect_stdout(io.StringIO()):
            from creator_api_client import TrackerAPIClient

        class Response:
            status_code = 200
            text = "ok"

        class Session:
            def __init__(self):
                self.payload = None

            def put(self, url, json, timeout):
                self.payload = json
                return Response()

        client = TrackerAPIClient.__new__(TrackerAPIClient)
        client.tracker_url = "http://tracker.test"
        client.timeout = 3
        client.session = Session()

        with contextlib.redirect_stdout(io.StringIO()):
            result = client.update_job_status(
                3024,
                "failed",
                diagnostic_report={"quality_gate": {"passed": False}},
                manifest_ref={"path": "manifest.json"},
                log_ref={"full_log_ref": {"path": "job.log"}},
                report_id="report-1",
                final=True,
                sequence=7,
            )

        self.assertTrue(result)
        self.assertEqual(client.session.payload["schema_version"], SCHEMA_VERSION)
        self.assertTrue(client.session.payload["final"])
        self.assertEqual(client.session.payload["report_id"], "report-1")
        self.assertEqual(client.session.payload["sequence"], 7)
        self.assertIn("diagnostic_report", client.session.payload)

    def test_creator_finalizer_sends_one_authoritative_diagnostic_report(self):
        with contextlib.redirect_stdout(io.StringIO()):
            from creator_api_client import CreatorService
            from sprunki_ultimate_master import ProcessingItem

        class Logger:
            def info(self, *args, **kwargs):
                pass

            def warning(self, *args, **kwargs):
                pass

            def error(self, *args, **kwargs):
                pass

        class Capture:
            log_path = None

            def start(self):
                return True

            def stop(self):
                pass

            def snapshot(self):
                return {
                    "upload_status": "not_available",
                    "full_log_ref": {"exists": False, "path": None},
                    "excerpt": "",
                    "truncated": False,
                    "line_count": 0,
                    "redactions": [],
                }

        class API:
            service_name = "creator-test"

            def __init__(self):
                self.final_calls = []

            def update_job_status(self, *args, **kwargs):
                if kwargs.get("final"):
                    self.final_calls.append(kwargs)
                return True

            def update_job_progress(self, *args, **kwargs):
                return True

            def update_mod_production_status(self, *args, **kwargs):
                return True

        item = SimpleNamespace(
            source_url="https://cocrea.world/@author/mod",
            project_slug="author-mod",
            mode="master",
            json_data={
                "currentUrl": "https://cocrea.world/@author/mod",
                "layout": {"icons": [], "slots": []},
            },
            status="failed",
            failure_code="no_icons",
            error_message="No icons",
            output_files=[],
            start_time=datetime(2026, 8, 20, 10, 0, 0),
            end_time=None,
            workflow_report=WorkflowReport(mode="master"),
            recorded_identity_status="not_started",
            recorded_identity_reason=None,
            recorded_identity_retryable=False,
            recorded_identity_files=[],
        )
        item.workflow_report.apply_quality_gate(QualityGateResult.failure("no_icons"))

        class Manager:
            stop_event = None
            config = SimpleNamespace(max_retries=2)

            def _record_quality_failure(self, current_item, code, message, **kwargs):
                current_item.failure_code = code
                current_item.error_message = message
                current_item.status = "failed"
                current_item.workflow_report.apply_quality_gate(
                    QualityGateResult.failure(code, message=message)
                )
                return current_item.workflow_report

            def _generate_manifest_for_item(self, current_item):
                return {
                    "projectInfo": {"outputDirectory": tempfile.gettempdir()},
                    "processingSummary": {"overallStatus": "failure"},
                }

        service = CreatorService.__new__(CreatorService)
        service.logger = Logger()
        service.service_stop_event = __import__("threading").Event()
        service.processing_manager = Manager()
        service.api_client = API()
        service.performance_analyzer = SimpleNamespace(
            record_job_start=lambda *args, **kwargs: None,
            record_job_completion=lambda *args, **kwargs: None,
        )
        service._create_job_log_capture = lambda queue_id: Capture()
        service._convert_job_to_processing_item = lambda job: item
        service._process_with_smart_retry = lambda current_item, queue_id: False
        service._organize_output_files = lambda files: {"validation_summary": {}}
        service.obs_controller = None
        service.game_player = None
        service._watchdog_failures = {}
        service._watchdog_failures_lock = __import__("threading").Lock()
        service.config_manager = SimpleNamespace(get=lambda *args, **kwargs: 60)

        with contextlib.redirect_stdout(io.StringIO()):
            result = service._process_job({"id": 3024, "production_mode": "master"})

        self.assertFalse(result)
        self.assertEqual(len(service.api_client.final_calls), 1)
        payload = service.api_client.final_calls[0]
        self.assertTrue(payload["final"])
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["diagnostic_report"]["summary"]["failure_code"], "no_icons")
        self.assertIn("logs", payload["diagnostic_report"])


if __name__ == "__main__":
    unittest.main()

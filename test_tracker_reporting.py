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
    build_mini_tracker_diagnostic_report,
    build_tracker_diagnostic_report,
)
from workflow_quality import (
    PASS,
    QualityGateResult,
    WorkflowReport,
    evaluate_idle_timeout_partial_success,
    evaluate_workflow_report,
)


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
    def test_final_mini_remix_reports_cleanup_as_not_required(self):
        item = SimpleNamespace(
            source_url="https://cocrea.world/@author/mini-final",
            project_slug="mini-final",
            output_namespace="mini-final",
            json_data={
                "currentUrl": "https://cocrea.world/@author/mini-final",
                "layout": {
                    "icons": [{"index": 0}, {"index": 4}, {"index": 6}],
                    "slots": [{"index": index} for index in range(9)],
                },
            },
            failure_code=None,
            error_message=None,
            start_time=datetime(2026, 8, 20, 10, 0, 0),
            end_time=datetime(2026, 8, 20, 10, 0, 35),
            output_files=["mini-final.mp4"],
            attempt_outputs={},
            recorded_identity_status="not_started",
            recorded_identity_reason=None,
            recorded_identity_retryable=False,
            recorded_identity_files=[],
            watchdog_evidence={},
        )
        report = WorkflowReport(
            mode="mini_remix",
            runtime_state=PASS,
            runtime_verified=True,
            layout_icons=3,
            layout_slots=9,
            placement_before=0,
            placement_after=3,
            placement_verified=True,
            cleanup_verified=False,
        )
        report.evidence.update(
            {
                "selectedIconIndices": [0, 4, 6],
                "targetSlotIndices": [3, 4, 5],
                "placementMapping": [
                    {"iconIndex": 0, "slotIndex": 3},
                    {"iconIndex": 4, "slotIndex": 4},
                    {"iconIndex": 6, "slotIndex": 5},
                ],
                "placementTransactions": [
                    {"status": "VERIFIED", "targetVerified": True, "before": 0, "after": 1},
                    {"status": "VERIFIED", "targetVerified": True, "before": 1, "after": 2},
                    {"status": "VERIFIED", "targetVerified": True, "before": 2, "after": 3},
                ],
                "placementEvidence": [
                    {"requestedIconIndex": 0, "targetSlotIndex": 3},
                    {"requestedIconIndex": 4, "targetSlotIndex": 4},
                    {"requestedIconIndex": 6, "targetSlotIndex": 5},
                ],
                "listenSeconds": 30,
                "recordingRequestedSeconds": 30,
                "miniRemixRecordingCompleted": True,
                "recordingStatus": "PASS",
                "outputVideoValid": True,
                "outputFiles": ["mini-final.mp4"],
                "cleanupNotRequired": True,
                "cleanupPolicy": "single_final_mini_remix",
            }
        )
        evaluate_workflow_report(report, mode="mini_remix", require_identity=False)

        diagnostic = build_mini_tracker_diagnostic_report(
            {"id": 3042, "production_mode": "mini_remix"},
            item,
            report,
            output_files_payload={"mini_remix": ["mini-final.mp4"]},
            terminal_status="completed",
        )

        self.assertEqual(diagnostic["status"], "completed")
        self.assertTrue(diagnostic["quality_gate"]["passed"])
        cleanup_check = next(
            check for check in diagnostic["quality_gate"]["checks"]
            if check["name"] == "cleanup"
        )
        self.assertEqual(cleanup_check["status"], "PASS")
        self.assertTrue(cleanup_check["evidence"]["cleanup_not_required"])
        self.assertFalse(cleanup_check["evidence"]["re_run_attempted"])

    def test_idle_timeout_partial_acceptance_remains_completed_in_tracker_report(self):
        item = SimpleNamespace(
            source_url="https://cocrea.world/@author/idle-partial",
            project_slug="idle-partial",
            mode="master",
            json_data={
                "currentUrl": "https://cocrea.world/@author/idle-partial",
                "layout": {
                    "icons": [{"index": 0}],
                    "slots": [{"index": 0}],
                },
            },
            failure_code=None,
            error_message=None,
            start_time=datetime(2026, 8, 20, 10, 0, 0),
            end_time=datetime(2026, 8, 20, 10, 0, 3),
            output_files=["job-3040.mkv"],
            recorded_identity_status="not_started",
            recorded_identity_reason=None,
            recorded_identity_retryable=False,
            recorded_identity_files=[],
        )
        report = passing_report()
        report.cleanup_verified = False
        report.normal_remixes_played = 2
        report.normal_remixes_total = 4
        report.evidence.update(
            {
                "cleanupBoundaryId": "watchdog-boundary-1",
                "boardState": "UNKNOWN",
                "cleanupBoundaryCompleted": True,
                "cleanupDecisionComplete": True,
                "reRunAttempted": True,
                "reRunStatus": "FAILED",
                "reRunVerified": False,
                "contaminationCarriedForward": True,
                "contentSuccessWithUnverifiedCleanup": True,
            }
        )
        accepted = evaluate_idle_timeout_partial_success(
            report,
            timeout_reason="idle_timeout",
            output_recorded=True,
            cleanup_status="cleanup_incomplete",
        )

        self.assertIsNotNone(accepted)
        diagnostic = build_tracker_diagnostic_report(
            {"id": 3040, "production_mode": "master"},
            item,
            report,
            terminal_status="completed",
        )

        self.assertEqual(diagnostic["status"], "completed")
        self.assertTrue(diagnostic["quality_gate"]["passed"])
        cleanup_check = next(
            check for check in diagnostic["quality_gate"]["checks"]
            if check["name"] == "cleanup"
        )
        remix_check = next(
            check for check in diagnostic["quality_gate"]["checks"]
            if check["name"] == "normal_remixes"
        )
        self.assertEqual(cleanup_check["status"], "FAIL")
        self.assertTrue(cleanup_check["accepted_by_policy"])
        self.assertTrue(cleanup_check["evidence"]["accepted_by_policy"])
        self.assertNotIn("cleanup", diagnostic["quality_gate"]["failed_checks"])
        self.assertEqual(remix_check["evidence"]["succeeded"], 2)
        self.assertEqual(remix_check["evidence"]["total"], 4)

    def test_final_cleanup_policy_is_reported_as_pass_without_new_status(self):
        item = SimpleNamespace(
            source_url="https://cocrea.world/@author/final-cleanup",
            project_slug="final-cleanup",
            mode="master",
            json_data={
                "currentUrl": "https://cocrea.world/@author/final-cleanup",
                "layout": {
                    "icons": [{"index": 0}],
                    "slots": [{"index": 0}],
                },
            },
            failure_code=None,
            error_message=None,
            start_time=datetime(2026, 8, 20, 10, 0, 0),
            end_time=datetime(2026, 8, 20, 10, 0, 3),
            output_files=[],
            recorded_identity_status="verified",
            recorded_identity_reason=None,
            recorded_identity_retryable=False,
            recorded_identity_files=[],
        )
        report = passing_report()
        report.cleanup_verified = False
        report.evidence.update(
            {
                "finalCleanupOptional": True,
                "finalRemixContentComplete": True,
                "cleanupBoundaryId": "final-boundary-1",
                "boardState": "UNKNOWN",
                "cleanupBoundaryCompleted": True,
                "cleanupDecisionComplete": True,
                "reRunAttempted": True,
                "reRunStatus": "FAILED",
                "reRunVerified": False,
                "contaminationCarriedForward": True,
                "contentSuccessWithUnverifiedCleanup": True,
            }
        )

        diagnostic = build_tracker_diagnostic_report(
            {"id": 3031, "production_mode": "master"},
            item,
            report,
            terminal_status="completed",
        )

        cleanup_check = next(
            check for check in diagnostic["quality_gate"]["checks"]
            if check["name"] == "cleanup"
        )
        self.assertTrue(diagnostic["quality_gate"]["passed"])
        self.assertEqual(cleanup_check["status"], "FAIL")
        self.assertTrue(cleanup_check["accepted_by_policy"])
        self.assertTrue(cleanup_check["evidence"]["accepted_by_policy"])
        self.assertNotIn("cleanup", diagnostic["quality_gate"]["failed_checks"])
        self.assertEqual(diagnostic["tracker_ui"]["badge"], "COMPLETED")

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
        identity_check = next(
            check for check in diagnostic["quality_gate"]["checks"]
            if check["name"] == "recorded_identity"
        )
        self.assertFalse(identity_check["required"])
        self.assertNotIn("recorded_identity", diagnostic["quality_gate"]["failed_checks"])
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
        self.assertNotIn("artifacts", diagnostic["phases"])
        self.assertNotIn("artifacts", diagnostic["phases"]["statuses"])
        self.assertNotIn("artifact_validation", diagnostic)
        self.assertNotIn(
            "artifacts",
            {check["name"] for check in diagnostic["quality_gate"]["checks"]},
        )
        self.assertIn("video_files", diagnostic["outputs"])

    def test_core_pass_does_not_require_local_output_files(self):
        item = SimpleNamespace(
            source_url="https://cocrea.world/@author/moved-output",
            project_slug="moved-output",
            mode="master",
            json_data={
                "currentUrl": "https://cocrea.world/@author/moved-output",
                "layout": {
                    "icons": [{"index": 0}],
                    "slots": [{"index": 0}],
                },
            },
            failure_code=None,
            error_message=None,
            start_time=datetime(2026, 8, 20, 10, 0, 0),
            end_time=datetime(2026, 8, 20, 10, 0, 3),
            # This path is intentionally absent because Syncthing may have
            # moved the file before Creator builds the report.
            output_files=[r"G:\\Syncthing\\moved-output\\showcase.mp4"],
            recorded_identity_status="needs_review",
            recorded_identity_reason="fixture_review",
            recorded_identity_retryable=False,
            recorded_identity_files=[],
        )
        diagnostic = build_tracker_diagnostic_report(
            {"id": 3030, "production_mode": "master"},
            item,
            passing_report(),
            output_files_payload={},
            terminal_status="completed",
        )

        self.assertEqual(diagnostic["status"], "completed")
        self.assertTrue(diagnostic["quality_gate"]["passed"])
        self.assertEqual(diagnostic["outputs"]["video_files"], {})
        identity_check = next(
            check for check in diagnostic["quality_gate"]["checks"]
            if check["name"] == "recorded_identity"
        )
        self.assertEqual(identity_check["status"], "NEEDS_REVIEW")
        self.assertFalse(identity_check["passed"])
        self.assertFalse(identity_check["required"])
        self.assertEqual(diagnostic["phases"]["identity"]["status"], "NEEDS_REVIEW")
        self.assertEqual(diagnostic["phases"]["identity"]["identity_status"], "needs_review")
        self.assertEqual(diagnostic["phases"]["statuses"]["identity"], "NEEDS_REVIEW")
        self.assertNotIn("recorded_identity", diagnostic["quality_gate"]["failed_checks"])

    def test_recorded_identity_failed_is_non_blocking_but_remains_failed(self):
        item = SimpleNamespace(
            source_url="https://cocrea.world/@author/identity-failure",
            project_slug="identity-failure",
            mode="master",
            json_data={
                "currentUrl": "https://cocrea.world/@author/identity-failure",
                "layout": {"icons": [{"index": 0}], "slots": [{"index": 0}]},
            },
            failure_code=None,
            error_message=None,
            start_time=datetime(2026, 8, 20, 10, 0, 0),
            end_time=datetime(2026, 8, 20, 10, 0, 3),
            recorded_identity_status="failed",
            recorded_identity_reason="recorded_identity_json_invalid",
            recorded_identity_retryable=False,
            recorded_identity_files=[],
        )
        diagnostic = build_tracker_diagnostic_report(
            {"id": 3031, "production_mode": "master"},
            item,
            passing_report(),
            output_files_payload={},
            terminal_status="completed",
        )

        identity_check = next(
            check for check in diagnostic["quality_gate"]["checks"]
            if check["name"] == "recorded_identity"
        )
        self.assertEqual(diagnostic["status"], "completed")
        self.assertTrue(diagnostic["quality_gate"]["passed"])
        self.assertEqual(identity_check["status"], "FAIL")
        self.assertFalse(identity_check["passed"])
        self.assertFalse(identity_check["required"])
        self.assertNotIn("recorded_identity", diagnostic["quality_gate"]["failed_checks"])

    def test_legacy_artifact_quality_fields_are_not_forwarded(self):
        item = SimpleNamespace(
            source_url="https://cocrea.world/@author/legacy-fields",
            project_slug="legacy-fields",
            mode="master",
            json_data={
                "currentUrl": "https://cocrea.world/@author/legacy-fields",
                "layout": {"icons": [{"index": 0}], "slots": [{"index": 0}]},
            },
            failure_code=None,
            error_message=None,
            start_time=None,
            end_time=None,
            output_files=[],
            recorded_identity_status="verified",
            recorded_identity_reason=None,
            recorded_identity_retryable=False,
            recorded_identity_files=[],
        )
        legacy_report = passing_report().as_dict()
        legacy_report["artifact_validation"] = {"valid": ["old.mp4"]}
        legacy_report["phases"] = {
            "statuses": {"artifacts": "PASS"},
            "artifacts": {"status": "PASS"},
        }
        legacy_report["quality_gate"]["checks"] = [
            {"name": "artifacts", "status": "PASS", "required": True}
        ]
        legacy_report["quality_gate"]["failed_checks"] = ["artifacts"]

        diagnostic = build_tracker_diagnostic_report(
            {"id": 3032, "production_mode": "master"},
            item,
            legacy_report,
            output_files_payload={"master": []},
            terminal_status="completed",
        )

        serialized = json.dumps(diagnostic)
        self.assertNotIn('"artifact_validation"', serialized)
        self.assertNotIn('"artifacts"', serialized)
        self.assertIn("video_files", diagnostic["outputs"])

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
        self.assertNotIn("path", snapshot["full_log_ref"])
        self.assertEqual(snapshot["file_name"], "job-1.log")
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
        self.assertTrue(client.session.payload["attempt_nonce"])
        self.assertEqual(client.session.payload["sequence"], 7)
        self.assertIn("diagnostic_report", client.session.payload)
        self.assertEqual(
            client.session.payload["diagnostic_report"]["attempt_nonce"],
            client.session.payload["attempt_nonce"],
        )

    def test_tracker_attempt_callbacks_share_identity_and_sequence(self):
        with contextlib.redirect_stdout(io.StringIO()):
            from creator_api_client import TrackerAPIClient

        class Response:
            status_code = 200
            text = '{"accepted":true}'

        class Session:
            def __init__(self):
                self.calls = []

            def put(self, url, json, timeout):
                self.calls.append(("PUT", url, json))
                return Response()

            def post(self, url, json, timeout):
                self.calls.append(("POST", url, json))
                return Response()

        client = TrackerAPIClient.__new__(TrackerAPIClient)
        client.tracker_url = "http://tracker.test"
        client.timeout = 3
        client.service_name = "creator-contract-test"
        client.session = Session()

        queue_id = 900001
        report_id = "report-900001"
        attempt_nonce = "nonce-900001"
        client.bind_job_report(queue_id, report_id, attempt_nonce)

        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(
                client.update_job_status(
                    queue_id,
                    "processing",
                    details={"stage": "starting"},
                    report_id=report_id,
                    attempt_nonce=attempt_nonce,
                    sequence=0,
                    stage="video_processing",
                    final=False,
                )
            )
            self.assertTrue(
                client.update_job_progress(
                    queue_id,
                    "video_processing",
                    "starting",
                    50,
                    "active",
                )
            )
            self.assertTrue(
                client.update_job_status(
                    queue_id,
                    "failed",
                    report_id=report_id,
                    attempt_nonce=attempt_nonce,
                    diagnostic_report={
                        "schema_version": "wrong",
                        "report_id": "wrong",
                        "attempt_nonce": "wrong",
                        "status": "failed",
                        "job": {},
                        "mod": {},
                        "summary": {},
                        "timing": {},
                        "phases": {"statuses": {}},
                        "quality_gate": {"passed": False, "checks": []},
                        "outputs": {},
                        "logs": {},
                    },
                    final=True,
                )
            )

        self.assertEqual([call[2]["sequence"] for call in client.session.calls], [0, 1, 2])
        self.assertEqual(
            {call[2]["report_id"] for call in client.session.calls},
            {report_id},
        )
        self.assertEqual(
            {call[2]["attempt_nonce"] for call in client.session.calls},
            {attempt_nonce},
        )
        self.assertFalse(client.session.calls[0][2]["final"])
        self.assertFalse(client.session.calls[1][2]["final"])
        terminal_payload = client.session.calls[2][2]
        self.assertTrue(terminal_payload["final"])
        self.assertEqual(terminal_payload["schema_version"], SCHEMA_VERSION)
        self.assertEqual(terminal_payload["worker_id"], "creator-contract-test")
        self.assertEqual(
            terminal_payload["diagnostic_report"]["report_id"], report_id
        )
        self.assertEqual(
            terminal_payload["diagnostic_report"]["attempt_nonce"], attempt_nonce
        )
        self.assertTrue(all("/api/mods/" not in call[1] for call in client.session.calls))

    def test_creator_mode_aliases_normalize_before_dispatch(self):
        with contextlib.redirect_stdout(io.StringIO()):
            from creator_api_client import CreatorService
            from sprunki_ultimate_master import ProcessingItem

        service = CreatorService.__new__(CreatorService)
        cases = (
            ("master", "master"),
            ("normal", "master"),
            ("individual", "individual"),
            ("both", "both"),
        )
        for requested, expected in cases:
            item = service._convert_job_to_processing_item(
                {
                    "id": 4000,
                    "production_mode": requested,
                    "mod_game_url": "https://cocrea.world/@author/mod",
                }
            )
            self.assertEqual(item.mode, expected)
            self.assertEqual(item.requested_production_mode, requested)

        class API:
            def update_job_progress(self, *args, **kwargs):
                return True

        class Manager:
            def __init__(self):
                self.dispatched = []

            def _process_master_mode(self, item):
                self.dispatched.append(item.mode)
                return True

            def _process_individual_mode(self, item):
                self.dispatched.append(item.mode)
                return True

            def _process_both_mode_enhanced(self, item):
                self.dispatched.append(item.mode)
                return True

        manager = Manager()
        service.api_client = API()
        service.processing_manager = manager
        service.performance_analyzer = SimpleNamespace(
            record_job_progress=lambda *args, **kwargs: None
        )
        normal_item = SimpleNamespace(
            mode="normal",
            requested_production_mode="normal",
            project_slug="normal-alias",
        )
        self.assertTrue(service._process_with_progress_callbacks(normal_item, 4001))
        self.assertEqual(manager.dispatched, ["master"])

    def test_diagnostic_keeps_received_mode_and_reports_effective_master(self):
        item = SimpleNamespace(
            source_url="https://cocrea.world/@author/normal-alias",
            project_slug="normal-alias",
            mode="master",
            requested_production_mode="normal",
            json_data={
                "currentUrl": "https://cocrea.world/@author/normal-alias",
                "layout": {"icons": [{"index": 0}], "slots": [{"index": 0}]},
            },
            failure_code=None,
            error_message=None,
            start_time=datetime(2026, 8, 20, 10, 0, 0),
            end_time=datetime(2026, 8, 20, 10, 0, 3),
            recorded_identity_status="verified",
            recorded_identity_reason=None,
            recorded_identity_retryable=False,
            recorded_identity_files=[],
        )
        diagnostic = build_tracker_diagnostic_report(
            {"id": 4002, "production_mode": "normal"},
            item,
            passing_report(),
            terminal_status="completed",
        )

        self.assertEqual(diagnostic["job"]["mode"], "normal")
        self.assertEqual(diagnostic["job"]["effective_mode"], "master")
        self.assertTrue(diagnostic["quality_gate"]["passed"])

    def test_skip_is_internal_and_never_serialized_as_tracker_mode(self):
        item = SimpleNamespace(
            source_url="https://cocrea.world/@author/skipped",
            project_slug="skipped",
            mode="skip",
            requested_production_mode="skip",
            json_data={},
            failure_code="skipped_by_policy",
            error_message="Skipped by local policy.",
            recorded_identity_status="not_started",
            recorded_identity_reason=None,
            recorded_identity_retryable=False,
            recorded_identity_files=[],
        )
        diagnostic = build_tracker_diagnostic_report(
            {"id": 4003, "production_mode": "skip"},
            item,
            None,
            terminal_status="failed",
        )

        self.assertNotEqual(diagnostic["job"]["mode"], "skip")
        self.assertEqual(diagnostic["job"]["mode"], "unknown")
        self.assertEqual(diagnostic["job"]["effective_mode"], "unknown")
        self.assertEqual(diagnostic["job"]["internal_action"], "skip")
        self.assertNotIn("production_mode", json.dumps(diagnostic))

    def test_placement_failure_projects_failed_check_without_workflow_report(self):
        item = SimpleNamespace(
            source_url="https://cocrea.world/@author/placement-failure-no-report",
            project_slug="placement-failure-no-report",
            mode="master",
            json_data={},
            failure_code="placement_not_verified",
            error_message="placement_not_verified",
            recorded_identity_status="not_started",
            recorded_identity_reason=None,
            recorded_identity_retryable=False,
            recorded_identity_files=[],
        )
        diagnostic = build_tracker_diagnostic_report(
            {"id": 4004, "production_mode": "master"},
            item,
            None,
            terminal_status="failed",
        )

        placement = next(
            check for check in diagnostic["quality_gate"]["checks"]
            if check["name"] == "placement"
        )
        self.assertEqual(placement["status"], "FAIL")
        self.assertIn("placement", diagnostic["quality_gate"]["failed_checks"])
        self.assertEqual(diagnostic["summary"]["failure_code"], "placement_not_verified")

    def test_callback_422_logs_full_response_body(self):
        with contextlib.redirect_stdout(io.StringIO()):
            from creator_api_client import TrackerAPIClient

        class Response:
            status_code = 422
            text = '{"error_code":"attempt_nonce_required","message":"Terminal status requires attempt_nonce."}'

        class Session:
            def put(self, url, json, timeout):
                return Response()

        client = TrackerAPIClient.__new__(TrackerAPIClient)
        client.tracker_url = "http://tracker.test"
        client.timeout = 3
        client.service_name = "creator-contract-test"
        client.session = Session()
        client.bind_job_report(900002, "report-900002", "nonce-900002")

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = client.update_job_status(
                900002,
                "processing",
                sequence=0,
                final=False,
                stage="video_processing",
            )

        self.assertFalse(result)
        self.assertIn("HTTP 422", output.getvalue())
        self.assertIn("attempt_nonce_required", output.getvalue())
        self.assertIn("Terminal status requires attempt_nonce.", output.getvalue())

    def test_completed_report_is_downgraded_when_placement_fails(self):
        item = SimpleNamespace(
            source_url="https://cocrea.world/@author/placement-failure",
            project_slug="placement-failure",
            mode="individual",
            json_data={
                "currentUrl": "https://cocrea.world/@author/placement-failure",
                "layout": {"icons": [{"index": 0}], "slots": [{"index": 0}]},
            },
            failure_code=None,
            error_message="placement_not_verified",
            start_time=datetime(2026, 8, 20, 10, 0, 0),
            end_time=datetime(2026, 8, 20, 10, 0, 2),
            recorded_identity_status="unavailable",
            recorded_identity_reason="missing_timestamps",
            recorded_identity_retryable=False,
            recorded_identity_files=[],
        )
        report = passing_report()
        report.placement_verified = False

        diagnostic = build_tracker_diagnostic_report(
            {"id": 3079, "mod_id": 196711, "production_mode": "master"},
            item,
            report,
            report_id="report-3079",
            attempt_nonce="nonce-3079",
            terminal_status="completed",
        )

        self.assertEqual(diagnostic["status"], "failed")
        self.assertEqual(diagnostic["summary"]["status"], "failed")
        self.assertEqual(diagnostic["summary"]["failure_code"], "placement_not_verified")
        self.assertFalse(diagnostic["quality_gate"]["passed"])
        self.assertEqual(diagnostic["job"]["mode"], "master")
        placement_check = next(
            check for check in diagnostic["quality_gate"]["checks"]
            if check["name"] == "placement"
        )
        self.assertIsNone(placement_check["failure_code"])
        self.assertEqual(placement_check["evidence"]["reason"], "placement_not_verified")
        self.assertEqual(diagnostic["attempt_nonce"], "nonce-3079")
        for section in ("mod", "summary", "timing", "phases", "quality_gate", "outputs", "logs"):
            self.assertIn(section, diagnostic)

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
                self.legacy_calls = 0

            def update_job_status(self, *args, **kwargs):
                if kwargs.get("final"):
                    self.final_calls.append(kwargs)
                return True

            def update_job_progress(self, *args, **kwargs):
                return True

            def update_mod_production_status(self, *args, **kwargs):
                self.legacy_calls += 1
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
        self.assertTrue(payload["attempt_nonce"])
        self.assertEqual(
            payload["diagnostic_report"]["attempt_nonce"], payload["attempt_nonce"]
        )
        self.assertEqual(payload["diagnostic_report"]["summary"]["failure_code"], "no_icons")
        self.assertIn("logs", payload["diagnostic_report"])
        self.assertEqual(service.api_client.legacy_calls, 0)

    def test_creator_completion_ignores_moved_output_transport_failure(self):
        with contextlib.redirect_stdout(io.StringIO()):
            from creator_api_client import CreatorService

        import threading

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

        class Manager:
            config = SimpleNamespace(max_retries=2)

            def _generate_manifest_for_item(self, current_item):
                raise OSError("manifest moved with output")

        item = SimpleNamespace(
            source_url="https://cocrea.world/@author/moved-output",
            project_slug="moved-output",
            mode="master",
            json_data={
                "currentUrl": "https://cocrea.world/@author/moved-output",
                "layout": {
                    "icons": [{"index": 0}],
                    "slots": [{"index": 0}],
                },
            },
            status="processing",
            failure_code=None,
            error_message=None,
            output_files=[r"G:\\Syncthing\\moved-output\\showcase.mp4"],
            start_time=datetime(2026, 8, 20, 10, 0, 0),
            end_time=None,
            workflow_report=passing_report(),
            recorded_identity_status="needs_review",
            recorded_identity_reason="review_required",
            recorded_identity_retryable=False,
            recorded_identity_files=[],
        )

        service = CreatorService.__new__(CreatorService)
        service.logger = Logger()
        service.service_stop_event = threading.Event()
        service.processing_manager = Manager()
        service.api_client = API()
        service.performance_analyzer = SimpleNamespace(
            record_job_start=lambda *args, **kwargs: None,
            record_job_completion=lambda *args, **kwargs: None,
        )
        service._create_job_log_capture = lambda queue_id: Capture()
        service._convert_job_to_processing_item = lambda job: item
        def process_success(current_item, queue_id):
            current_item.status = "completed"
            return True

        service._process_with_smart_retry = process_success
        service._organize_output_files = lambda files: (_ for _ in ()).throw(
            OSError("Syncthing moved output before inspection")
        )
        service.obs_controller = None
        service.game_player = None
        service._watchdog_failures = {}
        service._watchdog_failures_lock = threading.Lock()
        service.config_manager = SimpleNamespace(get=lambda *args, **kwargs: 60)

        with contextlib.redirect_stdout(io.StringIO()):
            result = service._process_job({"id": 3031, "production_mode": "master"})

        self.assertTrue(result)
        self.assertEqual(item.status, "completed")
        self.assertEqual(len(service.api_client.final_calls), 1)
        payload = service.api_client.final_calls[0]
        self.assertEqual(payload["status"], "completed")
        self.assertTrue(payload["diagnostic_report"]["quality_gate"]["passed"])
        self.assertEqual(payload["diagnostic_report"]["outputs"]["video_files"], {})

    def test_creator_finalizer_uses_diagnostic_status_when_gate_is_stale(self):
        with contextlib.redirect_stdout(io.StringIO()):
            from creator_api_client import CreatorService

        import threading

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

        class Manager:
            config = SimpleNamespace(max_retries=2)

            def _generate_manifest_for_item(self, current_item):
                return {"projectInfo": {"outputDirectory": tempfile.gettempdir()}}

        item = SimpleNamespace(
            source_url="https://cocrea.world/@author/stale-gate",
            project_slug="stale-gate",
            mode="master",
            json_data={
                "currentUrl": "https://cocrea.world/@author/stale-gate",
                # The workflow report below is stale and says PASS, but the
                # diagnostic contract must still reject this empty layout.
                "layout": {"icons": [], "slots": []},
            },
            status="processing",
            failure_code=None,
            error_message=None,
            output_files=[],
            start_time=datetime(2026, 8, 20, 10, 0, 0),
            end_time=None,
            workflow_report=passing_report(),
            recorded_identity_status="verified",
            recorded_identity_reason=None,
            recorded_identity_retryable=False,
            recorded_identity_files=[],
        )

        service = CreatorService.__new__(CreatorService)
        service.logger = Logger()
        service.service_stop_event = threading.Event()
        service.processing_manager = Manager()
        service.api_client = API()
        service.performance_analyzer = SimpleNamespace(
            record_job_start=lambda *args, **kwargs: None,
            record_job_completion=lambda *args, **kwargs: None,
        )
        service._create_job_log_capture = lambda queue_id: Capture()
        service._convert_job_to_processing_item = lambda job: item

        def process_success(current_item, queue_id):
            current_item.status = "completed"
            return True

        service._process_with_smart_retry = process_success
        service._organize_output_files = lambda files: {}
        service.obs_controller = None
        service.game_player = None
        service.config_manager = SimpleNamespace(get=lambda *args, **kwargs: 60)

        with contextlib.redirect_stdout(io.StringIO()):
            result = service._process_job({"id": 3032, "production_mode": "master"})

        self.assertFalse(result)
        self.assertEqual(len(service.api_client.final_calls), 1)
        payload = service.api_client.final_calls[0]
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["diagnostic_report"]["status"], "failed")
        self.assertFalse(payload["diagnostic_report"]["quality_gate"]["passed"])
        self.assertEqual(payload["diagnostic_report"]["outputs"]["video_files"], {})


if __name__ == "__main__":
    unittest.main()

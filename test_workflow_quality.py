import tempfile
import unittest

from workflow_quality import (
    PASS,
    QualityGateResult,
    RemixResult,
    WorkflowReport,
    evaluate_workflow_report,
    failure_is_retryable,
    validate_playable_layout,
)


def verified_report(mode="master"):
    return WorkflowReport(
        mode=mode,
        runtime_state=PASS,
        runtime_verified=True,
        layout_icons=10,
        layout_slots=1,
        placement_before=0,
        placement_after=1,
        placement_verified=True,
        cleanup_verified=True,
        identity_status="verified",
        normal_icons_showcased=8,
        normal_icons_total=10,
        normal_remixes_played=2,
        normal_remixes_total=3,
    )


class WorkflowQualityTests(unittest.TestCase):
    def test_empty_layout_is_rejected(self):
        result = validate_playable_layout({"layout": {"icons": [], "slots": [{"index": 0}]}})
        self.assertFalse(result.passed)
        self.assertEqual(result.failure_code, "no_icons")

    def test_empty_slots_are_rejected(self):
        result = validate_playable_layout({"layout": {"icons": [{"index": 0}], "slots": []}})
        self.assertFalse(result.passed)
        self.assertEqual(result.failure_code, "no_slots")

    def test_missing_playable_entry_is_rejected(self):
        result = validate_playable_layout(
            {"layout": {"icons": [{"index": 0}], "slots": [{"index": 0}]}}
        )
        self.assertFalse(result.passed)
        self.assertEqual(result.failure_code, "manual_json_invalid")

    def test_remix_zero_zero_is_false_even_when_legacy_unpacked(self):
        result = RemixResult()
        self.assertFalse(bool(result))
        succeeded, attempted = result
        self.assertEqual((succeeded, attempted), (0, 0))
        self.assertFalse(result.meets(0.5))

    def test_icon_showcase_exception_preserves_completed_icon_count(self):
        import threading

        from sprunki_game_player import SprunkiGamePlayer

        player = SprunkiGamePlayer.__new__(SprunkiGamePlayer)
        player.config = {
            "layout": {
                "icons": [{"index": 0}, {"index": 1}],
                "slots": [{"index": 0}],
            },
            "gameSettings": {},
        }
        player.stop_event = threading.Event()
        player.character_identity_map_written = True
        calls = []

        def fake_showcase(icon, mode, listen_duration, progress_callback):
            calls.append(icon["index"])
            if len(calls) == 1:
                return True
            raise RuntimeError("late cleanup read timeout")

        player.showcase_single_icon_corrected = fake_showcase

        result = player.showcase_all_icons_corrected("Normal")

        self.assertEqual(result, (1, 2))
        self.assertEqual(calls, [0, 1])

    def test_master_thresholds_and_coverage_pass(self):
        report = verified_report()
        result = evaluate_workflow_report(report, require_identity=True)
        self.assertTrue(result.passed)
        self.assertEqual(result.coverage["normal"], "verified")
        self.assertEqual(result.coverage["horror"], "not_checked_by_policy")

    def test_zero_to_zero_without_placement_is_rejected(self):
        report = verified_report()
        report.placement_before = 0
        report.placement_after = 0
        report.placement_verified = False
        result = evaluate_workflow_report(report, require_identity=False)
        self.assertFalse(result.passed)
        self.assertEqual(result.failure_code, "placement_not_verified")

    def test_cleanup_success_without_any_placement_is_rejected(self):
        report = verified_report()
        report.placement_verified = False
        report.cleanup_verified = True
        result = evaluate_workflow_report(report, require_identity=False)
        self.assertFalse(result.passed)
        self.assertEqual(result.failure_code, "placement_not_verified")

    def test_claimed_zero_to_zero_transaction_is_rejected(self):
        report = verified_report()
        report.evidence["placementTransactions"] = [
            {
                "status": "VERIFIED",
                "targetVerified": True,
                "before": 0,
                "after": 0,
                "cleanupVerified": True,
            }
        ]
        report.placement_before = 0
        report.placement_after = 0
        result = evaluate_workflow_report(report, require_identity=False)
        self.assertFalse(result.passed)
        self.assertEqual(result.failure_code, "placement_not_verified")

    def test_soft_aggregate_transaction_can_pass_with_clean_board(self):
        report = verified_report()
        report.placement_verified = True
        report.evidence["placementTransactions"] = [
            {
                "status": "VERIFIED",
                "targetVerified": False,
                "softVerified": True,
                "verificationConfidence": "soft",
                "before": 0,
                "after": 1,
                "cleanupVerified": True,
            }
        ]

        result = evaluate_workflow_report(report, require_identity=False)

        self.assertTrue(result.passed)
        self.assertEqual(result.coverage["normal"], "verified")

    def test_zero_remixes_is_rejected_instead_of_defaulting_to_one(self):
        report = verified_report()
        report.normal_remixes_played = 0
        report.normal_remixes_total = 0
        result = evaluate_workflow_report(report, require_identity=False)
        self.assertFalse(result.passed)
        self.assertEqual(result.failure_code, "empty_remix")

    def test_identity_missing_is_non_blocking_but_remains_evidence(self):
        report = verified_report()
        report.identity_status = "not_started"
        result = evaluate_workflow_report(report, require_identity=True)
        self.assertTrue(result.passed)
        self.assertIsNone(result.failure_code)
        self.assertEqual(report.identity_status, "not_started")
        self.assertFalse(result.evidence["identityVerified"])

    def test_identity_needs_review_is_non_blocking_but_remains_evidence(self):
        report = verified_report()
        report.identity_status = "needs_review"
        result = evaluate_workflow_report(report, require_identity=True)
        self.assertTrue(result.passed)
        self.assertIsNone(result.failure_code)
        self.assertEqual(report.identity_status, "needs_review")
        self.assertFalse(result.evidence["identityVerified"])

    def test_individual_threshold_is_strict(self):
        report = verified_report("individual")
        report.normal_remixes_played = 1
        result = evaluate_workflow_report(report, mode="individual", require_identity=False)
        self.assertFalse(result.passed)
        self.assertEqual(result.failure_code, "remix_threshold_not_met")

    def test_unknown_runtime_is_not_success_and_is_retryable(self):
        report = verified_report()
        report.runtime_state = "UNKNOWN"
        report.runtime_verified = False
        result = evaluate_workflow_report(report, require_identity=False)
        self.assertFalse(result.passed)
        self.assertEqual(result.status, "UNKNOWN")
        self.assertTrue(result.retryable)

    def test_job_3024_like_non_playable_runtime_cannot_complete(self):
        report = verified_report("master")
        report.runtime_state = "UNKNOWN"
        report.runtime_verified = False
        result = evaluate_workflow_report(report, mode="master", require_identity=False)
        self.assertFalse(result.passed)
        self.assertEqual(result.failure_code, "runtime_verification_unknown")
        self.assertFalse(report.was_successful)

    def test_retry_policy_only_retries_transient_codes(self):
        self.assertTrue(failure_is_retryable("runtime_verification_unknown"))
        self.assertFalse(failure_is_retryable("no_icons"))
        self.assertTrue(failure_is_retryable("recorded_identity_missing"))

    def test_both_requires_both_phase_reports(self):
        report = verified_report("both")
        report.phase_reports = {
            "master": {"was_successful": True, "quality_gate": {"passed": True}},
            "individual": None,
        }
        result = evaluate_workflow_report(report, mode="both", require_identity=False)
        self.assertFalse(result.passed)
        self.assertEqual(result.failure_code, "phase_skipped")

    def test_empty_or_moved_output_files_do_not_participate_in_core_gate(self):
        report = verified_report()
        result = evaluate_workflow_report(report, require_identity=False)
        self.assertTrue(result.passed)
        self.assertFalse(hasattr(report, "artifact_validation"))
        self.assertNotIn("artifact_missing", result.evidence)

    def test_failure_report_is_persisted_for_manifest_generation(self):
        import tempfile
        from types import SimpleNamespace

        from sprunki_ultimate_master import ProcessingItem, UnifiedProcessingManager

        class Logger:
            def info(self, *args, **kwargs):
                pass

            def warning(self, *args, **kwargs):
                pass

            def error(self, *args, **kwargs):
                pass

        with tempfile.TemporaryDirectory() as temp:
            manager = UnifiedProcessingManager.__new__(UnifiedProcessingManager)
            manager.config = SimpleNamespace(output_base_dir=temp)
            manager.logger = Logger()
            item = ProcessingItem(
                source_url="https://example.invalid/mod",
                mode="both",
                project_slug="failed-fixture",
            )
            manager._record_quality_failure(
                item,
                "no_icons",
                "Fixture has no icons.",
                evidence={"fixture": "Job 3024"},
            )
            manifest = manager._generate_manifest_for_item(item)
            self.assertEqual(item.status, "failed")
            self.assertIsNotNone(manifest)
            self.assertEqual(
                manifest["qualityGates"]["result"]["failure_code"],
                "no_icons",
            )

    def test_remix_only_entrypoint_rejects_empty_library(self):
        import threading

        from placement_verifier import OperationResult, OperationStatus
        from sprunki_game_player import SprunkiGamePlayer

        player = SprunkiGamePlayer.__new__(SprunkiGamePlayer)
        player.config = {
            "layout": {
                "icons": [{"index": 0}],
                "slots": [{"index": 0}],
            }
        }
        player.remix_library = []
        player.entry_strategy = {"selected": None, "tested": [], "verification": "PASS"}
        player.characters_available = False
        player.putted_chars_available = False
        player.should_stop = False
        player.stop_event = threading.Event()
        player.obs_controller = None
        player._workflow_placement_transactions = []
        player._run_cleanup_once = lambda **kwargs: OperationResult(
            OperationStatus.ALREADY_EMPTY,
            direction="cleanup",
            message="test_cleanup",
        )

        self.assertFalse(player.execute_remix_only_workflow_corrected())
        self.assertEqual(player.last_workflow_report.failure_code, "empty_remix")

    def test_whole_remix_retry_cleans_partial_board_before_replay(self):
        import contextlib
        import io
        import threading

        from sprunki_game_player import SprunkiGamePlayer

        player = SprunkiGamePlayer.__new__(SprunkiGamePlayer)
        player.config = {
            "layout": {
                "icons": [{"index": 0}, {"index": 1}],
                "slots": [{"index": 0}, {"index": 1}],
            }
        }
        player.remix_library = [
            {"name": "Retry fixture", "icons": [0, 1], "pattern": "fixture"}
        ]
        player.remix_max_attempts = 2
        player.remix_retry_delay_seconds = 0
        player.stop_event = threading.Event()
        player.should_stop = False
        player.obs_controller = None
        player._workflow_placement_transactions = []

        play_calls = []
        cleanup_calls = []

        def fake_play(remix, mode, listen_duration, progress_callback):
            play_calls.append(len(cleanup_calls))
            if len(play_calls) == 1:
                player._workflow_placement_transactions.append(
                    {
                        "status": "VERIFIED",
                        "targetVerified": True,
                        "cleanupVerified": False,
                    }
                )
                return False
            return True

        def fake_cleanup(**kwargs):
            cleanup_calls.append(kwargs["context"])
            return 1, 1, []

        player.play_complete_remix_corrected = fake_play
        player.smart_batch_return_all_slots_with_mode = fake_cleanup
        player.validate_workflow_completion = lambda: (True, [], [])

        with contextlib.redirect_stdout(io.StringIO()):
            result = player.play_all_remixes_corrected()

        self.assertEqual(play_calls, [0, 1])
        self.assertEqual(len(cleanup_calls), 1)
        self.assertEqual(result.attempted, 1)
        self.assertEqual(result.succeeded, 1)
        self.assertEqual(result.retry_attempts, 1)
        self.assertEqual(len(result.attempt_details), 1)
        self.assertEqual(len(result.attempt_details[0]["attempts"]), 2)
        self.assertTrue(
            player._workflow_placement_transactions[0]["cleanupVerified"]
        )

    def test_remix_retry_stops_when_partial_cleanup_is_not_verified(self):
        import contextlib
        import io
        import threading

        from sprunki_game_player import SprunkiGamePlayer

        player = SprunkiGamePlayer.__new__(SprunkiGamePlayer)
        player.config = {
            "layout": {
                "icons": [{"index": 0}],
                "slots": [{"index": 0}],
            }
        }
        player.remix_library = [
            {"name": "Dirty-board fixture", "icons": [0], "pattern": "fixture"},
            {"name": "Must-not-start", "icons": [0], "pattern": "fixture"},
        ]
        player.remix_max_attempts = 2
        player.remix_retry_delay_seconds = 0
        player.stop_event = threading.Event()
        player.should_stop = False
        player.obs_controller = None
        player._workflow_placement_transactions = []

        play_calls = []

        def fake_play(remix, mode, listen_duration, progress_callback):
            play_calls.append(remix["name"])
            return False

        player.play_complete_remix_corrected = fake_play
        player.smart_batch_return_all_slots_with_mode = lambda **kwargs: (
            0,
            1,
            ["cleanup_failed"],
        )
        player.validate_workflow_completion = lambda: (
            False,
            ["Characters count = 1"],
            ["Run cleanup"],
        )

        with contextlib.redirect_stdout(io.StringIO()):
            result = player.play_all_remixes_corrected()

        self.assertEqual(play_calls, ["Dirty-board fixture"])
        self.assertEqual(result.attempted, 1)
        self.assertEqual(result.succeeded, 0)
        self.assertEqual(result.skipped, 1)
        self.assertEqual(result.failure_code, "remix_threshold_not_met")

    def test_creator_job_entrypoint_returns_false_when_final_gate_fails(self):
        import contextlib
        import io
        import threading
        from types import SimpleNamespace

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

        class LogCapture:
            def start(self):
                return True

            def stop(self):
                pass

        class API:
            def __init__(self):
                self.statuses = []

            def update_job_status(self, *args, **kwargs):
                self.statuses.append(kwargs.get("status", args[1] if len(args) > 1 else None))
                return True

            def update_job_progress(self, *args, **kwargs):
                pass

            def update_mod_production_status(self, *args, **kwargs):
                pass

        class Manager:
            def __init__(self, item):
                self.stop_event = threading.Event()
                self.item = item

            def _generate_manifest_for_item(self, item):
                return {"overallStatus": "failure"}

            def _record_quality_failure(self, *args, **kwargs):
                return self.item.workflow_report

        item = ProcessingItem(
            source_url="https://example.invalid/mod",
            mode="master",
            project_slug="gate-fixture",
            status="processing",
        )
        item.workflow_report = WorkflowReport(mode="master")
        item.workflow_report.apply_quality_gate(
            QualityGateResult.failure("placement_not_verified")
        )

        service = CreatorService.__new__(CreatorService)
        service.logger = Logger()
        service.service_stop_event = threading.Event()
        service.config_manager = SimpleNamespace(get=lambda *args, **kwargs: 60)
        service.processing_manager = Manager(item)
        service.api_client = API()
        service.performance_analyzer = SimpleNamespace(
            record_job_start=lambda *args, **kwargs: None,
            record_job_completion=lambda *args, **kwargs: setattr(
                service, "completion_success", args[1]
            ),
        )
        service._create_job_log_capture = lambda queue_id: LogCapture()
        service._convert_job_to_processing_item = lambda job: item
        service._process_with_smart_retry = lambda current_item, queue_id: True
        service._organize_output_files = lambda files: {}
        service.obs_controller = None
        service.game_player = None

        with contextlib.redirect_stdout(io.StringIO()):
            result = service._process_job({"id": 3024, "production_mode": "master"})

        self.assertFalse(result)
        self.assertIn("failed", service.api_client.statuses)
        self.assertFalse(service.completion_success)

    def test_creator_skip_mode_is_recorded_as_policy_failure(self):
        import contextlib
        import io
        from types import SimpleNamespace

        with contextlib.redirect_stdout(io.StringIO()):
            from creator_api_client import CreatorService
        from sprunki_ultimate_master import ProcessingItem

        class Manager:
            def _record_quality_failure(self, item, code, message, **kwargs):
                item.workflow_report = WorkflowReport(mode=item.mode)
                item.workflow_report.apply_quality_gate(
                    QualityGateResult.failure(code, message=message)
                )
                item.failure_code = code
                item.error_message = message
                item.status = "failed"
                return item.workflow_report

        item = ProcessingItem(mode="skip", project_slug="skip-fixture")
        service = CreatorService.__new__(CreatorService)
        service.processing_manager = Manager()
        service.api_client = SimpleNamespace(update_job_progress=lambda *args, **kwargs: None)

        result = service._process_video_for_item(item, 3028)

        self.assertFalse(result)
        self.assertEqual(item.status, "failed")
        self.assertEqual(item.failure_code, "skipped_by_policy")

    def test_auto_skip_retry_policy_persists_policy_failure(self):
        import contextlib
        import io
        from types import SimpleNamespace

        with contextlib.redirect_stdout(io.StringIO()):
            from creator_api_client import CreatorService
        from sprunki_ultimate_master import ProcessingItem

        class Manager:
            def _record_quality_failure(self, item, code, message, **kwargs):
                item.workflow_report = WorkflowReport(mode=item.mode)
                item.workflow_report.apply_quality_gate(
                    QualityGateResult.failure(code, message=message)
                )
                item.failure_code = code
                item.error_message = message
                item.status = "failed"
                return item.workflow_report

        class Logger:
            def error(self, *args, **kwargs):
                pass

        item = ProcessingItem(mode="master", project_slug="auto-skip-fixture")
        service = CreatorService.__new__(CreatorService)
        service.processing_manager = Manager()
        service.logger = Logger()
        service.retry_manager = SimpleNamespace(
            should_retry=lambda *args, **kwargs: {
                "should_retry": False,
                "should_skip": True,
                "reason": "fixture policy skip",
            }
        )
        service.api_client = SimpleNamespace(
            update_job_progress=lambda *args, **kwargs: None,
            update_job_status=lambda *args, **kwargs: None,
        )

        with contextlib.redirect_stdout(io.StringIO()):
            result = service._execute_with_retry(
                3028,
                "video_processing",
                lambda: False,
                context={"item": item},
                max_step_retries=1,
            )

        self.assertFalse(result)
        self.assertEqual(item.failure_code, "skipped_by_policy")


if __name__ == "__main__":
    unittest.main()

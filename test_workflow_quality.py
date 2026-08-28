import tempfile
import unittest

from workflow_quality import (
    PASS,
    QualityGateResult,
    RemixResult,
    WorkflowReport,
    evaluate_idle_timeout_partial_success,
    evaluate_workflow_report,
    failure_is_retryable,
    placement_transaction_proves_progress,
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

    def test_remix_expected_total_includes_entries_skipped_after_watchdog_stop(self):
        result = RemixResult(attempted=3, succeeded=3, skipped=1)
        self.assertEqual(result.expected, 4)
        self.assertEqual(result.as_dict()["expected"], 4)

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

    def test_idle_timeout_reuses_normal_master_ratio_thresholds(self):
        report = WorkflowReport(
            mode="master",
            runtime_state=PASS,
            runtime_verified=True,
            layout_icons=20,
            layout_slots=4,
            placement_before=0,
            placement_after=1,
            placement_verified=True,
            cleanup_verified=False,
            normal_icons_showcased=20,
            normal_icons_total=20,
            # Master success currently accepts >=50% remix completion; this
            # deliberately uses 2/4 to prove watchdog recovery has no hidden
            # hardcoded "3/4" minimum.
            normal_remixes_played=2,
            normal_remixes_total=4,
        )
        report.evidence.update(
            {
                "cleanupBoundaryId": "watchdog-boundary-1",
                "boardState": "UNKNOWN",
                "cleanupVerified": False,
                "cleanupBoundaryCompleted": True,
                "cleanupDecisionComplete": True,
                "reRunAttempted": True,
                "reRunStatus": "FAILED",
                "reRunVerified": False,
                "contaminationCarriedForward": True,
                "contentSuccessWithUnverifiedCleanup": True,
            }
        )

        result = evaluate_idle_timeout_partial_success(
            report,
            timeout_reason="idle_timeout",
            output_recorded=True,
            cleanup_status="failed",
        )

        self.assertIsNotNone(result)
        self.assertTrue(result.passed)
        self.assertTrue(result.evidence["cleanupNonBlocking"])
        self.assertTrue(result.evidence["cleanupAcceptedByPolicy"])
        self.assertFalse(result.evidence["cleanupVerified"])
        self.assertEqual(result.evidence["normalRemixesCompleted"], 2)
        self.assertTrue(result.evidence["qualityGateReused"])

    def test_idle_timeout_partial_success_is_never_used_for_hard_or_manual_stop(self):
        report = verified_report()
        report.normal_remixes_played = 3
        report.normal_remixes_total = 4

        for reason in ("hard_timeout", "manual_cancellation", "service_cancelled"):
            with self.subTest(reason=reason):
                result = evaluate_idle_timeout_partial_success(
                    report,
                    timeout_reason=reason,
                    output_recorded=True,
                )
                self.assertIsNone(result)

    def test_idle_timeout_partial_success_requires_a_recorded_video(self):
        report = verified_report()
        report.normal_remixes_played = 2
        report.normal_remixes_total = 4

        result = evaluate_idle_timeout_partial_success(
            report,
            timeout_reason="idle_timeout",
            output_recorded=False,
        )

        self.assertIsNone(result)

    def test_idle_timeout_partial_success_rejects_ratio_below_normal_master_gate(self):
        report = verified_report()
        report.normal_remixes_played = 1
        report.normal_remixes_total = 4

        result = evaluate_idle_timeout_partial_success(
            report,
            timeout_reason="idle_timeout",
            output_recorded=True,
        )

        self.assertIsNone(result)

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

    def test_cleanup_only_failure_can_be_marked_retryable(self):
        report = verified_report()
        report.cleanup_verified = False
        report.evidence["cleanupRetryable"] = True

        result = evaluate_workflow_report(report, require_identity=False)

        self.assertFalse(result.passed)
        self.assertEqual(result.failure_code, "placement_not_verified")
        self.assertTrue(result.retryable)

    def test_final_cleanup_optional_flag_needs_completed_rerun_decision(self):
        report = verified_report("remix_only")
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
                "contentSuccessWithUnverifiedCleanup": True,
            }
        )

        result = evaluate_workflow_report(report, mode="remix_only", require_identity=False)

        self.assertTrue(result.passed)
        self.assertTrue(result.evidence["cleanupAcceptedByPolicy"])
        self.assertFalse(result.evidence["cleanupVerified"])

    def test_remix_cleanup_policy_rejects_two_known_residual_icons(self):
        from placement_verifier import PlacementSnapshot
        from sprunki_game_player import SprunkiGamePlayer

        player = SprunkiGamePlayer.__new__(SprunkiGamePlayer)
        player.remix_cleanup_max_residual_icons = 2
        player._capture_stable_placement_snapshot = lambda **kwargs: PlacementSnapshot(
            timestamp=1.0,
            characters_count=2,
            putted_count=2,
            polos=("0", "1", "1"),
        )

        evidence = player._remix_cleanup_policy_evidence(
            cleanup_attempted=True,
            final_remix=False,
        )

        self.assertFalse(evidence["policyAccepted"])
        self.assertFalse(evidence["emptyVerified"])
        self.assertEqual(evidence["residualCount"], 2)

    def test_remix_cleanup_policy_rejects_excessive_or_unknown_residual(self):
        from placement_verifier import PlacementSnapshot
        from sprunki_game_player import SprunkiGamePlayer

        player = SprunkiGamePlayer.__new__(SprunkiGamePlayer)
        player.remix_cleanup_max_residual_icons = 2
        player._capture_stable_placement_snapshot = lambda **kwargs: PlacementSnapshot(
            timestamp=1.0,
            characters_count=3,
            putted_count=3,
            polos=("1", "1", "1"),
        )

        excessive = player._remix_cleanup_policy_evidence(cleanup_attempted=True)
        self.assertFalse(excessive["policyAccepted"])
        self.assertEqual(excessive["reason"], "residual_present")

        player._capture_stable_placement_snapshot = lambda **kwargs: PlacementSnapshot(
            timestamp=1.0,
            polos=("0", "null"),
        )
        unknown = player._remix_cleanup_policy_evidence(cleanup_attempted=True)
        self.assertFalse(unknown["policyAccepted"])
        self.assertEqual(unknown["reason"], "cleanup_signal_unknown")

    def test_remix_skips_occupied_slot_and_keeps_content_success_with_unverified_cleanup(self):
        import contextlib
        import io
        import threading
        from unittest.mock import patch

        from placement_verifier import OperationResult, OperationStatus, PlacementSnapshot
        from sprunki_game_player import SprunkiGamePlayer

        player = SprunkiGamePlayer.__new__(SprunkiGamePlayer)
        player.config = {
            "layout": {
                "icons": [{"index": 0}, {"index": 1}],
                "slots": [{"index": 0}, {"index": 1}],
            }
        }
        player.stop_event = threading.Event()
        player.should_stop = False
        player.obs_controller = None
        player.RETURN_MODE_REMIX = "remix"
        player.verifier_accept_aggregate_progress = True
        player.remix_cleanup_max_residual_icons = 2
        player._workflow_placement_transactions = []
        player._active_remix_final_cleanup_optional = False
        player.set_return_mode_explicit = lambda *args, **kwargs: None
        player._capture_stable_placement_snapshot = lambda **kwargs: PlacementSnapshot(
            timestamp=1.0,
            characters_count=2,
            putted_count=2,
            polos=("1", "1"),
        )
        player._last_cleanup_boundary_evidence = {
            "cleanupBoundaryId": "residual-boundary-1",
            "boardState": "DIRTY",
            "cleanupBoundaryCompleted": True,
            "cleanupDecisionComplete": True,
            "cleanupVerified": False,
            "reRunAttempted": True,
            "reRunStatus": "FAILED",
            "reRunVerified": False,
            "contaminationCarriedForward": True,
            "stateEvidenceAfterRerun": {
                "checks": {
                    "characters": {"clean": False, "value": 2},
                    "putted": {"clean": False, "value": 2},
                }
            },
        }

        drag_calls = []

        def fake_drag(icon, target_slot, *args, **kwargs):
            drag_calls.append(target_slot["index"])
            if target_slot["index"] == 0:
                return OperationResult(
                    OperationStatus.ALREADY_PLACED,
                    direction="place",
                    message="target_already_occupied",
                )
            return OperationResult(
                OperationStatus.VERIFIED,
                direction="place",
                details={
                    "targetSpecific": True,
                    "beforeCharactersCount": 1,
                    "afterCharactersCount": 2,
                },
            )

        player.corrected_drag_and_drop = fake_drag
        player.smart_batch_return_all_slots_with_mode = lambda **kwargs: (
            0,
            2,
            ["slot_0_return_failed", "slot_1_return_failed"],
        )

        with contextlib.redirect_stdout(io.StringIO()), patch(
            "sprunki_game_player.time.sleep", return_value=None
        ):
            success = player.play_complete_remix_corrected(
                {"name": "Residual fixture", "icons": [0, 1]},
                listen_duration=0,
            )

        self.assertTrue(success)
        self.assertEqual(drag_calls, [0, 1])
        self.assertEqual(
            player._last_remix_cleanup_outcome["skippedCount"],
            1,
        )
        self.assertEqual(
            player._last_remix_cleanup_outcome["residualCount"],
            2,
        )
        self.assertEqual(len(player._workflow_placement_transactions), 1)

    def test_final_remix_content_success_requires_completed_rerun_decision(self):
        import contextlib
        import io
        import threading
        from unittest.mock import patch

        from placement_verifier import OperationResult, OperationStatus, PlacementSnapshot
        from sprunki_game_player import SprunkiGamePlayer

        player = SprunkiGamePlayer.__new__(SprunkiGamePlayer)
        player.config = {
            "layout": {
                "icons": [{"index": 0}],
                "slots": [{"index": 0}],
            }
        }
        player.stop_event = threading.Event()
        player.should_stop = False
        player.obs_controller = None
        player.RETURN_MODE_REMIX = "remix"
        player.verifier_accept_aggregate_progress = True
        player.remix_cleanup_max_residual_icons = 2
        player._workflow_placement_transactions = []
        player._active_remix_final_cleanup_optional = True
        player.set_return_mode_explicit = lambda *args, **kwargs: None
        player._capture_stable_placement_snapshot = lambda **kwargs: PlacementSnapshot(
            timestamp=1.0,
            characters_count=6,
            putted_count=6,
            polos=("1", "1", "1", "1", "1", "1"),
        )
        player._last_cleanup_boundary_evidence = {
            "cleanupBoundaryId": "final-boundary-2",
            "boardState": "DIRTY",
            "cleanupBoundaryCompleted": True,
            "cleanupDecisionComplete": True,
            "cleanupVerified": False,
            "reRunAttempted": True,
            "reRunStatus": "FAILED",
            "reRunVerified": False,
            "contaminationCarriedForward": True,
            "stateEvidenceAfterRerun": {
                "checks": {
                    "characters": {"clean": False, "value": 6},
                    "putted": {"clean": False, "value": 6},
                }
            },
        }
        player.corrected_drag_and_drop = lambda *args, **kwargs: OperationResult(
            OperationStatus.VERIFIED,
            direction="place",
            details={
                "targetSpecific": True,
                "beforeCharactersCount": 0,
                "afterCharactersCount": 1,
            },
        )
        player.smart_batch_return_all_slots_with_mode = lambda **kwargs: (
            0,
            1,
            ["final_cleanup_failed"],
        )

        with contextlib.redirect_stdout(io.StringIO()), patch(
            "sprunki_game_player.time.sleep", return_value=None
        ):
            success = player.play_complete_remix_corrected(
                {"name": "Final fixture", "icons": [0]},
                listen_duration=0,
            )

        self.assertTrue(success)
        self.assertFalse(player._last_remix_cleanup_outcome["policyAccepted"])
        self.assertFalse(player._last_remix_cleanup_outcome["emptyVerified"])
        self.assertEqual(player._last_remix_cleanup_outcome["residualCount"], 6)
        self.assertTrue(
            player._last_remix_cleanup_outcome[
                "contentSuccessWithUnverifiedCleanup"
            ]
        )

    def test_last_remix_marks_final_cleanup_as_optional_without_new_status(self):
        import contextlib
        import io
        import threading
        from unittest.mock import patch

        from sprunki_game_player import SprunkiGamePlayer

        player = SprunkiGamePlayer.__new__(SprunkiGamePlayer)
        player.config = {
            "layout": {
                "icons": [{"index": 0}, {"index": 1}],
                "slots": [{"index": 0}, {"index": 1}],
            }
        }
        player.remix_library = [
            {"name": "First", "icons": [0], "pattern": "first"},
            {"name": "Last", "icons": [1], "pattern": "last"},
        ]
        player.remix_max_attempts = 1
        player.remix_retry_delay_seconds = 0
        player.stop_event = threading.Event()
        player.should_stop = False
        player.obs_controller = None
        player._workflow_placement_transactions = []
        final_flags = []

        def fake_play(remix, mode, listen_duration, progress_callback):
            final_flags.append(player._active_remix_final_cleanup_optional)
            return True

        player.play_complete_remix_corrected = fake_play
        with contextlib.redirect_stdout(io.StringIO()), patch(
            "sprunki_game_player.time.sleep", return_value=None
        ):
            result = player.play_all_remixes_corrected(final_cleanup_optional=True)

        self.assertEqual(final_flags, [False, True])
        self.assertEqual(result.succeeded, 2)
        self.assertIsNone(result.failure_code)

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
        self.assertFalse(failure_is_retryable("placement_not_verified"))
        self.assertTrue(
            failure_is_retryable(
                "placement_not_verified",
                {"cleanupRetryable": True},
            )
        )

    def test_targeted_cleanup_does_not_map_occupied_polos_index_to_slot(self):
        import threading

        from placement_verifier import OperationContext, OperationResult, OperationStatus
        from sprunki_game_player import SprunkiGamePlayer

        player = SprunkiGamePlayer.__new__(SprunkiGamePlayer)
        player.config = {
            "layout": {
                "slots": [
                    {"index": 0, "x": 100, "y": 200},
                    {"index": 1, "x": 200, "y": 200},
                ]
            }
        }
        player.stop_event = threading.Event()
        player.empty_board_baseline = {"characters": None, "putted": None}
        player.verifier_targeted_cleanup_recovery_enabled = True
        player.verifier_targeted_cleanup_recovery_rounds = 1
        player.verifier_targeted_cleanup_radius_px = 24
        player.verifier_targeted_cleanup_backend_fallback = True
        player.verifier_targeted_cleanup_timeout_ms = 500

        state = {
            "characters_count": 1,
            "putted_characters": 1,
            "polos_array": ["0", "0", "0", "1"],
        }
        calls = []

        def read_state():
            return dict(state)

        def fake_precision(target_slot, use_cdp, **kwargs):
            calls.append(
                {
                    "slot": kwargs["target_slot_index"],
                    "x": target_slot["x"],
                    "y": target_slot["y"],
                }
            )
            state.update(
                {
                    "characters_count": 0,
                    "putted_characters": 0,
                    "polos_array": ["0", "0", "0", "0"],
                }
            )
            return OperationResult(OperationStatus.VERIFIED, direction="cleanup")

        player.get_comprehensive_slot_state = read_state
        player._execute_precision_return_click = fake_precision
        context = OperationContext.begin("cleanup", 0, max_input_attempts=20)
        player._active_operation_context = context

        result = player._targeted_cleanup_recovery(
            use_cdp=False,
            operation_context=context,
        )

        self.assertEqual(result.status, OperationStatus.VERIFIED)
        # Polos[3] is occupied, but it is never converted to slot index 3.
        # The bounded configured candidate list starts at JSON slot 0.
        self.assertEqual(calls[0]["slot"], 0)
        self.assertNotIn(3, [call["slot"] for call in calls])
        self.assertEqual(state["polos_array"][3], "0")

    def test_cleanup_candidates_are_configured_slots_not_polos_positions(self):
        from sprunki_game_player import SprunkiGamePlayer

        player = SprunkiGamePlayer.__new__(SprunkiGamePlayer)
        player.config = {
            "layout": {
                "slots": [
                    {"index": 0, "x": 100, "y": 200},
                    {"index": 2, "x": 200, "y": 200},
                ]
            }
        }
        player._workflow_placement_transactions = []

        state = {"polos_array": ["0", "0", "0", "1"]}

        self.assertIsNone(player._occupied_slot_indices_from_state(state))
        self.assertEqual(player._cleanup_candidate_slot_indices(), [0, 2])

        player.get_comprehensive_slot_state = lambda: state
        self.assertEqual(player.get_active_slots_queue(), [0, 2])

    def test_nine_configured_slots_are_all_available_to_cleanup_scan(self):
        from sprunki_game_player import SprunkiGamePlayer

        player = SprunkiGamePlayer.__new__(SprunkiGamePlayer)
        player.config = {
            "layout": {
                "slots": [{"index": index} for index in range(9)],
            }
        }
        player._workflow_placement_transactions = []

        self.assertEqual(player._cleanup_candidate_slot_indices(), list(range(9)))

    def test_strict_polos_transaction_proves_soft_progress_without_slot_proof(self):
        transaction = {
            "status": "VERIFIED",
            "targetVerified": False,
            "softVerified": True,
            "verificationConfidence": "soft",
            "transitionEvidence": {
                "polosTransition": "single_place",
                "polosSignalValid": True,
                "beforePolosLength": 9,
                "afterPolosLength": 9,
                "beforePolosOccupiedCount": 0,
                "afterPolosOccupiedCount": 1,
                "polosChangedIndices": [4],
            },
        }

        self.assertTrue(placement_transaction_proves_progress(transaction))

        ambiguous = {
            **transaction,
            "transitionEvidence": {
                **transaction["transitionEvidence"],
                "polosTransition": "ambiguous",
                "polosChangedIndices": [3, 4],
            },
        }
        self.assertFalse(placement_transaction_proves_progress(ambiguous))

    def test_soft_transaction_raw_polos_difference_without_transition_is_rejected(self):
        transaction = {
            "status": "VERIFIED",
            "targetVerified": False,
            "softVerified": True,
            "before": 0,
            "after": 0,
            "transitionEvidence": {
                "before": (0, 0, ("0", "0")),
                "after": (0, 0, ("0", "1")),
            },
        }

        self.assertFalse(placement_transaction_proves_progress(transaction))

    def test_cleanup_aggregate_progress_still_requires_empty_board(self):
        import threading

        from placement_verifier import OperationContext, OperationResult, OperationStatus, PlacementSnapshot
        from sprunki_game_player import SprunkiGamePlayer

        player = SprunkiGamePlayer.__new__(SprunkiGamePlayer)
        player.config = {
            "layout": {
                "slots": [{"index": 0, "x": 100, "y": 200}],
            }
        }
        player.stop_event = threading.Event()
        player._active_operation_context = OperationContext.begin(
            "cleanup", 3, max_input_attempts=20
        )
        player._targeted_cleanup_recovery = lambda **kwargs: OperationResult(
            OperationStatus.FAILED,
            direction="cleanup",
            message="targeted_cleanup_incomplete",
        )
        player.execute_sequential_carpet_bombing_sets = lambda *args, **kwargs: True
        snapshots = iter(
            [
                PlacementSnapshot(1.0, characters_count=2, putted_count=2),
                PlacementSnapshot(2.0, characters_count=1, putted_count=1),
            ]
        )
        player._capture_stable_placement_snapshot = lambda **kwargs: next(snapshots)
        player._is_board_count_clean = lambda characters_count=None, putted_count=None: (
            characters_count == 0 and putted_count == 0
        )

        result = player.execute_remix_return_strategy(
            {"index": 3, "x": 100, "y": 200},
            operation_context=player._active_operation_context,
        )

        self.assertEqual(result.status, OperationStatus.FAILED)
        self.assertEqual(result.message, "cleanup_incomplete")

    def test_restart_recovery_is_accepted_only_as_verified_empty_cleanup(self):
        import threading

        from placement_verifier import OperationContext, OperationResult, OperationStatus
        from sprunki_game_player import SprunkiGamePlayer

        player = SprunkiGamePlayer.__new__(SprunkiGamePlayer)
        player.stop_event = threading.Event()
        player.obs_controller = None
        player._session_recovery_count = 0
        player._max_session_recoveries = 2
        player._active_operation_context = OperationContext.begin(
            "cleanup", 3, max_input_attempts=20
        )
        player.execute_remix_return_strategy = lambda *args, **kwargs: OperationResult(
            OperationStatus.FAILED,
            direction="cleanup",
            message="cleanup_incomplete",
        )
        player.restart_game_to_initial_state = lambda *args, **kwargs: OperationResult(
            OperationStatus.RECOVERED,
            direction="recovery",
            message="game_recovered",
            details={
                "postState": {
                    "sequence": 2,
                    "timestamp": 2.0,
                    "characters_count": 0,
                    "putted_characters": 0,
                    "polos_array": ["0", "0", "0"],
                }
            },
        )

        result = player.smart_batch_return_all_slots_with_mode(
            context={"workflow": "test"},
            use_cdp=False,
        )

        self.assertEqual(result, (1, 1, []))

    def test_restart_state_requires_all_available_signals_to_be_empty(self):
        from placement_verifier import PlacementSnapshot
        from sprunki_game_player import SprunkiGamePlayer

        mixed_state = PlacementSnapshot(
            timestamp=1.0,
            characters_count=1,
            putted_count=0,
            polos=("0", "0", "0", "0"),
        )
        clean_state = PlacementSnapshot(
            timestamp=1.0,
            characters_count=0,
            putted_count=0,
            polos=("0", "0", "0", "0"),
        )

        mixed_verified, mixed_checks = SprunkiGamePlayer._evaluate_restart_state(
            mixed_state
        )
        clean_verified, clean_checks = SprunkiGamePlayer._evaluate_restart_state(
            clean_state
        )

        self.assertFalse(mixed_verified)
        self.assertFalse(mixed_checks["counters"]["clean"])
        self.assertTrue(mixed_checks["polos"]["clean"])
        self.assertTrue(clean_verified)
        self.assertTrue(all(check["clean"] for check in clean_checks.values()))

    def test_nonzero_empty_board_baseline_can_never_make_board_clean(self):
        from sprunki_game_player import SprunkiGamePlayer

        player = SprunkiGamePlayer.__new__(SprunkiGamePlayer)
        player.empty_board_baseline = {"characters": None, "putted": None}

        player._remember_empty_board_baseline(1, 1)

        self.assertEqual(player.empty_board_baseline, {"characters": None, "putted": None})
        self.assertFalse(player._is_board_count_clean(0, 1))

    def test_final_empty_evaluation_rejects_residual_polos_or_characters(self):
        from placement_verifier import PlacementSnapshot
        from sprunki_game_player import SprunkiGamePlayer

        residual_polos = PlacementSnapshot(
            1.0,
            characters_count=0,
            putted_count=0,
            polos=("0", "1"),
            characters=("0", "0"),
        )
        residual_characters = PlacementSnapshot(
            1.0,
            characters_count=0,
            putted_count=0,
            polos=("0", "0"),
            characters=("0", "1"),
        )
        clean = PlacementSnapshot(
            1.0,
            characters_count=0,
            putted_count=0,
            polos=("0", "0"),
            characters=("0", "0"),
        )

        self.assertFalse(SprunkiGamePlayer._evaluate_empty_snapshot(residual_polos)[0])
        self.assertFalse(SprunkiGamePlayer._evaluate_empty_snapshot(residual_characters)[0])
        self.assertTrue(SprunkiGamePlayer._evaluate_empty_snapshot(clean)[0])

    def test_final_validation_uses_stable_all_signal_empty_gate(self):
        import threading

        from placement_verifier import PlacementSnapshot
        from sprunki_game_player import SprunkiGamePlayer

        player = SprunkiGamePlayer.__new__(SprunkiGamePlayer)
        player.stop_event = threading.Event()
        player.verifier_final_snapshot_timeout_ms = 500
        player.verifier_final_stable_samples = 2
        player._capture_stable_placement_snapshot = lambda **kwargs: PlacementSnapshot(
            1.0,
            characters_count=0,
            putted_count=0,
            polos=("0", "1"),
            characters=("0", "0"),
        )

        complete, issues, _recommendations = player.validate_workflow_completion()

        self.assertFalse(complete)
        self.assertTrue(any("polos" in issue for issue in issues))

    def test_threshold_failure_with_clean_retry_path_is_retryable(self):
        report = verified_report()
        report.normal_remixes_played = 0
        report.normal_remixes_total = 2
        report.evidence["normalRemixRetryable"] = True

        result = evaluate_workflow_report(report, require_identity=False)

        self.assertFalse(result.passed)
        self.assertEqual(result.failure_code, "remix_threshold_not_met")
        self.assertTrue(result.retryable)

    def test_later_valid_transaction_overrides_stale_first_report_snapshot(self):
        report = verified_report()
        report.placement_before = 0
        report.placement_after = 0
        report.placement_verified = True
        report.evidence["placementTransactions"] = [
            {
                "status": "VERIFIED",
                "targetVerified": True,
                "before": 0,
                "after": 0,
            },
            {
                "status": "VERIFIED",
                "targetVerified": False,
                "softVerified": True,
                "before": 0,
                "after": 1,
            },
        ]

        result = evaluate_workflow_report(report, require_identity=False)

        self.assertTrue(result.passed)

    def test_cleanup_guard_allows_one_controlled_retry_after_failure(self):
        import threading

        from placement_verifier import OperationStatus
        from sprunki_game_player import SprunkiGamePlayer

        player = SprunkiGamePlayer.__new__(SprunkiGamePlayer)
        player.config = {"layout": {"slots": [{"index": 0}]}}
        player._reset_cleanup_guard()
        player.stop_event = threading.Event()
        calls = []

        def fake_cleanup(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return 0, 1, ["cleanup_incomplete"]
            return 1, 1, []

        player.smart_batch_return_all_slots_with_mode = fake_cleanup

        first = player._run_cleanup_once()
        second = player._run_cleanup_once()

        self.assertEqual(first.status, OperationStatus.FAILED)
        self.assertEqual(second.status, OperationStatus.VERIFIED)
        self.assertEqual(len(calls), 2)

    def test_whole_remix_retry_reuses_cleanup_already_run_by_attempt(self):
        import threading

        from sprunki_game_player import SprunkiGamePlayer

        player = SprunkiGamePlayer.__new__(SprunkiGamePlayer)
        player.config = {
            "layout": {
                "icons": [{"index": 0}],
                "slots": [{"index": 0}],
            }
        }
        player.remix_max_attempts = 2
        player.remix_retry_delay_seconds = 0
        player.stop_event = threading.Event()
        player.should_stop = False
        player._workflow_placement_transactions = []
        player.validate_workflow_completion = lambda: (True, [], [])
        cleanup_calls = []
        player.smart_batch_return_all_slots_with_mode = lambda **kwargs: (
            cleanup_calls.append(kwargs) or (0, 1, ["must_not_run"])
        )
        play_calls = []

        def fake_play(*args, **kwargs):
            play_calls.append(True)
            if len(play_calls) == 1:
                player._last_remix_cleanup_outcome = {
                    "transactionStart": 0,
                    "successCount": 1,
                    "totalAttempted": 1,
                    "errors": [],
                    "cleanupBoundaryId": "retry-boundary-1",
                    "boardState": "CLEAN",
                    "cleanupBoundaryCompleted": True,
                    "cleanupDecisionComplete": True,
                    "cleanupVerified": True,
                    "reRunAttempted": False,
                    "reRunVerified": False,
                }
                return False
            return True

        player.play_complete_remix_corrected = fake_play

        success, attempts = player._play_remix_with_retry(
            {"name": "Reuse cleanup", "icons": [0]}
        )

        self.assertTrue(success)
        self.assertEqual(len(play_calls), 2)
        self.assertEqual(cleanup_calls, [])
        self.assertEqual(attempts[0]["cleanup"]["cleanupSource"], "attempt_cleanup_reused")

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
            player._last_cleanup_boundary_evidence = {
                "cleanupBoundaryId": "retry-boundary-2",
                "boardState": "CLEAN",
                "cleanupBoundaryCompleted": True,
                "cleanupDecisionComplete": True,
                "cleanupVerified": True,
                "reRunAttempted": False,
                "reRunVerified": False,
            }
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

        self.assertEqual(play_calls, ["Dirty-board fixture", "Must-not-start"])
        self.assertEqual(result.attempted, 2)
        self.assertEqual(result.succeeded, 0)
        self.assertEqual(result.skipped, 0)
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

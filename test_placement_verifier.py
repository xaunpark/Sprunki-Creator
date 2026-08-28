import unittest
from unittest.mock import Mock

from manual_prepare_timeline import (
    MAX_PREPARE_ACTIONS,
    MAX_SINGLE_PREPARE_DELAY_MS,
    replay_prepare_timeline,
    validate_prepare_timeline,
)
from placement_verifier import (
    OperationContext,
    OperationResult,
    OperationStatus,
    classify_precondition,
    compare_polos_transition,
    evaluate_transition,
    is_progress_verified,
    observe_until_verified,
    promote_aggregate_transition,
    summarize_polos,
    wait_for_stable_snapshot,
)
from sprunki_game_player import SprunkiGamePlayer


def _snapshot(sequence, timestamp, **values):
    return {
        "sequence": sequence,
        "timestamp": timestamp,
        **values,
    }


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def now(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


class PlacementVerifierTests(unittest.TestCase):
    def test_polos_transition_is_verified_as_aggregate_only(self):
        before = _snapshot(
            1,
            1,
            polos_array=["0", "0"],
        )
        after = _snapshot(
            2,
            2,
            polos_array=["0", "1"],
        )

        evaluation = evaluate_transition(before, after, "place", 0)

        self.assertEqual(evaluation.status, OperationStatus.VERIFIED)
        self.assertFalse(evaluation.target_specific)
        self.assertIn("polos_aggregate_delta", evaluation.evidence)
        self.assertNotIn("polos_target", evaluation.evidence)
        self.assertTrue(evaluation.aggregate_fallback)
        self.assertTrue(evaluation.details["softVerified"])
        self.assertEqual(evaluation.details["verificationConfidence"], "soft")
        self.assertEqual(evaluation.details["polosChangedIndices"], [1])
        self.assertEqual(evaluation.details["targetSlot"], 0)

    def test_polos_summary_normalizes_supported_tokens_and_rejects_unknown(self):
        summary = summarize_polos([0, False, "", 1, True])

        self.assertTrue(summary.present)
        self.assertTrue(summary.valid)
        self.assertEqual(summary.length, 5)
        self.assertEqual(summary.occupied_count, 2)
        self.assertEqual(summary.occupied_indices, (3, 4))

        invalid = summarize_polos(["0", "pending"])
        self.assertTrue(invalid.present)
        self.assertFalse(invalid.valid)
        self.assertIsNone(invalid.occupied_count)
        self.assertEqual(invalid.invalid_indices, (1,))
        self.assertIsNone(summarize_polos(None).valid)

    def test_polos_transition_requires_one_expected_binary_change(self):
        place = compare_polos_transition([False, 0], ["0", "true"], "place")
        self.assertEqual(place.kind, "single_place")
        self.assertEqual(place.before_count, 0)
        self.assertEqual(place.after_count, 1)
        self.assertEqual(place.changed_indices, (1,))

        wrong_direction = compare_polos_transition(["1"], ["0"], "place")
        self.assertEqual(wrong_direction.kind, "ambiguous")

        multiple = compare_polos_transition(["0", "0"], ["1", "1"], "place")
        self.assertEqual(multiple.kind, "ambiguous")
        self.assertEqual(multiple.changed_indices, (0, 1))

        length_changed = compare_polos_transition(["0"], ["0", "1"], "place")
        self.assertEqual(length_changed.kind, "ambiguous")
        self.assertEqual(length_changed.reason, "length_changed")

        unknown = compare_polos_transition(["0"], ["none"], "place")
        self.assertEqual(unknown.kind, "unknown")

    def test_wrong_slot_is_not_marked_as_target_success(self):
        before = _snapshot(
            1,
            1,
            characters_count=0,
            putted_characters=0,
            polos_array=["0", "0"],
        )
        after = _snapshot(
            2,
            2,
            characters_count=1,
            putted_characters=1,
            polos_array=["0", "1"],
        )

        evaluation = evaluate_transition(before, after, "place", 0)
        result = OperationResult(
            evaluation.status,
            details={
                "targetSpecific": evaluation.target_specific,
                "aggregateFallback": evaluation.aggregate_fallback,
            },
        )

        self.assertEqual(evaluation.status, OperationStatus.VERIFIED)
        self.assertTrue(evaluation.aggregate_fallback)
        self.assertFalse(result.target_verified)
        self.assertTrue(result)  # aggregate fallback may keep the workflow moving

    def test_none_reader_stops_after_three_failures_without_input(self):
        clock = FakeClock()
        result = observe_until_verified(
            _snapshot(1, 1, putted_characters=0),
            lambda: None,
            "place",
            0,
            timeout_ms=3000,
            poll_ms=50,
            clock=clock.now,
            sleep_fn=clock.sleep,
        )

        self.assertEqual(result.status, OperationStatus.UNKNOWN)
        self.assertEqual(result.reads, 3)
        self.assertLess(clock.value, 1.0)

    def test_operation_read_budget_is_not_restarted_by_a_new_observer(self):
        clock = FakeClock()
        context = OperationContext.begin(
            "place",
            0,
            max_read_failures=3,
            deadline_seconds=30,
        )
        baseline = _snapshot(1, 1, putted_characters=0)
        context.register_read(None)
        context.register_read(None)
        context.register_read(None)

        result = observe_until_verified(
            baseline,
            lambda: _snapshot(2, 2, putted_characters=1),
            "place",
            0,
            timeout_ms=3000,
            poll_ms=50,
            clock=clock.now,
            sleep_fn=clock.sleep,
            context=context,
        )

        self.assertEqual(result.status, OperationStatus.UNKNOWN)
        self.assertEqual(result.reads, 0)

    def test_delayed_transition_is_observed_without_second_input(self):
        clock = FakeClock()
        baseline = _snapshot(1, 1, putted_characters=0)
        transition = _snapshot(2, 2, putted_characters=1)
        reads = iter([baseline, transition, transition])

        result = observe_until_verified(
            baseline,
            lambda: next(reads),
            "place",
            0,
            timeout_ms=3000,
            poll_ms=50,
            clock=clock.now,
            sleep_fn=clock.sleep,
        )

        self.assertEqual(result.status, OperationStatus.VERIFIED)
        self.assertIn("putted_characters", result.evidence)
        self.assertEqual(result.reads, 3)

    def test_operation_context_has_one_finite_input_and_recovery_budget(self):
        context = OperationContext.begin(
            "place",
            0,
            max_input_attempts=5,
            max_recovery_attempts=1,
            deadline_seconds=30,
        )
        for index in range(5):
            coordinates = (index, 0, index + 1, 1)
            self.assertTrue(context.can_send_input(coordinates))
            self.assertTrue(context.register_input(coordinates))
        self.assertFalse(context.can_send_input((99, 0, 100, 1)))
        self.assertTrue(context.begin_recovery())
        context.end_recovery()
        self.assertFalse(context.begin_recovery())

    def test_fake_executor_cannot_exceed_shared_input_budget(self):
        context = OperationContext.begin("place", 0, max_input_attempts=5)
        sent_inputs = []

        # Model a misbehaving nested retry caller.  The shared context is the
        # only authority allowed to admit another physical input.
        for candidate in range(100):
            coordinates = (candidate, 0, candidate + 1, 1)
            if not context.can_send_input(coordinates):
                break
            self.assertTrue(context.register_input(coordinates))
            sent_inputs.append(coordinates)

        self.assertEqual(len(sent_inputs), 5)
        self.assertEqual(context.input_attempts, 5)
        self.assertFalse(context.can_send_input((101, 0, 102, 1)))

    def test_precondition_statuses_prevent_redundant_input(self):
        placed = _snapshot(1, 1, polos_array=["1"])
        empty = _snapshot(2, 2, polos_array=["0"])
        unknown = _snapshot(3, 3, polos_array=[None])

        # Polos positions are diagnostic-only: neither value can identify the
        # requested layout slot or suppress a physical operation.
        self.assertIsNone(classify_precondition(placed, "place", 0))
        self.assertIsNone(classify_precondition(empty, "return", 0))
        self.assertIsNone(classify_precondition(unknown, "return", 0))

        # Return is action-first even when a target-specific signal reports
        # empty before the action.
        self.assertIsNone(
            classify_precondition(
                _snapshot(4, 4, characters_array=["0"]),
                "return",
                0,
            )
        )

        self.assertEqual(
            classify_precondition(
                _snapshot(4, 4, characters_array=["n-1"]),
                "place",
                0,
            ),
            OperationStatus.ALREADY_PLACED,
        )

    def test_polos_only_transition_requires_two_stable_post_action_reads(self):
        clock = FakeClock()
        baseline = _snapshot(1, 1, polos_array=["0", "0"])
        reads = iter([
            _snapshot(2, 2, polos_array=["0", "1"]),
            _snapshot(3, 3, polos_array=["0", "1"]),
        ])

        result = observe_until_verified(
            baseline,
            lambda: next(reads),
            "place",
            0,
            timeout_ms=3000,
            poll_ms=10,
            stable_samples=1,
            clock=clock.now,
            sleep_fn=clock.sleep,
        )

        self.assertEqual(result.status, OperationStatus.VERIFIED)
        self.assertFalse(result.target_verified)
        self.assertTrue(result.details["softVerified"])
        self.assertEqual(result.details["requiredStableSamples"], 2)
        self.assertEqual(result.details["polosChangedIndices"], [1])
        self.assertEqual(result.reads, 2)

    def test_soft_physical_drag_emits_correlated_success_event(self):
        player = SprunkiGamePlayer.__new__(SprunkiGamePlayer)
        player._active_operation_context = OperationContext.begin(
            "place",
            4,
            operation_id="place-event-test",
        )
        player.obs_controller = Mock()
        player.obs_controller.recording_start_time = 0.0
        player.drag_stats = Mock()

        player._record_aggregate_drag_progress(
            {"index": 0},
            {"index": 4},
            {
                "operationId": "place-event-test",
                "verificationMethod": "polos_aggregate_delta",
                "verificationConfidence": "soft",
                "softVerified": True,
                "beforePolosOccupiedCount": 0,
                "afterPolosOccupiedCount": 1,
                "polosChangedIndices": [7],
                "action_start_time": 1.0,
                "drop_time": 2.0,
                "verified_at": 3.0,
            },
            1,
            "search_mode_aggregate_fallback",
        )

        calls = player.obs_controller.log_event_timestamp.call_args_list
        self.assertEqual(
            [call.args[0] for call in calls],
            ["successful_drag_started", "icon_dropped_successfully"],
        )
        metadata = calls[0].args[2]
        self.assertEqual(metadata["operationId"], "place-event-test")
        self.assertEqual(metadata["icon_index"], 0)
        self.assertEqual(metadata["slot_index"], 4)
        self.assertEqual(metadata["verificationMethod"], "polos_aggregate_delta")
        self.assertEqual(metadata["verificationConfidence"], "soft")
        self.assertFalse(metadata["targetVerified"])
        self.assertEqual(metadata["beforePolosCount"], 0)
        self.assertEqual(metadata["afterPolosCount"], 1)
        self.assertEqual(metadata["changedPolosIndices"], [7])

    def test_stable_baseline_requires_two_semantically_equal_samples(self):
        clock = FakeClock()
        snapshots = iter([
            _snapshot(1, 1, polos_array=["0"]),
            _snapshot(2, 2, polos_array=["1"]),
            _snapshot(3, 3, polos_array=["0"]),
            _snapshot(4, 4, polos_array=["0"]),
        ])
        baseline = wait_for_stable_snapshot(
            lambda: next(snapshots),
            timeout_ms=500,
            poll_ms=50,
            clock=clock.now,
            sleep_fn=clock.sleep,
        )
        self.assertIsNotNone(baseline)
        self.assertEqual(baseline.polos, ("0",))

    def test_stability_ignores_flapping_ui_button_list(self):
        clock = FakeClock()
        snapshots = iter([
            _snapshot(1, 1, polos_array=["0"], button_enable_list=["0"]),
            _snapshot(2, 2, polos_array=["0"], button_enable_list=["1"]),
        ])

        stable = wait_for_stable_snapshot(
            lambda: next(snapshots),
            timeout_ms=500,
            poll_ms=50,
            clock=clock.now,
            sleep_fn=clock.sleep,
        )

        self.assertIsNotNone(stable)
        self.assertEqual(stable.polos, ("0",))

    def test_no_transition_times_out_without_another_attempt(self):
        clock = FakeClock()
        baseline = _snapshot(1, 1, putted_characters=0)
        result = observe_until_verified(
            baseline,
            lambda: _snapshot(2, 2, putted_characters=0),
            "place",
            0,
            timeout_ms=120,
            poll_ms=50,
            clock=clock.now,
            sleep_fn=clock.sleep,
        )
        self.assertEqual(result.status, OperationStatus.FAILED)
        self.assertLessEqual(result.reads, 4)

    def test_transition_seen_but_not_stable_is_unknown(self):
        clock = FakeClock()
        baseline = _snapshot(1, 1, polos_array=["0"], putted_characters=0)
        reads = iter([
            _snapshot(2, 2, polos_array=["1"], putted_characters=1),
            _snapshot(3, 3, polos_array=["0"], putted_characters=0),
        ])
        result = observe_until_verified(
            baseline,
            lambda: next(reads),
            "place",
            0,
            timeout_ms=100,
            poll_ms=50,
            clock=clock.now,
            sleep_fn=clock.sleep,
        )
        self.assertEqual(result.status, OperationStatus.UNKNOWN)
        self.assertIn("transition_seen", result.message)

    def test_unstable_exact_aggregate_delta_is_soft_success(self):
        result = OperationResult(
            OperationStatus.UNKNOWN,
            direction="place",
            message="transition_seen_but_not_stable",
            details={
                "beforeCharactersCount": 0,
                "afterCharactersCount": 1,
                "beforePuttedCount": 0,
                "afterPuttedCount": 1,
            },
        )

        soft = promote_aggregate_transition(result, "place")

        self.assertIsNotNone(soft)
        self.assertEqual(soft.status, OperationStatus.VERIFIED)
        self.assertFalse(soft.target_verified)
        self.assertTrue(soft.details["softVerified"])
        self.assertTrue(is_progress_verified(soft))
        self.assertIn("unstable_transition_soft_accept", soft.evidence)

    def test_unknown_without_exact_delta_stays_unknown(self):
        result = OperationResult(
            OperationStatus.UNKNOWN,
            direction="place",
            message="transition_seen_but_not_stable",
            details={
                "beforeCharactersCount": 1,
                "afterCharactersCount": 1,
                "beforePuttedCount": 1,
                "afterPuttedCount": 1,
            },
        )

        unchanged = promote_aggregate_transition(result, "place")

        self.assertEqual(unchanged.status, OperationStatus.UNKNOWN)
        self.assertFalse(is_progress_verified(unchanged))

    def test_atomic_empty_return_transition_survives_late_read_timeout(self):
        clock = FakeClock()
        baseline = _snapshot(
            1,
            1,
            characters_count=1,
            putted_characters=1,
        )
        reads = iter([
            _snapshot(2, 2, characters_count=0, putted_characters=0),
            None,
            None,
            None,
        ])

        result = observe_until_verified(
            baseline,
            lambda: next(reads),
            "return",
            4,
            timeout_ms=3000,
            poll_ms=50,
            clock=clock.now,
            sleep_fn=clock.sleep,
        )

        self.assertEqual(result.status, OperationStatus.ALREADY_EMPTY)
        self.assertIn("atomic_empty_after_return_transition", result.message)
        self.assertIn("characters_empty_after_return", result.evidence)

    def test_failure_signature_and_no_progress_stop_after_two_cycles(self):
        context = OperationContext.begin("place", 0, max_input_attempts=5)
        signature = "place:0:no_transition"
        self.assertEqual(context.note_failure(signature), 1)
        self.assertEqual(context.note_failure(signature), 2)
        self.assertEqual(context.failure_counts[signature], 2)
        self.assertEqual(context.no_progress_cycles, 2)

    def test_recovery_input_requires_explicit_recovery_permission(self):
        context = OperationContext.begin("return", 0, max_input_attempts=2)
        self.assertTrue(context.begin_recovery())
        self.assertFalse(context.can_send_input())
        self.assertTrue(context.can_send_input(allow_recovery_input=True))
        self.assertTrue(context.register_input())
        self.assertTrue(context.can_send_input(allow_recovery_input=True))
        self.assertTrue(context.register_input())
        self.assertFalse(context.can_send_input(allow_recovery_input=True))

    def test_recovered_is_not_operation_success(self):
        self.assertFalse(OperationResult(OperationStatus.RECOVERED))

    def test_replay_stops_at_first_failed_action_without_blind_fallback(self):
        actions = [
            {"type": "click", "x": 1, "y": 1, "delayMs": 0},
            {"type": "drag", "startX": 1, "startY": 1, "endX": 2, "endY": 2, "delayMs": 0},
            {"type": "click", "x": 3, "y": 3, "delayMs": 0},
        ]
        executed = []
        replayed = replay_prepare_timeline(
            actions,
            {},
            lambda index, action: executed.append(index) or index == 1,
            sleep_fn=lambda _seconds: None,
        )
        self.assertFalse(replayed)
        self.assertEqual(executed, [1, 2])

    def test_profile_limits_are_checked_before_replay(self):
        valid_action = {"type": "click", "x": 1, "y": 2, "delayMs": 10}
        self.assertEqual(
            validate_prepare_timeline(
                [valid_action] * (MAX_PREPARE_ACTIONS + 1),
                {},
            )[0],
            False,
        )
        self.assertFalse(
            validate_prepare_timeline(
                [{**valid_action, "delayMs": MAX_SINGLE_PREPARE_DELAY_MS + 1}],
                {},
            )[0]
        )
        self.assertFalse(
            validate_prepare_timeline(
                [{"type": "unsupported", "delayMs": 0}],
                {},
            )[0]
        )

        sleeps = []
        executed = []
        profile = {"prepareTiming": {"endDelayMs": 30, "durationMs": 40}}
        self.assertTrue(
            replay_prepare_timeline(
                [valid_action],
                profile,
                lambda index, action: executed.append(index) or True,
                sleep_fn=sleeps.append,
            )
        )
        self.assertEqual(executed, [1])
        self.assertEqual(sleeps, [0.01, 0.03])


class ReturnActionPolicyTests(unittest.TestCase):
    @staticmethod
    def _player():
        player = object.__new__(SprunkiGamePlayer)
        player._operation_counter = 0
        player._active_operation_context = None
        player.verifier_max_read_failures = 5
        player.config = {"layout": {"slots": [{"index": 0}]}}
        player.carpet_bombing_config = {"max_sets": 1}
        player.verifier_targeted_cleanup_recovery_rounds = 1
        player.verifier_targeted_cleanup_backend_fallback = False
        player._max_session_recoveries = 2
        player._session_recovery_count = 0
        player.obs_controller = None
        player._cleanup_input_budget = lambda: 4
        player._cleanup_deadline_seconds = lambda: 30.0
        return player

    @staticmethod
    def _state(sequence, count, polos, characters):
        return {
            "sequence": sequence,
            "timestamp": float(sequence),
            "characters_count": count,
            "putted_characters": count,
            "polos_array": polos,
            "characters_array": characters,
        }

    def test_empty_precondition_does_not_skip_return_click(self):
        player = self._player()
        baseline = self._state(1, 1, ["0"], ["1"])
        after_click = self._state(2, 0, ["0"], ["0"])
        player._capture_stable_placement_snapshot = Mock(
            side_effect=[baseline, after_click]
        )
        player._execute_precision_return_click = Mock(
            return_value=OperationResult(OperationStatus.FAILED, message="no_transition")
        )
        player._execute_carpet_bombing_return = Mock()

        result = player.corrected_return_icon({"index": 0, "x": 10, "y": 20}, use_cdp=False)

        self.assertEqual(result.status, OperationStatus.ALREADY_EMPTY)
        self.assertEqual(player._execute_precision_return_click.call_count, 1)
        player._execute_carpet_bombing_return.assert_not_called()

    def test_occupied_post_action_state_advances_to_next_return_method(self):
        player = self._player()
        player.verifier_targeted_cleanup_recovery_enabled = False
        baseline = self._state(1, 1, ["0"], ["1"])
        after_precision = self._state(2, 1, ["1"], ["1"])
        after_carpet = self._state(3, 1, ["1"], ["1"])
        after_targeted = self._state(4, 1, ["1"], ["1"])
        after_restart = self._state(5, 0, ["0"], ["0"])
        player._capture_stable_placement_snapshot = Mock(
            side_effect=[
                baseline,
                after_precision,
                after_carpet,
                after_targeted,
                after_restart,
            ]
        )
        player._execute_precision_return_click = Mock(
            return_value=OperationResult(OperationStatus.FAILED, message="no_transition")
        )
        player._execute_carpet_bombing_return = Mock(
            return_value=OperationResult(OperationStatus.FAILED, message="carpet_no_progress")
        )
        player.restart_game_to_initial_state = Mock(
            return_value=OperationResult(OperationStatus.RECOVERED, message="restarted")
        )

        result = player.corrected_return_icon({"index": 0, "x": 10, "y": 20}, use_cdp=False)

        self.assertEqual(result.status, OperationStatus.ALREADY_EMPTY)
        self.assertEqual(player._execute_precision_return_click.call_count, 1)
        self.assertEqual(player._execute_carpet_bombing_return.call_count, 1)
        player.restart_game_to_initial_state.assert_called_once()

    def test_stale_empty_polos_with_nonzero_counts_does_not_finish(self):
        player = self._player()
        # This mirrors the problematic mod: Polos says the target is empty,
        # while the aggregate counters still prove one icon remains.
        baseline = {
            "sequence": 1,
            "timestamp": 1.0,
            "characters_count": 1,
            "putted_characters": 1,
            "polos_array": ["0"],
        }
        after_precision = dict(baseline, sequence=2, timestamp=2.0)
        after_carpet = dict(baseline, sequence=3, timestamp=3.0)
        after_restart = self._state(4, 0, ["0"], ["0"])
        player._capture_stable_placement_snapshot = Mock(
            side_effect=[baseline, after_precision, after_carpet, after_restart]
        )
        player._execute_precision_return_click = Mock(
            return_value=OperationResult(OperationStatus.FAILED, message="no_transition")
        )
        player._execute_carpet_bombing_return = Mock(
            return_value=OperationResult(OperationStatus.FAILED, message="carpet_no_progress")
        )
        player.restart_game_to_initial_state = Mock(
            return_value=OperationResult(OperationStatus.RECOVERED, message="restarted")
        )

        result = player.corrected_return_icon({"index": 0, "x": 10, "y": 20}, use_cdp=False)

        self.assertEqual(result.status, OperationStatus.ALREADY_EMPTY)
        self.assertEqual(player._execute_precision_return_click.call_count, 1)
        self.assertEqual(player._execute_carpet_bombing_return.call_count, 1)

    def _cleanup_boundary_player(self):
        player = self._player()
        player._board_generation = 0
        player._board_contamination_carried_forward = False
        player._cleanup_boundary_history = []
        player._cleanup_boundary_states = {}
        player._last_cleanup_boundary_evidence = {}
        player._workflow_placement_transactions = []
        player.verifier_final_stable_samples = 2
        return player

    def test_clean_boundary_does_not_send_rerun(self):
        from placement_verifier import PlacementSnapshot

        player = self._cleanup_boundary_player()
        baseline = self._state(1, 1, ["1"], ["1"])
        clean = self._state(2, 0, ["0"], ["0"])
        context = OperationContext.begin(
            "cleanup",
            None,
            max_input_attempts=20,
            operation_id="cleanup-clean-boundary",
        )
        context.cleanup_boundary_id = "cleanup-clean-boundary"
        context.set_baseline(PlacementSnapshot.from_mapping(baseline))
        player.restart_game_to_initial_state = Mock()

        result = player._run_cleanup_boundary_recovery(
            context,
            use_cdp=False,
            baseline=baseline,
            current_snapshot=clean,
        )

        self.assertEqual(result.status, OperationStatus.VERIFIED)
        self.assertTrue(result.details["cleanupVerified"])
        self.assertFalse(result.details["reRunAttempted"])
        player.restart_game_to_initial_state.assert_not_called()

    def test_dirty_boundary_runs_exactly_one_rerun_and_invalidates_old_ledger(self):
        from placement_verifier import PlacementSnapshot

        player = self._cleanup_boundary_player()
        dirty = self._state(1, 1, ["1"], ["1"])
        clean = self._state(2, 0, ["0"], ["0"])
        player._workflow_placement_transactions = [
            {
                "status": "VERIFIED",
                "targetVerified": True,
                "slotIndex": 0,
            }
        ]
        context = OperationContext.begin(
            "cleanup",
            None,
            max_input_attempts=20,
            operation_id="cleanup-dirty-boundary",
        )
        context.cleanup_boundary_id = "cleanup-dirty-boundary"
        context.set_baseline(PlacementSnapshot.from_mapping(dirty))
        player.restart_game_to_initial_state = Mock(
            return_value=OperationResult(
                OperationStatus.RECOVERED,
                direction="recovery",
                details={"postState": clean},
            )
        )

        result = player._run_cleanup_boundary_recovery(
            context,
            use_cdp=False,
            baseline=dirty,
            current_snapshot=dirty,
        )

        self.assertEqual(result.status, OperationStatus.VERIFIED)
        self.assertTrue(result.details["cleanupVerified"])
        self.assertTrue(result.details["reRunAttempted"])
        self.assertTrue(result.details["reRunVerified"])
        self.assertEqual(player._board_generation, 1)
        self.assertTrue(player._workflow_placement_transactions[0]["invalidatedByRerun"])
        self.assertFalse(player._workflow_placement_transactions[0]["validForCurrentBoard"])
        self.assertEqual(
            player._workflow_placement_transactions[0]["cleanupBoundaryId"],
            "cleanup-dirty-boundary",
        )
        self.assertEqual(
            player._workflow_placement_transactions[0]["boardGenerationBeforeRerun"],
            0,
        )
        self.assertEqual(
            player._workflow_placement_transactions[0]["invalidatedByCleanupBoundary"],
            "cleanup-dirty-boundary",
        )
        player.restart_game_to_initial_state.assert_called_once()

    def test_unknown_boundary_runs_one_rerun_but_missing_post_signal_stays_unknown(self):
        from placement_verifier import PlacementSnapshot

        player = self._cleanup_boundary_player()
        dirty = self._state(1, 1, ["1"], ["1"])
        missing_signal = {
            "sequence": 2,
            "timestamp": 2.0,
            "characters_count": 0,
            "putted_characters": 0,
            "polos_array": ["0"],
            # ``characters_array`` existed in the baseline and is deliberately
            # absent after Re-Run, so the reset cannot be verified.
        }
        context = OperationContext.begin(
            "cleanup",
            None,
            max_input_attempts=20,
            operation_id="cleanup-unknown-boundary",
        )
        context.cleanup_boundary_id = "cleanup-unknown-boundary"
        context.set_baseline(PlacementSnapshot.from_mapping(dirty))
        player.restart_game_to_initial_state = Mock(
            return_value=OperationResult(
                OperationStatus.RECOVERED,
                direction="recovery",
                details={"postState": missing_signal},
            )
        )

        result = player._run_cleanup_boundary_recovery(
            context,
            use_cdp=False,
            baseline=dirty,
            current_snapshot={"sequence": 2, "timestamp": 2.0},
        )

        self.assertEqual(result.status, OperationStatus.UNKNOWN)
        self.assertEqual(result.details["boardState"], "UNKNOWN")
        self.assertTrue(result.details["reRunAttempted"])
        self.assertFalse(result.details["reRunVerified"])
        self.assertTrue(result.details["contaminationCarriedForward"])
        player.restart_game_to_initial_state.assert_called_once()

    def test_rerun_failure_is_terminal_for_boundary_and_is_not_repeated(self):
        from placement_verifier import PlacementSnapshot

        player = self._cleanup_boundary_player()
        dirty = self._state(1, 1, ["1"], ["1"])
        context = OperationContext.begin(
            "cleanup",
            None,
            max_input_attempts=20,
            operation_id="cleanup-failed-rerun",
        )
        context.cleanup_boundary_id = "cleanup-failed-rerun"
        context.set_baseline(PlacementSnapshot.from_mapping(dirty))
        player.restart_game_to_initial_state = Mock(
            return_value=OperationResult(
                OperationStatus.FAILED,
                direction="recovery",
                message="rerun_click_failed",
                details={"postState": dirty},
            )
        )

        first = player._run_cleanup_boundary_recovery(
            context,
            use_cdp=False,
            baseline=dirty,
            current_snapshot=dirty,
        )
        second = player._run_cleanup_boundary_recovery(
            context,
            use_cdp=False,
            baseline=dirty,
            current_snapshot=dirty,
        )

        self.assertEqual(first.status, OperationStatus.FAILED)
        self.assertEqual(second.status, OperationStatus.FAILED)
        self.assertTrue(second.details["cleanupDecisionComplete"])
        self.assertTrue(second.details["reRunAttempted"])
        self.assertFalse(second.details["reRunVerified"])
        player.restart_game_to_initial_state.assert_called_once()

    def test_conflicting_cleanup_signals_are_unknown_not_dirty(self):
        player = self._cleanup_boundary_player()
        state = self._state(1, 1, ["0"], ["0"])
        board_state, evidence = player._classify_cleanup_board(state)

        self.assertEqual(board_state, "UNKNOWN")
        self.assertEqual(evidence["reason"], "cleanup_signal_conflict")


if __name__ == "__main__":
    unittest.main()

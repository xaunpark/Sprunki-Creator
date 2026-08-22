import unittest

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
    evaluate_transition,
    is_progress_verified,
    observe_until_verified,
    promote_aggregate_transition,
    wait_for_stable_snapshot,
)


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
    def test_target_specific_polos_transition_is_verified(self):
        before = _snapshot(
            1,
            1,
            characters_count=0,
            putted_characters=0,
            polos_array=["0"],
        )
        after = _snapshot(
            2,
            2,
            characters_count=1,
            putted_characters=1,
            polos_array=["1"],
        )

        evaluation = evaluate_transition(before, after, "place", 0)

        self.assertEqual(evaluation.status, OperationStatus.VERIFIED)
        self.assertTrue(evaluation.target_specific)
        self.assertIn("polos_target", evaluation.evidence)
        self.assertFalse(evaluation.aggregate_fallback)

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

        self.assertEqual(
            classify_precondition(placed, "place", 0),
            OperationStatus.ALREADY_PLACED,
        )
        self.assertEqual(
            classify_precondition(empty, "return", 0),
            OperationStatus.ALREADY_EMPTY,
        )
        self.assertIsNone(classify_precondition(unknown, "return", 0))

        self.assertEqual(
            classify_precondition(
                _snapshot(4, 4, characters_array=["n-1"]),
                "place",
                0,
            ),
            OperationStatus.ALREADY_PLACED,
        )

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


if __name__ == "__main__":
    unittest.main()

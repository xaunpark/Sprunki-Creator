import math
import os
import tempfile
import time
import threading
import unittest
from datetime import datetime
from types import SimpleNamespace

from tracker_reporting import build_tracker_diagnostic_report
from watchdog_state import (
    DEFAULT_CLEANUP_GRACE_SECONDS,
    DEFAULT_IDLE_TIMEOUT_SECONDS,
    DEFAULT_JOB_TIMEOUT_MINUTES,
    DEFAULT_MAX_FULL_RETRIES,
    DEFAULT_RETRY_MIN_REMAINING_SECONDS,
    WatchdogState,
    attempt_scoped_filename,
    load_watchdog_settings,
)
from workflow_quality import PASS, QualityGateResult, WorkflowReport, failure_is_retryable


class FakeClock:
    def __init__(self, value=0.0):
        self.value = float(value)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += float(seconds)


class WatchdogTests(unittest.TestCase):
    def test_missing_and_invalid_config_values_use_safe_defaults(self):
        settings = load_watchdog_settings(lambda key, default=None: default)
        self.assertEqual(settings["job_timeout_minutes"], DEFAULT_JOB_TIMEOUT_MINUTES)
        self.assertEqual(settings["idle_timeout_seconds"], DEFAULT_IDLE_TIMEOUT_SECONDS)
        self.assertEqual(settings["cleanup_grace_seconds"], DEFAULT_CLEANUP_GRACE_SECONDS)
        self.assertEqual(settings["max_full_retries"], DEFAULT_MAX_FULL_RETRIES)
        self.assertEqual(
            settings["retry_min_remaining_seconds"],
            DEFAULT_RETRY_MIN_REMAINING_SECONDS,
        )

        invalid = {
            "processing.job_timeout_minutes": 0,
            "processing.watchdog_idle_timeout_seconds": -1,
            "processing.watchdog_cleanup_grace_seconds": "not-a-number",
            "processing.watchdog_max_full_retries": -3,
            "processing.watchdog_retry_min_remaining_seconds": math.inf,
        }
        settings = load_watchdog_settings(
            lambda key, default=None: invalid.get(key, default)
        )
        self.assertEqual(settings["job_timeout_minutes"], DEFAULT_JOB_TIMEOUT_MINUTES)
        self.assertEqual(settings["idle_timeout_seconds"], DEFAULT_IDLE_TIMEOUT_SECONDS)
        self.assertEqual(settings["cleanup_grace_seconds"], DEFAULT_CLEANUP_GRACE_SECONDS)
        self.assertEqual(settings["max_full_retries"], DEFAULT_MAX_FULL_RETRIES)
        self.assertEqual(
            settings["retry_min_remaining_seconds"],
            DEFAULT_RETRY_MIN_REMAINING_SECONDS,
        )

    def test_progress_refreshes_idle_but_never_hard_deadline(self):
        clock = FakeClock()
        state = WatchdogState(
            100,
            hard_timeout_seconds=600,
            idle_timeout_seconds=240,
            clock=clock,
        )
        deadline = state.snapshot()["deadline_at_monotonic"]

        clock.advance(239)
        state.touch("remix", "drag_verified", icon_index=1)
        clock.advance(239)
        snapshot = state.snapshot()
        self.assertLess(snapshot["last_progress_age_seconds"], 240)
        self.assertEqual(snapshot["deadline_at_monotonic"], deadline)

        clock.advance(122)
        self.assertEqual(state.snapshot()["remaining_seconds"], 0.0)
        event = state.mark_timeout("hard_timeout")
        self.assertEqual(event["reason"], "hard_timeout")
        self.assertEqual(state.mark_timeout("hard_timeout"), None)

    def test_idle_timeout_is_retryable_without_resetting_total_budget(self):
        clock = FakeClock()
        state = WatchdogState(
            101,
            hard_timeout_seconds=3600,
            idle_timeout_seconds=240,
            clock=clock,
        )
        deadline = state.snapshot()["deadline_at_monotonic"]
        clock.advance(241)
        event = state.mark_timeout("idle_timeout")
        self.assertEqual(event["attempt"], 1)
        state.take_timeout()
        self.assertTrue(state.can_retry(900))
        self.assertTrue(state.begin_retry(2))
        self.assertEqual(state.snapshot()["attempt"], 2)
        self.assertEqual(state.snapshot()["deadline_at_monotonic"], deadline)

    def test_hard_timeout_cannot_start_retry_when_budget_is_short(self):
        clock = FakeClock()
        state = WatchdogState(
            102,
            hard_timeout_seconds=1000,
            idle_timeout_seconds=10,
            clock=clock,
        )
        clock.advance(101)
        state.mark_timeout("idle_timeout")
        state.take_timeout()
        self.assertFalse(state.can_retry(900))

    def test_watchdog_failure_is_retryable_except_manual_cancellation(self):
        self.assertTrue(failure_is_retryable("watchdog_timeout", {"reason": "idle_timeout"}))
        self.assertTrue(failure_is_retryable("watchdog_timeout", {"reason": "hard_timeout"}))
        self.assertFalse(
            failure_is_retryable("watchdog_timeout", {"reason": "service_cancelled"})
        )

    def test_early_idle_timeout_runs_one_full_workflow_retry(self):
        from creator_api_client import CreatorService

        class FakeState:
            queue_id = 104

            def __init__(self):
                self.timeout_pending = True
                self.attempt = 1
                self.finished_event = threading.Event()
                self.finished_event.set()

            def take_timeout(self):
                if self.timeout_pending:
                    self.timeout_pending = False
                    return {
                        "reason": "idle_timeout",
                        "attempt": self.attempt,
                        "elapsed_seconds": 241.0,
                        "hard_timeout_seconds": 3600.0,
                        "idle_timeout_seconds": 240.0,
                        "phase": "remix",
                        "substep": "started",
                    }
                return None

            def snapshot(self):
                return {"remaining_seconds": 3000.0}

            def can_retry(self, minimum):
                return True

            def begin_retry(self, attempt):
                self.attempt = attempt
                return True

            def remaining_seconds(self):
                return 3000.0

            def touch(self, *args, **kwargs):
                return None

        class Manager:
            def __init__(self):
                self.stop_event = threading.Event()
                self.should_stop = False
                self.workflow_attempt_index = 1

            def set_stop_event(self, event):
                self.stop_event = event

            def set_workflow_attempt(self, attempt):
                self.workflow_attempt_index = attempt

            def set_watchdog_progress_callback(self, callback):
                self.callback = callback

        class API:
            def __init__(self):
                self.progress = []

            def update_job_progress(self, *args, **kwargs):
                self.progress.append((args, kwargs))
                return True

        service = CreatorService.__new__(CreatorService)
        service.watchdog_settings = {
            "max_full_retries": 1,
            "retry_min_remaining_seconds": 900.0,
            "cleanup_grace_seconds": 120.0,
        }
        service.service_stop_event = threading.Event()
        service.processing_manager = Manager()
        service.api_client = API()
        service.game_player = None
        service._watchdog_states = {104: FakeState()}
        service._watchdog_states_lock = threading.RLock()
        service._manual_json_failure = None
        service._process_calls = []

        def fake_process(item, queue_id):
            service._process_calls.append(item.workflow_attempt)
            return item.workflow_attempt == 2

        service._process_with_smart_retry = fake_process
        service._run_watchdog_cleanup = lambda *args, **kwargs: {"status": "completed"}
        service.logger = SimpleNamespace(info=lambda *a, **k: None)
        item = SimpleNamespace(
            workflow_attempt=1,
            status="processing",
            error_message=None,
            failure_code=None,
            output_files=[],
            start_time=None,
            end_time=None,
            master_mode_status="not_started",
            individual_mode_status="not_started",
            workflow_report=None,
            recording_area=None,
            recorded_identity_status="not_started",
            recorded_identity_reason=None,
            recorded_identity_retryable=False,
            recorded_identity_files=[],
            watchdog_evidence={},
        )
        initial_event = threading.Event()
        initial_thread = threading.Thread(target=lambda: None)
        initial_thread.start()
        success, failure, _ = service._run_workflow_with_watchdog(
            item,
            104,
            service._watchdog_states[104],
            initial_workflow_stop_event=initial_event,
            initial_watcher_thread=initial_thread,
        )
        self.assertTrue(success)
        self.assertIsNone(failure)
        self.assertEqual(service._process_calls, [1, 2])
        self.assertEqual(item.workflow_attempt, 2)
        self.assertTrue(
            any(args[2] == "watchdog_recovery" for args, _ in service.api_client.progress)
        )

    def test_retry_output_names_are_distinct(self):
        self.assertEqual(attempt_scoped_filename("remix-1.mp4", 1), "remix-1.mp4")
        self.assertEqual(
            attempt_scoped_filename("remix-1.mp4", 2),
            "remix-1-attempt-2.mp4",
        )
        self.assertEqual(
            attempt_scoped_filename("n-3.mp4", 2), "n-3-attempt-2.mp4"
        )

    def test_cleanup_call_is_aborted_at_grace_deadline(self):
        from creator_api_client import CreatorService

        cleanup_event = __import__("threading").Event()
        completed, value, error = CreatorService._run_bounded_cleanup_call(
            lambda: time.sleep(0.2),
            cleanup_event,
            time.monotonic() + 0.02,
        )
        self.assertFalse(completed)
        self.assertIsNone(value)
        self.assertTrue(cleanup_event.is_set())
        self.assertIsNotNone(error)

    def test_diagnostic_keeps_bounded_attempt_references_and_marks_only_final(self):
        with tempfile.TemporaryDirectory() as folder:
            first = os.path.join(folder, "remix-1.mp4")
            second = os.path.join(folder, "remix-1-attempt-2.mp4")
            with open(first, "wb") as handle:
                handle.write(b"first")
            with open(second, "wb") as handle:
                handle.write(b"second")

            report = WorkflowReport(
                mode="master",
                runtime_state=PASS,
                runtime_verified=True,
                layout_icons=2,
                layout_slots=1,
                placement_before=0,
                placement_after=1,
                placement_verified=True,
                cleanup_verified=True,
                normal_icons_showcased=2,
                normal_icons_total=2,
                normal_remixes_played=2,
                normal_remixes_total=3,
            )
            report.apply_quality_gate(QualityGateResult.pass_result())
            item = SimpleNamespace(
                source_url="https://cocrea.world/@author/test",
                project_slug="author-test",
                mode="master",
                json_data={
                    "currentUrl": "https://cocrea.world/@author/test",
                    "layout": {
                        "icons": [{"index": 0}, {"index": 1}],
                        "slots": [{"index": 0}],
                    },
                },
                failure_code=None,
                error_message=None,
                start_time=datetime(2026, 8, 23, 1, 0, 0),
                end_time=datetime(2026, 8, 23, 1, 0, 3),
                output_files=[second],
                workflow_attempt=2,
                attempt_outputs={1: [first], 2: [second]},
                recorded_identity_status="not_started",
                recorded_identity_reason=None,
                recorded_identity_retryable=False,
                recorded_identity_files=[],
                watchdog_evidence={},
            )
            diagnostic = build_tracker_diagnostic_report(
                {"id": 103, "production_mode": "master"},
                item,
                report,
                terminal_status="completed",
            )
            refs = diagnostic["outputs"]["attempt_output_references"]["attempts"]
            self.assertFalse(refs[0]["published"])
            self.assertTrue(refs[1]["published"])
            self.assertNotIn(folder, str(diagnostic))


if __name__ == "__main__":
    unittest.main()

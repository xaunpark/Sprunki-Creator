import unittest

from cdp_gesture_executor import execute_cdp_gesture
from manual_prepare_gestures import (
    DOUBLE_CLICK_DRAG_TYPE,
    action_timing_ms,
    can_merge_click_and_drag,
    force_double_click_drag,
    merge_click_and_drag,
    split_double_click_drag,
)
from manual_prepare_timeline import (
    replay_prepare_timeline,
    validate_prepare_timeline,
)


class ManualPrepareGestureTests(unittest.TestCase):
    def test_recorded_click_and_drag_merge_into_one_compound_action(self):
        click = {
            "type": "click",
            "x": 1013,
            "y": 864,
            "delayMs": 2187,
            "pressDurationMs": 85,
        }
        drag = {
            "type": "drag",
            "startX": 1013,
            "startY": 864,
            "endX": 990,
            "endY": 560,
            "delayMs": 313,
            "dragDurationMs": 1484,
            "holdBeforeMoveMs": 240,
        }

        self.assertTrue(can_merge_click_and_drag(click, drag))
        compound = merge_click_and_drag(click, drag)
        self.assertEqual(compound["type"], DOUBLE_CLICK_DRAG_TYPE)
        self.assertEqual(compound["delayMs"], 2187)
        self.assertEqual(compound["clickIntervalMs"], 313)
        self.assertEqual(compound["dragDurationMs"], 1484)
        self.assertEqual(action_timing_ms(compound), 3984)

    def test_far_or_slow_click_and_drag_are_not_merged(self):
        click = {"type": "click", "x": 100, "y": 100, "delayMs": 0}
        drag = {
            "type": "drag",
            "startX": 130,
            "startY": 100,
            "endX": 200,
            "endY": 200,
            "delayMs": 100,
            "dragDurationMs": 300,
        }
        self.assertFalse(can_merge_click_and_drag(click, drag))
        drag["startX"] = 100
        drag["startY"] = 100
        drag["delayMs"] = 501
        self.assertFalse(can_merge_click_and_drag(click, drag))

    def test_forced_override_round_trips_to_original_drag_timing(self):
        drag = {
            "type": "drag",
            "startX": 10,
            "startY": 20,
            "endX": 30,
            "endY": 40,
            "delayMs": 100,
            "dragDurationMs": 500,
        }
        compound, extension_ms = force_double_click_drag(drag)
        self.assertEqual(extension_ms, 200)
        self.assertTrue(compound["syntheticFirstClick"])
        self.assertEqual(split_double_click_drag(compound)[0]["delayMs"], 100)

    def test_timeline_accepts_compound_action_and_replays_once(self):
        action = {
            "type": DOUBLE_CLICK_DRAG_TYPE,
            "startX": 10,
            "startY": 20,
            "endX": 30,
            "endY": 40,
            "delayMs": 100,
            "clickIntervalMs": 300,
            "firstClickDurationMs": 100,
            "holdBeforeMoveMs": 200,
            "dragDurationMs": 500,
        }
        profile = {"prepareTiming": {"endDelayMs": 50, "durationMs": 950}}
        self.assertEqual(validate_prepare_timeline([action], profile)[0], True)
        executed = []
        self.assertTrue(
            replay_prepare_timeline(
                [action],
                profile,
                lambda index, value: executed.append((index, value["type"])) or True,
                sleep_fn=lambda _seconds: None,
            )
        )
        self.assertEqual(executed, [(1, DOUBLE_CLICK_DRAG_TYPE)])

    def test_timeline_safety_total_counts_click_press_duration(self):
        actions = [
            {"type": "click", "x": 10, "y": 20, "pressDurationMs": 600_000}
            for _ in range(4)
        ]
        self.assertFalse(validate_prepare_timeline(actions, {})[0])

    def test_timeline_safety_total_counts_first_click_and_not_hold_twice(self):
        double_click_actions = [
            {
                "type": DOUBLE_CLICK_DRAG_TYPE,
                "startX": 10,
                "startY": 20,
                "endX": 30,
                "endY": 40,
                "clickIntervalMs": 600_000,
                "firstClickDurationMs": 600_000,
                "holdBeforeMoveMs": 0,
                "dragDurationMs": 0,
            }
            for _ in range(2)
        ]
        self.assertFalse(validate_prepare_timeline(double_click_actions, {})[0])

        drag_actions = [
            {
                "type": "drag",
                "startX": 10,
                "startY": 20,
                "endX": 30,
                "endY": 40,
                "dragDurationMs": 600_000,
                "holdBeforeMoveMs": 600_000,
            }
            for _ in range(3)
        ]
        self.assertTrue(validate_prepare_timeline(drag_actions, {})[0])

    def test_executor_emits_one_compound_event_sequence(self):
        events = []

        def send(_ws, command):
            events.append(command)
            return {}

        result = execute_cdp_gesture(
            object(),
            send,
            100,
            200,
            300,
            400,
            mode=DOUBLE_CLICK_DRAG_TYPE,
            drag_duration_ms=400,
            click_interval_ms=300,
            first_click_duration_ms=100,
            hold_before_move_ms=200,
            move_steps=2,
            sleep_fn=lambda _seconds: None,
        )

        self.assertTrue(result.success)
        mouse_events = [event["params"] for event in events]
        presses = [event for event in mouse_events if event["type"] == "mousePressed"]
        releases = [event for event in mouse_events if event["type"] == "mouseReleased"]
        self.assertEqual([event["clickCount"] for event in presses], [1, 2])
        self.assertEqual([event["clickCount"] for event in releases], [1, 2])
        self.assertEqual(len([event for event in mouse_events if event["type"] == "mouseMoved"]), 3)


if __name__ == "__main__":
    unittest.main()

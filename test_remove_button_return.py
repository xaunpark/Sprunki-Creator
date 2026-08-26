import threading
import unittest
from unittest.mock import Mock, patch

from json_generator import SprunkiJSONGenerator
from placement_verifier import OperationContext, OperationResult, OperationStatus, PlacementSnapshot
from sprunki_game_player import SprunkiGamePlayer


def _occupied_snapshot(slot_count=1):
    return PlacementSnapshot.from_mapping({
        "timestamp": 1,
        "sequence": 1,
        "characters_count": 1,
        "putted_characters": 1,
        "characters_array": ["1"] * slot_count,
    })


def _empty_snapshot():
    return PlacementSnapshot.from_mapping({
        "timestamp": 2,
        "sequence": 2,
        "characters_count": 0,
        "putted_characters": 0,
        "characters_array": ["0"],
    })


def _remove_config():
    return {
        "layout": {
            "returnControls": {
                "schemaVersion": 1,
                "remove": {
                    "enabled": True,
                    "targetName": "Mute Buttons",
                    "costumeName": "Remove",
                    "coordinateSystem": "reference-screen-1920x1080",
                    "referenceScreen": {"width": 1920, "height": 1080},
                    "maxAttempts": 9,
                    "clickOffsets": [
                        [0, 0], [-5, 0], [5, 0], [0, -5], [0, 5],
                        [-8, -8], [8, -8], [-8, 8], [8, 8],
                    ],
                },
            },
            "slots": [{
                "index": 0,
                "x": 386,
                "y": 655,
                "removeButton": {
                    "clickX": 386,
                    "clickY": 655,
                    "bounds": {
                        "left": 373,
                        "top": 645,
                        "width": 25,
                        "height": 21,
                        "right": 398,
                        "bottom": 666,
                    },
                    "safeJitterPx": 8,
                    "resolved": True,
                },
            }],
        }
    }


class RemoveButtonReturnTests(unittest.TestCase):
    def test_remove_candidates_are_bounded_by_captured_bounds(self):
        player = object.__new__(SprunkiGamePlayer)
        player.config = _remove_config()

        candidates = player._remove_button_click_candidates(
            player.config["layout"]["slots"][0]
        )

        self.assertEqual(len(candidates), 9)
        self.assertEqual((candidates[0]["x"], candidates[0]["y"]), (386, 655))
        self.assertTrue(all(373 <= item["x"] <= 398 for item in candidates))
        self.assertTrue(all(645 <= item["y"] <= 666 for item in candidates))

    def test_old_json_without_remove_metadata_uses_no_remove_candidates(self):
        player = object.__new__(SprunkiGamePlayer)
        player.config = {"layout": {"slots": [{"index": 0, "x": 386, "y": 655}]}}

        self.assertEqual(
            player._remove_button_click_candidates(player.config["layout"]["slots"][0]),
            [],
        )

    def test_remove_first_stops_after_target_specific_success(self):
        player = object.__new__(SprunkiGamePlayer)
        player.config = _remove_config()
        player.stop_event = threading.Event()
        player.remove_button_verify_timeout_ms = 250

        context = OperationContext.begin(
            "return",
            0,
            max_input_attempts=20,
            deadline_seconds=10,
            operation_id="test-remove",
        )
        player._active_operation_context = context
        player._capture_stable_placement_snapshot = Mock(
            side_effect=[_occupied_snapshot(), _empty_snapshot()]
        )
        player._execute_precision_return_click = Mock(side_effect=[
            OperationResult(OperationStatus.FAILED, message="no_transition"),
            OperationResult(
                OperationStatus.VERIFIED,
                details={"targetSpecific": True},
                evidence=("remove_target_verified",),
            ),
        ])

        with patch("builtins.print"):
            result = player._try_remove_button_return(
                player.config["layout"]["slots"][0],
                use_cdp=True,
                operation_context=context,
                before_snapshot=_occupied_snapshot(),
                target_slot_index=0,
            )

        self.assertEqual(result.status, OperationStatus.ALREADY_EMPTY)
        self.assertTrue((result.details or {}).get("targetSpecific"))
        self.assertEqual(player._execute_precision_return_click.call_count, 2)
        first = player._execute_precision_return_click.call_args_list[0].args[0]
        second = player._execute_precision_return_click.call_args_list[1].args[0]
        self.assertEqual((first["x"], first["y"]), (386, 655))
        self.assertEqual((second["x"], second["y"]), (381, 655))

    def test_remove_action_is_sent_even_when_baseline_looks_empty(self):
        player = object.__new__(SprunkiGamePlayer)
        player.config = _remove_config()
        player.stop_event = threading.Event()
        player.remove_button_verify_timeout_ms = 250

        context = OperationContext.begin(
            "return",
            0,
            max_input_attempts=20,
            deadline_seconds=10,
            operation_id="test-action-first",
        )
        player._active_operation_context = context
        empty = PlacementSnapshot.from_mapping({
            "timestamp": 1,
            "sequence": 1,
            "characters_count": 0,
            "putted_characters": 0,
        })
        player._capture_stable_placement_snapshot = Mock(return_value=empty)
        player._execute_precision_return_click = Mock(
            return_value=OperationResult(OperationStatus.FAILED, message="no_transition")
        )

        with patch("builtins.print"):
            result = player._try_remove_button_return(
                player.config["layout"]["slots"][0],
                use_cdp=True,
                operation_context=context,
                before_snapshot=empty,
                target_slot_index=0,
            )

        self.assertEqual(result.status, OperationStatus.ALREADY_EMPTY)
        self.assertEqual(player._execute_precision_return_click.call_count, 1)
        first = player._execute_precision_return_click.call_args_list[0].args[0]
        self.assertEqual((first["x"], first["y"]), (386, 655))

    def test_remove_action_is_sent_when_baseline_is_unavailable(self):
        player = object.__new__(SprunkiGamePlayer)
        player.config = _remove_config()
        player.stop_event = threading.Event()
        player.remove_button_verify_timeout_ms = 250

        context = OperationContext.begin(
            "return",
            0,
            max_input_attempts=20,
            deadline_seconds=10,
            operation_id="test-unknown-baseline",
        )
        player._active_operation_context = context
        player._capture_stable_placement_snapshot = Mock(return_value=None)
        player._execute_precision_return_click = Mock(
            return_value=OperationResult(OperationStatus.FAILED, message="no_transition")
        )

        with patch("builtins.print"):
            result = player._try_remove_button_return(
                player.config["layout"]["slots"][0],
                use_cdp=True,
                operation_context=context,
                before_snapshot=None,
                target_slot_index=0,
            )

        self.assertEqual(result.status, OperationStatus.UNKNOWN)
        self.assertEqual(result.message, "remove_button_post_verify_unknown")
        self.assertEqual(player._execute_precision_return_click.call_count, 1)

    def test_remove_jitter_stops_when_post_action_verification_is_unknown(self):
        player = object.__new__(SprunkiGamePlayer)
        player.config = _remove_config()
        player.stop_event = threading.Event()
        player.remove_button_verify_timeout_ms = 250

        context = OperationContext.begin(
            "return",
            0,
            max_input_attempts=20,
            deadline_seconds=10,
            operation_id="test-unknown-post-verify",
        )
        player._active_operation_context = context
        player._capture_stable_placement_snapshot = Mock(return_value=None)
        player._execute_precision_return_click = Mock(
            return_value=OperationResult(OperationStatus.FAILED, message="no_transition")
        )

        with patch("builtins.print"):
            result = player._try_remove_button_return(
                player.config["layout"]["slots"][0],
                use_cdp=True,
                operation_context=context,
                before_snapshot=_occupied_snapshot(),
                target_slot_index=0,
            )

        self.assertEqual(result.status, OperationStatus.UNKNOWN)
        self.assertEqual(result.message, "remove_button_post_verify_unknown")
        self.assertEqual(player._execute_precision_return_click.call_count, 1)

    def test_corrected_return_routes_remove_before_legacy_precision(self):
        player = object.__new__(SprunkiGamePlayer)
        player.config = _remove_config()
        player.stop_event = threading.Event()
        player.obs_controller = None
        player._operation_counter = 0
        player._active_operation_context = None
        player._session_recovery_count = 0
        player._max_session_recoveries = 1
        player.verifier_max_read_failures = 5
        player.remove_button_verify_timeout_ms = 250
        player._cleanup_input_budget = lambda: 20
        player._cleanup_deadline_seconds = lambda: 10.0

        occupied = _occupied_snapshot()
        empty = PlacementSnapshot.from_mapping({
            "timestamp": 2,
            "sequence": 2,
            "characters_count": 0,
            "putted_characters": 0,
        })
        player._capture_stable_placement_snapshot = Mock(
            side_effect=[occupied, empty]
        )
        player._execute_precision_return_click = Mock(
            return_value=OperationResult(OperationStatus.FAILED, message="no_transition")
        )

        with patch("builtins.print"):
            result = player.corrected_return_icon(
                player.config["layout"]["slots"][0],
                use_cdp=False,
            )

        self.assertEqual(result.status, OperationStatus.ALREADY_EMPTY)
        player._execute_precision_return_click.assert_called_once()
        clicked_slot = player._execute_precision_return_click.call_args.args[0]
        self.assertEqual((clicked_slot["x"], clicked_slot["y"]), (386, 655))

    def test_manual_layout_merge_preserves_manual_slots_and_adds_live_controls(self):
        manual_state = {
            "layout": {
                "icons": [{"index": 0, "x": 100, "y": 700}],
                "slots": [{"index": 0, "x": 386, "y": 655, "originalSprite": "Polo 1"}],
                "bonus": [],
            }
        }
        live_state = {
            "layout": {
                "returnControls": {
                    "schemaVersion": 1,
                    "remove": {"enabled": True},
                },
                "slots": [{
                    "index": 0,
                    "removeButton": {"resolved": True, "clickX": 386, "clickY": 655},
                }],
            }
        }

        merged = SprunkiJSONGenerator._merge_live_return_controls_into_manual_layout(
            manual_state,
            live_state,
        )

        self.assertEqual(merged["layout"]["icons"][0]["index"], 0)
        self.assertTrue(merged["layout"]["slots"][0]["removeButton"]["resolved"])
        self.assertEqual(merged["layout"]["returnControls"]["schemaVersion"], 1)


if __name__ == "__main__":
    unittest.main()

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
        "polos_array": ["1"] * slot_count,
        "characters_array": ["1"] * slot_count,
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
        player._return_target_occupancy = SprunkiGamePlayer._return_target_occupancy
        player._capture_stable_placement_snapshot = Mock(return_value=_occupied_snapshot())
        player._execute_precision_return_click = Mock(side_effect=[
            OperationResult(OperationStatus.FAILED, message="no_transition"),
            OperationResult(
                OperationStatus.VERIFIED,
                details={"targetSpecific": True},
                evidence=("polos_target",),
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

        self.assertEqual(result.status, OperationStatus.VERIFIED)
        self.assertTrue(result.target_verified)
        self.assertEqual(player._execute_precision_return_click.call_count, 2)
        first = player._execute_precision_return_click.call_args_list[0].args[0]
        second = player._execute_precision_return_click.call_args_list[1].args[0]
        self.assertEqual((first["x"], first["y"]), (386, 655))
        self.assertEqual((second["x"], second["y"]), (381, 655))

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

import threading
import unittest
from unittest.mock import Mock, patch

from json_generator import SprunkiJSONGenerator
from cdp_gesture_executor import execute_cdp_click
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
    def test_cdp_click_emits_complete_primary_button_gesture(self):
        commands = []

        def send_command(ws, command):
            commands.append(command)
            return {"id": len(commands), "result": {}}

        result = execute_cdp_click(
            object(),
            send_command,
            123,
            456,
            hold_ms=0,
            sleep_fn=lambda seconds: None,
        )

        self.assertTrue(result.success)
        self.assertEqual(
            [command["method"] for command in commands],
            [
                "Input.dispatchMouseEvent",
                "Input.dispatchMouseEvent",
                "Input.dispatchMouseEvent",
            ],
        )
        self.assertEqual(commands[0]["params"]["type"], "mouseMoved")
        self.assertEqual(commands[1]["params"]["buttons"], 1)
        self.assertEqual(commands[2]["params"]["buttons"], 0)
        self.assertEqual(commands[1]["params"]["clickCount"], 1)

    def test_exact_game_page_target_does_not_fallback_to_other_cocrea_tab(self):
        player = object.__new__(SprunkiGamePlayer)
        player.game_url = "https://www.cocrea.world/@Mirr/MyProject9"
        player.debugging_port = 9222
        player.ws_url = None
        player._remove_cdp_target_id = None

        response = Mock()
        response.json.return_value = [
            {
                "type": "page",
                "id": "wrong",
                "url": "https://www.cocrea.world/@Other/OtherProject",
                "webSocketDebuggerUrl": "ws://wrong",
            },
            {
                "type": "page",
                "id": "right",
                "url": "https://www.cocrea.world/@Mirr/MyProject9",
                "webSocketDebuggerUrl": "ws://right",
            },
        ]

        with patch("sprunki_game_player.requests.get", return_value=response), \
             patch("builtins.print"):
            target = player._find_exact_game_page_target()

        self.assertEqual(target["id"], "right")

    def test_exact_game_page_target_rejects_ambiguous_duplicate_tabs(self):
        player = object.__new__(SprunkiGamePlayer)
        player.game_url = "https://www.cocrea.world/@Mirr/MyProject9"
        player.debugging_port = 9222
        player.ws_url = None
        player._remove_cdp_target_id = None

        response = Mock()
        response.json.return_value = [
            {
                "type": "page",
                "id": "one",
                "url": "https://www.cocrea.world/@Mirr/MyProject9",
                "webSocketDebuggerUrl": "ws://one",
            },
            {
                "type": "page",
                "id": "two",
                "url": "https://www.cocrea.world/@Mirr/MyProject9/",
                "webSocketDebuggerUrl": "ws://two",
            },
        ]

        with patch("sprunki_game_player.requests.get", return_value=response), \
             patch("builtins.print"):
            target = player._find_exact_game_page_target()

        self.assertIsNone(target)

    def test_live_remove_resolver_uses_matching_runtime_target_and_bounds(self):
        player = object.__new__(SprunkiGamePlayer)
        player.game_url = "https://www.cocrea.world/@Mirr/MyProject9"
        player._send_cdp_command = Mock(return_value={
            "result": {
                "result": {
                    "value": {
                        "ok": True,
                        "page": {
                            "href": player.game_url,
                            "innerWidth": 1280,
                            "innerHeight": 720,
                            "devicePixelRatio": 1,
                        },
                        "canvas": {"scaleX": 2, "scaleY": 2},
                        "removeButtons": [
                            {
                                "runtimeIndex": 2,
                                "id": "target-right",
                                "logicX": 40,
                                "logicY": -120,
                                "clickX": 700,
                                "clickY": 600,
                                "bounds": {
                                    "left": 688,
                                    "top": 588,
                                    "right": 712,
                                    "bottom": 612,
                                },
                            },
                            {
                                "runtimeIndex": 1,
                                "id": "target-wrong",
                                "logicX": -40,
                                "logicY": -120,
                                "clickX": 300,
                                "clickY": 600,
                                "bounds": {
                                    "left": 288,
                                    "top": 588,
                                    "right": 312,
                                    "bottom": 612,
                                },
                            },
                        ],
                    }
                }
            }
        })

        target_slot = {
            "removeButton": {
                "logicX": 40,
                "logicY": -120,
                "clickX": 700,
                "clickY": 600,
            }
        }
        delivery = player._resolve_live_remove_button(
            object(),
            target_slot,
            700,
            600,
        )

        self.assertEqual(delivery["targetId"], "target-right")
        self.assertEqual((delivery["runtimeX"], delivery["runtimeY"]), (700, 600))

    def test_remove_input_uses_strict_cdp_even_when_cleanup_caller_requests_legacy_transport(self):
        player = object.__new__(SprunkiGamePlayer)
        player._active_operation_context = object()
        player._execute_remove_button_cdp_click = Mock(return_value=True)
        player._execute_pyautogui_single_click = Mock(return_value=True)
        target_slot = {"removeButton": {"resolved": True}}

        with patch("builtins.print"):
            result = player._execute_single_return_click(
                386,
                655,
                use_cdp=False,
                domain_adj={},
                target_slot=target_slot,
                operation_context=player._active_operation_context,
            )

        self.assertTrue(result)
        player._execute_remove_button_cdp_click.assert_called_once()
        player._execute_pyautogui_single_click.assert_not_called()

    def test_rerun_click_is_bound_to_visible_current_game_page(self):
        from types import SimpleNamespace

        player = object.__new__(SprunkiGamePlayer)
        player.game_url = "https://www.cocrea.world/@Mirr/MyProject9"
        session = {
            "operationId": "cleanup-1",
            "targetId": "game-target",
            "ws": object(),
        }
        player._get_remove_cdp_session = Mock(return_value=session)
        player._send_cdp_command = Mock()
        player._read_cdp_page_identity = Mock(
            return_value={
                "href": player.game_url,
                "visibilityState": "visible",
            }
        )
        player._normalize_cdp_page_url = lambda value: str(value).rstrip("/")
        player._close_remove_cdp_session = Mock()

        with patch(
            "sprunki_game_player.execute_cdp_click",
            return_value=SimpleNamespace(success=True, error=None),
        ) as click:
            delivered, evidence = player._execute_rerun_button_click(
                SimpleNamespace(operation_id="cleanup-1")
            )

        self.assertTrue(delivered)
        self.assertEqual(evidence["targetId"], "game-target")
        self.assertEqual(evidence["pageUrl"], player.game_url)
        click.assert_called_once()
        self.assertEqual(click.call_args.args[2:4], (40, 75))
        player._close_remove_cdp_session.assert_not_called()

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
                evidence=("characters_target",),
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

    def test_remove_first_stops_jitter_when_polos_shape_is_uncertain(self):
        player = object.__new__(SprunkiGamePlayer)
        player.config = _remove_config()
        player.stop_event = threading.Event()
        player.remove_button_verify_timeout_ms = 250

        context = OperationContext.begin(
            "return",
            0,
            max_input_attempts=20,
            deadline_seconds=10,
            operation_id="test-remove-uncertain-polos",
        )
        player._active_operation_context = context
        player._capture_stable_placement_snapshot = Mock(return_value=_occupied_snapshot())
        player._execute_precision_return_click = Mock(
            return_value=OperationResult(
                OperationStatus.FAILED,
                message="expected_transition_not_observed",
                details={"polosTransitionReason": "length_changed"},
            )
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
        self.assertEqual(player._execute_precision_return_click.call_count, 1)

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

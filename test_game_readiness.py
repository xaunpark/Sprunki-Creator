import threading
from unittest.mock import Mock, patch

from sprunki_game_player import SprunkiGamePlayer


GAME_URL = "https://www.cocrea.world/@Example/SlowMod"


def make_player(probes, *, stall_timeout=0.05, phase_timeout=0.5):
    player = object.__new__(SprunkiGamePlayer)
    player.game_url = GAME_URL
    player.game_verification_enabled = True
    player.game_verification_timeout = 1.0
    player.game_load_stall_timeout = stall_timeout
    player.game_load_phase_timeout = phase_timeout
    player.game_entry_total_timeout = 1.0
    player.game_probe_timeout = 0.05
    player.game_readiness_poll_intervals = (0.001, 0.001, 0.001)
    player.run_button_max_retries = 3
    player.stop_event = threading.Event()
    player._entry_attempt_id = "entry-test"
    player._game_entry_deadline = None
    player._cdp_target_id = "target-test"
    player._cdp_target_url = GAME_URL
    player._last_game_readiness_probe = {}
    player._last_game_weak_token = None
    player.entry_strategy = {"selected": None, "tested": [], "verification": "UNKNOWN"}
    player._execute_verification_js = Mock(side_effect=probes)
    player._touch_watchdog_progress = Mock()
    return player


def readiness_probe(*, run=False, loading=False, percent=None, strong=None, weak=None):
    return {
        "pageReady": True,
        "documentState": "complete",
        "pageIdentity": {"href": GAME_URL, "isTopFrame": True},
        "errorPage": {"detected": False},
        "stage": {
            "found": True,
            "visible": True,
            "bounds": {"left": 0, "top": 0, "width": 640, "height": 360},
            "identity": {"className": "ccw-stage-wrapper"},
            "childCount": 2,
        },
        "runButton": {
            "found": run,
            "visible": run,
            "enabled": run,
            "clickable": run,
            "strategy": "specific_run_class" if run else None,
            "bounds": {"centerX": 320, "centerY": 180} if run else None,
        },
        "loading": {"active": loading, "percent": percent, "label": "Loading 43%" if loading else ""},
        "runtime": {"responding": True, "canvasReady": True, "targetCount": 10},
        "stageActive": not loading and not run,
        "prepareReady": not loading and not run,
        "networkActivity": {"resourceCount": 1, "transferredBytes": 100},
        "strongProgressToken": strong if strong is not None else {"loading": loading, "run": run},
        "weakProgressToken": weak if weak is not None else {"resourceCount": 1},
    }


def test_generic_run_text_without_semantic_button_is_not_ready():
    player = make_player([readiness_probe(run=False, strong={"step": 1})], stall_timeout=0.01)
    with patch("sprunki_game_player.time.sleep", return_value=None):
        result = player.verify_game_components_ready(timeout_override=0.02)

    assert not result
    assert result.failure_code in {"game_load_stalled", "game_load_timeout"}
    assert not result.evidence["runButtonEvidence"].get("found")


def test_loading_43_percent_is_not_playable_even_if_probe_repeats():
    probes = [
        readiness_probe(loading=True, percent=43, strong={"percent": 43}),
        readiness_probe(loading=True, percent=43, strong={"percent": 43}),
    ]
    player = make_player(probes, stall_timeout=0.01)
    with patch("sprunki_game_player.time.sleep", return_value=None):
        result = player.verify_game_components_ready(timeout_override=0.02)

    assert not result
    assert result.failure_code == "game_load_stalled"
    assert result.evidence["lastLoadingPercent"] == 43


def test_strong_progress_can_reach_run_without_fixed_long_sleep():
    player = make_player([
        readiness_probe(strong={"phase": "dom"}),
        readiness_probe(strong={"phase": "canvas"}),
        readiness_probe(run=True, strong={"phase": "run"}),
    ])
    with patch("sprunki_game_player.time.sleep", return_value=None) as sleep:
        result = player.verify_game_components_ready()

    assert result.passed
    assert result.evidence["adaptiveLoading"]["progressExtensions"] >= 1
    assert sleep.call_count == 2


def test_resource_only_activity_cannot_extend_stall_forever():
    probes = [
        readiness_probe(strong={"phase": "same"}, weak={"resourceCount": 1}),
        readiness_probe(strong={"phase": "same"}, weak={"resourceCount": 2}),
        readiness_probe(strong={"phase": "same"}, weak={"resourceCount": 3}),
    ]
    player = make_player(probes, stall_timeout=0.01, phase_timeout=0.2)
    with patch("sprunki_game_player.time.sleep", return_value=None):
        result = player.verify_game_components_ready()

    assert not result
    assert result.failure_code in {"game_load_stalled", "game_load_timeout"}
    assert result.evidence["weakProgressObservations"] >= 1


def test_one_cdp_probe_failure_is_recovered_by_the_next_probe():
    player = make_player([None, readiness_probe(run=True)])
    with patch("sprunki_game_player.time.sleep", return_value=None):
        result = player.verify_game_components_ready()

    assert result.passed
    assert player._execute_verification_js.call_count == 2


def test_two_consecutive_cdp_probe_failures_are_stalled():
    player = make_player([None, None], stall_timeout=1.0)
    with patch("sprunki_game_player.time.sleep", return_value=None):
        result = player.verify_game_components_ready()

    assert not result
    assert result.failure_code == "game_load_stalled"
    assert result.evidence["probeAttempts"] == 2


def test_page_identity_matching_does_not_accept_another_mod_as_the_target():
    assert SprunkiGamePlayer._cdp_urls_match(GAME_URL, GAME_URL)
    assert not SprunkiGamePlayer._cdp_urls_match(
        GAME_URL,
        "https://www.cocrea.world/@Example/SlowMod-Other",
    )
    assert not SprunkiGamePlayer._cdp_urls_match(
        GAME_URL,
        "https://www.cocrea.world/@Example/SlowMod?other=1",
    )


def test_stage_gate_requires_two_active_samples_before_prepare():
    player = make_player([])
    player._read_game_readiness_probe = Mock(side_effect=[
        readiness_probe(loading=True, percent=43, strong={"percent": 43}),
        readiness_probe(strong={"phase": "active"}),
        readiness_probe(strong={"phase": "active"}),
    ])
    with patch("sprunki_game_player.time.sleep", return_value=None):
        result = player.wait_for_prepare_ready()

    assert result.passed
    assert player._game_readiness_state == "PREPARE_READY"
    assert player._read_game_readiness_probe.call_count == 3


def test_playable_runtime_waits_for_late_icons_and_slots():
    player = make_player([])
    player._read_game_readiness_probe = Mock(side_effect=[
        readiness_probe(strong={"phase": "active"}),
        readiness_probe(strong={"phase": "active"}),
        readiness_probe(strong={"phase": "active"}),
    ])
    player._get_websocket_url = Mock(return_value="ws://game")
    player._evaluate_entry_state_via_ws = Mock(side_effect=[
        {"ok": True, "iconCount": 0, "slotCount": 0, "openPanelIndicators": []},
        {"ok": True, "iconCount": 3, "slotCount": 5, "openPanelIndicators": []},
        {"ok": True, "iconCount": 3, "slotCount": 5, "openPanelIndicators": []},
    ])
    fake_ws = Mock()
    with patch("sprunki_game_player.websocket.create_connection", return_value=fake_ws), \
            patch("sprunki_game_player.time.sleep", return_value=None):
        result = player.verify_playable_runtime_entry()

    assert result.passed
    assert result.evidence["stableSamples"] == 2
    assert player._game_readiness_state == "PLAYABLE_RUNTIME"


def test_run_click_uses_same_cdp_target_and_verifies_transition():
    player = make_player([])
    player._get_websocket_url = Mock(return_value="ws://game")
    player._read_cdp_page_identity = Mock(return_value={"href": GAME_URL, "visibilityState": "visible"})
    player._evaluate_game_readiness_on_ws = Mock(side_effect=[
        readiness_probe(run=True),
        readiness_probe(run=False),
    ])
    def send(_ws, command, **_kwargs):
        if command.get("method") == "Runtime.evaluate":
            return {"result": {"result": {"value": {"success": True}}}}
        return {"result": {}}

    player._send_cdp_command = Mock(side_effect=send)
    fake_ws = Mock()
    with patch("sprunki_game_player.websocket.create_connection", return_value=fake_ws), \
            patch("sprunki_game_player.time.sleep", return_value=None):
        result = player._click_run_button_cdp_only()

    assert result.passed
    assert result.evidence["method"] == "same_cdp_session_mouse_events"
    assert result.evidence["dispatchesDelivered"] == 3
    event_methods = [call.args[1]["method"] for call in player._send_cdp_command.call_args_list]
    assert event_methods.count("Input.dispatchMouseEvent") == 3
    fake_ws.close.assert_called_once()


def test_run_click_does_not_dispatch_old_bounds_when_candidate_disappears():
    player = make_player([])
    player._get_websocket_url = Mock(return_value="ws://game")
    player._read_cdp_page_identity = Mock(return_value={"href": GAME_URL, "visibilityState": "visible"})
    player._send_cdp_command = Mock(side_effect=lambda _ws, command, **_kwargs: (
        {"result": {"result": {"value": {"success": False, "reason": "candidate_disappeared"}}}}
        if command.get("method") == "Runtime.evaluate" else {"result": {}}
    ))
    fake_ws = Mock()
    with patch("sprunki_game_player.websocket.create_connection", return_value=fake_ws):
        result = player._click_run_button_cdp_only()

    assert not result
    assert result.failure_code == "run_button_not_verified"
    event_methods = [call.args[1]["method"] for call in player._send_cdp_command.call_args_list]
    assert "Input.dispatchMouseEvent" not in event_methods

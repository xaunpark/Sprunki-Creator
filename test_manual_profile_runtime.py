import json
import os
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

import manual_profile_manager as profile_manager
from json_generator import SprunkiJSONGenerator
from sprunki_game_player import SprunkiGamePlayer
from workflow_quality import QualityGateResult


GAME_URL = "https://www.cocrea.world/@Example/ManualMod"
OTHER_URL = "https://www.cocrea.world/@Example/OtherMod"


def make_profile(url=GAME_URL):
    return {
        "version": 2,
        "sourceUrl": url,
        "canonicalUrl": url,
        "viewport": {"width": 1000, "height": 600},
        "coordinateSpace": "screen_1000x600_with_menu",
        "prepareActions": [
            {"type": "click", "x": 100, "y": 100, "delayMs": 0},
        ],
        "prepareTiming": {"endDelayMs": 0, "durationMs": 0},
        "layout": {
            "icons": [{"index": 0, "label": "n-1", "x": 200, "y": 200}],
            "slots": [{"index": 0, "x": 700, "y": 300}],
        },
    }


@pytest.fixture
def profile_workspace():
    with tempfile.TemporaryDirectory(
        prefix=".test-manual-profile-",
        dir=os.getcwd(),
    ) as directory:
        yield Path(directory)


def patch_profile_store(monkeypatch, tmp_path):
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    monkeypatch.setattr(profile_manager, "MANUAL_PROFILES_DIR", str(tmp_path))
    monkeypatch.setattr(profile_manager, "MANUAL_PROFILES_INDEX_PATH", str(tmp_path / "index.json"))
    monkeypatch.setattr(profile_manager, "MANUAL_PROFILES_PROFILE_DIR", str(profiles_dir))
    return profiles_dir


def write_profile(path: Path, profile):
    path.write_text(json.dumps(profile), encoding="utf-8")


def make_geometry(*, width=800, height=480, target_id="target-1", canvas_left=80):
    return {
        "ok": True,
        "targetId": target_id,
        "targetUrl": GAME_URL,
        "frameId": "frame-1",
        "viewport": {"width": width, "height": height, "devicePixelRatio": 1},
        "canvasBounds": {
            "left": canvas_left,
            "top": 40,
            "width": width - canvas_left - 20,
            "height": height - 80,
        },
        "stageBounds": {
            "left": canvas_left,
            "top": 40,
            "width": width - canvas_left - 20,
            "height": height - 80,
        },
        "screenOrigin": {"x": 0, "y": 0},
        "scroll": {"x": 0, "y": 0},
        "pageIdentity": {
            "href": GAME_URL,
            "visibilityState": "visible",
            "isTopFrame": True,
            "frameName": "",
            "cdpTargetId": target_id,
            "cdpTargetUrl": GAME_URL,
        },
    }


def test_runtime_resolver_accepts_only_strict_valid_profile(monkeypatch, profile_workspace):
    profiles_dir = patch_profile_store(monkeypatch, profile_workspace)
    profile_path = profiles_dir / "valid.json"
    write_profile(profile_path, make_profile())

    result = profile_manager.resolve_profile_for_runtime(GAME_URL)

    assert result.status == "VALID"
    assert result.profile["canonicalUrl"] == GAME_URL
    assert result.path == str(profile_path)


def test_index_path_with_manual_profiles_prefix_is_not_prefixed_twice(monkeypatch):
    monkeypatch.setattr(profile_manager, "MANUAL_PROFILES_DIR", "manual_profiles")

    resolved = profile_manager._resolve_index_path(
        os.path.join("manual_profiles", "profiles", "profile.json")
    )

    assert os.path.normcase(resolved) == os.path.normcase(
        os.path.abspath(os.path.join("manual_profiles", "profiles", "profile.json"))
    )
    assert "manual_profiles" + os.sep + "manual_profiles" not in resolved


def test_explicit_invalid_profile_fails_closed_without_auto_fallback(monkeypatch, profile_workspace):
    profiles_dir = patch_profile_store(monkeypatch, profile_workspace)
    valid_path = profiles_dir / "valid.json"
    explicit_path = profile_workspace / "broken.json"
    write_profile(valid_path, make_profile())
    explicit_path.write_text("{not-json", encoding="utf-8")

    result = profile_manager.resolve_profile_for_runtime(
        GAME_URL,
        explicit_path=str(explicit_path),
    )

    assert result.status == "INVALID"
    assert result.path == str(explicit_path)


def test_profile_with_matching_url_key_but_wrong_url_is_absent(monkeypatch, profile_workspace):
    profiles_dir = patch_profile_store(monkeypatch, profile_workspace)
    wrong = make_profile(OTHER_URL)
    wrong["urlKey"] = "Example/ManualMod"
    write_profile(profiles_dir / "wrong.json", wrong)

    result = profile_manager.resolve_profile_for_runtime(GAME_URL)

    assert result.status == "ABSENT"


def test_multiple_strict_profiles_are_ambiguous(monkeypatch, profile_workspace):
    profiles_dir = patch_profile_store(monkeypatch, profile_workspace)
    write_profile(profiles_dir / "one.json", make_profile())
    write_profile(profiles_dir / "two.json", make_profile())

    result = profile_manager.resolve_profile_for_runtime(GAME_URL)

    assert result.status == "AMBIGUOUS"
    assert len(result.candidates) == 2


def test_runtime_resolver_preserves_query_from_source_url_when_canonical_was_legacy_normalized(
    monkeypatch, profile_workspace
):
    profiles_dir = patch_profile_store(monkeypatch, profile_workspace)
    versioned_url = f"{GAME_URL}?version=2"
    profile = make_profile(versioned_url)
    profile["canonicalUrl"] = GAME_URL
    profile_path = profiles_dir / "versioned.json"
    write_profile(profile_path, profile)

    result = profile_manager.resolve_profile_for_runtime(versioned_url)

    assert result.status == "VALID"
    assert result.path == str(profile_path)


def test_malformed_suggested_profile_is_not_silently_treated_as_absent(monkeypatch, profile_workspace):
    profiles_dir = patch_profile_store(monkeypatch, profile_workspace)
    suggested = Path(profile_manager.suggested_profile_path_for_url(GAME_URL))
    suggested.write_text("not-json", encoding="utf-8")

    result = profile_manager.resolve_profile_for_runtime(GAME_URL)

    assert result.status == "INVALID"
    assert result.path == str(suggested)


def test_manual_layout_transform_uses_current_canvas_and_keeps_source_unchanged():
    player = object.__new__(SprunkiGamePlayer)
    player.game_url = GAME_URL
    player.config = {"currentUrl": GAME_URL, "settings": {}}
    geometry = make_geometry()
    profile = make_profile()

    with patch.object(player, "_read_manual_profile_geometry", return_value=(geometry, None)), \
            patch("sprunki_game_player.time.sleep", return_value=None):
        context, result = player._resolve_manual_profile_runtime_context(
            profile,
            "profile.json",
        )

    assert result.passed
    assert context["profile"]["layout"]["icons"][0]["x"] != profile["layout"]["icons"][0]["x"]
    assert context["profile"]["layout"]["slots"][0]["x"] != profile["layout"]["slots"][0]["x"]
    assert profile["layout"]["icons"][0]["x"] == 200
    assert profile["prepareActions"][0]["x"] == 100
    assert context["evidence"]["frameId"] == "frame-1"


def test_manual_context_refreshes_after_geometry_change():
    player = object.__new__(SprunkiGamePlayer)
    player.game_url = GAME_URL
    player.config = {"currentUrl": GAME_URL, "settings": {}}
    first_geometry = make_geometry(width=800, height=480, canvas_left=80)
    second_geometry = make_geometry(width=1000, height=600, canvas_left=120)
    profile = make_profile()
    geometry_reader = Mock(side_effect=[
        (first_geometry, None),
        (first_geometry, None),
        (second_geometry, None),
        (second_geometry, None),
        (second_geometry, None),
    ])

    with patch.object(player, "_read_manual_profile_geometry", geometry_reader), \
            patch("sprunki_game_player.time.sleep", return_value=None):
        context, result = player._resolve_manual_profile_runtime_context(profile, "profile.json")
        assert result.passed
        refreshed, refresh_result = player._refresh_manual_profile_context_if_needed(context)

    assert refresh_result.passed
    assert refreshed is not context
    assert refreshed["geometryToken"] != context["geometryToken"]


def test_manual_playable_gate_does_not_require_live_icon_slot_counts():
    player = object.__new__(SprunkiGamePlayer)
    player.game_url = GAME_URL
    player.game_load_phase_timeout = 0.1
    player.game_entry_deadline = None
    player._game_entry_deadline = None
    player.game_readiness_poll_intervals = (0.001,)
    player.stop_event = Mock(is_set=lambda: False)
    player._entry_attempt_id = "entry-test"
    player.entry_strategy = {"selected": None, "tested": [], "verification": "UNKNOWN"}
    player._last_game_readiness_probe = {}
    player._get_websocket_url = Mock(return_value="ws://test")
    player._read_game_readiness_probe = Mock(return_value={
        "pageReady": True,
        "pageIdentity": {"href": GAME_URL},
        "stage": {"visible": True},
        "stageActive": True,
        "loading": {"active": False},
        "runtime": {"responding": True, "canvasReady": True},
    })
    player._evaluate_entry_state_via_ws = Mock(return_value={
        "ok": True,
        "iconCount": 0,
        "slotCount": 0,
        "targetCount": 1,
        "openPanelIndicators": [],
    })
    fake_ws = Mock()
    context = {
        "sourceProfile": make_profile(),
        "profile": make_profile(),
        "geometryToken": "stable",
        "boardGeneration": 0,
        "evidence": {"layoutSource": "manual_profile"},
    }

    with patch("sprunki_game_player.websocket.create_connection", return_value=fake_ws), \
            patch("sprunki_game_player.time.sleep", return_value=None):
        result = player._wait_for_playable_runtime_entry(
            timeout=0.05,
            manual_profile=context,
        )

    assert result.passed
    assert result.evidence["liveIconCount"] is None
    assert result.evidence["liveSlotCount"] is None
    assert result.evidence["profileIconCount"] == 1
    assert result.evidence["profileSlotCount"] == 1


def test_manual_playable_gate_does_not_fail_when_live_vm_state_is_unavailable():
    player = object.__new__(SprunkiGamePlayer)
    player.game_url = GAME_URL
    player.game_load_phase_timeout = 0.1
    player.game_entry_deadline = None
    player._game_entry_deadline = None
    player.game_readiness_poll_intervals = (0.001,)
    player.stop_event = Mock(is_set=lambda: False)
    player._entry_attempt_id = "entry-manual-no-live-state"
    player.entry_strategy = {"selected": None, "tested": [], "verification": "UNKNOWN"}
    player._last_game_readiness_probe = {}
    player._get_websocket_url = Mock(return_value="ws://test")
    player._read_game_readiness_probe = Mock(return_value={
        "pageReady": True,
        "pageIdentity": {"href": GAME_URL, "cdpTargetId": "target-1", "frameId": "frame-1"},
        "stage": {
            "visible": True,
            "bounds": {"left": 0, "top": 0, "width": 640, "height": 360},
            "identity": {"className": "game-stage"},
        },
        "stageActive": True,
        "runButton": {"visible": False},
        "loading": {"active": False},
        "runtime": {"responding": False, "canvasReady": True},
    })
    player._evaluate_entry_state_via_ws = Mock(return_value=None)
    context = {
        "sourceProfile": make_profile(),
        "profile": make_profile(),
        "geometryToken": "stable",
        "boardGeneration": 0,
        "evidence": {"layoutSource": "manual_profile"},
    }

    with patch("sprunki_game_player.websocket.create_connection") as create_connection, \
            patch("sprunki_game_player.time.sleep", return_value=None):
        result = player._wait_for_playable_runtime_entry(
            timeout=0.05,
            manual_profile=context,
        )

    assert result.passed
    assert result.evidence["layoutSource"] == "manual_profile"
    assert result.evidence["runtimeProofSource"] == "readiness_probe"
    assert result.evidence["runtimeStateAvailable"] is False
    create_connection.assert_not_called()
    player._evaluate_entry_state_via_ws.assert_not_called()


def test_manual_playable_gate_ignores_name_based_live_panel_indicators():
    player = object.__new__(SprunkiGamePlayer)
    player.game_url = GAME_URL
    player.game_load_phase_timeout = 0.1
    player.game_entry_deadline = None
    player._game_entry_deadline = None
    player.game_readiness_poll_intervals = (0.001,)
    player.stop_event = Mock(is_set=lambda: False)
    player._entry_attempt_id = "entry-manual-panel-names"
    player.entry_strategy = {"selected": None, "tested": [], "verification": "UNKNOWN"}
    player._last_game_readiness_probe = {}
    player._read_game_readiness_probe = Mock(return_value={
        "pageReady": True,
        "pageIdentity": {"href": GAME_URL},
        "stage": {"visible": True, "bounds": {"left": 0, "top": 0, "width": 640, "height": 360}},
        "stageActive": True,
        "runButton": {"visible": False},
        "loading": {"active": False},
        "runtime": {"responding": True, "canvasReady": True},
    })
    context = {
        "sourceProfile": make_profile(),
        "profile": make_profile(),
        "geometryToken": "stable",
        "boardGeneration": 0,
        "evidence": {"layoutSource": "manual_profile"},
    }

    with patch("sprunki_game_player.time.sleep", return_value=None):
        result = player._wait_for_playable_runtime_entry(
            timeout=0.05,
            manual_profile=context,
        )

    assert result.passed
    assert result.evidence["profileIconCount"] == 1
    assert result.evidence["profileSlotCount"] == 1


def test_malformed_capture_geometry_fails_closed_instead_of_using_legacy_geometry():
    player = object.__new__(SprunkiGamePlayer)
    player.game_url = GAME_URL
    player.config = {"currentUrl": GAME_URL, "settings": {}}
    profile = make_profile()
    profile["captureGeometry"] = {
        "canvasBounds": {
            "left": 0,
            "top": 0,
            "width": 640,
            "height": 360,
        },
        # screenOrigin is intentionally missing
    }
    geometry = make_geometry()

    with patch.object(player, "_read_manual_profile_geometry", return_value=(geometry, None)), \
            patch("sprunki_game_player.time.sleep", return_value=None):
        context, result = player._resolve_manual_profile_runtime_context(
            profile,
            "profile.json",
        )

    assert context is None
    assert result.failure_code == "manual_profile_geometry_unknown"
    assert "screen origin" in result.evidence["geometryError"]


def test_manual_control_test_accepts_zero_coordinates_without_falling_back():
    generator = object.__new__(SprunkiJSONGenerator)
    profile = make_profile()
    profile["layout"]["icons"][0]["x"] = 0
    profile["layout"]["slots"][0]["x"] = 0
    manual_context = {"profile": profile}
    generator.test_double_click_drag = Mock(
        return_value=(False, "manual_profile_input_target_mismatch")
    )

    control_type, details = generator._detect_game_control_type(
        Mock(),
        manual_profile=profile,
        manual_profile_context=manual_context,
    )

    assert control_type == "manual_profile_failed"
    assert details["test_performed"] is True
    generator.test_double_click_drag.assert_called_once()


def test_manual_target_mismatch_blocks_input_before_opening_socket():
    player = object.__new__(SprunkiGamePlayer)
    player.game_url = GAME_URL
    player.config = {"currentUrl": GAME_URL, "settings": {}}
    context = {
        "geometry": make_geometry(target_id="old-target"),
        "geometryToken": "stable",
        "evidence": {},
    }
    player._refresh_manual_profile_context_if_needed = Mock(
        return_value=(context, QualityGateResult.pass_result())
    )
    player._find_exact_game_page_target = Mock(return_value={
        "id": "new-target",
        "url": GAME_URL,
        "webSocketDebuggerUrl": "ws://new",
    })

    with patch("sprunki_game_player.websocket.create_connection") as create_connection:
        result = player._execute_cdp_single_click(100, 100, manual_context=context)

    assert not result
    assert player._last_manual_input_result.failure_code == "manual_profile_input_target_mismatch"
    create_connection.assert_not_called()


def test_manual_layout_does_not_pair_live_remove_control_by_position():
    manual_state = {
        "layout": {
            "icons": [],
            "slots": [
                {"index": 0, "x": 10, "y": 10},
                {"index": 1, "x": 20, "y": 20},
            ],
        }
    }
    live_state = {
        "layout": {
            "slots": [
                {"index": 99, "removeButton": {"resolved": True, "x": 50, "y": 50}},
            ]
        }
    }

    result = SprunkiJSONGenerator._merge_live_return_controls_into_manual_layout(
        manual_state,
        live_state,
    )

    assert "removeButton" not in result["layout"]["slots"][0]
    assert "removeButton" not in result["layout"]["slots"][1]


def test_manual_layout_does_not_merge_unidentified_top_level_return_controls():
    manual_state = {
        "layout": {
            "icons": [],
            "slots": [{"index": 0, "x": 10, "y": 10}],
        }
    }
    live_state = {
        "layout": {
            "returnControls": {
                "remove": {
                    "targetName": "Some Other Sprite",
                    "costumeName": "Remove",
                    "enabled": True,
                }
            }
        }
    }

    result = SprunkiJSONGenerator._merge_live_return_controls_into_manual_layout(
        manual_state,
        live_state,
    )

    assert "returnControls" not in result["layout"]


def test_readiness_probe_selects_game_iframe_and_maps_run_bounds():
    player = object.__new__(SprunkiGamePlayer)
    player.game_url = GAME_URL
    player.game_probe_timeout = 0.2
    player._cdp_target_id = "target-1"
    player._cdp_target_url = GAME_URL
    player._read_cdp_page_identity = Mock(return_value={
        "href": GAME_URL,
        "visibilityState": "visible",
        "hidden": False,
        "innerWidth": 1200,
        "innerHeight": 800,
        "devicePixelRatio": 1,
    })

    root_probe = {
        "pageReady": True,
        "pageIdentity": {
            "href": GAME_URL,
            "visibilityState": "visible",
            "isTopFrame": True,
            "innerWidth": 1200,
            "innerHeight": 800,
            "devicePixelRatio": 1,
        },
        "stage": {"found": False, "visible": False, "bounds": None},
        "runButton": {"found": False, "visible": False, "enabled": False, "clickable": False},
        "loading": {"active": False},
        "runtime": {"responding": False, "canvasReady": False},
    }
    child_probe = {
        "pageReady": True,
        "pageIdentity": {
            "href": "https://game.example/embed",
            "visibilityState": "visible",
            "isTopFrame": False,
            "frameName": "game-frame",
            "innerWidth": 800,
            "innerHeight": 600,
            "devicePixelRatio": 1,
        },
        "stage": {
            "found": True,
            "visible": True,
            "bounds": {"left": 0, "top": 0, "width": 800, "height": 600},
            "identity": {"className": "game-stage"},
        },
        "runButton": {
            "found": True,
            "visible": True,
            "enabled": True,
            "clickable": True,
            "bounds": {
                "left": 350,
                "top": 250,
                "width": 100,
                "height": 100,
                "centerX": 400,
                "centerY": 300,
            },
        },
        "loading": {"active": False},
        "runtime": {"responding": False, "canvasReady": True},
        "strongProgressToken": {
            "stageBounds": {"left": 0, "top": 0, "width": 800, "height": 600},
        },
    }

    def send(_ws, command, **_kwargs):
        method = command.get("method")
        params = command.get("params") or {}
        if method == "Page.getFrameTree":
            return {"result": {"frameTree": {
                "frame": {"id": "root", "url": GAME_URL},
                "childFrames": [{
                    "frame": {
                        "id": "child",
                        "url": "https://game.example/embed",
                        "name": "game-frame",
                        "parentId": "root",
                    }
                }],
            }}}
        if method == "Page.createIsolatedWorld":
            return {"result": {"executionContextId": 77}}
        if method == "DOM.getFrameOwner":
            return {"result": {"backendNodeId": 42}}
        if method == "DOM.getBoxModel":
            return {"result": {"model": {
                "content": [100, 50, 500, 50, 500, 350, 100, 350],
            }}}
        if method == "Runtime.evaluate":
            if params.get("contextId") == 77:
                return {"result": {"result": {"value": child_probe}}}
            return {"result": {"result": {"value": root_probe}}}
        return {"result": {}}

    player._send_cdp_command = Mock(side_effect=send)
    fake_ws = Mock()

    probe = player._evaluate_game_readiness_on_ws(fake_ws)

    assert probe["pageIdentity"]["href"] == GAME_URL
    assert probe["pageIdentity"]["frameId"] == "child"
    assert probe["pageIdentity"]["frameHref"] == "https://game.example/embed"
    assert probe["runButton"]["clickable"] is True
    assert probe["runButton"]["bounds"]["centerX"] == 300
    assert probe["runButton"]["bounds"]["centerY"] == 200
    assert player._last_game_probe_frame_id == "child"

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from manual_profile_v3 import ManualProfileJobContext
from mini_remix_workflow import (
    MiniRemixRequest,
    MiniRemixValidationError,
    parse_mini_remix_request,
    resolve_center_slot_indices,
    resolve_mini_remix_layout,
    run_mini_remix_workflow,
)
from creator_api_client import CreatorService


def _layout(icon_order=None, slot_count=9):
    icon_order = list(icon_order if icon_order is not None else [0, 4, 6])
    return {
        "icons": [{"index": index, "x": index + 1, "y": 1} for index in icon_order],
        "slots": [{"index": index, "x": index + 1, "y": 2} for index in range(slot_count)],
    }


def test_center_slot_formula_is_left_biased_for_odd_and_even_counts():
    assert resolve_center_slot_indices(9, 1) == [4]
    assert resolve_center_slot_indices(9, 2) == [3, 4]
    assert resolve_center_slot_indices(9, 3) == [3, 4, 5]
    assert resolve_center_slot_indices(9, 4) == [2, 3, 4, 5]
    assert resolve_center_slot_indices(4, 1) == [1]
    assert resolve_center_slot_indices(4, 2) == [0, 1]
    assert resolve_center_slot_indices(4, 4) == [0, 1, 2, 3]


def test_icon_mapping_uses_marker_index_and_preserves_tracker_order():
    layout = {
        "icons": [
            {"index": 6, "x": 6, "y": 1},
            {"index": 0, "x": 0, "y": 1},
            {"index": 4, "x": 4, "y": 1},
        ],
        "slots": [
            {"index": 2, "x": 2, "y": 2},
            {"index": 0, "x": 0, "y": 2},
            {"index": 1, "x": 1, "y": 2},
        ],
    }
    plan = resolve_mini_remix_layout(layout, MiniRemixRequest([0, 4, 6]))
    assert plan.icon_positions == [1, 2, 0]
    assert plan.target_slot_indices == [0, 1, 2]
    assert [entry["iconIndex"] for entry in plan.mapping] == [0, 4, 6]


@pytest.mark.parametrize(
    "job, code",
    [
        ({"production_mode": "mini_remix"}, "mini_remix_block_missing"),
        ({"mini_remix": {"icon_indices": [0]}}, "mini_remix_mode_contract"),
        ({"production_mode": "master", "mini_remix": {"icon_indices": [0]}}, "mini_remix_mode_contract"),
        ({"production_mode": "mini_remix", "mini_remix": {"icon_indices": [0, 0]}}, "mini_remix_duplicate_icon_index"),
        ({"production_mode": "mini_remix", "mini_remix": {"icon_indices": [True]}}, "mini_remix_icon_indices_invalid"),
    ],
)
def test_mini_payload_is_strict_and_never_falls_back(job, code):
    with pytest.raises(MiniRemixValidationError) as raised:
        parse_mini_remix_request(job)
    assert raised.value.code == code


def test_legacy_payload_without_mode_or_mini_block_returns_legacy_signal():
    assert parse_mini_remix_request({}) is None


def test_converter_keeps_legacy_default_but_marks_malformed_mini_as_mini():
    service = CreatorService.__new__(CreatorService)
    legacy = service._convert_job_to_processing_item({"id": 101, "mod_game_url": "https://cocrea.world/@a/mod"})
    assert legacy.mode == "master"

    malformed = service._convert_job_to_processing_item(
        {"id": 102, "mod_game_url": "https://cocrea.world/@a/mod", "mini_remix": {"icon_indices": [0]}}
    )
    assert malformed.mode == "mini_remix"
    assert malformed.mini_remix_error.startswith("mini_remix_mode_contract:")


def _valid_manual_context():
    fixture_path = Path(__file__).parent / "tests" / "fixtures" / "MANUAL_PROFILE_V3_FIXTURE.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    return ManualProfileJobContext(
        field_state="object",
        envelope=fixture["envelope"],
        expected_revision_id=fixture["envelope"]["revision_id"],
    )


class _FakePlayer:
    def __init__(self, layout):
        self.config = {"layout": layout}
        self.entry_strategy = {"selected": {"url": "game"}, "tested": [], "verification": "PASS"}
        self.should_stop = False
        self._workflow_placement_transactions = []
        self._last_cleanup_boundary_evidence = {
            "cleanupBoundaryCompleted": True,
            "cleanupDecisionComplete": True,
            "cleanupVerified": True,
            "reRunAttempted": False,
            "boardState": "CLEAN",
        }
        self.closed = False

    def setup_game_environment(self, first_setup=False):
        return True

    def _update_report_cleanup_evidence(self, report):
        report.cleanup_verified = True
        report.evidence.update(self._last_cleanup_boundary_evidence)

    def close_game_window(self):
        self.closed = True


class _FakeObs:
    recording_area = {"x": 1, "y": 2, "width": 700, "height": 480}

    def full_setup(self, use_full_screen=False):
        assert use_full_screen is True
        return True


class _FakeSafeOps:
    def __init__(self, player, output_path, complete=True):
        self.player = player
        self.output_path = str(output_path)
        self.complete = complete
        self.calls = []

    def safe_remix_operation(self, icons, slots, filename, recording_duration=20):
        self.calls.append((list(icons), list(slots), filename, recording_duration))
        for position, slot in enumerate(slots if self.complete else slots[:-1]):
            self.player._workflow_placement_transactions.append(
                {
                    "iconIndex": icons[position],
                    "slotIndex": slot["index"],
                    "before": position,
                    "after": position + 1,
                    "status": "VERIFIED",
                    "targetVerified": True,
                    "softVerified": False,
                    "cleanupVerified": True,
                }
            )
        return True, self.output_path, None

    def emergency_cleanup_all(self):
        return None


def _item(request, layout, context=None):
    return SimpleNamespace(
        mini_remix_request=request,
        manual_profile_context=context or _valid_manual_context(),
        json_data={"currentUrl": "https://www.cocrea.world/@a/mod", "layout": layout},
        output_files=[],
        attempt_outputs={},
        workflow_report=None,
        output_namespace="job-1-mini",
        project_slug="job-1",
        status="processing",
        failure_code=None,
        error_message=None,
    )


def test_runner_calls_shared_safe_remix_once_for_exact_combo_and_30_seconds(tmp_path):
    layout = _layout([6, 0, 4])
    item = _item(MiniRemixRequest([0, 4, 6]), layout)
    player = _FakePlayer(layout)
    output = tmp_path / "mini-remix.mp4"
    output.write_bytes(b"video")
    safe = _FakeSafeOps(player, output)

    assert run_mini_remix_workflow(
        item,
        game_player=player,
        obs_controller=_FakeObs(),
        logger=SimpleNamespace(info=lambda *a: None, warning=lambda *a: None, error=lambda *a: None),
        safe_operation_manager_factory=lambda *a: safe,
        safe_operation_manager=safe,
    ) is True
    assert len(safe.calls) == 1
    icons, slots, _, duration = safe.calls[0]
    assert icons == [1, 2, 0]
    assert [slot["index"] for slot in slots] == [3, 4, 5]
    assert duration == 30
    assert len(item.output_files) == 1
    assert item.workflow_report.mode == "mini_remix"
    assert item.workflow_report.quality_gate.passed is True


def test_partial_placement_cannot_publish_video(tmp_path):
    layout = _layout([0, 4, 6])
    item = _item(MiniRemixRequest([0, 4, 6]), layout)
    player = _FakePlayer(layout)
    output = tmp_path / "partial.mp4"
    output.write_bytes(b"partial")
    safe = _FakeSafeOps(player, output, complete=False)

    assert run_mini_remix_workflow(
        item,
        game_player=player,
        obs_controller=_FakeObs(),
        logger=SimpleNamespace(info=lambda *a: None, warning=lambda *a: None, error=lambda *a: None),
        safe_operation_manager_factory=lambda *a: safe,
        safe_operation_manager=safe,
    ) is False
    assert item.output_files == []
    assert item.failure_code == "placement_not_verified"


def test_retry_resets_ledger_and_uses_a_new_attempt_bucket(tmp_path):
    layout = _layout([0, 4, 6])
    item = _item(MiniRemixRequest([0, 4, 6]), layout)
    player = _FakePlayer(layout)
    output = tmp_path / "retry.mp4"
    output.write_bytes(b"retry")
    safe = _FakeSafeOps(player, output, complete=False)

    assert run_mini_remix_workflow(
        item,
        game_player=player,
        obs_controller=_FakeObs(),
        logger=SimpleNamespace(info=lambda *a: None, warning=lambda *a: None, error=lambda *a: None),
        safe_operation_manager_factory=lambda *a: safe,
        safe_operation_manager=safe,
    ) is False
    assert item.output_files == []
    assert set(item.attempt_outputs) == {1}

    safe.complete = True
    assert run_mini_remix_workflow(
        item,
        game_player=player,
        obs_controller=_FakeObs(),
        logger=SimpleNamespace(info=lambda *a: None, warning=lambda *a: None, error=lambda *a: None),
        safe_operation_manager_factory=lambda *a: safe,
        safe_operation_manager=safe,
    ) is True
    assert item.workflow_attempt == 2
    assert set(item.attempt_outputs) == {1, 2}
    assert len(player._workflow_placement_transactions) == 3


def test_mini_runner_requires_tracker_profile_before_obs_or_safe_operation(tmp_path):
    layout = _layout([0])
    item = _item(
        MiniRemixRequest([0]),
        layout,
        context=ManualProfileJobContext(field_state="omitted"),
    )
    player = _FakePlayer(layout)
    output = tmp_path / "never.mp4"
    output.write_bytes(b"never")
    safe = _FakeSafeOps(player, output)

    assert run_mini_remix_workflow(
        item,
        game_player=player,
        obs_controller=_FakeObs(),
        logger=SimpleNamespace(info=lambda *a: None, warning=lambda *a: None, error=lambda *a: None),
        safe_operation_manager_factory=lambda *a: safe,
        safe_operation_manager=safe,
    ) is False
    assert safe.calls == []
    assert item.failure_code == "mini_remix_manual_profile_required"

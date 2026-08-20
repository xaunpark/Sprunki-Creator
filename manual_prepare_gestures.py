"""Gesture schema and recognition helpers for manual PREPARE profiles.

The recorder historically emitted a standalone ``click`` followed by a
standalone ``drag``.  Some Scratch/Sprunki projects require the second click
to remain held while the pointer starts moving.  This module keeps that
meaning explicit at action level and provides deterministic, side-effect-free
helpers for recognizing and converting the compound gesture.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple


DOUBLE_CLICK_DRAG_TYPE = "double_click_drag"
DRAG_TYPE = "drag"
CLICK_TYPE = "click"

# These defaults match the proven CDP gesture used by the gameplay path.  A
# newly recorded gesture may override them with measured timings.
DEFAULT_FIRST_CLICK_DURATION_MS = 100
DEFAULT_DOUBLE_CLICK_INTERVAL_MS = 300
DEFAULT_HOLD_BEFORE_MOVE_MS = 200

# The operating system's double-click window is normally 500 ms.  We keep a
# bounded profile-level ceiling so a malformed or hand-edited profile cannot
# turn an ordinary click + drag separated by several seconds into a compound
# operation.
DEFAULT_MAX_DOUBLE_CLICK_INTERVAL_MS = 500
DEFAULT_MERGE_DISTANCE_PX = 15
MAX_DOUBLE_CLICK_INTERVAL_MS = 2000


def safe_int(value: Any, default: int = 0) -> int:
    try:
        number = float(value)
        if not math.isfinite(number):
            return default
        return int(round(number))
    except (TypeError, ValueError, OverflowError):
        return default


def action_type(action: Optional[Mapping[str, Any]]) -> str:
    if not isinstance(action, Mapping):
        return ""
    return str(action.get("type") or "").strip().lower()


def _distance_squared(first: Mapping[str, Any], second: Mapping[str, Any]) -> int:
    dx = safe_int(first.get("x")) - safe_int(second.get("startX"))
    dy = safe_int(first.get("y")) - safe_int(second.get("startY"))
    return dx * dx + dy * dy


def can_merge_click_and_drag(
    click_action: Optional[Mapping[str, Any]],
    drag_action: Optional[Mapping[str, Any]],
    *,
    max_interval_ms: int = DEFAULT_MAX_DOUBLE_CLICK_INTERVAL_MS,
    max_distance_px: int = DEFAULT_MERGE_DISTANCE_PX,
) -> bool:
    """Return whether two already-recorded actions form one compound gesture.

    ``drag.delayMs`` is the time from the first click's mouse-down to the
    second mouse-down because the recorder records a click at mouse-down and a
    drag boundary at mouse-up while subtracting the drag duration.  This is
    intentionally a pure check so it can be reused by the UI and tests.
    """
    if action_type(click_action) != CLICK_TYPE:
        return False
    if action_type(drag_action) != DRAG_TYPE:
        return False
    if _distance_squared(click_action, drag_action) > max_distance_px * max_distance_px:
        return False
    interval_ms = safe_int(drag_action.get("delayMs"), -1)
    if interval_ms < 0 or interval_ms > min(max_interval_ms, MAX_DOUBLE_CLICK_INTERVAL_MS):
        return False
    return True


def merge_click_and_drag(
    click_action: Mapping[str, Any],
    drag_action: Mapping[str, Any],
    *,
    max_interval_ms: int = DEFAULT_MAX_DOUBLE_CLICK_INTERVAL_MS,
    max_distance_px: int = DEFAULT_MERGE_DISTANCE_PX,
) -> Optional[Dict[str, Any]]:
    """Merge a compatible click + drag pair into one action.

    The returned ``clickIntervalMs`` is measured from first mouse-down to
    second mouse-down.  Therefore the original timeline can be represented
    without losing the delay between the two parts of the gesture.
    """
    if not can_merge_click_and_drag(
        click_action,
        drag_action,
        max_interval_ms=max_interval_ms,
        max_distance_px=max_distance_px,
    ):
        return None

    interval_ms = safe_int(drag_action.get("delayMs"), DEFAULT_DOUBLE_CLICK_INTERVAL_MS)
    first_click_duration = safe_int(
        click_action.get("pressDurationMs"),
        DEFAULT_FIRST_CLICK_DURATION_MS,
    )
    first_click_duration = max(0, min(first_click_duration, interval_ms))

    hold_before_move = safe_int(
        drag_action.get("holdBeforeMoveMs"),
        DEFAULT_HOLD_BEFORE_MOVE_MS,
    )
    drag_duration = max(0, safe_int(drag_action.get("dragDurationMs")))
    hold_before_move = max(0, min(hold_before_move, drag_duration))

    return {
        "type": DOUBLE_CLICK_DRAG_TYPE,
        "startX": safe_int(drag_action.get("startX")),
        "startY": safe_int(drag_action.get("startY")),
        "endX": safe_int(drag_action.get("endX")),
        "endY": safe_int(drag_action.get("endY")),
        "delayMs": max(0, safe_int(click_action.get("delayMs"))),
        "clickIntervalMs": interval_ms,
        "firstClickDurationMs": first_click_duration,
        "holdBeforeMoveMs": hold_before_move,
        "dragDurationMs": drag_duration,
        "syntheticFirstClick": False,
        "source": "merged_click_drag",
    }


def force_double_click_drag(
    drag_action: Mapping[str, Any],
    *,
    click_interval_ms: int = DEFAULT_DOUBLE_CLICK_INTERVAL_MS,
) -> Tuple[Dict[str, Any], int]:
    """Convert a drag-only action to a compound gesture.

    When possible, the synthetic first click is moved into the existing wait
    before the drag so the original second mouse-down time is preserved.  The
    returned integer is the unavoidable timeline extension (normally zero).
    """
    original_delay = max(0, safe_int(drag_action.get("delayMs")))
    requested_interval = max(0, min(safe_int(click_interval_ms), MAX_DOUBLE_CLICK_INTERVAL_MS))
    interval_ms = min(requested_interval, original_delay)
    extension_ms = max(requested_interval - interval_ms, 0)

    drag_duration = max(0, safe_int(drag_action.get("dragDurationMs")))
    hold_before_move = max(
        0,
        min(
            safe_int(drag_action.get("holdBeforeMoveMs"), DEFAULT_HOLD_BEFORE_MOVE_MS),
            drag_duration,
        ),
    )
    converted = {
        "type": DOUBLE_CLICK_DRAG_TYPE,
        "startX": safe_int(drag_action.get("startX")),
        "startY": safe_int(drag_action.get("startY")),
        "endX": safe_int(drag_action.get("endX")),
        "endY": safe_int(drag_action.get("endY")),
        "delayMs": max(original_delay - interval_ms, 0),
        "clickIntervalMs": interval_ms,
        "firstClickDurationMs": min(DEFAULT_FIRST_CLICK_DURATION_MS, interval_ms),
        "holdBeforeMoveMs": hold_before_move,
        "dragDurationMs": drag_duration,
        "syntheticFirstClick": True,
        "source": "forced_drag",
        "_originalDragDelayMs": original_delay,
    }
    return converted, extension_ms


def split_double_click_drag(action: Mapping[str, Any]) -> Sequence[Dict[str, Any]]:
    """Restore a compound action to its original click/drag representation."""
    if action_type(action) != DOUBLE_CLICK_DRAG_TYPE:
        return [dict(action)]

    if bool(action.get("syntheticFirstClick")):
        restored = {
            "type": DRAG_TYPE,
            "startX": safe_int(action.get("startX")),
            "startY": safe_int(action.get("startY")),
            "endX": safe_int(action.get("endX")),
            "endY": safe_int(action.get("endY")),
            "delayMs": max(
                0,
                safe_int(action.get("_originalDragDelayMs"), safe_int(action.get("delayMs"))),
            ),
            "dragDurationMs": max(0, safe_int(action.get("dragDurationMs"))),
        }
        if "holdBeforeMoveMs" in action:
            restored["holdBeforeMoveMs"] = max(0, safe_int(action.get("holdBeforeMoveMs")))
        return [restored]

    click = {
        "type": CLICK_TYPE,
        "x": safe_int(action.get("startX")),
        "y": safe_int(action.get("startY")),
        "delayMs": max(0, safe_int(action.get("delayMs"))),
    }
    if "firstClickDurationMs" in action:
        click["pressDurationMs"] = max(0, safe_int(action.get("firstClickDurationMs")))

    drag = {
        "type": DRAG_TYPE,
        "startX": safe_int(action.get("startX")),
        "startY": safe_int(action.get("startY")),
        "endX": safe_int(action.get("endX")),
        "endY": safe_int(action.get("endY")),
        "delayMs": max(0, safe_int(action.get("clickIntervalMs"))),
        "dragDurationMs": max(0, safe_int(action.get("dragDurationMs"))),
    }
    if "holdBeforeMoveMs" in action:
        drag["holdBeforeMoveMs"] = max(0, safe_int(action.get("holdBeforeMoveMs")))
    return [click, drag]


def action_timing_ms(action: Mapping[str, Any]) -> int:
    """Return the timeline cost of one action, including compound sub-timing."""
    kind = action_type(action)
    total = max(0, safe_int(action.get("delayMs")))
    if kind in {DRAG_TYPE, DOUBLE_CLICK_DRAG_TYPE}:
        total += max(0, safe_int(action.get("dragDurationMs")))
    if kind == DOUBLE_CLICK_DRAG_TYPE:
        total += max(0, safe_int(action.get("clickIntervalMs")))
    return total

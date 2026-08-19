"""Shared timing helpers for manually recorded PREPARE flows.

The recorder stores the delay before each action in ``prepareActions[*].delayMs``.
Drag actions additionally contain ``startX``, ``startY``, ``endX``, ``endY`` and
``dragDurationMs`` so a drag can be replayed as a real mouse gesture.
The delay after the last action, up to the user's PREPARE -> ICONS transition,
is stored in ``prepareTiming.endDelayMs``.  Keeping the timing model here lets
all replay entry points apply the same timing rules while remaining compatible
with older profiles that do not have ``prepareTiming`` yet.
"""

from __future__ import annotations

import threading
import time
import math
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Sequence, Tuple


PREPARE_TIMING_KEY = "prepareTiming"
END_DELAY_KEY = "endDelayMs"
DURATION_KEY = "durationMs"

# A corrupted or hand-edited profile must never be able to keep an unattended
# replay asleep for an effectively unbounded amount of time.  These are hard
# safety ceilings; normal recorded profiles are far below them.
MAX_PREPARE_ACTIONS = 256
MAX_SINGLE_PREPARE_DELAY_MS = 10 * 60 * 1000
MAX_TOTAL_PREPARE_DURATION_MS = 30 * 60 * 1000


def coerce_delay_ms(value: Any) -> int:
    """Return a safe, non-negative integer delay in milliseconds."""
    try:
        return max(int(round(float(value or 0))), 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def format_duration_ms(value: Any) -> str:
    """Format milliseconds as a compact ``MM:SS.mmm`` clock string."""
    total_ms = coerce_delay_ms(value)
    total_seconds, milliseconds = divmod(total_ms, 1000)
    hours, remaining_seconds = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remaining_seconds, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"
    return f"{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


def extract_prepare_timing(profile_or_timing: Optional[Mapping[str, Any]]) -> Mapping[str, Any]:
    """Get the timing object from a full profile or a timing object.

    ``prepareTiming`` is the canonical new location.  Direct timing fields are
    also accepted so callers remain tolerant of an intermediate/hand-edited
    profile shape.
    """
    if not isinstance(profile_or_timing, Mapping):
        return {}

    nested_timing = profile_or_timing.get(PREPARE_TIMING_KEY)
    if isinstance(nested_timing, Mapping):
        return nested_timing

    return profile_or_timing


def get_prepare_end_delay_ms(profile_or_timing: Optional[Mapping[str, Any]]) -> int:
    """Return the terminal PREPARE wait, defaulting to zero for old profiles."""
    timing = extract_prepare_timing(profile_or_timing)
    if END_DELAY_KEY in timing:
        return coerce_delay_ms(timing.get(END_DELAY_KEY))

    # Tolerate a flat field if a profile was produced by an older development
    # build or edited manually before the nested ``prepareTiming`` shape was
    # introduced.
    return coerce_delay_ms(timing.get("prepareEndDelayMs", 0))


def get_prepare_duration_ms(profile_or_timing: Optional[Mapping[str, Any]]) -> int:
    """Return the total recorded PREPARE duration when available."""
    timing = extract_prepare_timing(profile_or_timing)
    return coerce_delay_ms(timing.get(DURATION_KEY, 0))


def validate_prepare_timeline(
    actions: Optional[Sequence[Mapping[str, Any]]],
    profile_or_timing: Optional[Mapping[str, Any]],
    *,
    max_actions: int = MAX_PREPARE_ACTIONS,
    max_single_delay_ms: int = MAX_SINGLE_PREPARE_DELAY_MS,
    max_total_duration_ms: int = MAX_TOTAL_PREPARE_DURATION_MS,
) -> Tuple[bool, str]:
    """Validate replay cost before the first physical input or sleep."""
    normalized_actions = list(actions or [])
    if len(normalized_actions) > max_actions:
        return False, f"too_many_actions:{len(normalized_actions)}>{max_actions}"

    def parse_delay(raw_value, field_name, action_index=None):
        if raw_value is None:
            return True, 0, ""
        if isinstance(raw_value, bool):
            return False, 0, f"invalid_delay:{action_index or 'profile'}:{field_name}"
        try:
            numeric = float(raw_value)
        except (TypeError, ValueError, OverflowError):
            return False, 0, f"invalid_delay:{action_index or 'profile'}:{field_name}"
        if not math.isfinite(numeric) or numeric < 0:
            return False, 0, f"invalid_delay:{action_index or 'profile'}:{field_name}"
        normalized = int(round(numeric))
        return True, normalized, ""

    def valid_coordinate(raw_value, field_name, action_index):
        if raw_value is None or isinstance(raw_value, bool):
            return False, f"invalid_coordinate:{action_index}:{field_name}"
        try:
            numeric = float(raw_value)
        except (TypeError, ValueError, OverflowError):
            return False, f"invalid_coordinate:{action_index}:{field_name}"
        if not math.isfinite(numeric):
            return False, f"invalid_coordinate:{action_index}:{field_name}"
        return True, ""

    total_ms = 0
    for index, action in enumerate(normalized_actions, start=1):
        if not isinstance(action, Mapping):
            return False, f"invalid_action:{index}"
        action_type = str(action.get("type") or "").strip().lower()
        if action_type not in {"click", "drag", "key"}:
            return False, f"unsupported_action:{index}:{action_type or 'missing_type'}"
        coordinate_fields = ("x", "y") if action_type == "click" else (
            ("startX", "startY", "endX", "endY") if action_type == "drag" else ()
        )
        for field_name in coordinate_fields:
            coordinate_valid, coordinate_reason = valid_coordinate(
                action.get(field_name), field_name, index
            )
            if not coordinate_valid:
                return False, coordinate_reason
        if action_type == "key" and not str(action.get("key") or "").strip():
            return False, f"invalid_key:{index}"

        valid, delay_ms, reason = parse_delay(action.get("delayMs", 0), "delayMs", index)
        if not valid:
            return False, reason
        valid, drag_duration_ms, reason = parse_delay(
            action.get("dragDurationMs", 0), "dragDurationMs", index
        )
        if not valid:
            return False, reason
        for field_name, value in (("delayMs", delay_ms), ("dragDurationMs", drag_duration_ms)):
            if value > max_single_delay_ms:
                return False, f"delay_exceeds_limit:{index}:{field_name}:{value}"
        total_ms += delay_ms + drag_duration_ms

    timing = extract_prepare_timing(profile_or_timing)
    valid, end_delay_ms, reason = parse_delay(timing.get(END_DELAY_KEY, timing.get("prepareEndDelayMs", 0)), END_DELAY_KEY)
    if not valid:
        return False, reason
    if end_delay_ms > max_single_delay_ms:
        return False, f"delay_exceeds_limit:endDelayMs:{end_delay_ms}"
    total_ms += end_delay_ms

    valid, declared_duration, reason = parse_delay(timing.get(DURATION_KEY, 0), DURATION_KEY)
    if not valid:
        return False, reason
    if declared_duration > max_total_duration_ms:
        return False, f"declared_duration_exceeds_limit:{declared_duration}"
    if total_ms > max_total_duration_ms:
        return False, f"replay_duration_exceeds_limit:{total_ms}"
    return True, "ok"


def sleep_prepare_delay(
    delay_ms: Any,
    sleep_fn: Callable[[float], None] = time.sleep,
    *,
    max_delay_ms: int = MAX_SINGLE_PREPARE_DELAY_MS,
) -> int:
    """Sleep for a recorded delay and return the normalized delay."""
    normalized_delay_ms = coerce_delay_ms(delay_ms)
    if normalized_delay_ms > max_delay_ms:
        raise ValueError(
            f"prepare delay {normalized_delay_ms}ms exceeds safety limit {max_delay_ms}ms"
        )
    if normalized_delay_ms > 0:
        sleep_fn(normalized_delay_ms / 1000.0)
    return normalized_delay_ms


def replay_prepare_timeline(
    actions: Iterable[Mapping[str, Any]],
    profile_or_timing: Optional[Mapping[str, Any]],
    execute_action: Callable[[int, Mapping[str, Any]], bool],
    sleep_fn: Callable[[float], None] = time.sleep,
) -> bool:
    """Replay action delays, actions, and the terminal PREPARE wait.

    ``execute_action`` receives a one-based action number and the action data.
    The terminal delay is applied only after all actions succeed.  Profiles
    created before ``prepareTiming`` was added naturally use a zero terminal
    delay, preserving their existing replay behavior.
    """
    normalized_actions = list(actions or [])
    is_valid, _reason = validate_prepare_timeline(
        normalized_actions,
        profile_or_timing,
    )
    if not is_valid:
        return False

    for index, action in enumerate(normalized_actions, start=1):
        if not isinstance(action, Mapping):
            return False
        sleep_prepare_delay(action.get("delayMs", 0), sleep_fn=sleep_fn)
        if not execute_action(index, action):
            return False

    sleep_prepare_delay(
        get_prepare_end_delay_ms(profile_or_timing),
        sleep_fn=sleep_fn,
    )
    return True


class PrepareTimelineRecorder:
    """Record inter-action and terminal PREPARE timing.

    The monotonic timestamps stay in memory only.  JSON receives stable delay
    values, so recordings can be replayed on another run or machine.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._started_at: Optional[float] = None
        self._last_event_at: Optional[float] = None
        self._event_times = []
        self._finished = False
        self._timing: Dict[str, int] = {
            END_DELAY_KEY: 0,
            DURATION_KEY: 0,
        }

    def start(self, now: Optional[float] = None) -> None:
        """Start a fresh PREPARE timeline."""
        timestamp = self._clock() if now is None else float(now)
        with self._lock:
            self._started_at = timestamp
            self._last_event_at = timestamp
            self._event_times = []
            self._finished = False
            self._timing = {
                END_DELAY_KEY: 0,
                DURATION_KEY: 0,
            }

    def mark_event(self, now: Optional[float] = None, duration_ms: Any = 0) -> int:
        """Record an action boundary and return its delay before execution.

        ``duration_ms`` is used for gestures such as drag: the timeline
        boundary is recorded at mouse-up, while the returned delay represents
        the time from the previous action to mouse-down.
        """
        timestamp = self._clock() if now is None else float(now)
        with self._lock:
            if self._started_at is None:
                self._started_at = timestamp
                self._last_event_at = timestamp

            previous = (
                self._last_event_at
                if self._last_event_at is not None
                else self._started_at
                if self._started_at is not None
                else timestamp
            )
            delay_ms = coerce_delay_ms((timestamp - previous) * 1000.0)
            delay_ms = max(delay_ms - coerce_delay_ms(duration_ms), 0)
            self._last_event_at = timestamp
            self._event_times.append(timestamp)
            self._finished = False
            return delay_ms

    def undo_last(self) -> None:
        """Remove the last recorded event from the timing sequence."""
        with self._lock:
            if self._event_times:
                self._event_times.pop()
            self._last_event_at = (
                self._event_times[-1]
                if self._event_times
                else self._started_at
            )
            self._finished = False

    def snapshot(self, now: Optional[float] = None) -> Dict[str, Any]:
        """Return live elapsed/current-wait values for a realtime UI."""
        timestamp = self._clock() if now is None else float(now)
        with self._lock:
            if self._started_at is None:
                return {
                    "elapsedMs": 0,
                    "currentWaitMs": 0,
                    "endDelayMs": 0,
                    "durationMs": 0,
                    "finished": False,
                }

            if self._finished:
                return {
                    "elapsedMs": self._timing[DURATION_KEY],
                    "currentWaitMs": 0,
                    "endDelayMs": self._timing[END_DELAY_KEY],
                    "durationMs": self._timing[DURATION_KEY],
                    "finished": True,
                }

            previous = (
                self._last_event_at
                if self._last_event_at is not None
                else self._started_at
            )
            elapsed_ms = coerce_delay_ms((timestamp - self._started_at) * 1000.0)
            current_wait_ms = coerce_delay_ms((timestamp - previous) * 1000.0)
            return {
                "elapsedMs": elapsed_ms,
                "currentWaitMs": current_wait_ms,
                "endDelayMs": 0,
                "durationMs": elapsed_ms,
                "finished": False,
            }

    def finish(self, now: Optional[float] = None) -> Dict[str, int]:
        """Freeze and return timing through PREPARE -> ICONS."""
        timestamp = self._clock() if now is None else float(now)
        with self._lock:
            if self._started_at is None:
                self._timing = {
                    END_DELAY_KEY: 0,
                    DURATION_KEY: 0,
                }
                self._finished = True
                return dict(self._timing)

            if not self._finished:
                previous = (
                    self._last_event_at
                    if self._last_event_at is not None
                    else self._started_at
                )
                self._timing = {
                    END_DELAY_KEY: coerce_delay_ms((timestamp - previous) * 1000.0),
                    DURATION_KEY: coerce_delay_ms((timestamp - self._started_at) * 1000.0),
                }
                self._finished = True

            return dict(self._timing)

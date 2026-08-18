"""Shared timing helpers for manually recorded PREPARE flows.

The recorder stores the delay before each action in ``prepareActions[*].delayMs``.
The delay after the last action, up to the user's PREPARE -> ICONS transition,
is stored in ``prepareTiming.endDelayMs``.  Keeping the timing model here lets
all replay entry points apply the same timing rules while remaining compatible
with older profiles that do not have ``prepareTiming`` yet.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, Iterable, Mapping, Optional


PREPARE_TIMING_KEY = "prepareTiming"
END_DELAY_KEY = "endDelayMs"
DURATION_KEY = "durationMs"


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


def sleep_prepare_delay(delay_ms: Any, sleep_fn: Callable[[float], None] = time.sleep) -> int:
    """Sleep for a recorded delay and return the normalized delay."""
    normalized_delay_ms = coerce_delay_ms(delay_ms)
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
    for index, action in enumerate(actions or [], start=1):
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

    def mark_event(self, now: Optional[float] = None) -> int:
        """Record an action boundary and return its preceding delay."""
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

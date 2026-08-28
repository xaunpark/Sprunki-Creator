"""Bounded, race-safe watchdog state for one Creator queue job.

The workflow itself remains responsible for cooperative cancellation.  This
module only owns the monotonic deadlines, meaningful-progress heartbeat, and
the immutable timeout evidence that the final diagnostic report consumes.
"""

from __future__ import annotations

import math
import os
import json
import threading
import time
from typing import Any, Callable, Dict, Optional


DEFAULT_JOB_TIMEOUT_MINUTES = 60.0
DEFAULT_IDLE_TIMEOUT_SECONDS = 240.0
DEFAULT_CLEANUP_GRACE_SECONDS = 120.0
DEFAULT_MAX_FULL_RETRIES = 1
DEFAULT_RETRY_MIN_REMAINING_SECONDS = 900.0
DEFAULT_PROGRESS_EXTENSION_SECONDS = 60.0
DEFAULT_MAX_PROGRESS_EXTENSIONS = 8
DEFAULT_PHASE_MAX_SECONDS = {
    "runtime_entry": 480.0,
    "json_generation": 600.0,
    "initialization": 420.0,
}


def safe_positive_float(value: Any, fallback: float, *, minimum: float = 0.1) -> float:
    """Return a finite positive float or the supplied fallback."""

    try:
        candidate = float(value)
        if not math.isfinite(candidate) or candidate < minimum:
            raise ValueError("value must be finite and positive")
        return candidate
    except (TypeError, ValueError, OverflowError):
        return float(fallback)


def safe_non_negative_int(value: Any, fallback: int) -> int:
    """Return a non-negative integer or the supplied fallback."""

    try:
        candidate = int(value)
        if candidate < 0:
            raise ValueError("value must be non-negative")
        return candidate
    except (TypeError, ValueError, OverflowError):
        return int(fallback)


def safe_non_negative_float(value: Any, fallback: float) -> float:
    """Return a finite non-negative float or the supplied fallback."""

    try:
        candidate = float(value)
        if not math.isfinite(candidate) or candidate < 0:
            raise ValueError("value must be finite and non-negative")
        return candidate
    except (TypeError, ValueError, OverflowError):
        return float(fallback)


def safe_bool(value: Any, fallback: bool) -> bool:
    """Parse common JSON/config boolean values safely."""

    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on", "enabled"}:
            return True
        if normalized in {"0", "false", "no", "off", "disabled"}:
            return False
    return bool(fallback)


def load_watchdog_settings(config_get: Callable[[str, Any], Any]) -> Dict[str, Any]:
    """Load and validate watchdog settings from ConfigManager.get."""

    hard_minutes = safe_positive_float(
        config_get("processing.job_timeout_minutes", DEFAULT_JOB_TIMEOUT_MINUTES),
        DEFAULT_JOB_TIMEOUT_MINUTES,
    )
    idle_seconds = safe_positive_float(
        config_get("processing.watchdog_idle_timeout_seconds", DEFAULT_IDLE_TIMEOUT_SECONDS),
        DEFAULT_IDLE_TIMEOUT_SECONDS,
    )
    cleanup_grace = safe_positive_float(
        config_get(
            "processing.watchdog_cleanup_grace_seconds",
            DEFAULT_CLEANUP_GRACE_SECONDS,
        ),
        DEFAULT_CLEANUP_GRACE_SECONDS,
    )
    max_retries = safe_non_negative_int(
        config_get("processing.watchdog_max_full_retries", DEFAULT_MAX_FULL_RETRIES),
        DEFAULT_MAX_FULL_RETRIES,
    )
    retry_min_remaining = safe_positive_float(
        config_get(
            "processing.watchdog_retry_min_remaining_seconds",
            DEFAULT_RETRY_MIN_REMAINING_SECONDS,
        ),
        DEFAULT_RETRY_MIN_REMAINING_SECONDS,
    )
    adaptive = config_get("processing.watchdog_adaptive", {})
    if not isinstance(adaptive, dict):
        adaptive = {}
    adaptive_enabled = safe_bool(adaptive.get("enabled", True), True)
    progress_extension = safe_non_negative_float(
        adaptive.get(
            "progress_extension_seconds",
            DEFAULT_PROGRESS_EXTENSION_SECONDS,
        ),
        DEFAULT_PROGRESS_EXTENSION_SECONDS,
    )
    max_progress_extensions = safe_non_negative_int(
        adaptive.get(
            "max_progress_extensions",
            DEFAULT_MAX_PROGRESS_EXTENSIONS,
        ),
        DEFAULT_MAX_PROGRESS_EXTENSIONS,
    )
    stall_timeout = safe_positive_float(
        adaptive.get("stall_timeout_seconds", idle_seconds),
        idle_seconds,
    )
    phase_max_seconds = dict(DEFAULT_PHASE_MAX_SECONDS)
    configured_phase_max = adaptive.get("phase_max_seconds", {})
    if isinstance(configured_phase_max, dict):
        for phase, raw_seconds in configured_phase_max.items():
            if not isinstance(phase, str) or not phase.strip():
                continue
            normalized_phase = phase.strip()
            phase_max_seconds[normalized_phase] = safe_positive_float(
                raw_seconds,
                phase_max_seconds.get(
                    normalized_phase,
                    DEFAULT_PHASE_MAX_SECONDS["runtime_entry"],
                ),
            )
    return {
        "job_timeout_minutes": hard_minutes,
        "hard_timeout_seconds": hard_minutes * 60.0,
        "idle_timeout_seconds": idle_seconds,
        "cleanup_grace_seconds": cleanup_grace,
        "max_full_retries": max_retries,
        "retry_min_remaining_seconds": retry_min_remaining,
        "adaptive_enabled": adaptive_enabled,
        "stall_timeout_seconds": stall_timeout,
        "progress_extension_seconds": progress_extension,
        "max_progress_extensions": max_progress_extensions,
        "phase_max_seconds": phase_max_seconds,
    }


def _canonical_progress_token(value: Any) -> Optional[str]:
    """Convert a small probe token into a stable comparable string."""

    if value is None:
        return None
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            default=str,
        )
    except (TypeError, ValueError, OverflowError):
        return repr(value)


def attempt_scoped_filename(filename: str, attempt_index: int) -> str:
    """Keep legacy output names on attempt 1 and isolate later retries."""

    try:
        attempt = max(1, int(attempt_index or 1))
    except (TypeError, ValueError, OverflowError):
        attempt = 1
    if attempt <= 1:
        return filename
    stem, extension = os.path.splitext(str(filename))
    return f"{stem}-attempt-{attempt}{extension}"


class WatchdogState:
    """Thread-safe watchdog state shared by workflow and monitor threads."""

    def __init__(
        self,
        queue_id: int,
        *,
        hard_timeout_seconds: float,
        idle_timeout_seconds: float,
        adaptive_enabled: bool = True,
        stall_timeout_seconds: Optional[float] = None,
        progress_extension_seconds: float = DEFAULT_PROGRESS_EXTENSION_SECONDS,
        max_progress_extensions: int = DEFAULT_MAX_PROGRESS_EXTENSIONS,
        phase_max_seconds: Optional[Dict[str, Any]] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.queue_id = queue_id
        self.clock = clock
        self.hard_timeout_seconds = safe_positive_float(
            hard_timeout_seconds,
            DEFAULT_JOB_TIMEOUT_MINUTES * 60.0,
        )
        self.idle_timeout_seconds = safe_positive_float(
            idle_timeout_seconds,
            DEFAULT_IDLE_TIMEOUT_SECONDS,
        )
        self.adaptive_enabled = safe_bool(adaptive_enabled, True)
        self.stall_timeout_seconds = safe_positive_float(
            stall_timeout_seconds
            if stall_timeout_seconds is not None
            else self.idle_timeout_seconds,
            self.idle_timeout_seconds,
        )
        self.progress_extension_seconds = safe_non_negative_float(
            progress_extension_seconds,
            DEFAULT_PROGRESS_EXTENSION_SECONDS,
        )
        self.max_progress_extensions = safe_non_negative_int(
            max_progress_extensions,
            DEFAULT_MAX_PROGRESS_EXTENSIONS,
        )
        self.phase_max_seconds = {}
        if isinstance(phase_max_seconds, dict):
            for phase, raw_seconds in phase_max_seconds.items():
                if not isinstance(phase, str) or not phase.strip():
                    continue
                normalized_phase = phase.strip()
                self.phase_max_seconds[normalized_phase] = safe_positive_float(
                    raw_seconds,
                    DEFAULT_PHASE_MAX_SECONDS["runtime_entry"],
                )
        self.started_at = clock()
        self.deadline_at = self.started_at + self.hard_timeout_seconds
        self.last_progress_at = self.started_at
        self.last_observed_at = self.started_at
        self.phase = "starting"
        self.substep = "starting"
        self.phase_started_at = self.started_at
        self.phase_deadline_at: Optional[float] = None
        self.phase_absolute_deadline_at: Optional[float] = None
        self.phase_progress_count = 0
        self.phase_progress_extensions = 0
        self._last_progress_token: Optional[str] = None
        self.attempt = 1
        self.full_retry_count = 0
        self.finished = False
        self.active_timeout: Optional[Dict[str, Any]] = None
        self.timeout_history = []
        self._last_progress_metadata: Dict[str, Any] = {}
        self._last_observation_metadata: Dict[str, Any] = {}
        self._attempt_timeout_claimed = False
        self._lock = threading.RLock()
        self.finished_event = threading.Event()

    def touch(self, phase: str, substep: Optional[str] = None, **metadata: Any) -> None:
        """Record meaningful workflow progress without extending hard deadline."""

        self.observe(
            phase,
            substep,
            force_progress=True,
            **metadata,
        )

    def _start_phase_locked(self, phase: str, now: float) -> None:
        """Reset phase-local adaptive deadlines when a phase changes."""

        self.phase = str(phase or self.phase)
        self.phase_started_at = now
        self.phase_progress_count = 0
        self.phase_progress_extensions = 0
        self._last_progress_token = None

        phase_cap = self.phase_max_seconds.get(self.phase)
        if self.adaptive_enabled and phase_cap is not None:
            self.phase_absolute_deadline_at = min(
                self.deadline_at,
                now + phase_cap,
            )
            self.phase_deadline_at = min(
                self.phase_absolute_deadline_at,
                now + self.stall_timeout_seconds,
            )
        else:
            self.phase_absolute_deadline_at = None
            self.phase_deadline_at = None

    def observe(
        self,
        phase: str,
        substep: Optional[str] = None,
        *,
        progress_token: Any = None,
        force_progress: bool = False,
        **metadata: Any,
    ) -> bool:
        """Observe a probe and refresh only on meaningful progress.

        Explicit completed operations use ``touch``. Polling callers use a
        stable ``progress_token`` so identical observations cannot keep a
        stalled page alive forever.
        """

        with self._lock:
            if self.finished or self._attempt_timeout_claimed:
                return False

            now = self.clock()
            next_phase = str(phase or self.phase)
            next_substep = str(substep or self.substep)
            phase_changed = next_phase != self.phase
            substep_changed = next_substep != self.substep
            if phase_changed:
                self._start_phase_locked(next_phase, now)

            token = _canonical_progress_token(progress_token)
            token_changed = token is not None and token != self._last_progress_token
            meaningful = bool(
                force_progress
                or phase_changed
                or substep_changed
                or token_changed
            )

            self.phase = next_phase
            self.substep = next_substep
            self.last_observed_at = now
            if token is not None:
                self._last_progress_token = token
            if metadata:
                self._last_observation_metadata = dict(metadata)

            if meaningful:
                self.last_progress_at = now
                self.phase_progress_count += 1
                if self.adaptive_enabled and self.phase_deadline_at is not None:
                    extension = 0.0
                    # Entering a phase starts its initial stall window. An
                    # extension is reserved for a later, observable change;
                    # otherwise a phase-start callback would consume one of
                    # the bounded extensions before any load progress exists.
                    if (
                        not phase_changed
                        and self.phase_progress_extensions < self.max_progress_extensions
                    ):
                        extension = self.progress_extension_seconds
                        self.phase_progress_extensions += 1
                    self.phase_deadline_at = min(
                        self.phase_absolute_deadline_at or self.deadline_at,
                        now + self.stall_timeout_seconds + extension,
                    )
                self._last_progress_metadata = dict(metadata or {})
            return meaningful

    def begin_retry(self, attempt: int) -> bool:
        """Reset only the idle window for a new workflow attempt."""

        with self._lock:
            now = self.clock()
            if self.finished or now >= self.deadline_at:
                return False
            self.attempt = max(1, int(attempt))
            self.full_retry_count = max(0, self.attempt - 1)
            self._start_phase_locked("retry", now)
            self.last_progress_at = now
            self.substep = "watchdog_recovery"
            self.last_observed_at = now
            self.active_timeout = None
            self._attempt_timeout_claimed = False
            return True

    def timeout_reason(self) -> Optional[str]:
        """Return the bounded timeout reason for the current state, if any."""

        with self._lock:
            if self.finished:
                return None
            now = self.clock()
            if now >= self.deadline_at:
                return "hard_timeout"
            if (
                self.adaptive_enabled
                and self.phase_absolute_deadline_at is not None
                and now >= self.phase_absolute_deadline_at
            ):
                return "phase_timeout"
            if (
                self.adaptive_enabled
                and self.phase_deadline_at is not None
                and now >= self.phase_deadline_at
            ):
                return "idle_timeout"
            idle_limit = (
                self.stall_timeout_seconds
                if self.adaptive_enabled
                else self.idle_timeout_seconds
            )
            if now - self.last_progress_at >= idle_limit:
                return "idle_timeout"
            return None

    def mark_timeout(self, reason: str) -> Optional[Dict[str, Any]]:
        """Claim the current attempt's timeout exactly once."""

        with self._lock:
            if self.finished or self._attempt_timeout_claimed:
                return None
            now = self.clock()
            elapsed = max(0.0, now - self.started_at)
            event = {
                "reason": str(reason),
                "queue_id": self.queue_id,
                "attempt": self.attempt,
                "timeout_at_monotonic": now,
                "elapsed_seconds": round(elapsed, 3),
                "hard_timeout_seconds": self.hard_timeout_seconds,
                "idle_timeout_seconds": self.idle_timeout_seconds,
                "adaptive_enabled": self.adaptive_enabled,
                "stall_timeout_seconds": self.stall_timeout_seconds,
                "progress_extension_seconds": self.progress_extension_seconds,
                "max_progress_extensions": self.max_progress_extensions,
                "last_progress_at_monotonic": self.last_progress_at,
                "last_observed_at_monotonic": self.last_observed_at,
                "phase": self.phase,
                "substep": self.substep,
                "phase_started_at_monotonic": self.phase_started_at,
                "phase_elapsed_seconds": round(
                    max(0.0, now - self.phase_started_at), 3
                ),
                "phase_deadline_at_monotonic": self.phase_deadline_at,
                "phase_absolute_deadline_at_monotonic": self.phase_absolute_deadline_at,
                "phase_progress_count": self.phase_progress_count,
                "phase_progress_extensions": self.phase_progress_extensions,
                "phase_max_seconds": dict(self.phase_max_seconds),
                "last_progress_metadata": dict(
                    getattr(self, "_last_progress_metadata", {}) or {}
                ),
                "last_observation_metadata": dict(
                    getattr(self, "_last_observation_metadata", {}) or {}
                ),
            }
            self.active_timeout = event
            self.timeout_history.append(dict(event))
            self._attempt_timeout_claimed = True
            return dict(event)

    def take_timeout(self) -> Optional[Dict[str, Any]]:
        """Consume the current timeout for the orchestration thread."""

        with self._lock:
            event = dict(self.active_timeout) if self.active_timeout else None
            self.active_timeout = None
            return event

    def mark_finished(self) -> None:
        """Stop the monitor; a previously claimed timeout remains immutable."""

        with self._lock:
            self.finished = True
            self.finished_event.set()

    def remaining_seconds(self) -> float:
        with self._lock:
            return max(0.0, self.deadline_at - self.clock())

    def can_retry(self, minimum_remaining_seconds: float) -> bool:
        with self._lock:
            return (
                not self.finished
                and self.clock() < self.deadline_at
                and self.deadline_at - self.clock()
                >= safe_positive_float(minimum_remaining_seconds, DEFAULT_RETRY_MIN_REMAINING_SECONDS)
            )

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            now = self.clock()
            return {
                "queue_id": self.queue_id,
                "attempt": self.attempt,
                "full_retry_count": self.full_retry_count,
                "started_at_monotonic": self.started_at,
                "deadline_at_monotonic": self.deadline_at,
                "elapsed_seconds": round(max(0.0, now - self.started_at), 3),
                "remaining_seconds": round(max(0.0, self.deadline_at - now), 3),
                "hard_timeout_seconds": self.hard_timeout_seconds,
                "idle_timeout_seconds": self.idle_timeout_seconds,
                "adaptive_enabled": self.adaptive_enabled,
                "stall_timeout_seconds": self.stall_timeout_seconds,
                "progress_extension_seconds": self.progress_extension_seconds,
                "max_progress_extensions": self.max_progress_extensions,
                "last_progress_at_monotonic": self.last_progress_at,
                "last_observed_at_monotonic": self.last_observed_at,
                "last_progress_age_seconds": round(
                    max(0.0, now - self.last_progress_at), 3
                ),
                "last_observation_age_seconds": round(
                    max(0.0, now - self.last_observed_at), 3
                ),
                "phase": self.phase,
                "substep": self.substep,
                "phase_started_at_monotonic": self.phase_started_at,
                "phase_elapsed_seconds": round(
                    max(0.0, now - self.phase_started_at), 3
                ),
                "phase_deadline_at_monotonic": self.phase_deadline_at,
                "phase_absolute_deadline_at_monotonic": self.phase_absolute_deadline_at,
                "phase_progress_count": self.phase_progress_count,
                "phase_progress_extensions": self.phase_progress_extensions,
                "phase_max_seconds": dict(self.phase_max_seconds),
                "last_progress_metadata": dict(
                    getattr(self, "_last_progress_metadata", {}) or {}
                ),
                "last_observation_metadata": dict(
                    getattr(self, "_last_observation_metadata", {}) or {}
                ),
                "finished": self.finished,
                "timeout_history": [dict(entry) for entry in self.timeout_history],
            }

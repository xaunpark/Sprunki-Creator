"""Bounded, race-safe watchdog state for one Creator queue job.

The workflow itself remains responsible for cooperative cancellation.  This
module only owns the monotonic deadlines, meaningful-progress heartbeat, and
the immutable timeout evidence that the final diagnostic report consumes.
"""

from __future__ import annotations

import math
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional


DEFAULT_JOB_TIMEOUT_MINUTES = 120.0
DEFAULT_IDLE_TIMEOUT_SECONDS = 900.0
DEFAULT_CLEANUP_GRACE_SECONDS = 300.0
DEFAULT_MAX_FULL_RETRIES = 1
DEFAULT_RETRY_MIN_REMAINING_SECONDS = 900.0


@dataclass
class RecoveryRequest:
    """A durable request for one coordinator-owned game recovery.

    A ``threading.Event`` is useful for interrupting the current operation,
    but it is not sufficient as a recovery protocol: it has no reason,
    identity, acknowledgement, or deduplication semantics.  This record is
    the business-level request.  The watchdog may create it, while only a
    workflow/operation coordinator may claim and execute it.
    """

    source: str
    reason: str
    queue_id: Optional[int] = None
    direction: Optional[str] = None
    target_slot_index: Optional[int] = None
    operation_id: Optional[str] = None
    cleanup_boundary_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    request_id: str = field(
        default_factory=lambda: f"recovery-{uuid.uuid4().hex[:16]}"
    )
    created_at_monotonic: float = field(default_factory=time.monotonic)
    status: str = "PENDING"
    claimed_at_monotonic: Optional[float] = None
    completed_at_monotonic: Optional[float] = None
    result_status: Optional[str] = None
    result_message: Optional[str] = None

    def dedupe_key(self):
        """Return the identity of the logical recovery boundary."""

        boundary = self.cleanup_boundary_id or self.operation_id
        if boundary:
            return (
                str(self.queue_id or ""),
                str(boundary),
                str(self.direction or "cleanup"),
            )
        return (
            str(self.queue_id or ""),
            str(self.source or "watchdog"),
            str(self.reason or "recovery"),
        )

    def claim(self) -> bool:
        """Claim a pending request exactly once."""

        if self.status != "PENDING":
            return False
        self.status = "RUNNING"
        self.claimed_at_monotonic = time.monotonic()
        return True

    def complete(
        self,
        status: str,
        *,
        result_status: Optional[str] = None,
        result_message: Optional[str] = None,
    ) -> None:
        """Acknowledge the request without changing its physical result."""

        self.status = str(status or "UNKNOWN").upper()
        self.result_status = result_status
        self.result_message = result_message
        self.completed_at_monotonic = time.monotonic()

    def as_dict(self) -> Dict[str, Any]:
        return {
            "requestId": self.request_id,
            "source": self.source,
            "reason": self.reason,
            "queueId": self.queue_id,
            "direction": self.direction,
            "targetSlotIndex": self.target_slot_index,
            "operationId": self.operation_id,
            "cleanupBoundaryId": self.cleanup_boundary_id,
            "metadata": dict(self.metadata or {}),
            "createdAtMonotonic": self.created_at_monotonic,
            "status": self.status,
            "claimedAtMonotonic": self.claimed_at_monotonic,
            "completedAtMonotonic": self.completed_at_monotonic,
            "resultStatus": self.result_status,
            "resultMessage": self.result_message,
        }


class RecoveryRequestRegistry:
    """Thread-safe, boundary-aware registry for recovery requests.

    There is deliberately no job-wide Re-run counter here.  Deduplication is
    only for the same logical boundary, so a later icon/remix receives a new
    request identity while concurrent watchdog/operation signals collapse into
    one recovery transaction.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._requests: Dict[str, RecoveryRequest] = {}
        self._by_key: Dict[Any, str] = {}

    def submit(self, request: RecoveryRequest) -> RecoveryRequest:
        with self._lock:
            existing_id = self._by_key.get(request.dedupe_key())
            existing = self._requests.get(existing_id) if existing_id else None
            if existing is not None:
                # A boundary is single-shot even after acknowledgement.  A
                # later signal for the same boundary must observe the existing
                # result rather than create a second physical Re-run.
                return existing
            self._requests[request.request_id] = request
            self._by_key[request.dedupe_key()] = request.request_id
            return request

    def get(self, request_id: Optional[str]) -> Optional[RecoveryRequest]:
        if not request_id:
            return None
        with self._lock:
            return self._requests.get(str(request_id))

    def pending(self, queue_id: Optional[int] = None):
        with self._lock:
            return [
                request
                for request in self._requests.values()
                if request.status == "PENDING"
                and (queue_id is None or request.queue_id == queue_id)
            ]

    def claim_pending(self, queue_id: Optional[int] = None) -> Optional[RecoveryRequest]:
        with self._lock:
            candidates = self.pending(queue_id)
            if not candidates:
                return None
            candidates.sort(key=lambda request: request.created_at_monotonic)
            request = candidates[0]
            request.claim()
            return request

    def complete(
        self,
        request: Optional[RecoveryRequest],
        status: str,
        *,
        result_status: Optional[str] = None,
        result_message: Optional[str] = None,
    ) -> None:
        if request is None:
            return
        with self._lock:
            request.complete(
                status,
                result_status=result_status,
                result_message=result_message,
            )

    def snapshot(self):
        with self._lock:
            return [request.as_dict() for request in self._requests.values()]


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
    return {
        "job_timeout_minutes": hard_minutes,
        "hard_timeout_seconds": hard_minutes * 60.0,
        "idle_timeout_seconds": idle_seconds,
        "cleanup_grace_seconds": cleanup_grace,
        "max_full_retries": max_retries,
        "retry_min_remaining_seconds": retry_min_remaining,
    }


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
        self.started_at = clock()
        self.deadline_at = self.started_at + self.hard_timeout_seconds
        self.last_progress_at = self.started_at
        self.phase = "starting"
        self.substep = "starting"
        self.attempt = 1
        self.full_retry_count = 0
        self.finished = False
        self.active_timeout: Optional[Dict[str, Any]] = None
        self.timeout_history = []
        self._last_progress_metadata: Dict[str, Any] = {}
        self._attempt_timeout_claimed = False
        self._lock = threading.RLock()
        self.finished_event = threading.Event()

    def touch(self, phase: str, substep: Optional[str] = None, **metadata: Any) -> None:
        """Record meaningful workflow progress without extending hard deadline."""

        with self._lock:
            if self.finished or self._attempt_timeout_claimed:
                return
            self.last_progress_at = self.clock()
            self.phase = str(phase or self.phase)
            self.substep = str(substep or self.substep)
            if metadata:
                self._last_progress_metadata = dict(metadata)
            else:
                self._last_progress_metadata = {}

    def begin_retry(self, attempt: int) -> bool:
        """Reset only the idle window for a new workflow attempt."""

        with self._lock:
            now = self.clock()
            if self.finished or now >= self.deadline_at:
                return False
            self.attempt = max(1, int(attempt))
            self.full_retry_count = max(0, self.attempt - 1)
            self.last_progress_at = now
            self.phase = "retry"
            self.substep = "watchdog_recovery"
            self.active_timeout = None
            self._attempt_timeout_claimed = False
            return True

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
                "last_progress_at_monotonic": self.last_progress_at,
                "phase": self.phase,
                "substep": self.substep,
                "last_progress_metadata": dict(
                    getattr(self, "_last_progress_metadata", {}) or {}
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
                "last_progress_at_monotonic": self.last_progress_at,
                "last_progress_age_seconds": round(
                    max(0.0, now - self.last_progress_at), 3
                ),
                "phase": self.phase,
                "substep": self.substep,
                "finished": self.finished,
                "timeout_history": [dict(entry) for entry in self.timeout_history],
            }

"""Bounded placement verification and operation retry guards.

This module deliberately contains no browser or input code.  It owns the
state model used by the game player and gives callers one finite budget for a
single place/return operation.  Keeping the verifier read-only is important:
when a state read is inconclusive, this module can return ``UNKNOWN`` without
silently issuing another mouse action.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import time
import uuid
import math
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Sequence, Set, Tuple


class OperationStatus(str, Enum):
    VERIFIED = "VERIFIED"
    ALREADY_PLACED = "ALREADY_PLACED"
    ALREADY_EMPTY = "ALREADY_EMPTY"
    RECOVERED = "RECOVERED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    STOPPED = "STOPPED"


SUCCESS_STATUSES = frozenset({
    OperationStatus.VERIFIED,
    OperationStatus.ALREADY_PLACED,
    OperationStatus.ALREADY_EMPTY,
})


def _optional_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        try:
            return int(float(value))
        except (ValueError, OverflowError):
            return None
    return None


def _token(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _optional_tuple(value: Any) -> Optional[Tuple[str, ...]]:
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return tuple(_token(item) for item in value)
    return None


POLOS_EMPTY_TOKENS = frozenset({"", "0", "false"})
POLOS_OCCUPIED_TOKENS = frozenset({"1", "true"})
POLOS_UNKNOWN_TOKENS = frozenset({"none", "null"})

# Recovery input is intentionally separate from the normal operation budget.
# A failed placement/return may consume every coordinate in its bounded
# cascade, but that must not consume the one physical Re-Run click and the
# entry actions needed to recover the page.
# Validated manual prepare timelines allow up to 256 actions.  Keep enough
# headroom for the Re-Run click, entry probes, and the no-profile intro flow.
DEFAULT_RECOVERY_INPUT_ATTEMPTS = 512


@dataclass(frozen=True)
class PolosSignalSummary:
    """Normalized, index-agnostic summary of the runtime ``Polos`` list.

    The list positions are deliberately retained only as diagnostics.  This
    summary is the safe interface for aggregate occupancy decisions when a
    mod does not use the same ordering as the JSON layout slots.
    """

    present: bool
    valid: Optional[bool]
    length: int
    occupied_count: Optional[int]
    occupied_indices: Tuple[int, ...] = field(default_factory=tuple)
    unknown_indices: Tuple[int, ...] = field(default_factory=tuple)
    invalid_indices: Tuple[int, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class PolosTransition:
    """Comparison of two Polos arrays without assigning an array index to a slot."""

    kind: str
    expected_delta: int
    before_count: Optional[int]
    after_count: Optional[int]
    changed_indices: Tuple[int, ...] = field(default_factory=tuple)
    reason: str = ""


def _normalize_polos_token(value: Any) -> str:
    return _token(value).strip().lower()


def summarize_polos(values: Any) -> PolosSignalSummary:
    """Return a conservative aggregate summary for a Polos value."""

    if values is None:
        return PolosSignalSummary(False, None, 0, None)
    if not isinstance(values, (list, tuple)):
        return PolosSignalSummary(True, False, 0, None, invalid_indices=(0,))

    occupied_indices = []
    unknown_indices = []
    invalid_indices = []
    for index, value in enumerate(values):
        token = _normalize_polos_token(value)
        if token in POLOS_OCCUPIED_TOKENS:
            occupied_indices.append(index)
        elif token in POLOS_EMPTY_TOKENS:
            continue
        elif token in POLOS_UNKNOWN_TOKENS:
            unknown_indices.append(index)
        else:
            invalid_indices.append(index)

    valid = not unknown_indices and not invalid_indices
    return PolosSignalSummary(
        True,
        valid,
        len(values),
        len(occupied_indices) if valid else None,
        tuple(occupied_indices),
        tuple(unknown_indices),
        tuple(invalid_indices),
    )


def compare_polos_transition(
    before: Any,
    after: Any,
    direction: str,
) -> PolosTransition:
    """Compare Polos before/after as one safe aggregate transition.

    A soft transition is accepted only when exactly one binary element moved
    in the expected direction.  The changed index is returned for logging,
    never as a slot mapping.
    """

    expected_delta = _expected_delta(direction)
    before_summary = summarize_polos(before)
    after_summary = summarize_polos(after)
    if not before_summary.present or not after_summary.present:
        return PolosTransition(
            "unknown",
            expected_delta,
            before_summary.occupied_count,
            after_summary.occupied_count,
            reason="missing_signal",
        )
    if not before_summary.valid or not after_summary.valid:
        return PolosTransition(
            "unknown",
            expected_delta,
            before_summary.occupied_count,
            after_summary.occupied_count,
            reason="invalid_or_unknown_values",
        )
    if before_summary.length != after_summary.length:
        return PolosTransition(
            "ambiguous",
            expected_delta,
            before_summary.occupied_count,
            after_summary.occupied_count,
            reason="length_changed",
        )

    before_tokens = tuple(_normalize_polos_token(value) for value in before)
    after_tokens = tuple(_normalize_polos_token(value) for value in after)
    changed_indices = tuple(
        index
        for index, (old, new) in enumerate(zip(before_tokens, after_tokens))
        if old != new
    )
    if not changed_indices:
        return PolosTransition(
            "unchanged",
            expected_delta,
            before_summary.occupied_count,
            after_summary.occupied_count,
            reason="no_changed_elements",
        )

    if len(changed_indices) == 1:
        index = changed_indices[0]
        old = before_tokens[index]
        new = after_tokens[index]
        if expected_delta > 0 and old in POLOS_EMPTY_TOKENS and new in POLOS_OCCUPIED_TOKENS:
            return PolosTransition(
                "single_place",
                expected_delta,
                before_summary.occupied_count,
                after_summary.occupied_count,
                changed_indices,
            )
        if expected_delta < 0 and old in POLOS_OCCUPIED_TOKENS and new in POLOS_EMPTY_TOKENS:
            return PolosTransition(
                "single_return",
                expected_delta,
                before_summary.occupied_count,
                after_summary.occupied_count,
                changed_indices,
            )

    return PolosTransition(
        "ambiguous",
        expected_delta,
        before_summary.occupied_count,
        after_summary.occupied_count,
        changed_indices,
        reason="multiple_or_unexpected_changes",
    )


@dataclass(frozen=True)
class PlacementSnapshot:
    """One logically consistent read of all available placement signals."""

    timestamp: float
    sequence: int = 0
    characters_count: Optional[int] = None
    putted_count: Optional[int] = None
    polos: Optional[Tuple[str, ...]] = None
    characters: Optional[Tuple[str, ...]] = None
    button_enable_list: Optional[Tuple[str, ...]] = None
    read_error: Optional[str] = None

    @classmethod
    def from_mapping(cls, value: Any) -> Optional["PlacementSnapshot"]:
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            return None

        timestamp = value.get("timestamp", value.get("timestampMs", time.time()))
        try:
            timestamp = float(timestamp)
            if timestamp > 10_000_000_000:
                timestamp /= 1000.0
        except (TypeError, ValueError):
            timestamp = time.time()

        sequence = _optional_int(value.get("sequence", value.get("snapshotSequence"))) or 0
        characters = value.get("characters_array", value.get("charactersArray", value.get("characters")))
        polos = value.get("polos_array", value.get("polos", value.get("Polos")))
        buttons = value.get(
            "button_enable_list",
            value.get(
                "set_of_buttons_enable_list",
                value.get("setOfButtonsEnableList", value.get("enabled_buttons")),
            ),
        )

        characters_count = _optional_int(
            value.get("characters_count", value.get("charactersCount"))
        )
        putted_count = _optional_int(
            value.get(
                "putted_characters",
                value.get("putted_count", value.get("puttedCharacters")),
            )
        )

        if characters_count is None and isinstance(characters, (list, tuple)):
            normalized = [_token(item) for item in characters]
            characters_count = sum(1 for item in normalized if item == "1")

        return cls(
            timestamp=timestamp,
            sequence=sequence,
            characters_count=characters_count,
            putted_count=putted_count,
            polos=_optional_tuple(polos),
            characters=_optional_tuple(characters),
            button_enable_list=_optional_tuple(buttons),
            read_error=str(value.get("read_error")) if value.get("read_error") else None,
        )

    @property
    def has_known_signal(self) -> bool:
        polos_known = self.polos is not None and self.polos_signal_valid is True
        return any(
            signal is not None
            for signal in (
                self.characters_count,
                self.putted_count,
                self.characters,
                self.button_enable_list,
            )
        ) or polos_known

    @property
    def polos_summary(self) -> PolosSignalSummary:
        """Return the normalized aggregate view of the raw Polos array."""
        return summarize_polos(self.polos)

    @property
    def polos_occupied_count(self) -> Optional[int]:
        return self.polos_summary.occupied_count

    @property
    def polos_signal_valid(self) -> Optional[bool]:
        return self.polos_summary.valid

    @property
    def polos_length(self) -> Optional[int]:
        return self.polos_summary.length if self.polos is not None else None

    def semantic_key(self) -> Tuple[Any, ...]:
        """Return a comparison key without volatile timestamp/sequence fields."""
        return (
            self.characters_count,
            self.putted_count,
            self.polos,
            self.characters,
            self.button_enable_list,
            self.read_error,
        )

    def aggregate_key(self) -> Tuple[Any, ...]:
        """Return a retry/cleanup key without Polos array positions.

        Raw Polos is retained in ``semantic_key`` for transition diagnostics,
        but callers deciding whether a new physical gesture is safe must not
        interpret an individual Polos position as a layout-slot signal.
        """
        return (
            self.characters_count,
            self.putted_count,
            self.polos_occupied_count,
            self.polos_signal_valid,
            self.polos_length,
            self.characters,
            self.button_enable_list,
            self.read_error,
        )

    def stability_key(self) -> Tuple[Any, ...]:
        """Return the board-state key used to detect a settled snapshot.

        ``button_enable_list`` is a UI affordance, not occupancy.  Some mods
        toggle it while the board is already settled, so including it in the
        stability key can turn a valid empty-board read into a false UNKNOWN.
        It remains in ``semantic_key`` for diagnostic/transition evidence.
        """
        return (
            self.characters_count,
            self.putted_count,
            self.polos,
            self.characters,
            self.read_error,
        )


@dataclass
class OperationResult:
    """Structured result that remains boolean-compatible for old callers."""

    status: OperationStatus
    operation_id: str = ""
    direction: str = ""
    message: str = ""
    evidence: Tuple[str, ...] = field(default_factory=tuple)
    details: Dict[str, Any] = field(default_factory=dict)
    attempts: int = 0
    reads: int = 0
    recovery_attempts: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.status, OperationStatus):
            self.status = OperationStatus(str(self.status))
        if not isinstance(self.evidence, tuple):
            self.evidence = tuple(self.evidence or ())

    @property
    def succeeded(self) -> bool:
        return self.status in SUCCESS_STATUSES

    @property
    def recovered(self) -> bool:
        return self.status == OperationStatus.RECOVERED

    @property
    def target_verified(self) -> bool:
        """Whether this result proves the requested target, not just progress.

        Aggregate counters are useful for keeping a workflow moving, but they
        cannot prove that the requested slot changed.  New verifier results
        carry ``targetSpecific`` explicitly; legacy results keep the historical
        default of ``True`` for compatibility.
        """
        if self.status == OperationStatus.ALREADY_PLACED:
            return True
        if self.status == OperationStatus.ALREADY_EMPTY:
            # ``ALREADY_EMPTY`` can be target-specific or only prove that the
            # whole board is empty.  Preserve the historical default for
            # legacy callers, while honoring explicit board-level evidence.
            details = self.details or {}
            if "targetSpecific" in details:
                return bool(details.get("targetSpecific"))
            if (
                details.get("softVerified")
                or details.get("aggregateFallback")
                or details.get("cleanupVerificationScope") in {"board", "unknown"}
            ):
                return False
            return True
        if self.status != OperationStatus.VERIFIED:
            return False
        details = self.details or {}
        if "targetSpecific" in details:
            return bool(details.get("targetSpecific"))
        if details.get("softVerified") or details.get("aggregateFallback"):
            return False
        # Keep the historical default for old strong VERIFIED payloads that
        # predate the explicit target/soft distinction.
        return True

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            OperationStatus.VERIFIED,
            OperationStatus.ALREADY_PLACED,
            OperationStatus.ALREADY_EMPTY,
            OperationStatus.RECOVERED,
            OperationStatus.FAILED,
            OperationStatus.UNKNOWN,
            OperationStatus.STOPPED,
        }

    def __bool__(self) -> bool:
        """Keep legacy ``if result`` callers safe.

        Recovery and uncertainty are intentionally false.  They must be
        handled explicitly by the caller instead of being mistaken for a
        target-specific success.
        """
        return self.succeeded

    def as_dict(self) -> Dict[str, Any]:
        details = dict(self.details)
        return {
            "status": self.status.value,
            "operationId": self.operation_id,
            "direction": self.direction,
            "message": self.message,
            "evidence": list(self.evidence),
            "details": details,
            "attempts": self.attempts,
            "reads": self.reads,
            "recoveryAttempts": self.recovery_attempts,
            "targetVerified": self.target_verified,
            "softVerified": bool(details.get("softVerified") or details.get("aggregateFallback")),
            "verificationConfidence": details.get(
                "verificationConfidence",
                "strong" if self.target_verified else None,
            ),
        }


@dataclass
class OperationContext:
    """Finite budget and state for exactly one physical operation."""

    operation_id: str
    direction: str
    target_slot: Optional[int]
    started_at: float
    deadline_at: float
    max_input_attempts: int = 5
    max_recovery_input_attempts: int = DEFAULT_RECOVERY_INPUT_ATTEMPTS
    max_read_failures: int = 3
    max_recovery_attempts: int = 1
    baseline: Optional[PlacementSnapshot] = None
    last_snapshot: Optional[PlacementSnapshot] = None
    input_attempts: int = 0
    recovery_input_attempts: int = 0
    read_count: int = 0
    consecutive_read_failures: int = 0
    recovery_attempts: int = 0
    recovery_in_progress: bool = False
    closed: bool = False
    attempted_coordinates: Set[Tuple[int, int, int, int]] = field(default_factory=set)
    failure_counts: Dict[str, int] = field(default_factory=dict)
    no_progress_cycles: int = 0
    last_failure_signature: Optional[str] = None
    # Cleanup-boundary metadata is intentionally kept on the shared context
    # so nested return helpers cannot start a second Re-Run for the same
    # logical cleanup operation.
    cleanup_boundary_id: Optional[str] = None
    board_generation: int = 0
    board_state: str = "UNKNOWN"
    rerun_attempted: bool = False
    rerun_verified: bool = False
    rerun_status: Optional[str] = None

    @classmethod
    def begin(
        cls,
        direction: str,
        target_slot: Optional[int],
        *,
        max_input_attempts: int = 5,
        max_recovery_input_attempts: int = DEFAULT_RECOVERY_INPUT_ATTEMPTS,
        deadline_seconds: float = 12.0,
        max_read_failures: int = 3,
        max_recovery_attempts: int = 1,
        operation_id: Optional[str] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> "OperationContext":
        started_at = clock()
        try:
            safe_deadline_seconds = float(deadline_seconds)
            if not math.isfinite(safe_deadline_seconds):
                raise ValueError("non-finite deadline")
        except (TypeError, ValueError, OverflowError):
            safe_deadline_seconds = 0.1
        return cls(
            operation_id=operation_id or f"{direction}-{uuid.uuid4().hex[:12]}",
            direction=direction,
            target_slot=target_slot,
            started_at=started_at,
            deadline_at=started_at + max(safe_deadline_seconds, 0.1),
            max_input_attempts=max(int(max_input_attempts), 0),
            max_recovery_input_attempts=max(int(max_recovery_input_attempts), 1),
            max_read_failures=max(int(max_read_failures), 1),
            max_recovery_attempts=max(int(max_recovery_attempts), 0),
        )

    def expired(self, clock: Callable[[], float] = time.monotonic) -> bool:
        return clock() >= self.deadline_at

    def can_send_input(
        self,
        coordinates: Optional[Sequence[float]] = None,
        *,
        stop_requested: bool = False,
        allow_recovery_input: bool = False,
        use_recovery_budget: bool = False,
        clock: Callable[[], float] = time.monotonic,
    ) -> bool:
        if (
            self.closed
            or (self.recovery_in_progress and not allow_recovery_input)
            or stop_requested
            or self.expired(clock)
        ):
            return False
        attempt_count = (
            self.recovery_input_attempts
            if use_recovery_budget
            else self.input_attempts
        )
        max_attempts = (
            self.max_recovery_input_attempts
            if use_recovery_budget
            else self.max_input_attempts
        )
        if attempt_count >= max_attempts:
            return False
        if coordinates is not None and len(coordinates) >= 4:
            try:
                key = tuple(int(round(float(item))) for item in coordinates[:4])
                if key in self.attempted_coordinates:
                    return False
            except (TypeError, ValueError):
                return False
        return True

    def register_input(
        self,
        coordinates: Optional[Sequence[float]] = None,
        *,
        recovery: bool = False,
    ) -> bool:
        if recovery:
            if self.recovery_input_attempts >= self.max_recovery_input_attempts:
                return False
            self.recovery_input_attempts += 1
        else:
            if self.input_attempts >= self.max_input_attempts:
                return False
            self.input_attempts += 1
        if coordinates is not None and len(coordinates) >= 4:
            try:
                self.attempted_coordinates.add(
                    tuple(int(round(float(item))) for item in coordinates[:4])
                )
            except (TypeError, ValueError):
                pass
        return True

    def register_recovery_input(self) -> bool:
        """Consume one input from the independent Re-Run/entry budget."""
        return self.register_input(recovery=True)

    def register_read(self, snapshot: Optional[PlacementSnapshot]) -> None:
        self.read_count += 1
        self.last_snapshot = snapshot
        if snapshot is None or not snapshot.has_known_signal or snapshot.read_error:
            self.consecutive_read_failures += 1
        else:
            self.consecutive_read_failures = 0

    def can_read_again(self) -> bool:
        return self.consecutive_read_failures < self.max_read_failures

    def allow_recovery(self) -> bool:
        return (
            not self.closed
            and not self.recovery_in_progress
            and self.recovery_attempts < self.max_recovery_attempts
        )

    def begin_recovery(self) -> bool:
        if not self.allow_recovery():
            return False
        self.recovery_attempts += 1
        self.recovery_in_progress = True
        return True

    def end_recovery(self) -> None:
        self.recovery_in_progress = False

    def note_failure(self, signature: str, progressed: bool = False) -> int:
        if progressed:
            self.no_progress_cycles = 0
        else:
            self.no_progress_cycles += 1
        count = self.failure_counts.get(signature, 0) + 1
        self.failure_counts[signature] = count
        self.last_failure_signature = signature
        return count

    def set_baseline(self, snapshot: Optional[PlacementSnapshot]) -> None:
        if snapshot is not None:
            self.baseline = snapshot

    def close(self) -> None:
        self.closed = True
        self.recovery_in_progress = False


@dataclass(frozen=True)
class TransitionEvaluation:
    status: OperationStatus
    evidence: Tuple[str, ...] = field(default_factory=tuple)
    target_specific: bool = False
    aggregate_fallback: bool = False
    message: str = ""
    details: Dict[str, Any] = field(default_factory=dict)


def _expected_delta(direction: str) -> int:
    return 1 if str(direction).lower() in {"place", "drag", "put"} else -1


def _target_changed(
    before: Optional[Tuple[str, ...]],
    after: Optional[Tuple[str, ...]],
    target_slot: Optional[int],
    expected_delta: int,
) -> bool:
    if before is None or after is None or target_slot is None:
        return False
    if target_slot < 0 or target_slot >= len(before) or target_slot >= len(after):
        return False
    old = _token(before[target_slot])
    new = _token(after[target_slot])
    if expected_delta > 0:
        return old == "0" and new == "1"
    return old == "1" and new == "0"


def _is_empty_token(value: Any) -> bool:
    # ``None`` means that the game did not expose a value.  It is not an
    # observed empty slot and must not be converted into a successful
    # 1 -> 0/0 -> 1 transition.
    return _token(value).lower() in {"", "0", "false"}


def _target_array_changed(
    before: Optional[Tuple[str, ...]],
    after: Optional[Tuple[str, ...]],
    target_slot: Optional[int],
    expected_delta: int,
) -> bool:
    """Detect a target-specific identity/occupancy change in Characters."""
    if before is None or after is None or target_slot is None:
        return False
    if target_slot < 0 or target_slot >= len(before) or target_slot >= len(after):
        return False
    old = before[target_slot]
    new = after[target_slot]
    if old == new:
        return False
    if expected_delta > 0:
        return _is_empty_token(old) and not _is_empty_token(new)
    return not _is_empty_token(old) and _is_empty_token(new)


def evaluate_transition(
    before: Any,
    after: Any,
    direction: str,
    target_slot: Optional[int] = None,
    *,
    allow_aggregate_fallback: bool = True,
    allow_polos: bool = True,
    allow_characters_target: bool = False,
) -> TransitionEvaluation:
    """Evaluate one before/after pair without issuing input.

    Polos is intentionally aggregate-only here.  Its array position is not
    assumed to be the same as the requested layout slot, so a valid one-bit
    transition can prove board progress but can never prove the target slot.
    Target-specific button evidence and the existing aggregate counters retain
    their historical roles.  ``Characters`` is only target-specific when the
    caller has explicitly established that its array is indexed by layout
    slot.  Many Scratch mods expose ``Characters`` as an icon/runtime vector;
    interpreting that vector with a layout-slot index creates false target
    matches (for example, icon 2 makes ``Characters[2]`` non-empty even when
    slot 2 is empty).
    """
    before_snapshot = PlacementSnapshot.from_mapping(before)
    after_snapshot = PlacementSnapshot.from_mapping(after)
    if before_snapshot is None or after_snapshot is None:
        return TransitionEvaluation(OperationStatus.UNKNOWN, message="missing_snapshot")

    delta = _expected_delta(direction)
    target_evidence = []
    putted_changed = (
        before_snapshot.putted_count is not None
        and after_snapshot.putted_count is not None
        and after_snapshot.putted_count - before_snapshot.putted_count == delta
    )
    characters_changed = (
        before_snapshot.characters_count is not None
        and after_snapshot.characters_count is not None
        and after_snapshot.characters_count - before_snapshot.characters_count == delta
    )
    if allow_polos:
        polos_transition = compare_polos_transition(
            before_snapshot.polos,
            after_snapshot.polos,
            direction,
        )
    else:
        # A Polos array may be present but noisy for a particular mod.  Keep
        # its raw values in the snapshot diagnostics, but do not let a random
        # bit flip prove or disprove a physical operation.
        polos_transition = PolosTransition(
            "ignored",
            delta,
            before_snapshot.polos_occupied_count,
            after_snapshot.polos_occupied_count,
            reason="signal_untrusted",
        )
    details: Dict[str, Any] = {
        "before": before_snapshot.semantic_key(),
        "after": after_snapshot.semantic_key(),
        "beforeCharactersCount": before_snapshot.characters_count,
        "afterCharactersCount": after_snapshot.characters_count,
        "beforePuttedCount": before_snapshot.putted_count,
        "afterPuttedCount": after_snapshot.putted_count,
        "beforePolosOccupiedCount": polos_transition.before_count,
        "afterPolosOccupiedCount": polos_transition.after_count,
        "polosSignalValid": (
            before_snapshot.polos_signal_valid is True
            and after_snapshot.polos_signal_valid is True
        ),
        "polosSignalAllowed": bool(allow_polos),
        "beforePolosLength": before_snapshot.polos_length,
        "afterPolosLength": after_snapshot.polos_length,
        "polosChangedIndices": list(polos_transition.changed_indices),
        "polosTransition": polos_transition.kind,
        "polosTransitionReason": polos_transition.reason,
        "expectedDelta": delta,
        "targetSlot": target_slot,
    }

    if _target_changed(
        before_snapshot.button_enable_list,
        after_snapshot.button_enable_list,
        target_slot,
        delta,
    ):
        target_evidence.append("set_of_buttons_enable_list_target")
    if allow_characters_target and _target_array_changed(
        before_snapshot.characters,
        after_snapshot.characters,
        target_slot,
        delta,
    ):
        target_evidence.append("characters_target")

    polos_aggregate_changed = polos_transition.kind in {
        "single_place",
        "single_return",
    }

    aggregate_evidence = []
    aggregate_signals = []
    if putted_changed:
        aggregate_evidence.append("putted_characters")
        aggregate_signals.append("putted")
    if characters_changed:
        aggregate_evidence.append("characters_count")
        aggregate_signals.append("characters")
    if polos_aggregate_changed:
        aggregate_evidence.append("polos_aggregate_delta")
        aggregate_signals.append("polos_occupied_count")

    # Prefer target-specific evidence whenever it exists.  The aggregate
    # signals are alternatives, not three predicates that must all be true:
    # one exact counter/Polos transition is enough to prove one-icon progress.
    if target_evidence:
        evidence = list(target_evidence)
        evidence = aggregate_evidence + evidence
        target_specific = True
        aggregate_fallback = False
    elif aggregate_evidence and allow_aggregate_fallback:
        # Any one of the exact aggregate transitions is sufficient.  Keep all
        # matching evidence for diagnostics when more than one signal moved.
        evidence = aggregate_evidence
        target_specific = False
        aggregate_fallback = True
    else:
        evidence = []
        target_specific = False
        aggregate_fallback = False

    details.update(
        {
            "targetSpecific": target_specific,
            "aggregateFallback": aggregate_fallback,
            "softVerified": bool(aggregate_fallback and not target_specific),
            "aggregateSignals": aggregate_signals,
            "charactersTargetAllowed": bool(allow_characters_target),
            "verificationPolicy": "any_valid_aggregate_signal",
            "verificationConfidence": "strong" if target_specific else "soft",
        }
    )

    if evidence:
        return TransitionEvaluation(
            OperationStatus.VERIFIED,
            tuple(evidence),
            target_specific=target_specific,
            aggregate_fallback=aggregate_fallback,
            message="transition_detected",
            details=details,
        )

    if not before_snapshot.has_known_signal or not after_snapshot.has_known_signal:
        return TransitionEvaluation(
            OperationStatus.UNKNOWN,
            message="no_comparable_signal",
            details=details,
        )

    return TransitionEvaluation(
        OperationStatus.FAILED,
        message="expected_transition_not_observed",
        details=details,
    )


def _aggregate_delta_from_details(details: Any, direction: str) -> Tuple[str, ...]:
    """Return aggregate counters that show the expected one-icon delta.

    A target-specific array is not required here.  This helper is deliberately
    narrow: it only accepts an exact ``+1`` placement or ``-1`` return from a
    counter that was captured in the same before/after observation.
    """

    if not isinstance(details, Mapping):
        return ()
    expected_delta = _expected_delta(direction)
    matched = []
    for label in ("Characters", "Putted"):
        before = _optional_int(details.get(f"before{label}Count"))
        after = _optional_int(details.get(f"after{label}Count"))
        if before is not None and after is not None and after - before == expected_delta:
            matched.append(label.lower())
    expected_polos_transition = "single_place" if expected_delta > 0 else "single_return"
    if details.get("polosTransition") == expected_polos_transition:
        before = _optional_int(details.get("beforePolosOccupiedCount"))
        after = _optional_int(details.get("afterPolosOccupiedCount"))
        if (
            before is not None
            and after is not None
            and after - before == expected_delta
            and details.get("polosSignalValid") is True
        ):
            matched.append("polos_occupied_count")
    return tuple(matched)


def promote_aggregate_transition(
    result: Optional[OperationResult],
    direction: str,
    *,
    allow_unstable: bool = True,
) -> Optional[OperationResult]:
    """Promote narrowly-proven aggregate progress to a soft success.

    Some Scratch/Cocrea mods update their aggregate counters before the
    target-specific arrays become observable.  A late CDP read can therefore
    return ``UNKNOWN`` even though an atomic snapshot already recorded the
    exact one-icon delta.  That is safe to treat as *soft* progress: callers
    may continue, but they must not learn a target coordinate from it.

    Unknown results without an exact aggregate delta remain unknown.  This
    keeps the relaxed policy from turning a missing or unchanged state into a
    false success.
    """

    if result is None:
        return None
    if result.status in {OperationStatus.ALREADY_PLACED, OperationStatus.ALREADY_EMPTY}:
        return result

    details = dict(result.details or {})
    aggregate_signals = _aggregate_delta_from_details(details, direction)

    if result.status == OperationStatus.VERIFIED and result.target_verified:
        details.setdefault("verificationConfidence", "strong")
        result.details = details
        return result

    is_aggregate_verified = result.status == OperationStatus.VERIFIED and bool(
        result.details.get("aggregateFallback")
        or result.details.get("softVerified")
        or not result.details.get("targetSpecific", True)
    )
    is_unstable_transition = (
        allow_unstable
        and result.status == OperationStatus.UNKNOWN
        and result.message == "transition_seen_but_not_stable"
    )
    # A Polos-only transition must have been observed in a stable snapshot;
    # accepting a transient bit can mistake an animation or delayed update
    # for a completed drag.  Existing counter-based unstable promotion stays
    # compatible with the historical verifier policy.
    if (
        is_unstable_transition
        and aggregate_signals
        and set(aggregate_signals) <= {"polos_occupied_count"}
    ):
        return result
    if not aggregate_signals or not (is_aggregate_verified or is_unstable_transition):
        return result

    details.update(
        {
            "targetSpecific": False,
            "aggregateFallback": True,
            "softVerified": True,
            "verificationConfidence": "soft",
            "aggregateSignals": list(aggregate_signals),
            "unstableTransitionAccepted": bool(is_unstable_transition),
            "originalStatus": result.status.value,
        }
    )
    evidence = tuple(result.evidence or ())
    if "aggregate_delta" not in evidence:
        evidence += ("aggregate_delta",)
    if is_unstable_transition and "unstable_transition_soft_accept" not in evidence:
        evidence += ("unstable_transition_soft_accept",)

    return OperationResult(
        OperationStatus.VERIFIED,
        operation_id=result.operation_id,
        direction=result.direction or str(direction),
        message="soft_aggregate_transition_verified",
        evidence=evidence,
        details=details,
        attempts=result.attempts,
        reads=result.reads,
        recovery_attempts=result.recovery_attempts,
    )


def is_progress_verified(result: Optional[OperationResult], *, allow_soft: bool = True) -> bool:
    """Return whether an operation is safe to count as progress.

    ``target_verified`` remains reserved for strong target evidence.  Soft
    aggregate progress is intentionally accepted for workflow continuation,
    while coordinate learning and diagnostics can still distinguish it.
    """

    if result is None:
        return False
    if result.status in {OperationStatus.ALREADY_PLACED, OperationStatus.ALREADY_EMPTY}:
        return True
    if result.status != OperationStatus.VERIFIED:
        return False
    if result.target_verified:
        return True
    if not allow_soft:
        return False
    details = result.details or {}
    # ``aggregateSignals`` is the canonical diagnostic record for the
    # one-of-three policy.  The explicit soft flags remain supported for
    # legacy callers, but a valid aggregate result must not be rejected just
    # because an older wrapper omitted one of those flags.
    return bool(
        details.get("softVerified")
        or details.get("aggregateFallback")
        or details.get("aggregateSignals")
    )


def classify_precondition(
    snapshot: Any,
    direction: str,
    target_slot: Optional[int],
    *,
    allow_characters_target: bool = False,
) -> Optional[OperationStatus]:
    """Return a safe idempotent status without trusting Polos array indices.

    Polos is deliberately excluded from this precondition path because its
    list ordering is not normalized against layout slots.  Return callers
    must also perform their physical action first, so this function never
    short-circuits a return operation.
    """
    current = PlacementSnapshot.from_mapping(snapshot)
    if current is None or target_slot is None:
        return None
    normalized_direction = str(direction).lower()

    # Action-first is a hard invariant for every return path.  A pre-action
    # empty observation is retained in state logs, but cannot suppress Remove
    # or any of the legacy return actions.
    if normalized_direction in {"return", "remove", "take"}:
        return None

    # Some mods expose slot occupancy/identity through Characters, but many
    # others expose an icon/runtime vector instead.  The latter is not safe for
    # a layout-slot precondition: Characters[2] can be occupied because icon 2
    # is active in slot 0.  Only an explicitly validated slot-indexed signal
    # may suppress a physical placement.
    if allow_characters_target and current.characters is not None:
        if target_slot < 0 or target_slot >= len(current.characters):
            return None
        value = current.characters[target_slot]
        if normalized_direction in {"place", "drag", "put"} and not _is_empty_token(value):
            return OperationStatus.ALREADY_PLACED
    return None


def wait_for_stable_snapshot(
    read_snapshot: Callable[[], Any],
    *,
    timeout_ms: int = 1000,
    poll_ms: int = 50,
    stable_samples: int = 2,
    stop_requested: Optional[Callable[[], bool]] = None,
    stability_key_fn: Optional[Callable[[PlacementSnapshot], Any]] = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Optional[PlacementSnapshot]:
    """Read a stable baseline without ever sending an input action."""
    safe_timeout_ms = max(int(timeout_ms), 1)
    safe_poll_ms = max(int(poll_ms), 1)
    deadline = clock() + safe_timeout_ms / 1000.0
    max_polls = max(3, int(math.ceil(safe_timeout_ms / safe_poll_ms)) + 3)
    previous: Optional[PlacementSnapshot] = None
    key_fn = stability_key_fn or (lambda snapshot: snapshot.stability_key())
    stable = 0
    consecutive_failures = 0
    polls = 0
    while clock() < deadline and polls < max_polls:
        polls += 1
        if stop_requested and stop_requested():
            return None
        try:
            current = PlacementSnapshot.from_mapping(read_snapshot())
        except Exception:
            current = None
        if current is not None and current.has_known_signal and not current.read_error:
            consecutive_failures = 0
            if previous is not None and key_fn(current) == key_fn(previous):
                stable += 1
            else:
                stable = 1
            previous = current
            if stable >= max(int(stable_samples), 1):
                return current
        else:
            consecutive_failures += 1
            if consecutive_failures >= 3:
                return None
        if poll_ms > 0:
            sleep_fn(min(safe_poll_ms, max(int((deadline - clock()) * 1000), 0)) / 1000.0)
    return None


def _confirmed_empty_return_result(
    before_snapshot: PlacementSnapshot,
    last_eval: Optional[TransitionEvaluation],
    *,
    direction: str,
    operation_id: str,
    reads: int,
) -> Optional[OperationResult]:
    """Promote an explicit one-icon -> empty cleanup transition.

    A late CDP read can time out after an atomic snapshot already captured
    both board counters at zero.  For a return operation, that snapshot is
    sufficient when the same atomic read also proves that the board started
    non-empty.  This is deliberately narrower than treating ``UNKNOWN`` as
    success: placement still requires its normal stable target evidence, and
    partial/unknown counters are never promoted.
    """
    if str(direction).lower() not in {"return", "cleanup"}:
        return None
    if last_eval is None or last_eval.status != OperationStatus.VERIFIED:
        return None

    details = dict(last_eval.details or {})
    before_characters = details.get("beforeCharactersCount", before_snapshot.characters_count)
    before_putted = details.get("beforePuttedCount", before_snapshot.putted_count)
    after_characters = details.get("afterCharactersCount")
    after_putted = details.get("afterPuttedCount")
    before_polos = details.get(
        "beforePolosOccupiedCount",
        before_snapshot.polos_occupied_count,
    )
    after_polos = details.get("afterPolosOccupiedCount")

    signal_pairs = []
    for before_value, after_value, name in (
        (before_characters, after_characters, "characters"),
        (before_putted, after_putted, "putted"),
        (before_polos, after_polos, "polos"),
    ):
        before_value = _optional_int(before_value)
        after_value = _optional_int(after_value)
        if before_value is None and after_value is None:
            continue
        if before_value is None or after_value is None:
            # A missing signal is not a veto.  The verifier intentionally
            # accepts a complete transition from any other available signal.
            continue
        signal_pairs.append((before_value, after_value, name))

    expected_delta = _expected_delta(direction)
    matched_signals = [
        name
        for before_value, after_value, name in signal_pairs
        if after_value - before_value == expected_delta
    ]
    # Polos is validated as a strict one-bit transition; its position is
    # never treated as the requested layout slot.
    if (
        details.get("polosTransition") == "single_return"
        and details.get("polosSignalValid") is True
        and "polos" not in matched_signals
    ):
        before_value = _optional_int(details.get("beforePolosOccupiedCount"))
        after_value = _optional_int(details.get("afterPolosOccupiedCount"))
        if (
            before_value is not None
            and after_value is not None
            and after_value - before_value == expected_delta
        ):
            matched_signals.append("polos")

    if not matched_signals:
        return None

    details["targetSpecific"] = last_eval.target_specific
    details["aggregateFallback"] = last_eval.aggregate_fallback
    details["aggregateSignals"] = matched_signals
    details["verificationPolicy"] = "any_valid_aggregate_signal"
    details["stabilityFallback"] = "atomic_empty_after_return"
    after_by_signal = {
        name: after_value for _, after_value, name in signal_pairs
    }
    empty_evidence = tuple(
        (
            f"{name}_empty_after_return"
            if after_by_signal.get(name) == 0
            else f"{name}_transition_after_return"
        )
        for name in matched_signals
    )
    return OperationResult(
        OperationStatus.ALREADY_EMPTY,
        operation_id=operation_id,
        direction="return",
        message="atomic_empty_after_return_transition",
        evidence=tuple(last_eval.evidence) + empty_evidence,
        details=details,
        reads=reads,
    )


def observe_until_verified(
    before: Any,
    read_snapshot: Callable[[], Any],
    direction: str,
    target_slot: Optional[int],
    *,
    operation_id: str = "",
    timeout_ms: int = 3000,
    poll_ms: int = 50,
    stable_samples: int = 2,
    stop_requested: Optional[Callable[[], bool]] = None,
    allow_polos: bool = True,
    allow_characters_target: bool = False,
    stability_key_fn: Optional[Callable[[PlacementSnapshot], Any]] = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    context: Optional[OperationContext] = None,
) -> OperationResult:
    """Observe a transition for a bounded period after one physical action."""
    before_snapshot = PlacementSnapshot.from_mapping(before)
    if before_snapshot is None:
        return OperationResult(
            OperationStatus.UNKNOWN,
            operation_id=operation_id,
            direction=direction,
            message="invalid_before_snapshot",
        )

    safe_timeout_ms = max(int(timeout_ms), 1)
    safe_poll_ms = max(int(poll_ms), 1)
    deadline = clock() + safe_timeout_ms / 1000.0
    max_polls = max(3, int(math.ceil(safe_timeout_ms / safe_poll_ms)) + 3)
    previous_key: Optional[Tuple[Any, ...]] = None
    stable = 0
    transition_seen = False
    last_eval: Optional[TransitionEvaluation] = None
    reads = 0
    consecutive_read_failures = 0

    polls = 0
    while clock() < deadline and polls < max_polls:
        polls += 1
        if stop_requested and stop_requested():
            return OperationResult(
                OperationStatus.STOPPED,
                operation_id=operation_id,
                direction=direction,
                message="stop_requested",
                reads=reads,
            )
        if context is not None and context.expired(clock):
            confirmed_empty = _confirmed_empty_return_result(
                before_snapshot,
                last_eval,
                direction=direction,
                operation_id=operation_id,
                reads=reads,
            )
            if confirmed_empty is not None:
                return confirmed_empty
            return OperationResult(
                OperationStatus.STOPPED,
                operation_id=operation_id,
                direction=direction,
                message="operation_deadline_exceeded",
                reads=reads,
            )
        if context is not None and not context.can_read_again():
            confirmed_empty = _confirmed_empty_return_result(
                before_snapshot,
                last_eval,
                direction=direction,
                operation_id=operation_id,
                reads=reads,
            )
            if confirmed_empty is not None:
                return confirmed_empty
            return OperationResult(
                OperationStatus.UNKNOWN,
                operation_id=operation_id,
                direction=direction,
                message="consecutive_snapshot_read_failures",
                reads=reads,
            )

        try:
            current = PlacementSnapshot.from_mapping(read_snapshot())
        except Exception as error:
            current = PlacementSnapshot(
                timestamp=time.time(),
                read_error=f"snapshot_reader_exception:{error}",
            )
        reads += 1
        if context is not None:
            context.register_read(current)
        if current is None or not current.has_known_signal or current.read_error:
            consecutive_read_failures += 1
            if consecutive_read_failures >= 3:
                confirmed_empty = _confirmed_empty_return_result(
                    before_snapshot,
                    last_eval,
                    direction=direction,
                    operation_id=operation_id,
                    reads=reads,
                )
                if confirmed_empty is not None:
                    return confirmed_empty
                return OperationResult(
                    OperationStatus.UNKNOWN,
                    operation_id=operation_id,
                    direction=direction,
                    message="consecutive_snapshot_read_failures",
                    reads=reads,
                )
        else:
            consecutive_read_failures = 0
            if (
                (before_snapshot.sequence and current.sequence and current.sequence <= before_snapshot.sequence)
                or current.timestamp < before_snapshot.timestamp
            ):
                last_eval = TransitionEvaluation(
                    OperationStatus.UNKNOWN,
                    message="stale_snapshot",
                )
                consecutive_read_failures += 1
                if consecutive_read_failures >= 3:
                    confirmed_empty = _confirmed_empty_return_result(
                        before_snapshot,
                        last_eval,
                        direction=direction,
                        operation_id=operation_id,
                        reads=reads,
                    )
                    if confirmed_empty is not None:
                        return confirmed_empty
                    return OperationResult(
                        OperationStatus.UNKNOWN,
                        operation_id=operation_id,
                        direction=direction,
                        message="stale_snapshot_stream",
                        reads=reads,
                    )
                current = None
            if current is None:
                remaining = max(deadline - clock(), 0.0)
                if remaining <= 0:
                    break
                sleep_fn(min(safe_poll_ms / 1000.0, remaining))
                continue
            last_eval = evaluate_transition(
                before_snapshot,
                current,
                direction,
                target_slot,
                allow_polos=allow_polos,
                allow_characters_target=allow_characters_target,
            )
            if last_eval.status == OperationStatus.VERIFIED:
                transition_seen = True
                # Polos-only evidence must stabilize on its own raw array;
                # other evidence can use the caller's core signal key, which
                # intentionally tolerates noisy Polos updates.
                if set(last_eval.evidence or ()) <= {"polos_aggregate_delta"}:
                    current_key = current.stability_key()
                else:
                    key_fn = stability_key_fn or (
                        lambda snapshot: snapshot.stability_key()
                    )
                    current_key = key_fn(current)
                if current_key == previous_key:
                    stable += 1
                else:
                    stable = 1
                previous_key = current_key
                required_stable = max(int(stable_samples), 1)
                # A Polos-only result is intentionally soft and index-blind.
                # Require two identical post-action reads before accepting it;
                # one transient bit during an animation is not enough.
                if set(last_eval.evidence or ()) <= {"polos_aggregate_delta"}:
                    required_stable = max(required_stable, 2)
                if stable >= required_stable:
                    target_specific = last_eval.target_specific
                    aggregate_fallback = last_eval.aggregate_fallback
                    return OperationResult(
                        OperationStatus.VERIFIED,
                        operation_id=operation_id,
                        direction=direction,
                        message="stable_transition_verified",
                        evidence=last_eval.evidence,
                        details={
                            **last_eval.details,
                            "targetSpecific": target_specific,
                            "aggregateFallback": aggregate_fallback,
                            "softVerified": bool(aggregate_fallback and not target_specific),
                            "verificationConfidence": (
                                "strong" if target_specific else "soft"
                            ),
                            "requiredStableSamples": required_stable,
                        },
                        reads=reads,
                    )

        remaining = max(deadline - clock(), 0.0)
        if remaining <= 0:
            break
        sleep_fn(min(safe_poll_ms / 1000.0, remaining))

    confirmed_empty = _confirmed_empty_return_result(
        before_snapshot,
        last_eval,
        direction=direction,
        operation_id=operation_id,
        reads=reads,
    )
    if confirmed_empty is not None:
        return confirmed_empty

    status = OperationStatus.UNKNOWN if transition_seen or last_eval is None else OperationStatus.FAILED
    message = "transition_seen_but_not_stable" if transition_seen else (
        last_eval.message if last_eval else "transition_not_observed"
    )
    return OperationResult(
        status,
        operation_id=operation_id,
        direction=direction,
        message=message,
        evidence=last_eval.evidence if last_eval else (),
        details=last_eval.details if last_eval else {},
        reads=reads,
    )

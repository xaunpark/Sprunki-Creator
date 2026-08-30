"""Shared quality contracts for Sprunki production workflows.

This module intentionally contains no browser, GUI, OBS, or filesystem side
effects.  It is the single source of truth for deciding whether a workflow has
enough core gameplay evidence to be published as completed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


PASS = "PASS"
FAIL = "FAIL"
UNKNOWN = "UNKNOWN"

NON_RETRYABLE_FAILURE_CODES = frozenset(
    {
        "no_icons",
        "no_slots",
        "no_playable_entry",
        "placement_not_verified",
        "empty_remix",
        "remix_threshold_not_met",
        "phase_skipped",
        "manual_json_invalid",
        "manual_profile_invalid",
        "manual_profile_ambiguous",
        "skipped_by_policy",
        "unsupported_production_mode",
        "workflow_stopped",
    }
)

# Tracker may use ``normal`` as its canonical database value while the
# Creator-facing queue contract uses ``master`` for the same full-showcase
# workflow.  Keep the translation in one pure helper so claim, retry,
# dispatch, manifest and quality-gate paths cannot drift apart.
PRODUCTION_MODE_ALIASES = {
    "normal": "master",
    "master": "master",
    "individual": "individual",
    "both": "both",
    "horror": "horror",
    "skip": "skip",
}


def normalize_production_mode(value: Any, *, allow_skip: bool = True) -> str:
    """Return the Creator's effective mode for a Tracker job.

    ``normal`` is an external alias for the Creator's ``master`` workflow.
    ``skip`` is retained only for local policy handling and must never be
    serialized as a Tracker production mode.  Unknown values remain visible
    to the caller so they can be rejected with an explicit diagnostic rather
    than silently falling into another workflow.
    """

    raw = str(value or "").strip().lower()
    if not raw:
        return "unknown"
    if raw == "skip" and not allow_skip:
        return "unknown"
    return PRODUCTION_MODE_ALIASES.get(raw, raw)


CREATOR_PRODUCTION_MODES = frozenset({"master", "individual", "both"})


@dataclass
class QualityGateResult:
    """Decision produced by the shared workflow quality gate."""

    passed: bool = False
    status: str = FAIL
    failure_code: Optional[str] = None
    retryable: bool = False
    coverage: Dict[str, str] = field(
        default_factory=lambda: {
            "normal": "unknown",
            "horror": "not_checked_by_policy",
        }
    )
    evidence: Dict[str, Any] = field(default_factory=dict)
    message: str = ""

    def __post_init__(self) -> None:
        self.status = str(self.status or (PASS if self.passed else FAIL)).upper()
        self.passed = bool(self.passed and self.status == PASS)
        if self.failure_code:
            self.retryable = bool(self.retryable)

    @classmethod
    def pass_result(
        cls,
        *,
        coverage: Optional[Dict[str, str]] = None,
        evidence: Optional[Dict[str, Any]] = None,
        message: str = "Quality gate passed.",
    ) -> "QualityGateResult":
        return cls(
            passed=True,
            status=PASS,
            coverage=coverage or {"normal": "verified", "horror": "not_checked_by_policy"},
            evidence=evidence or {},
            message=message,
        )

    @classmethod
    def failure(
        cls,
        code: str,
        *,
        status: str = FAIL,
        retryable: Optional[bool] = None,
        coverage: Optional[Dict[str, str]] = None,
        evidence: Optional[Dict[str, Any]] = None,
        message: str = "",
    ) -> "QualityGateResult":
        if retryable is None:
            retryable = code not in NON_RETRYABLE_FAILURE_CODES
        return cls(
            passed=False,
            status=status,
            failure_code=code,
            retryable=retryable,
            coverage=coverage or {"normal": "unknown", "horror": "not_checked_by_policy"},
            evidence=evidence or {},
            message=message or code,
        )

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def __bool__(self) -> bool:
        """Keep legacy ``if result`` callers fail-closed."""
        return self.passed


@dataclass
class RemixResult:
    """Explicit remix counters; never rely on tuple truthiness."""

    attempted: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    failure_code: Optional[str] = None
    quality_pass: Optional[bool] = None
    retry_attempts: int = 0
    cleanup_unverified: int = 0
    contamination_carried_forward: bool = False
    attempt_details: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def success_ratio(self) -> float:
        return self.succeeded / self.attempted if self.attempted > 0 else 0.0

    @property
    def expected(self) -> int:
        """Return attempted plus intentionally unprocessed remixes."""

        return max(0, int(self.attempted or 0)) + max(0, int(self.skipped or 0))

    def meets(self, threshold: float, *, require_non_zero: bool = True) -> bool:
        if require_non_zero and self.attempted <= 0:
            return False
        return self.attempted > 0 and self.success_ratio >= threshold

    def as_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["success_ratio"] = self.success_ratio
        payload["expected"] = self.expected
        return payload

    def __iter__(self):
        """Keep existing ``success, total = result`` callers source-compatible."""
        yield self.succeeded
        yield self.attempted

    def __bool__(self) -> bool:
        """Fail closed for any legacy caller that still uses ``if result``."""
        return bool(self.quality_pass) if self.quality_pass is not None else False


@dataclass
class WorkflowReport:
    """Serializable evidence collected by a workflow before finalization."""

    mode: str = "master"
    was_successful: bool = False
    message: str = "Workflow did not run."
    failure_code: Optional[str] = None
    retryable: bool = False
    coverage: Dict[str, str] = field(
        default_factory=lambda: {
            "normal": "unknown",
            "horror": "not_checked_by_policy",
        }
    )
    evidence: Dict[str, Any] = field(default_factory=dict)
    phase_reports: Dict[str, Any] = field(default_factory=dict)
    entry_candidate: Optional[Dict[str, Any]] = None
    tested_candidates: List[Dict[str, Any]] = field(default_factory=list)
    runtime_state: str = UNKNOWN
    runtime_verified: bool = False
    layout_icons: int = 0
    layout_slots: int = 0
    placement_before: Optional[int] = None
    placement_after: Optional[int] = None
    placement_verified: bool = False
    cleanup_verified: bool = False
    cleanup_state: str = UNKNOWN
    rerun_attempted: bool = False
    rerun_verified: bool = False
    contamination_carried_forward: bool = False
    content_success_with_unverified_cleanup: bool = False
    identity_status: str = "not_started"
    quality_gate: Optional[QualityGateResult] = None

    # Existing counters retained for manifest/API compatibility.
    normal_icons_showcased: int = 0
    normal_icons_total: int = 0
    horror_icons_showcased: int = 0
    horror_icons_total: int = 0
    normal_remixes_played: int = 0
    normal_remixes_total: int = 0
    horror_remixes_played: int = 0
    horror_remixes_total: int = 0
    issues: List[str] = field(default_factory=list)

    @property
    def normal_icon_ratio(self) -> float:
        return (
            self.normal_icons_showcased / self.normal_icons_total
            if self.normal_icons_total > 0
            else 0.0
        )

    @property
    def normal_remix_ratio(self) -> float:
        return (
            self.normal_remixes_played / self.normal_remixes_total
            if self.normal_remixes_total > 0
            else 0.0
        )

    def apply_quality_gate(self, result: QualityGateResult) -> QualityGateResult:
        self.quality_gate = result
        self.was_successful = result.passed
        self.failure_code = result.failure_code
        self.retryable = result.retryable
        self.coverage = dict(result.coverage)
        self.evidence.update(result.evidence)
        identity_from_evidence = result.evidence.get("identityStatus")
        if identity_from_evidence is not None:
            self.identity_status = str(identity_from_evidence)
        self.message = result.message
        if result.failure_code and result.failure_code not in self.issues:
            self.issues.append(result.failure_code)
        return result

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _known_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _polos_transition_proves_progress(transaction: Any) -> bool:
    """Validate a strict aggregate Polos transition from transaction evidence.

    The changed position is intentionally never interpreted as a layout slot.
    This helper only accepts the metadata emitted by the shared verifier for a
    one-bit, one-icon transition with matching array lengths.
    """

    if not isinstance(transaction, dict):
        return False
    evidence = transaction.get("transitionEvidence") or {}
    if not isinstance(evidence, dict):
        return False
    transition = evidence.get("polosTransition")
    if transition not in {"single_place", "single_return"}:
        return False
    if evidence.get("polosSignalValid") is not True:
        return False

    before_length = _known_int(evidence.get("beforePolosLength"))
    after_length = _known_int(evidence.get("afterPolosLength"))
    # A strict aggregate transition is only valid when both snapshots expose
    # the same array shape.  Do not reconstruct this proof from counts alone;
    # that would allow an incomplete/legacy payload to masquerade as a
    # one-icon Polos transition.
    if before_length is None or after_length is None or before_length != after_length:
        return False

    changed_indices = evidence.get("polosChangedIndices")
    if not isinstance(changed_indices, (list, tuple)) or len(changed_indices) != 1:
        return False

    before = _known_int(evidence.get("beforePolosOccupiedCount"))
    after = _known_int(evidence.get("afterPolosOccupiedCount"))
    if before is None or after is None:
        return False
    expected_delta = 1 if transition == "single_place" else -1
    return after - before == expected_delta


def placement_transaction_proves_progress(transaction: Any) -> bool:
    """Return whether a strong or soft transaction proves a state change.

    Lenient verification may accept aggregate counter evidence when a mod does
    not expose a reliable target-specific array.  It still must prove that the
    before/after state changed; a claimed ``0 -> 0`` transaction never counts.
    """

    if not isinstance(transaction, dict):
        return False
    if transaction.get("status") != "VERIFIED":
        return False
    if not (
        transaction.get("targetVerified") is True
        or transaction.get("softVerified") is True
        or transaction.get("verificationConfidence") == "soft"
    ):
        return False

    # Polos is valid only as the strict aggregate evidence captured by the
    # shared verifier.  Never accept a raw semantic Polos-array difference,
    # an arbitrary count delta, or its changed index as slot proof.
    if _polos_transition_proves_progress(transaction):
        return True

    evidence = transaction.get("transitionEvidence") or {}
    if isinstance(evidence, dict) and transaction.get("targetVerified") is not True:
        # If a soft transaction carries Polos-array evidence, a generic
        # before/after counter must not silently turn an ambiguous raw-array
        # difference into progress.  Keep legacy counter-only transactions
        # compatible, and allow the branch below to use an explicit
        # Characters/Putted aggregate change.
        aggregate_signals = {
            str(signal).strip().lower()
            for signal in (evidence.get("aggregateSignals") or [])
        }
        non_polos_aggregate = bool(
            aggregate_signals.intersection(
                {"characters", "characters_count", "putted", "putted_characters"}
            )
        )
        before_counts = (
            _known_int(evidence.get("beforeCharactersCount")),
            _known_int(evidence.get("beforePuttedCount")),
        )
        after_counts = (
            _known_int(evidence.get("afterCharactersCount")),
            _known_int(evidence.get("afterPuttedCount")),
        )
        non_polos_count_changed = any(
            before_value is not None
            and after_value is not None
            and before_value != after_value
            for before_value, after_value in zip(before_counts, after_counts)
        )
        semantic_before = evidence.get("before")
        semantic_after = evidence.get("after")
        raw_polos_changed = False
        if (
            isinstance(semantic_before, (list, tuple))
            and isinstance(semantic_after, (list, tuple))
            and len(semantic_before) > 2
            and len(semantic_after) > 2
        ):
            raw_polos_changed = semantic_before[2] != semantic_after[2]
        has_polos_evidence = "polosTransition" in evidence or raw_polos_changed
        if has_polos_evidence and not (non_polos_aggregate or non_polos_count_changed):
            return False

    before = _known_int(transaction.get("before"))
    after = _known_int(transaction.get("after"))
    if before is not None and after is not None:
        return before != after

    if isinstance(evidence, dict):
        # A semantic-key difference is useful for a target-specific result
        # (for example Characters/button identity), but a soft result must be
        # backed by an explicit aggregate counter/Polos transition below.
        semantic_before = evidence.get("before")
        semantic_after = evidence.get("after")
        if (
            transaction.get("targetVerified") is True
            and semantic_before is not None
            and semantic_after is not None
            and semantic_before != semantic_after
        ):
            return True
        before_values = (
            _known_int(evidence.get("beforeCharactersCount")),
            _known_int(evidence.get("beforePuttedCount")),
        )
        after_values = (
            _known_int(evidence.get("afterCharactersCount")),
            _known_int(evidence.get("afterPuttedCount")),
        )
        for before_value, after_value in zip(before_values, after_values):
            if before_value is not None and after_value is not None:
                if before_value != after_value:
                    return True
    return False


def validate_playable_layout(config_data: Any) -> QualityGateResult:
    """Validate the minimum static layout contract before browser work."""

    if not isinstance(config_data, dict):
        return QualityGateResult.failure(
            "manual_json_invalid",
            message="Configuration must be a JSON object.",
        )

    layout = config_data.get("layout")
    if not isinstance(layout, dict):
        return QualityGateResult.failure(
            "manual_json_invalid",
            message="Configuration is missing layout.",
        )

    icons = layout.get("icons")
    slots = layout.get("slots")
    if not isinstance(icons, list) or not icons:
        return QualityGateResult.failure(
            "no_icons",
            evidence={"icons": 0, "slots": len(slots) if isinstance(slots, list) else 0},
            message="Layout contains no playable icons.",
        )
    if not isinstance(slots, list) or not slots:
        return QualityGateResult.failure(
            "no_slots",
            evidence={"icons": len(icons), "slots": 0},
            message="Layout contains no playable slots.",
        )

    entry_url = config_data.get("currentUrl") or config_data.get("url")
    if not isinstance(entry_url, str) or not entry_url.strip():
        return QualityGateResult.failure(
            "manual_json_invalid",
            evidence={"icons": len(icons), "slots": len(slots), "entry": entry_url},
            message="Configuration is missing a playable entry URL.",
        )

    return QualityGateResult.pass_result(
        coverage={"normal": "not_started", "horror": "not_checked_by_policy"},
        evidence={"icons": len(icons), "slots": len(slots), "entry": entry_url},
        message="Static playable layout validated.",
    )


def cleanup_can_be_non_blocking_for_content(report: WorkflowReport) -> bool:
    """Return whether cleanup-only uncertainty may coexist with content success.

    Cleanup is still reported as unverified in this case.  The exception is
    deliberately narrow: the cleanup boundary must have completed its
    decision path, and a Re-Run must have been attempted when the board was
    dirty or unreadable.  This prevents a missing cleanup call from being
    mistaken for the content-only success policy.
    """
    evidence = report.evidence if isinstance(report.evidence, dict) else {}
    if report.cleanup_verified:
        return False
    content_complete = bool(
        report.content_success_with_unverified_cleanup
        or evidence.get("contentSuccessWithUnverifiedCleanup")
    )
    decision_complete = bool(
        evidence.get("cleanupDecisionComplete")
        or evidence.get("cleanupBoundaryCompleted")
    )
    rerun_attempted = bool(
        report.rerun_attempted
        or evidence.get("reRunAttempted")
        or evidence.get("rerunAttempted")
    )
    board_state = str(
        evidence.get("boardState") or report.cleanup_state or ""
    ).upper()
    # Content-only acceptance is an exception for a completed cleanup
    # decision that remains DIRTY/UNKNOWN after its bounded Re-Run.  It must
    # never turn an inconsistent/missing board verdict into a pass.
    return (
        content_complete
        and decision_complete
        and rerun_attempted
        and board_state in {"DIRTY", "UNKNOWN"}
    )


def evaluate_workflow_report(
    report: WorkflowReport,
    *,
    mode: Optional[str] = None,
    identity_status: Optional[str] = None,
    require_identity: bool = True,
) -> QualityGateResult:
    """Evaluate core gameplay evidence and configured thresholds fail-closed.

    ``require_identity`` remains in the signature for source compatibility,
    but recorded identity is diagnostic metadata rather than a completion
    blocker under the Tracker Diagnostic Contract.
    """

    effective_mode = normalize_production_mode(mode or report.mode or "master", allow_skip=False)
    identity = str(identity_status or report.identity_status or "not_started").lower()
    report.identity_status = identity
    coverage = dict(report.coverage or {})
    coverage.setdefault("horror", "not_checked_by_policy")

    if effective_mode not in CREATOR_PRODUCTION_MODES and effective_mode not in {
        "remix_only",
        "remix-only",
        "test",
    }:
        return report.apply_quality_gate(
            QualityGateResult.failure(
                "unsupported_production_mode",
                coverage=coverage,
                evidence={"requestedMode": mode or report.mode, "effectiveMode": effective_mode},
                message=f"Unsupported production mode: {mode or report.mode}",
            )
        )

    if report.layout_icons <= 0:
        return report.apply_quality_gate(
            QualityGateResult.failure(
                "no_icons",
                coverage=coverage,
                evidence={"layoutIcons": report.layout_icons, "layoutSlots": report.layout_slots},
                message="Workflow cannot pass with zero icons.",
            )
        )
    if report.layout_slots <= 0:
        return report.apply_quality_gate(
            QualityGateResult.failure(
                "no_slots",
                coverage=coverage,
                evidence={"layoutIcons": report.layout_icons, "layoutSlots": report.layout_slots},
                message="Workflow cannot pass with zero slots.",
            )
        )

    if report.runtime_state == UNKNOWN or not report.runtime_verified:
        code = "runtime_verification_unknown" if report.runtime_state == UNKNOWN else "no_playable_entry"
        return report.apply_quality_gate(
            QualityGateResult.failure(
                code,
                status=UNKNOWN if report.runtime_state == UNKNOWN else FAIL,
                coverage=coverage,
                evidence={"runtimeState": report.runtime_state, "entryCandidate": report.entry_candidate},
                message="Runtime playability was not proven.",
            )
        )

    if not report.placement_verified:
        return report.apply_quality_gate(
            QualityGateResult.failure(
                "placement_not_verified",
                coverage=coverage,
                evidence={
                    "placementBefore": report.placement_before,
                    "placementAfter": report.placement_after,
                },
                message="No verified placement transaction was recorded.",
            )
        )

    transactions = report.evidence.get("placementTransactions")
    if isinstance(transactions, list) and transactions:
        transition_proven = any(
            placement_transaction_proves_progress(transaction)
            for transaction in transactions
        )
        if not transition_proven:
            return report.apply_quality_gate(
                QualityGateResult.failure(
                    "placement_not_verified",
                    coverage=coverage,
                    evidence={"placementTransactions": transactions},
                    message="Placement transactions do not prove a state transition.",
                )
            )
    else:
        # A status flag alone is not enough when no per-transaction evidence
        # exists: reject the classic 0 -> 0 false-success.  If transaction
        # evidence exists, it is authoritative and may contain a later valid
        # placement even when the first captured transaction was unchanged.
        if (
            report.placement_before is not None
            and report.placement_after is not None
            and report.placement_before == report.placement_after
        ):
            return report.apply_quality_gate(
                QualityGateResult.failure(
                    "placement_not_verified",
                    coverage=coverage,
                    evidence={
                        "placementBefore": report.placement_before,
                        "placementAfter": report.placement_after,
                    },
                    message="Placement evidence shows no state transition.",
                )
            )

    # Cleanup is a separate operational result.  A content-complete workflow
    # may remain publishable after the bounded cleanup/Re-Run decision has
    # completed, but only through the explicit content-only policy.  The old
    # finalCleanupOptional flag is retained as diagnostic compatibility data;
    # it is no longer a cleanup bypass.
    final_cleanup_optional = bool(report.evidence.get("finalCleanupOptional", False))
    final_content_complete = bool(
        report.evidence.get("finalRemixContentComplete", False)
    )
    cleanup_accepted_by_policy = cleanup_can_be_non_blocking_for_content(report)

    if not report.cleanup_verified and not cleanup_accepted_by_policy:
        cleanup_retryable = bool(report.evidence.get("cleanupRetryable", False))
        return report.apply_quality_gate(
            QualityGateResult.failure(
                "placement_not_verified",
                retryable=cleanup_retryable,
                coverage=coverage,
                evidence={
                    "cleanupVerified": False,
                    "cleanupRetryable": cleanup_retryable,
                    "cleanupDecisionComplete": bool(
                        report.evidence.get("cleanupDecisionComplete")
                        or report.evidence.get("cleanupBoundaryCompleted")
                    ),
                    "reRunAttempted": bool(
                        report.rerun_attempted
                        or report.evidence.get("reRunAttempted")
                        or report.evidence.get("rerunAttempted")
                    ),
                },
                message="Workflow cleanup was not verified after placement.",
            )
        )

    if effective_mode == "both":
        phases = report.phase_reports or {}
        phase_failures = []
        for phase_name in ("master", "individual"):
            phase = phases.get(phase_name)
            gate = phase.get("quality_gate") if isinstance(phase, dict) else None
            phase_passed = bool(isinstance(phase, dict) and phase.get("was_successful"))
            if isinstance(gate, dict):
                phase_passed = phase_passed and bool(gate.get("passed"))
            if not phase_passed:
                phase_failures.append(phase_name)
        if phase_failures:
            return report.apply_quality_gate(
                QualityGateResult.failure(
                    "phase_skipped",
                    coverage=coverage,
                    evidence={"failedPhases": phase_failures},
                    message="BOTH workflow requires Master and Individual quality gates to pass.",
                )
            )
    elif effective_mode in {"remix_only", "remix-only", "test"}:
        if report.normal_remixes_total <= 0:
            return report.apply_quality_gate(
                QualityGateResult.failure(
                    "empty_remix",
                    coverage=coverage,
                    evidence={"attempted": report.normal_remixes_total},
                    message="Remix-only workflow had no remix to attempt.",
                )
            )
        if report.normal_remixes_played <= 0:
            return report.apply_quality_gate(
                QualityGateResult.failure(
                    "remix_threshold_not_met",
                    retryable=bool(
                        report.evidence.get("normalRemixRetryable")
                        or report.evidence.get("workflowRetryable")
                    ),
                    coverage=coverage,
                    evidence={
                        "succeeded": report.normal_remixes_played,
                        "attempted": report.normal_remixes_total,
                        "workflowRetryable": bool(
                            report.evidence.get("normalRemixRetryable")
                            or report.evidence.get("workflowRetryable")
                        ),
                    },
                    message="Remix-only workflow produced no successful remix.",
                )
            )
    elif effective_mode in {"individual", "both"}:
        if report.normal_icons_total <= 0:
            return report.apply_quality_gate(
                QualityGateResult.failure(
                    "no_icons",
                    coverage=coverage,
                    message="Individual workflow has no expected icons.",
                )
            )
        if report.normal_remixes_total <= 0:
            return report.apply_quality_gate(
                QualityGateResult.failure(
                    "empty_remix",
                    coverage=coverage,
                    message="Individual workflow has no expected remixes.",
                )
            )
        icon_ok = report.normal_icons_showcased / report.normal_icons_total >= 0.80
        remix_ok = report.normal_remixes_played / report.normal_remixes_total >= (2 / 3)
        if not icon_ok or not remix_ok:
            return report.apply_quality_gate(
                QualityGateResult.failure(
                    "remix_threshold_not_met",
                    retryable=bool(
                        report.evidence.get("normalRemixRetryable")
                        or report.evidence.get("workflowRetryable")
                    ),
                    coverage=coverage,
                    evidence={
                        "iconRatio": report.normal_icons_showcased / report.normal_icons_total,
                        "remixRatio": report.normal_remixes_played / report.normal_remixes_total,
                        "iconThreshold": 0.80,
                        "remixThreshold": 2 / 3,
                        "workflowRetryable": bool(
                            report.evidence.get("normalRemixRetryable")
                            or report.evidence.get("workflowRetryable")
                        ),
                    },
                    message="Individual workflow did not meet its configured thresholds.",
                )
            )
    else:
        if report.normal_icons_total <= 0:
            return report.apply_quality_gate(
                QualityGateResult.failure("no_icons", coverage=coverage, message="No normal icons were attempted.")
            )
        if report.normal_remixes_total <= 0:
            return report.apply_quality_gate(
                QualityGateResult.failure("empty_remix", coverage=coverage, message="No normal remixes were attempted.")
            )
        icon_ok = report.normal_icons_showcased / report.normal_icons_total >= 0.80
        remix_ok = report.normal_remixes_played / report.normal_remixes_total >= 0.50
        if not icon_ok or not remix_ok:
            return report.apply_quality_gate(
                QualityGateResult.failure(
                    "remix_threshold_not_met",
                    retryable=bool(
                        report.evidence.get("normalRemixRetryable")
                        or report.evidence.get("workflowRetryable")
                    ),
                    coverage=coverage,
                    evidence={
                        "iconRatio": report.normal_icons_showcased / report.normal_icons_total,
                        "remixRatio": report.normal_remixes_played / report.normal_remixes_total,
                        "iconThreshold": 0.80,
                        "remixThreshold": 0.50,
                        "workflowRetryable": bool(
                            report.evidence.get("normalRemixRetryable")
                            or report.evidence.get("workflowRetryable")
                        ),
                    },
                    message="Master workflow did not meet its configured thresholds.",
                )
            )

    coverage["normal"] = "verified"
    return report.apply_quality_gate(
        QualityGateResult.pass_result(
            coverage=coverage,
            evidence={
                "runtimeVerified": report.runtime_verified,
                "placementVerified": report.placement_verified,
                "cleanupVerified": report.cleanup_verified,
                "cleanupAcceptedByPolicy": cleanup_accepted_by_policy,
                "finalCleanupOptional": final_cleanup_optional,
                "cleanupDecisionComplete": bool(
                    report.evidence.get("cleanupDecisionComplete")
                    or report.evidence.get("cleanupBoundaryCompleted")
                ),
                "reRunAttempted": bool(
                    report.rerun_attempted
                    or report.evidence.get("reRunAttempted")
                    or report.evidence.get("rerunAttempted")
                ),
                "boardState": report.cleanup_state
                or report.evidence.get("boardState"),
                "contaminationCarriedForward": bool(
                    report.contamination_carried_forward
                    or report.evidence.get("contaminationCarriedForward")
                ),
                "identityStatus": identity,
                "identityVerified": identity == "verified",
            },
            message="Workflow passed the shared quality gate.",
        )
    )


def evaluate_idle_timeout_partial_success(
    report: WorkflowReport,
    *,
    timeout_reason: Optional[str],
    output_recorded: bool,
    cleanup_status: Optional[str] = None,
) -> Optional[QualityGateResult]:
    """Return a controlled pass for a productive idle-timeout workflow.

    This is intentionally an opt-in exception to the normal workflow result:
    only an ``idle_timeout`` may use it, a real video artifact must exist, and
    the core gameplay evidence must already pass the same shared quality gate
    used by a normal completed Master workflow.  Manual cancellation and the
    hard deadline return ``None`` and therefore remain failures.

    Final cleanup is recorded as evidence but is deliberately non-blocking in
    this branch because the workflow has no further gameplay operation to
    perform after the bounded watchdog cleanup.
    """

    reason = str(timeout_reason or "").strip().lower()
    if reason != "idle_timeout" or not bool(output_recorded):
        return None

    # Reuse the normal Master quality gate instead of maintaining a second
    # hardcoded ratio/count policy for watchdog recovery.  The watchdog may
    # use the same content-only cleanup policy, but only when the cleanup
    # coordinator explicitly recorded a completed decision and a Re-Run
    # attempt.  All layout, runtime, placement, icon-ratio, and remix-ratio
    # checks remain identical to a normal success decision.
    evidence = report.evidence
    previous_cleanup_flags = {
        "finalCleanupOptional": evidence.get("finalCleanupOptional"),
        "finalRemixContentComplete": evidence.get("finalRemixContentComplete"),
        "contentSuccessWithUnverifiedCleanup": evidence.get(
            "contentSuccessWithUnverifiedCleanup"
        ),
    }
    evidence["finalCleanupOptional"] = True
    evidence["finalRemixContentComplete"] = True
    cleanup_decision_complete = bool(
        evidence.get("cleanupDecisionComplete")
        or evidence.get("cleanupBoundaryCompleted")
    )
    rerun_attempted = bool(
        report.rerun_attempted
        or evidence.get("reRunAttempted")
        or evidence.get("rerunAttempted")
    )
    evidence["contentSuccessWithUnverifiedCleanup"] = bool(
        cleanup_decision_complete and rerun_attempted
    )
    result = evaluate_workflow_report(report, mode="master", require_identity=False)
    if not result.passed:
        for key, previous in previous_cleanup_flags.items():
            if previous is None:
                evidence.pop(key, None)
            else:
                evidence[key] = previous
        return None

    completed = int(report.normal_remixes_played or 0)
    attempted = int(report.normal_remixes_total or 0)
    showcased = int(report.normal_icons_showcased or 0)
    icon_total = int(report.normal_icons_total or 0)
    icon_ratio = showcased / icon_total if icon_total > 0 else 0.0
    remix_ratio = completed / attempted if attempted > 0 else 0.0

    partial_evidence = {
        "idleTimeoutPartialSuccess": True,
        "workflowCompleteForPolicy": True,
        "watchdogReason": "idle_timeout",
        "outputRecorded": True,
        "normalRemixesCompleted": completed,
        "normalRemixesAttempted": attempted,
        "normalRemixRatio": round(remix_ratio, 4),
        "normalIconsShowcased": showcased,
        "normalIconsTotal": icon_total,
        "normalIconRatio": round(icon_ratio, 4),
        "qualityGateReused": "evaluate_workflow_report",
        # Keep the content-only decision explicit in downstream diagnostics;
        # cleanup itself remains unverified when the board could not be reset.
        "finalCleanupOptional": True,
        "finalRemixContentComplete": True,
        "cleanupAcceptedByPolicy": bool(
            result.evidence.get("cleanupAcceptedByPolicy", False)
        ),
        "cleanupNonBlocking": bool(
            result.evidence.get("cleanupAcceptedByPolicy", False)
        ),
        "cleanupVerified": bool(report.cleanup_verified),
        "cleanupStatus": cleanup_status,
        "cleanupDecisionComplete": cleanup_decision_complete,
        "reRunAttempted": rerun_attempted,
    }
    result.evidence.update(partial_evidence)
    result.message = (
        "Idle timeout occurred after the normal Master quality thresholds "
        "were met; bounded final cleanup is non-blocking by policy."
    )
    return report.apply_quality_gate(result)


def failure_is_retryable(
    code: Optional[str],
    evidence: Optional[Dict[str, Any]] = None,
) -> bool:
    """Return whether a quality failure has an explicitly safe retry path.

    ``placement_not_verified`` normally remains fail-closed.  The one
    exception is a cleanup-only failure that completed its bounded recovery
    chain and explicitly marked itself retryable; the next workflow attempt
    starts from a fresh board and still has to pass the empty-board gate.
    """

    if not code:
        return False
    if code == "watchdog_timeout":
        evidence = evidence or {}
        return str(evidence.get("reason", "")).lower() not in {
            "service_cancelled",
            "manual_cancellation",
        }
    if code == "placement_not_verified":
        return bool((evidence or {}).get("cleanupRetryable", False))
    if code == "remix_threshold_not_met":
        return bool((evidence or {}).get("workflowRetryable", False))
    return code not in NON_RETRYABLE_FAILURE_CODES

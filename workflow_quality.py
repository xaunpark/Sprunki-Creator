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
        "skipped_by_policy",
        "workflow_stopped",
    }
)


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

    @property
    def success_ratio(self) -> float:
        return self.succeeded / self.attempted if self.attempted > 0 else 0.0

    def meets(self, threshold: float, *, require_non_zero: bool = True) -> bool:
        if require_non_zero and self.attempted <= 0:
            return False
        return self.attempted > 0 and self.success_ratio >= threshold

    def as_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["success_ratio"] = self.success_ratio
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

    effective_mode = (mode or report.mode or "master").lower()
    identity = str(identity_status or report.identity_status or "not_started").lower()
    report.identity_status = identity
    coverage = dict(report.coverage or {})
    coverage.setdefault("horror", "not_checked_by_policy")

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

    # A status flag alone is not enough: reject a claimed placement when the
    # captured state stayed unchanged (the classic 0 -> 0 false-success).
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

    transactions = report.evidence.get("placementTransactions")
    if isinstance(transactions, list) and transactions:
        transition_proven = False
        for transaction in transactions:
            if not isinstance(transaction, dict):
                continue
            if transaction.get("status") != "VERIFIED" or transaction.get("targetVerified") is not True:
                continue
            before = transaction.get("before")
            after = transaction.get("after")
            if before is not None and after is not None:
                if before != after:
                    transition_proven = True
                    break
                continue
            evidence = transaction.get("transitionEvidence") or {}
            semantic_before = evidence.get("before")
            semantic_after = evidence.get("after")
            if semantic_before is not None and semantic_after is not None and semantic_before != semantic_after:
                transition_proven = True
                break
        if not transition_proven:
            return report.apply_quality_gate(
                QualityGateResult.failure(
                    "placement_not_verified",
                    coverage=coverage,
                    evidence={"placementTransactions": transactions},
                    message="Placement transactions do not prove a state transition.",
                )
            )

    if not report.cleanup_verified:
        return report.apply_quality_gate(
            QualityGateResult.failure(
                "placement_not_verified",
                coverage=coverage,
                evidence={"cleanupVerified": False},
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
                    coverage=coverage,
                    evidence={
                        "succeeded": report.normal_remixes_played,
                        "attempted": report.normal_remixes_total,
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
                    coverage=coverage,
                    evidence={
                        "iconRatio": report.normal_icons_showcased / report.normal_icons_total,
                        "remixRatio": report.normal_remixes_played / report.normal_remixes_total,
                        "iconThreshold": 0.80,
                        "remixThreshold": 2 / 3,
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
                    coverage=coverage,
                    evidence={
                        "iconRatio": report.normal_icons_showcased / report.normal_icons_total,
                        "remixRatio": report.normal_remixes_played / report.normal_remixes_total,
                        "iconThreshold": 0.80,
                        "remixThreshold": 0.50,
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
                "identityStatus": identity,
                "identityVerified": identity == "verified",
            },
            message="Workflow passed the shared quality gate.",
        )
    )


def failure_is_retryable(code: Optional[str]) -> bool:
    """Return false for deterministic quality/data failures."""

    return bool(code) and code not in NON_RETRYABLE_FAILURE_CODES

"""Independent mini-remix workflow for Tracker jobs.

The legacy showcase and full-remix workflows live in
``sprunki_ultimate_master.py`` and intentionally remain the owner of their
existing behaviour.  This module only contains the small amount of routing,
mapping and orchestration that is unique to a Tracker ``mini_remix`` job.

The runner receives the existing ``SafeOperationManager`` as a factory.  That
keeps this module independent from the large legacy controller (and avoids an
import cycle), while making the drag/record/cleanup implementation shared by
the old and new workflows.
"""

from __future__ import annotations

import copy
import hmac
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from manual_profile_v3 import (
    ManualProfileJobContext,
    ManualProfileV3Error,
    canonicalize_cocrea_url,
    profile_sha256,
    validate_v3_content,
)
from workflow_quality import (
    QualityGateResult,
    WorkflowReport,
    evaluate_workflow_report,
    placement_transaction_proves_progress,
)


MINI_REMIX_MODE = "mini_remix"
MINI_REMIX_DURATION_SECONDS = 30
MINI_REMIX_FINAL_CLEANUP_POLICY = "single_final_mini_remix"
_VIDEO_EXTENSIONS = {".mkv", ".mp4", ".webm", ".mov", ".avi"}


class MiniRemixValidationError(ValueError):
    """Deterministic, fail-closed validation error for a mini job."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        evidence: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.code = str(code)
        self.evidence = dict(evidence or {})
        super().__init__(message)


@dataclass
class MiniRemixRequest:
    """Tracker's immutable mini-remix selection.

    The list order is meaningful: it is the left-to-right placement order.
    Values are deliberately kept as marker indices, never character names or
    slot positions.
    """

    icon_indices: List[int] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.icon_indices = list(self.icon_indices or [])

    @classmethod
    def from_payload(cls, payload: Any) -> "MiniRemixRequest":
        if not isinstance(payload, Mapping):
            raise MiniRemixValidationError(
                "mini_remix_block_invalid",
                "mini_remix must be an object.",
            )

        forbidden_fields = {
            "duration",
            "recording_duration",
            "listen_seconds",
            "slot_indices",
            "slot_indexes",
            "character_names",
            "names",
            "characters",
        }
        present_forbidden = sorted(forbidden_fields.intersection(payload.keys()))
        if present_forbidden:
            raise MiniRemixValidationError(
                "mini_remix_payload_forbidden_field",
                "Mini-remix accepts only icon_indices; duration, names and slot indices are Creator-owned.",
                evidence={"fields": present_forbidden},
            )

        raw_indices = payload.get("icon_indices")
        if not isinstance(raw_indices, list) or not raw_indices:
            raise MiniRemixValidationError(
                "mini_remix_icon_indices_invalid",
                "mini_remix.icon_indices must be a non-empty list.",
            )

        indices: List[int] = []
        for value in raw_indices:
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise MiniRemixValidationError(
                    "mini_remix_icon_indices_invalid",
                    "Every mini-remix icon index must be a non-negative integer.",
                    evidence={"value": value},
                )
            indices.append(value)

        if len(indices) != len(set(indices)):
            raise MiniRemixValidationError(
                "mini_remix_duplicate_icon_index",
                "mini_remix.icon_indices must not contain duplicates.",
                evidence={"iconIndices": indices},
            )

        return cls(indices)

    def as_dict(self) -> Dict[str, Any]:
        return {"icon_indices": list(self.icon_indices)}


@dataclass
class MiniRemixPlan:
    """Resolved icon positions and slot objects for one mini operation."""

    request: MiniRemixRequest
    icon_markers: List[Dict[str, Any]]
    icon_positions: List[int]
    target_slots: List[Dict[str, Any]]
    target_slot_indices: List[int]
    mapping: List[Dict[str, Any]]


def parse_mini_remix_request(job: Optional[Mapping[str, Any]]) -> Optional[MiniRemixRequest]:
    """Parse the strict Tracker mode contract.

    ``None`` means that the payload is a legacy job.  A present mini block is
    never silently ignored: it requires the exact ``production_mode`` value
    and therefore cannot fall back to a showcase job.
    """

    if not isinstance(job, Mapping):
        raise MiniRemixValidationError(
            "mini_remix_payload_invalid",
            "Tracker job payload must be an object.",
        )

    has_block = "mini_remix" in job
    requested_mode = job.get("production_mode")

    if not has_block:
        if requested_mode == MINI_REMIX_MODE:
            raise MiniRemixValidationError(
                "mini_remix_block_missing",
                "production_mode=mini_remix requires a mini_remix block.",
            )
        return None

    if requested_mode != MINI_REMIX_MODE:
        raise MiniRemixValidationError(
            "mini_remix_mode_contract",
            "A mini_remix block requires production_mode=mini_remix; it never falls back to a legacy mode.",
            evidence={
                "productionMode": requested_mode,
                "hasMiniRemixBlock": True,
            },
        )

    return MiniRemixRequest.from_payload(job.get("mini_remix"))


def validate_tracker_manual_profile_context(
    context: Any,
    *,
    expected_url: Optional[str] = None,
) -> ManualProfileJobContext:
    """Validate the queue claim before any local override can be consulted."""

    if not isinstance(context, ManualProfileJobContext) or context.field_state != "object":
        raise MiniRemixValidationError(
            "mini_remix_manual_profile_required",
            "mini_remix requires an object manual_profile snapshot from Tracker.",
        )

    expected_revision_id = context.expected_revision_id
    if (
        isinstance(expected_revision_id, bool)
        or not isinstance(expected_revision_id, int)
        or expected_revision_id <= 0
    ):
        raise MiniRemixValidationError(
            "mini_remix_revision_required",
            "mini_remix requires a positive manual_profile_revision_id.",
        )

    envelope = context.envelope
    if not isinstance(envelope, Mapping) or not isinstance(envelope.get("content"), Mapping):
        raise MiniRemixValidationError(
            "mini_remix_profile_invalid",
            "Tracker manual_profile must contain a v3 content object.",
        )

    envelope_revision_id = envelope.get("revision_id")
    if (
        isinstance(envelope_revision_id, bool)
        or not isinstance(envelope_revision_id, int)
        or envelope_revision_id <= 0
        or envelope_revision_id != expected_revision_id
    ):
        raise MiniRemixValidationError(
            "mini_remix_revision_mismatch",
            "manual_profile_revision_id does not match the Tracker profile snapshot.",
            evidence={
                "expectedRevisionId": expected_revision_id,
                "profileRevisionId": envelope_revision_id,
            },
        )

    for field_name in ("id", "revision"):
        value = envelope.get(field_name)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise MiniRemixValidationError(
                "mini_remix_profile_invalid",
                f"Tracker manual_profile.{field_name} is invalid.",
            )
    envelope_hash = envelope.get("sha256")
    if not isinstance(envelope_hash, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", envelope_hash):
        raise MiniRemixValidationError(
            "mini_remix_profile_invalid",
            "Tracker manual_profile.sha256 is invalid.",
        )

    # This is the same schema validator used by materialize_tracker_profile;
    # hash, URL and viewport binding are verified there once the live viewport
    # is known.  Running the schema check here still guarantees that a bad
    # snapshot cannot reach local JSON fallback or browser input.
    try:
        validate_v3_content(envelope["content"])
    except ManualProfileV3Error as exc:
        code = str(exc).split(":", 1)[0] or "mini_remix_profile_invalid"
        if not code.startswith("manual_profile_"):
            code = "mini_remix_profile_invalid"
        raise MiniRemixValidationError(
            "mini_remix_profile_invalid",
            f"Tracker manual profile failed v3 validation: {exc}",
            evidence={"profileError": str(exc)},
        ) from exc

    calculated_hash = profile_sha256(envelope["content"])
    if not hmac.compare_digest(calculated_hash.lower(), envelope_hash.lower()):
        raise MiniRemixValidationError(
            "mini_remix_profile_invalid",
            "Tracker manual_profile.sha256 does not match its content.",
        )
    if expected_url:
        try:
            profile_url = canonicalize_cocrea_url(envelope["content"].get("canonicalUrl"))
            job_url = canonicalize_cocrea_url(expected_url)
        except Exception as exc:
            raise MiniRemixValidationError(
                "mini_remix_profile_invalid",
                f"Tracker manual_profile URL is invalid: {exc}",
            ) from exc
        if profile_url != job_url:
            raise MiniRemixValidationError(
                "mini_remix_profile_invalid",
                "Tracker manual_profile canonicalUrl does not match the job URL.",
            )

    return context


def _marker_index(marker: Any, group_name: str, position: int) -> int:
    if not isinstance(marker, Mapping):
        raise MiniRemixValidationError(
            "mini_remix_layout_invalid",
            f"{group_name}[{position}] is not an object.",
        )
    value = marker.get("index")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MiniRemixValidationError(
            "mini_remix_layout_invalid",
            f"{group_name}[{position}].index is invalid.",
            evidence={"group": group_name, "position": position, "value": value},
        )
    return value


def _is_disabled_marker(marker: Mapping[str, Any]) -> bool:
    """Accept the explicit disabled shapes used by legacy/generated layouts."""

    return bool(
        marker.get("disabled") is True
        or marker.get("enabled") is False
        or marker.get("active") is False
    )


def resolve_center_slot_indices(slot_count: int, selection_count: int) -> List[int]:
    """Return left-biased contiguous positions around the center slot."""

    if isinstance(slot_count, bool) or not isinstance(slot_count, int) or slot_count <= 0:
        raise MiniRemixValidationError(
            "mini_remix_slot_invalid",
            "The layout must contain at least one slot.",
        )
    if isinstance(selection_count, bool) or not isinstance(selection_count, int) or selection_count <= 0:
        raise MiniRemixValidationError(
            "mini_remix_icon_indices_invalid",
            "Mini-remix selection must contain at least one icon.",
        )
    if selection_count > slot_count:
        raise MiniRemixValidationError(
            "mini_remix_slot_count_insufficient",
            "The number of selected icons cannot exceed the number of slots.",
            evidence={"selectionCount": selection_count, "slotCount": slot_count},
        )

    center = (slot_count - 1) // 2
    start = center - (selection_count // 2)
    start = max(0, min(start, slot_count - selection_count))
    return list(range(start, start + selection_count))


def resolve_mini_remix_layout(
    json_data: Mapping[str, Any],
    request: MiniRemixRequest,
) -> MiniRemixPlan:
    """Resolve marker indices and calculate contiguous center slots.

    Icon lookup is explicitly by ``icon["index"]``.  The returned
    ``icon_positions`` are only an adapter for the existing SafeOperationManager,
    whose legacy API consumes positions in ``config.layout.icons``.
    """

    if not isinstance(json_data, Mapping):
        raise MiniRemixValidationError(
            "mini_remix_layout_invalid",
            "Generated JSON is not an object.",
        )
    layout = json_data.get("layout")
    # Keep the pure resolver convenient for callers/tests that already
    # extracted the layout object, while the workflow passes complete JSON.
    if layout is None and isinstance(json_data, Mapping) and {
        "icons",
        "slots",
    }.issubset(json_data):
        layout = json_data
    if not isinstance(layout, Mapping):
        raise MiniRemixValidationError(
            "mini_remix_layout_invalid",
            "Generated JSON has no layout object.",
        )

    icons = layout.get("icons")
    slots = layout.get("slots")
    if not isinstance(icons, list) or not icons:
        raise MiniRemixValidationError(
            "mini_remix_no_icons",
            "Generated JSON has no icon markers.",
        )
    if not isinstance(slots, list) or not slots:
        raise MiniRemixValidationError(
            "mini_remix_no_slots",
            "Generated JSON has no slot markers.",
        )

    icons_by_index: Dict[int, Dict[str, Any]] = {}
    icon_positions_by_index: Dict[int, int] = {}
    for position, raw_icon in enumerate(icons):
        icon_index = _marker_index(raw_icon, "icons", position)
        if icon_index in icons_by_index:
            raise MiniRemixValidationError(
                "mini_remix_layout_invalid",
                f"Duplicate icon marker index: {icon_index}.",
                evidence={"iconIndex": icon_index},
            )
        icons_by_index[icon_index] = dict(raw_icon)
        icon_positions_by_index[icon_index] = position

    sorted_slots: List[Dict[str, Any]] = []
    seen_slot_indices = set()
    for position, raw_slot in enumerate(slots):
        slot_index = _marker_index(raw_slot, "slots", position)
        if slot_index in seen_slot_indices:
            raise MiniRemixValidationError(
                "mini_remix_layout_invalid",
                f"Duplicate slot marker index: {slot_index}.",
                evidence={"slotIndex": slot_index},
            )
        seen_slot_indices.add(slot_index)
        if _is_disabled_marker(raw_slot):
            raise MiniRemixValidationError(
                "mini_remix_slot_disabled",
                f"Target slot marker {slot_index} is disabled.",
                evidence={"slotIndex": slot_index},
            )
        sorted_slots.append(dict(raw_slot))
    sorted_slots.sort(key=lambda slot: slot["index"])

    selected_markers: List[Dict[str, Any]] = []
    selected_positions: List[int] = []
    for requested_index in request.icon_indices:
        if requested_index not in icons_by_index:
            raise MiniRemixValidationError(
                "mini_remix_icon_index_not_found",
                f"Icon marker index {requested_index} does not exist in the manual-profile layout.",
                evidence={"iconIndex": requested_index},
            )
        marker = icons_by_index[requested_index]
        if _is_disabled_marker(marker):
            raise MiniRemixValidationError(
                "mini_remix_icon_disabled",
                f"Icon marker index {requested_index} is disabled.",
                evidence={"iconIndex": requested_index},
            )
        selected_markers.append(marker)
        selected_positions.append(icon_positions_by_index[requested_index])

    target_positions = resolve_center_slot_indices(len(sorted_slots), len(selected_markers))
    target_slots = [sorted_slots[position] for position in target_positions]
    target_slot_indices = [int(slot["index"]) for slot in target_slots]
    mapping = [
        {
            "iconIndex": request.icon_indices[position],
            "iconPosition": selected_positions[position],
            "slotIndex": target_slot_indices[position],
            "slotPosition": target_positions[position],
        }
        for position in range(len(selected_markers))
    ]
    return MiniRemixPlan(
        request=request,
        icon_markers=selected_markers,
        icon_positions=selected_positions,
        target_slots=target_slots,
        target_slot_indices=target_slot_indices,
        mapping=mapping,
    )


def _valid_video_output(path: Any) -> bool:
    try:
        normalized = os.path.abspath(str(path))
        return (
            os.path.isfile(normalized)
            and os.path.getsize(normalized) > 0
            and os.path.splitext(normalized)[1].lower() in _VIDEO_EXTENSIONS
        )
    except (OSError, TypeError, ValueError):
        return False


class MiniRemixWorkflow:
    """Run one validated mini-remix using the shared safe-operation engine."""

    def __init__(
        self,
        item: Any,
        *,
        game_player: Any,
        obs_controller: Any,
        logger: Any,
        safe_operation_manager_factory: Callable[..., Any],
        attempt_index: int = 1,
        output_namespace: Optional[str] = None,
        safe_operation_manager: Any = None,
    ) -> None:
        self.item = item
        self.game_player = game_player
        self.obs_controller = obs_controller
        self.logger = logger
        self.safe_operation_manager_factory = safe_operation_manager_factory
        self.attempt_index = max(1, int(attempt_index or 1))
        self.output_namespace = output_namespace
        self.safe_operation_manager = safe_operation_manager
        self.report = WorkflowReport(mode=MINI_REMIX_MODE)
        self.plan: Optional[MiniRemixPlan] = None
        self.output_path: Optional[str] = None
        self.safe_operation_called = False
        self.final_cleanup_not_required = False

    def _log(self, level: str, message: str, *args: Any) -> None:
        method = getattr(self.logger, level, None)
        if callable(method):
            method(message, *args)

    def _set_failure(
        self,
        code: str,
        message: str,
        *,
        retryable: Optional[bool] = None,
        evidence: Optional[Dict[str, Any]] = None,
    ) -> None:
        gate = QualityGateResult.failure(
            code,
            retryable=retryable,
            coverage={"normal": "verified" if self.plan else "unknown", "horror": "not_checked_by_policy"},
            evidence=evidence or {},
            message=message,
        )
        self.report.apply_quality_gate(gate)
        self.item.failure_code = code
        self.item.error_message = message
        self.item.status = "failed"

    def _capture_runtime_evidence(self) -> None:
        strategy = dict(getattr(self.game_player, "entry_strategy", {}) or {})
        runtime_state = str(strategy.get("verification") or "UNKNOWN").upper()
        self.report.entry_candidate = strategy.get("selected")
        self.report.tested_candidates = list(strategy.get("tested", []) or [])
        self.report.runtime_state = runtime_state
        self.report.runtime_verified = runtime_state == "PASS"
        self.report.evidence["entryStrategy"] = strategy

    def _placement_evidence(self) -> List[Dict[str, Any]]:
        if not self.plan:
            return []
        raw_transactions = list(
            getattr(self.game_player, "_workflow_placement_transactions", []) or []
        )
        position_to_icon = {
            position: marker_index
            for position, marker_index in zip(
                self.plan.icon_positions,
                self.plan.request.icon_indices,
            )
        }
        evidence: List[Dict[str, Any]] = []
        for transaction in raw_transactions:
            entry = dict(transaction or {})
            position = entry.get("iconIndex")
            entry["resolvedIconPosition"] = position
            entry["requestedIconIndex"] = position_to_icon.get(position)
            entry["targetSlotIndex"] = entry.get("slotIndex")
            evidence.append(entry)
        return evidence

    def _refresh_cleanup_and_placement(self) -> None:
        if not self.plan:
            return

        transactions = list(
            getattr(self.game_player, "_workflow_placement_transactions", []) or []
        )
        placement_evidence = self._placement_evidence()
        expected_pairs = [
            (mapping["iconIndex"], mapping["slotIndex"])
            for mapping in self.plan.mapping
        ]
        actual_pairs = [
            (entry.get("requestedIconIndex"), entry.get("targetSlotIndex"))
            for entry in placement_evidence
        ]
        exact_mapping = (
            len(transactions) == len(self.plan.mapping)
            and actual_pairs == expected_pairs
            and all(
                placement_transaction_proves_progress(transaction)
                for transaction in transactions
            )
        )
        self.report.placement_verified = bool(exact_mapping)
        self.report.evidence["placementTransactions"] = transactions
        self.report.evidence["placementEvidence"] = placement_evidence
        self.report.evidence["placementMapping"] = list(self.plan.mapping)
        if transactions:
            self.report.placement_before = transactions[0].get("before")
            self.report.placement_after = transactions[-1].get("after")

        updater = getattr(self.game_player, "_update_report_cleanup_evidence", None)
        if callable(updater):
            try:
                updater(self.report)
            except Exception as exc:
                self._log("warning", "Mini-remix cleanup evidence update failed: %s", exc)

        boundary = dict(
            getattr(self.game_player, "_last_cleanup_boundary_evidence", {}) or {}
        )
        self.report.evidence["cleanupBoundary"] = boundary
        if boundary:
            self.report.evidence.setdefault(
                "boardState", boundary.get("boardState", self.report.cleanup_state)
            )
            self.report.cleanup_state = str(
                boundary.get("boardState", self.report.cleanup_state or "UNKNOWN")
            )

        cleanup_verified = bool(
            self.report.placement_verified
            and transactions
            and all(entry.get("cleanupVerified") is True for entry in transactions)
        )
        boundary_checker = getattr(
            self.game_player, "_cleanup_boundary_result_is_verified", None
        )
        if (
            not cleanup_verified
            and callable(boundary_checker)
            and boundary.get("cleanupBoundaryCompleted") is True
        ):
            try:
                cleanup_verified = bool(boundary_checker(boundary)) and bool(
                    self.report.placement_verified
                )
            except Exception:
                cleanup_verified = False
        self.report.cleanup_verified = cleanup_verified

        recording_complete = bool(
            self.report.evidence.get("miniRemixRecordingCompleted", False)
        )
        content_complete = bool(
            self.report.runtime_verified
            and self.report.placement_verified
            and recording_complete
            and _valid_video_output(self.output_path)
        )
        self.report.evidence["finalRemixContentComplete"] = content_complete
        self.report.evidence["finalCleanupOptional"] = bool(
            self.report.evidence.get("cleanupNotRequired", False)
        )

    def _mark_final_cleanup_not_required(self) -> None:
        """Record that this one-shot mini job has no board to preserve.

        This is deliberately set only after the exact placement ledger and
        valid 30-second output have passed.  Any earlier failure still uses
        the normal bounded cleanup/Re-Run recovery path.
        """

        self.final_cleanup_not_required = True
        self.report.evidence.update(
            {
                "cleanupNotRequired": True,
                "cleanupRequired": False,
                "cleanupPolicy": MINI_REMIX_FINAL_CLEANUP_POLICY,
                "cleanupSkipReason": "single_mini_remix_job_complete",
                # Keep the existing field for consumers that already render
                # final cleanup policy decisions.
                "finalCleanupOptional": True,
            }
        )
        self._log(
            "info",
            "Mini-remix content is complete; skipping board reset/Re-Run because this job has no follow-up workflow.",
        )

    def _create_safe_operation_manager(self) -> Any:
        if self.safe_operation_manager is not None:
            return self.safe_operation_manager
        return self.safe_operation_manager_factory(
            self.game_player,
            self.obs_controller,
            self.logger,
            self.attempt_index,
        )

    def _preserve_attempt_output(self, path: Any) -> None:
        if not path:
            return
        attempts = getattr(self.item, "attempt_outputs", None)
        if not isinstance(attempts, dict):
            attempts = {}
            self.item.attempt_outputs = attempts
        bucket = attempts.setdefault(self.attempt_index, [])
        path = str(path)
        if path not in bucket:
            bucket.append(path)

    def _select_attempt_index(self) -> None:
        """Give every fresh mini execution its own output attempt namespace."""

        attempts = getattr(self.item, "attempt_outputs", None)
        known_attempts = []
        if isinstance(attempts, dict):
            for raw_attempt in attempts:
                try:
                    known_attempts.append(int(raw_attempt))
                except (TypeError, ValueError):
                    continue
        highest_attempt = max(known_attempts, default=0)
        if highest_attempt and self.attempt_index <= highest_attempt:
            self.attempt_index = highest_attempt + 1
        elif getattr(self.item, "output_files", None) and self.attempt_index <= 1:
            # Be defensive for callers that populated output_files without
            # first writing the bounded attempt ledger.
            self.attempt_index = 2
        self.item.workflow_attempt = self.attempt_index

    def run(self) -> bool:
        self._select_attempt_index()
        prior_report = getattr(self.item, "workflow_report", None)
        prior_retry_attempts = []
        if isinstance(prior_report, WorkflowReport):
            prior_retry_attempts = list(prior_report.evidence.get("retryAttempts", []) or [])
        self.report.evidence["retryAttempts"] = prior_retry_attempts
        self.item.workflow_report = self.report
        self.item.failure_code = None
        self.item.error_message = None
        self.item.status = "processing"

        # Each mini execution is a fresh publication attempt.  Keep old paths
        # in the bounded attempt ledger, but never leave them in output_files.
        for old_path in list(getattr(self.item, "output_files", []) or []):
            self._preserve_attempt_output(old_path)
        self.item.output_files = []
        # A smart retry may reuse an adapter/test double instead of creating a
        # fresh GamePlayer.  Reset the shared placement ledger explicitly so
        # the exact-N gate can never inherit transactions from an older try.
        if hasattr(self.game_player, "_workflow_placement_transactions"):
            self.game_player._workflow_placement_transactions = []

        safe_ops = None
        try:
            request = getattr(self.item, "mini_remix_request", None)
            if not isinstance(request, MiniRemixRequest):
                if isinstance(request, Mapping):
                    request = MiniRemixRequest.from_payload(request)
                else:
                    raise MiniRemixValidationError(
                        "mini_remix_block_missing",
                        "No validated mini-remix request is attached to the job.",
                    )
            self.item.mini_remix_request = request
            validate_tracker_manual_profile_context(
                getattr(self.item, "manual_profile_context", None),
                expected_url=getattr(self.item, "source_url", None),
            )

            self.plan = resolve_mini_remix_layout(
                getattr(self.item, "json_data", None) or {},
                request,
            )
            self.report.layout_icons = len(
                (self.item.json_data or {}).get("layout", {}).get("icons", [])
            )
            self.report.layout_slots = len(
                (self.item.json_data or {}).get("layout", {}).get("slots", [])
            )
            self.report.evidence.update(
                {
                    "effectiveMode": MINI_REMIX_MODE,
                    "selectedIconIndices": list(request.icon_indices),
                    "targetSlotIndices": list(self.plan.target_slot_indices),
                    "targetSlotPositions": [
                        mapping["slotPosition"] for mapping in self.plan.mapping
                    ],
                    "placementMapping": list(self.plan.mapping),
                    "listenSeconds": MINI_REMIX_DURATION_SECONDS,
                    "recordingRequestedSeconds": MINI_REMIX_DURATION_SECONDS,
                }
            )

            if not self.obs_controller.full_setup(use_full_screen=True):
                raise RuntimeError("OBS full-screen setup failed")
            if not self.game_player.setup_game_environment(first_setup=False):
                setup_result = getattr(self.game_player, "last_setup_result", None)
                if isinstance(setup_result, QualityGateResult):
                    self._set_failure(
                        setup_result.failure_code or "runtime_verification_unknown",
                        setup_result.message or "Game environment setup failed.",
                        retryable=setup_result.retryable,
                        evidence={"setupResult": setup_result.as_dict()},
                    )
                    return False
                else:
                    raise RuntimeError("Game environment setup failed")

            # start_recording() clears OBS.recording_area, so capture it before
            # the one shared safe operation can start recording.
            self.item.recording_area = copy.deepcopy(
                getattr(self.obs_controller, "recording_area", None)
            )
            self.report.evidence["recordingArea"] = copy.deepcopy(
                self.item.recording_area
            )
            self._capture_runtime_evidence()
            if not self.report.runtime_verified:
                self._set_failure(
                    "runtime_verification_unknown",
                    "Mini-remix runtime playability was not verified.",
                    retryable=True,
                    evidence={"entryStrategy": self.report.evidence.get("entryStrategy", {})},
                )
                return False

            safe_ops = self._create_safe_operation_manager()
            self.safe_operation_called = True
            namespace = (
                self.output_namespace
                or getattr(self.item, "output_namespace", None)
                or getattr(self.item, "project_slug", None)
                or "mini-remix"
            )
            filename = f"{namespace}-mini-remix.mp4"
            success, output_path, error_message = safe_ops.safe_remix_operation(
                list(self.plan.icon_positions),
                list(self.plan.target_slots),
                filename,
                recording_duration=MINI_REMIX_DURATION_SECONDS,
                cleanup_after_recording=False,
            )
            self.output_path = output_path
            self._preserve_attempt_output(output_path)
            output_valid = bool(success and _valid_video_output(output_path))
            self.report.evidence.update(
                {
                    "miniRemixRecordingCompleted": output_valid,
                    "recordingStatus": "PASS" if output_valid else "FAIL",
                    "recordingOutputPath": str(output_path) if output_path else None,
                    "outputVideoValid": output_valid,
                    "outputFiles": [str(output_path)] if output_valid else [],
                    "recordingError": error_message,
                }
            )
            self._refresh_cleanup_and_placement()
            if not output_valid:
                self._set_failure(
                    "mini_remix_recording_not_verified",
                    error_message or "Mini-remix recording did not produce one valid video.",
                    retryable=True,
                    evidence={"outputPath": output_path, "outputVideoValid": False},
                )
                return False
            if not self.report.placement_verified:
                self._set_failure(
                    "placement_not_verified",
                    "Mini-remix placement ledger does not match every requested icon/slot pair.",
                    retryable=True,
                    evidence={
                        "placementEvidence": self.report.evidence.get("placementEvidence", []),
                        "placementRetryable": True,
                        "cleanupRetryable": True,
                    },
                )
                return False

            # The video is the only artifact this mini job produces.  Once it
            # is valid and every requested placement is verified, there is no
            # subsequent remix/showcase that needs a clean board.  The game
            # window is still closed in finally; only the reset/Re-Run is
            # intentionally skipped.
            self._mark_final_cleanup_not_required()
            self.item.output_files = [str(output_path)]
            return True

        except MiniRemixValidationError as exc:
            self._set_failure(exc.code, str(exc), retryable=False, evidence=exc.evidence)
            return False
        except Exception as exc:
            self._log("error", "Mini-remix workflow failed: %s", exc)
            self._set_failure(
                "workflow_stopped" if getattr(self.game_player, "should_stop", False) else "runtime_verification_unknown",
                f"Mini-remix workflow failed: {exc}",
                retryable=False if getattr(self.game_player, "should_stop", False) else True,
                evidence={"error": str(exc)},
            )
            return False
        finally:
            if safe_ops is not None and not self.final_cleanup_not_required:
                try:
                    safe_ops.emergency_cleanup_all()
                except Exception as cleanup_error:
                    self._log("error", "Mini-remix emergency cleanup failed: %s", cleanup_error)

            if self.plan is not None:
                self._refresh_cleanup_and_placement()
                if self.report.quality_gate is None or self.report.quality_gate.passed:
                    evaluate_workflow_report(
                        self.report,
                        mode=MINI_REMIX_MODE,
                        require_identity=False,
                    )
                self.item.workflow_report = self.report

            if not self.report.was_successful:
                # A partial/failed recording remains available only through
                # attempt diagnostics; it is never a publishable output.
                self.item.output_files = []
                self.item.status = "failed"
            elif self.output_path:
                self.item.output_files = [str(self.output_path)]

            try:
                self.game_player.close_game_window()
            except Exception as close_error:
                self._log("warning", "Mini-remix browser cleanup failed: %s", close_error)


def run_mini_remix_workflow(
    item: Any,
    *,
    game_player: Any,
    obs_controller: Any,
    logger: Any,
    safe_operation_manager_factory: Callable[..., Any],
    attempt_index: int = 1,
    output_namespace: Optional[str] = None,
    safe_operation_manager: Any = None,
) -> bool:
    """Convenience entry point used by UnifiedProcessingManager and tests."""

    return MiniRemixWorkflow(
        item,
        game_player=game_player,
        obs_controller=obs_controller,
        logger=logger,
        safe_operation_manager_factory=safe_operation_manager_factory,
        attempt_index=attempt_index,
        output_namespace=output_namespace,
        safe_operation_manager=safe_operation_manager,
    ).run()


__all__ = [
    "MINI_REMIX_DURATION_SECONDS",
    "MINI_REMIX_MODE",
    "MiniRemixPlan",
    "MiniRemixRequest",
    "MiniRemixValidationError",
    "MiniRemixWorkflow",
    "parse_mini_remix_request",
    "resolve_center_slot_indices",
    "resolve_mini_remix_layout",
    "run_mini_remix_workflow",
    "validate_tracker_manual_profile_context",
]

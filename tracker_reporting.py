"""Structured diagnostics sent from Creator to Tracker.

The gameplay quality gate remains the authority for completion.  This module
only turns the already-collected workflow evidence into a bounded, predictable
wire format that Tracker can render and inspect.  It deliberately does not
decide whether a workflow is playable.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Optional
from urllib.parse import urlparse


SCHEMA_VERSION = "sprunki.tracker.report.v1"
PASS = "PASS"
FAIL = "FAIL"
UNKNOWN = "UNKNOWN"
SKIPPED = "SKIPPED"
NOT_STARTED = "NOT_STARTED"
NOT_CHECKED_BY_POLICY = "NOT_CHECKED_BY_POLICY"

_SENSITIVE_KEYS = {
    "password",
    "passwd",
    "token",
    "secret",
    "authorization",
    "cookie",
    "api_key",
    "apikey",
    "access_key",
    "private_key",
}

_FAILURE_CONTEXT = {
    "no_icons": (
        "layout",
        "Mod không có icon playable.",
        "Bổ sung icon vào layout hoặc kiểm tra lại JSON.",
    ),
    "no_slots": (
        "layout",
        "Mod không có slot playable.",
        "Bổ sung slot vào layout hoặc kiểm tra lại JSON.",
    ),
    "manual_json_invalid": (
        "json_generation",
        "Manual JSON không hợp lệ.",
        "Sửa JSON và chạy lại validation trước khi mở runtime.",
    ),
    "no_playable_entry": (
        "runtime_entry",
        "Không tìm thấy entry playable.",
        "Kiểm tra URL/entry candidate và trạng thái runtime.",
    ),
    "runtime_verification_unknown": (
        "runtime_entry",
        "Không chứng minh được runtime gameplay.",
        "Retry browser/runtime verification và kiểm tra entry candidate.",
    ),
    "placement_not_verified": (
        "placement",
        "Placement không chứng minh được state transition.",
        "Kiểm tra target slot, before/after state và cleanup transaction.",
    ),
    "empty_remix": (
        "remix",
        "Không có remix nào được attempt.",
        "Kiểm tra remix library và dữ liệu slot.",
    ),
    "remix_threshold_not_met": (
        "remix",
        "Remix/icon coverage không đạt threshold.",
        "Kiểm tra các remix failed và threshold hiện hành.",
    ),
    "phase_skipped": (
        "both",
        "BOTH workflow chưa hoàn tất đủ Master và Individual.",
        "Chạy đủ cả hai phase; không publish output một phase như completed.",
    ),
    "recorded_identity_missing": (
        "identity",
        "Thiếu recorded identity bắt buộc.",
        "Chạy lại identity resolver và kiểm tra artifact identity.",
    ),
    "recorded_identity_needs_review": (
        "identity",
        "Recorded identity cần review.",
        "Review identity report trước khi cho phép completed.",
    ),
    "artifact_missing": (
        "artifacts",
        "Artifact output thiếu, rỗng hoặc không đọc được.",
        "Kiểm tra output path và quyền đọc file.",
    ),
    "skipped_by_policy": (
        "policy",
        "Job bị skip theo policy.",
        "Không publish job này như completed.",
    ),
    "workflow_stopped": (
        "workflow",
        "Workflow bị dừng trước khi hoàn tất.",
        "Kiểm tra stop reason và phase cuối cùng đã chạy.",
    ),
}


def _safe_value(value: Any, key: Optional[str] = None) -> Any:
    """Convert report data to JSON-safe values and redact sensitive keys."""

    if key and key.lower() in _SENSITIVE_KEYS:
        return "[REDACTED]"
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, Path)):
        return value.isoformat() if isinstance(value, datetime) else str(value)
    if is_dataclass(value):
        return _safe_value(asdict(value), key)
    if isinstance(value, dict):
        return {str(k): _safe_value(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_safe_value(entry) for entry in value]
    return str(value)


def json_safe(value: Any) -> Any:
    """Public JSON-safe conversion used by Creator before requests.json."""

    return _safe_value(value)


def _redact_text(text: str) -> tuple[str, list[str]]:
    redactions: list[str] = []
    patterns = (
        (
            "credential",
            re.compile(
                r"(?i)(password|passwd|token|secret|api[_-]?key|authorization)"
                r"(\s*[:=]\s*)([\"']?)([^\s,;\"']+)",
            ),
            r"\1\2\3[REDACTED]",
        ),
        (
            "cookie",
            re.compile(r"(?i)(cookie)(\s*[:=]\s*)([^\r\n]+)"),
            r"\1\2[REDACTED]",
        ),
    )
    redacted = text
    for label, pattern, replacement in patterns:
        redacted, count = pattern.subn(replacement, redacted)
        if count:
            redactions.append(f"{label}:{count}")
    return redacted, redactions


def _hash_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_reference(
    path: Optional[str],
    *,
    kind: str = "artifact",
    hash_file: bool = True,
) -> Dict[str, Any]:
    """Describe a local file without assuming Tracker shares the filesystem."""

    normalized = str(path) if path else ""
    result: Dict[str, Any] = {
        "kind": kind,
        "path": os.path.abspath(normalized) if normalized else None,
        "filename": os.path.basename(normalized) if normalized else None,
        "exists": False,
        "readable": False,
        "size_bytes": 0,
        "sha256": None,
        "access_mode": "local_path",
        "download_url": None,
    }
    if not normalized:
        return result

    try:
        absolute = os.path.abspath(normalized)
        result["path"] = absolute
        result["exists"] = os.path.isfile(absolute)
        if result["exists"]:
            result["size_bytes"] = os.path.getsize(absolute)
            with open(absolute, "rb") as handle:
                handle.read(1)
            result["readable"] = True
            if hash_file:
                result["sha256"] = _hash_file(absolute)
            else:
                result["hash_skipped"] = True
    except OSError as exc:
        result["error"] = str(exc)
    return result


def build_log_snapshot(
    log_path: Optional[str],
    *,
    excerpt_chars: int = 12000,
) -> Dict[str, Any]:
    """Return a redacted tail plus a reference to the complete local log."""

    reference = file_reference(log_path, kind="job_log")
    snapshot: Dict[str, Any] = {
        "upload_status": "local_reference_only" if reference["exists"] else "missing",
        "full_log_ref": reference,
        "excerpt": "",
        "truncated": False,
        "line_count": 0,
        "redactions": [],
    }
    if not reference["exists"]:
        return snapshot

    try:
        with open(reference["path"], "rb") as handle:
            raw = handle.read()
        text = raw.decode("utf-8", errors="replace")
        snapshot["line_count"] = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
        redacted, redactions = _redact_text(text)
        excerpt = redacted[-max(1, int(excerpt_chars)) :]
        snapshot["excerpt"] = excerpt
        snapshot["truncated"] = len(redacted) > len(excerpt)
        snapshot["redactions"] = redactions
    except OSError as exc:
        snapshot["upload_status"] = "read_failed"
        snapshot["read_error"] = str(exc)
    return snapshot


def _report_dict(workflow_report: Any) -> Dict[str, Any]:
    if workflow_report is None:
        return {}
    if hasattr(workflow_report, "as_dict"):
        return json_safe(workflow_report.as_dict())
    return json_safe(workflow_report) if isinstance(workflow_report, dict) else {}


def _normalise_status(value: Any) -> str:
    raw = str(value or "").strip().lower()
    return {
        "pass": PASS,
        "passed": PASS,
        "success": PASS,
        "successful": PASS,
        "completed": PASS,
        "verified": PASS,
        "fail": FAIL,
        "failed": FAIL,
        "failure": FAIL,
        "error": FAIL,
        "unknown": UNKNOWN,
        "skipped": SKIPPED,
        "skip": SKIPPED,
        "not_started": NOT_STARTED,
        "not started": NOT_STARTED,
        "not_checked_by_policy": NOT_CHECKED_BY_POLICY,
    }.get(raw, str(value).upper() if value else NOT_STARTED)


def _phase_from_item(value: Any) -> str:
    return _normalise_status(value)


def _ratio(numerator: Any, denominator: Any) -> float:
    try:
        denominator_value = float(denominator or 0)
        return float(numerator or 0) / denominator_value if denominator_value > 0 else 0.0
    except (TypeError, ValueError):
        return 0.0


def _check(
    name: str,
    status: str,
    *,
    required: bool = True,
    failure_code: Optional[str] = None,
    evidence: Optional[Dict[str, Any]] = None,
    accepted_by_policy: bool = False,
) -> Dict[str, Any]:
    normalized = _normalise_status(status)
    passed = normalized == PASS or (accepted_by_policy and normalized == NOT_CHECKED_BY_POLICY)
    return {
        "name": name,
        "status": normalized,
        "required": required,
        "passed": passed,
        "accepted_by_policy": accepted_by_policy,
        "failure_code": failure_code,
        "evidence": json_safe(evidence or {}),
    }


def _phase_status_from_report(
    report: Dict[str, Any],
    *,
    runtime: bool = False,
) -> str:
    if not report:
        return NOT_STARTED
    if runtime:
        if report.get("runtime_verified") is True and str(report.get("runtime_state", "")).upper() == PASS:
            return PASS
        if str(report.get("runtime_state", "")).upper() == UNKNOWN:
            return UNKNOWN
        return FAIL
    if report.get("was_successful") is True:
        return PASS
    gate = report.get("quality_gate")
    if isinstance(gate, dict) and str(gate.get("status", "")).upper() == UNKNOWN:
        return UNKNOWN
    return FAIL


def _normalise_attempts(attempts: Iterable[Any]) -> list[Dict[str, Any]]:
    normalized = []
    for attempt in attempts or []:
        if not isinstance(attempt, dict):
            continue
        normalized.append(
            {
                "operation": attempt.get("operation"),
                "attempt": attempt.get("attempt"),
                "status": _normalise_status(attempt.get("status") or (PASS if "succeeded" in str(attempt.get("error", "")).lower() else FAIL)),
                "failure_code": attempt.get("failure_code") or attempt.get("failureCode"),
                "retryable": attempt.get("retryable"),
                "started_at": attempt.get("started_at") or attempt.get("startedAt"),
                "ended_at": attempt.get("ended_at") or attempt.get("endedAt"),
                "duration_ms": attempt.get("duration_ms") or attempt.get("durationMs"),
                "error_summary": attempt.get("error_summary") or attempt.get("error"),
                "exception": bool(attempt.get("exception", False)),
            }
        )
    return json_safe(normalized)


def _manifest_reference(manifest_data: Optional[Dict[str, Any]], manifest_path: Optional[str]) -> Dict[str, Any]:
    path = manifest_path
    if not path and isinstance(manifest_data, dict):
        output_directory = manifest_data.get("projectInfo", {}).get("outputDirectory")
        if output_directory:
            path = os.path.join(str(output_directory), "manifest.json")
    return file_reference(path, kind="manifest")


def build_tracker_diagnostic_report(
    job: Optional[Dict[str, Any]],
    item: Any,
    workflow_report: Any,
    *,
    manifest_data: Optional[Dict[str, Any]] = None,
    manifest_path: Optional[str] = None,
    log_snapshot: Optional[Dict[str, Any]] = None,
    output_files_payload: Optional[Dict[str, Any]] = None,
    report_id: Optional[str] = None,
    attempt: int = 1,
    max_attempts: Optional[int] = None,
    generated_at: Optional[datetime] = None,
    terminal_status: Optional[str] = None,
    terminal_error: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the complete Creator-to-Tracker diagnostic envelope."""

    job = job or {}
    report = _report_dict(workflow_report)
    gate = report.get("quality_gate") if isinstance(report.get("quality_gate"), dict) else {}
    item_json = getattr(item, "json_data", None) if item is not None else None
    item_json = item_json if isinstance(item_json, dict) else {}
    layout = item_json.get("layout") if isinstance(item_json.get("layout"), dict) else {}
    icons = layout.get("icons") if isinstance(layout.get("icons"), list) else []
    slots = layout.get("slots") if isinstance(layout.get("slots"), list) else []
    source_url = (
        getattr(item, "source_url", None)
        or item_json.get("currentUrl")
        or item_json.get("url")
        or job.get("mod_game_url")
        or ""
    )
    project_slug = getattr(item, "project_slug", None) or job.get("project_slug")
    mode = getattr(item, "mode", None) or job.get("production_mode") or report.get("mode") or "unknown"
    failure_code = (
        getattr(item, "failure_code", None)
        or report.get("failure_code")
        or gate.get("failure_code")
    )
    if not workflow_report and not failure_code:
        failure_code = "runtime_verification_unknown"
    final_status = str(terminal_status or ("completed" if gate.get("passed") is True else "failed")).lower()
    if final_status != "completed" and not failure_code:
        failure_code = "workflow_stopped"
    gate_status = _normalise_status(gate.get("status") or (PASS if gate.get("passed") is True else UNKNOWN if not workflow_report else FAIL))
    gate_passed = gate.get("passed") is True
    retryable = bool(
        getattr(item, "recorded_identity_retryable", False)
        if failure_code in {"recorded_identity_missing", "recorded_identity_needs_review"}
        else gate.get("retryable", report.get("retryable", False))
    )

    phase_reports = report.get("phase_reports") if isinstance(report.get("phase_reports"), dict) else {}
    runtime_status = _phase_status_from_report(report, runtime=True)
    placement_before = report.get("placement_before")
    placement_after = report.get("placement_after")
    placement_transactions = report.get("evidence", {}).get("placementTransactions", []) if isinstance(report.get("evidence"), dict) else []
    placement_status = PASS if report.get("placement_verified") is True and placement_before != placement_after else FAIL if workflow_report else NOT_STARTED
    cleanup_status = PASS if report.get("cleanup_verified") is True else FAIL if workflow_report else NOT_STARTED
    identity_status = str(
        getattr(item, "recorded_identity_status", None)
        or report.get("identity_status")
        or "not_started"
    ).lower()
    identity_phase_status = PASS if identity_status == "verified" else FAIL if identity_status in {"failed", "needs_review", "unavailable"} else NOT_STARTED

    normal_icons_total = int(report.get("normal_icons_total") or 0)
    normal_icons_showcased = int(report.get("normal_icons_showcased") or 0)
    normal_remixes_total = int(report.get("normal_remixes_total") or 0)
    normal_remixes_played = int(report.get("normal_remixes_played") or 0)
    icon_ratio = _ratio(normal_icons_showcased, normal_icons_total)
    remix_ratio = _ratio(normal_remixes_played, normal_remixes_total)
    threshold_remix = 2 / 3 if str(mode).lower() in {"individual", "both"} else 0.50
    normal_gameplay_status = (
        PASS
        if normal_icons_total > 0
        and normal_remixes_total > 0
        and icon_ratio >= 0.80
        and remix_ratio >= threshold_remix
        else FAIL
        if workflow_report
        else NOT_STARTED
    )

    artifact_validation = report.get("artifact_validation") if isinstance(report.get("artifact_validation"), dict) else {}
    artifact_paths = artifact_validation.get("paths") or getattr(item, "output_files", []) or []
    artifact_status = (
        PASS
        if artifact_validation and artifact_validation.get("invalid") == [] and artifact_validation.get("valid")
        else FAIL
        if artifact_validation or artifact_paths
        else NOT_STARTED
    )

    phase_statuses = {
        "json_generation": _phase_from_item(getattr(item, "json_generation_status", None) if item is not None else None),
        "runtime_entry": runtime_status,
        "normal_gameplay": normal_gameplay_status,
        "horror": NOT_CHECKED_BY_POLICY,
        "placement": placement_status,
        "cleanup": cleanup_status,
        "identity": identity_phase_status,
        "artifacts": artifact_status,
    }
    if str(mode).lower() == "both":
        phase_statuses["master"] = _phase_status_from_report(phase_reports.get("master") or {})
        phase_statuses["individual"] = _phase_status_from_report(phase_reports.get("individual") or {})

    checks = [
        _check(
            "layout",
            PASS if icons and slots else FAIL,
            failure_code=None if icons and slots else ("no_icons" if not icons else "no_slots"),
            evidence={"icon_count": len(icons), "slot_count": len(slots)},
        ),
        _check(
            "runtime_verification",
            runtime_status,
            failure_code=failure_code if failure_code in {"runtime_verification_unknown", "no_playable_entry"} else None,
            evidence={
                "runtime_state": report.get("runtime_state"),
                "runtime_verified": report.get("runtime_verified"),
                "entry_candidate": report.get("entry_candidate"),
                "tested_candidates": report.get("tested_candidates", []),
            },
        ),
        _check(
            "placement",
            placement_status,
            failure_code="placement_not_verified" if placement_status != PASS else None,
            evidence={
                "before": placement_before,
                "after": placement_after,
                "delta": placement_after - placement_before
                if isinstance(placement_before, (int, float)) and isinstance(placement_after, (int, float))
                else None,
                "transactions": placement_transactions,
            },
        ),
        _check(
            "cleanup",
            cleanup_status,
            failure_code="placement_not_verified" if cleanup_status != PASS else None,
            evidence={"cleanup_verified": report.get("cleanup_verified")},
        ),
        _check(
            "normal_icons",
            PASS if normal_icons_total > 0 and icon_ratio >= 0.80 else FAIL if workflow_report else NOT_STARTED,
            failure_code="no_icons" if normal_icons_total <= 0 else "remix_threshold_not_met",
            evidence={
                "showcased": normal_icons_showcased,
                "total": normal_icons_total,
                "ratio": icon_ratio,
                "threshold": 0.80,
            },
        ),
        _check(
            "normal_remixes",
            PASS if normal_remixes_total > 0 and remix_ratio >= threshold_remix else FAIL if workflow_report else NOT_STARTED,
            failure_code="empty_remix" if normal_remixes_total <= 0 else "remix_threshold_not_met",
            evidence={
                "succeeded": normal_remixes_played,
                "total": normal_remixes_total,
                "ratio": remix_ratio,
                "threshold": threshold_remix,
            },
        ),
        _check(
            "horror",
            NOT_CHECKED_BY_POLICY,
            required=False,
            accepted_by_policy=True,
        ),
    ]
    if str(mode).lower() == "both":
        for phase_name in ("master", "individual"):
            phase_report = phase_reports.get(phase_name)
            checks.append(
                _check(
                    phase_name,
                    phase_statuses[phase_name],
                    failure_code=("phase_skipped" if phase_statuses[phase_name] != PASS else None),
                    evidence=phase_report if isinstance(phase_report, dict) else {},
                )
            )
    checks.extend(
        [
            _check(
                "recorded_identity",
                identity_phase_status,
                failure_code=(failure_code if failure_code in {"recorded_identity_missing", "recorded_identity_needs_review"} else None),
                evidence={
                    "status": identity_status,
                    "reason": getattr(item, "recorded_identity_reason", None) if item is not None else None,
                    "files": getattr(item, "recorded_identity_files", []) if item is not None else [],
                },
            ),
            _check(
                "artifacts",
                artifact_status,
                failure_code="artifact_missing" if artifact_status != PASS else None,
                evidence=artifact_validation,
            ),
        ]
    )
    failed_checks = [check["name"] for check in checks if check["required"] and not check["passed"]]

    context = _FAILURE_CONTEXT.get(
        failure_code,
        (
            "workflow",
            "Workflow failed before it could provide a successful quality report.",
            "Inspect the phase evidence and retry history.",
        ),
    )
    if gate_passed and final_status == "completed":
        headline = "Workflow completed with verified gameplay evidence."
        root_cause = ""
        next_action = ""
        severity = "info"
        confidence = "high"
    else:
        headline = context[1]
        root_cause = terminal_error or getattr(item, "error_message", None) or report.get("message") or context[1]
        next_action = context[2]
        severity = "error"
        confidence = "high" if failure_code else "medium"

    generated = generated_at or datetime.now()
    generated_iso = generated.isoformat() if isinstance(generated, datetime) else str(generated)
    if not report_id:
        report_id = f"job-{job.get('id', 'unknown')}-{generated.strftime('%Y%m%d%H%M%S%f')}"

    start_time = getattr(item, "start_time", None) if item is not None else None
    end_time = getattr(item, "end_time", None) if item is not None else None
    duration_ms = None
    if isinstance(start_time, datetime) and isinstance(end_time, datetime):
        duration_ms = max(0, int((end_time - start_time).total_seconds() * 1000))

    manifest_ref = _manifest_reference(manifest_data, manifest_path)
    report_evidence = report.get("evidence") if isinstance(report.get("evidence"), dict) else {}
    retry_attempts = _normalise_attempts(report_evidence.get("retryAttempts", []))
    if log_snapshot is None:
        log_snapshot = {
            "upload_status": "not_available",
            "full_log_ref": file_reference(None, kind="job_log"),
            "excerpt": "",
            "truncated": False,
            "line_count": 0,
            "redactions": [],
        }

    input_snapshot = json_safe(item_json)
    diagnostic = {
        "schema_version": SCHEMA_VERSION,
        "report_id": report_id,
        "generated_at": generated_iso,
        "status": final_status,
        "job": {
            "queue_id": job.get("id"),
            "tracker_job_id": job.get("tracker_job_id") or job.get("id"),
            "mod_id": job.get("mod_id"),
            "attempt": attempt,
            "max_attempts": max_attempts,
            "mode": mode,
            "producer": job.get("producer") or job.get("service_name"),
        },
        "mod": {
            "source_url": source_url,
            "domain": urlparse(source_url).netloc.replace("www.", "") if source_url else "",
            "project_slug": project_slug,
            "page_title": item_json.get("pageTitle"),
            "layout": {
                "icon_count": len(icons),
                "slot_count": len(slots),
                "recording_area": layout.get("recordingArea"),
                "icons": icons,
                "slots": slots,
            },
            "game_settings": item_json.get("gameSettings", {}),
            "coverage": report.get("coverage") or gate.get("coverage") or {
                "normal": "unknown",
                "horror": "not_checked_by_policy",
            },
            "input_snapshot": input_snapshot,
        },
        "summary": {
            "outcome": final_status,
            "headline": headline,
            "blocking_stage": context[0] if final_status != "completed" else None,
            "failure_code": failure_code,
            "retryable": retryable,
            "severity": severity,
            "root_cause": root_cause,
            "next_action": next_action,
            "confidence": confidence,
        },
        "timing": {
            "started_at": start_time.isoformat() if isinstance(start_time, datetime) else None,
            "ended_at": end_time.isoformat() if isinstance(end_time, datetime) else None,
            "duration_ms": duration_ms,
        },
        "phases": {
            "statuses": phase_statuses,
            "json_generation": {
                "status": phase_statuses["json_generation"],
                "failure_code": failure_code if phase_statuses["json_generation"] == FAIL else None,
            },
            "runtime_entry": {
                "status": runtime_status,
                "entry_candidate": report.get("entry_candidate"),
                "tested_candidates": report.get("tested_candidates", []),
                "runtime_state": report.get("runtime_state"),
                "runtime_verified": report.get("runtime_verified"),
                "evidence": report_evidence.get("entryStrategy", {})
                if isinstance(report_evidence, dict)
                else {},
            },
            "normal_gameplay": {
                "status": normal_gameplay_status,
                "icons": {
                    "showcased": normal_icons_showcased,
                    "total": normal_icons_total,
                    "ratio": icon_ratio,
                    "threshold": 0.80,
                },
                "remixes": {
                    "succeeded": normal_remixes_played,
                    "total": normal_remixes_total,
                    "ratio": remix_ratio,
                    "threshold": threshold_remix,
                },
            },
            "horror": {"status": NOT_CHECKED_BY_POLICY, "required": False},
            "placement": {
                "status": placement_status,
                "before": placement_before,
                "after": placement_after,
                "delta": placement_after - placement_before
                if isinstance(placement_before, (int, float)) and isinstance(placement_after, (int, float))
                else None,
                "verified": report.get("placement_verified"),
                "transactions": placement_transactions,
            },
            "cleanup": {
                "status": cleanup_status,
                "verified": report.get("cleanup_verified"),
            },
            "identity": {
                "status": identity_phase_status,
                "identity_status": identity_status,
                "reason": getattr(item, "recorded_identity_reason", None) if item is not None else None,
                "retryable": getattr(item, "recorded_identity_retryable", False) if item is not None else False,
                "files": getattr(item, "recorded_identity_files", []) if item is not None else [],
            },
            "artifacts": {
                "status": artifact_status,
                "validation": artifact_validation,
                "files": [file_reference(path, kind="video", hash_file=False) for path in artifact_paths],
            },
            "phase_reports": phase_reports,
        },
        "quality_gate": {
            "passed": gate_passed,
            "status": gate_status,
            "failure_code": failure_code,
            "retryable": retryable,
            "coverage": report.get("coverage") or gate.get("coverage") or {},
            "failed_checks": failed_checks,
            "checks": checks,
            "evidence": report_evidence,
        },
        "attempts": retry_attempts,
        "outputs": {
            "manifest_ref": manifest_ref,
            "manifest_snapshot": json_safe(manifest_data) if manifest_data else None,
            "video_files": json_safe(output_files_payload or {}),
        },
        "logs": json_safe(log_snapshot),
        "workflow_report": report,
        "tracker_ui": {
            "badge": "COMPLETED" if final_status == "completed" and gate_passed else "FAILED",
            "display_title": headline,
            "display_message": root_cause or headline,
            "action_required": bool(final_status != "completed"),
            "blocking_stage": context[0] if final_status != "completed" else None,
            "failed_checks": failed_checks,
        },
    }
    return json_safe(diagnostic)

"""
Creator API Client - Bridge between Creator and Tracker
Replaces tkinter GUI with API-based job polling
"""

import json
import re
import threading
import time
import uuid
import requests
from threading import Thread
import os
import sys
from typing import Dict, Optional, Any, Tuple
from datetime import datetime
from config_manager import get_config
from performance_analyzer import performance_analyzer
import logging

# --- FIX: Import necessary components ---
from sprunki_game_player import SprunkiGamePlayer
from sprunki_obs_controller import SprunkiController
# --- FIX: Import ProcessingConfig to create the configuration object ---
from sprunki_ultimate_master import ProcessingConfig

from collections.abc import Callable
from smart_retry_manager import SmartRetryManager
from file_validator import FileQualityValidator
from manual_profile_manager import suggested_profile_path_for_url, url_key_from_url
from workflow_quality import (
    CREATOR_PRODUCTION_MODES,
    evaluate_idle_timeout_partial_success,
    failure_is_retryable,
    normalize_production_mode,
    validate_playable_layout,
)
from tracker_reporting import (
    MAX_ERROR_LOG_BYTES,
    MAX_LOG_EXCERPT_BYTES,
    SCHEMA_VERSION as TRACKER_REPORT_SCHEMA_VERSION,
    build_log_snapshot,
    build_tracker_diagnostic_report,
    compact_log_snapshot,
    json_safe,
    truncate_utf8_text,
)
from watchdog_state import WatchdogState, load_watchdog_settings

# --- CONFIG: Thư mục chứa file JSON thủ công ---
MANUAL_JSON_DIR = "manual_jsons"
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
TERMINAL_MAX_REQUEST_BYTES = 1_800_000
TERMINAL_413_TARGET_BYTES = 1_200_000
CALLBACK_MAX_RETRIES = 3
CALLBACK_RETRY_DELAYS_SECONDS = (1.0, 2.0, 4.0)
_TRANSPORT_FORBIDDEN_KEYS = frozenset(
    {
        "fulllog",
        "rawlog",
        "content",
        "stdout",
        "stderr",
        "traceback",
        "workflowreport",
    }
)
_TRANSPORT_PATH_KEYS = frozenset(
    {
        "path",
        "absolutepath",
        "localpath",
        "filepath",
        "outputpath",
        "directory",
        "outputdirectory",
    }
)


class _TeeStream:
    """Write to the original stream and a job log file without owning either."""

    def __init__(self, original_stream, log_file):
        self.original_stream = original_stream
        self.log_file = log_file
        self.encoding = getattr(original_stream, "encoding", "utf-8")

    def write(self, data):
        if not data:
            return 0

        try:
            self.original_stream.write(data)
        except Exception:
            pass

        try:
            self.log_file.write(data)
        except Exception:
            pass

        return len(data)

    def flush(self):
        for stream in (self.original_stream, self.log_file):
            try:
                stream.flush()
            except Exception:
                pass

    def isatty(self):
        return bool(getattr(self.original_stream, "isatty", lambda: False)())

    def fileno(self):
        return self.original_stream.fileno()


class JobLogCapture:
    """Best-effort per-job log capture for Tracker tailing."""

    def __init__(self, queue_id: int, log_directory: str, enabled: bool = True):
        self.queue_id = queue_id
        self.log_directory = log_directory
        self.enabled = enabled
        self.log_path = os.path.join(log_directory, f"job-{queue_id}.log")
        self.log_file = None
        self.file_handler = None
        self.original_stdout = None
        self.original_stderr = None
        self.started = False

    def start(self) -> bool:
        if not self.enabled:
            return False

        try:
            os.makedirs(self.log_directory, exist_ok=True)
            self.log_file = open(self.log_path, "a", encoding="utf-8", buffering=1)
            self.log_file.write(
                f"\n===== Job {self.queue_id} log started at {datetime.now().isoformat(timespec='seconds')} =====\n"
            )

            formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
            self.file_handler = logging.FileHandler(self.log_path, mode="a", encoding="utf-8")
            self.file_handler.setLevel(logging.DEBUG)
            self.file_handler.setFormatter(formatter)
            logging.getLogger().addHandler(self.file_handler)

            self.original_stdout = sys.stdout
            self.original_stderr = sys.stderr
            sys.stdout = _TeeStream(self.original_stdout, self.log_file)
            sys.stderr = _TeeStream(self.original_stderr, self.log_file)
            self.started = True
            return True
        except Exception as exc:
            self._safe_restore_streams()
            self._safe_close_handler()
            self._safe_close_file()
            try:
                print(f"⚠️ Job log capture disabled for job {self.queue_id}: {exc}")
            except Exception:
                pass
            return False

    def stop(self):
        if not self.started:
            return

        try:
            if self.log_file:
                self.log_file.write(
                    f"\n===== Job {self.queue_id} log ended at {datetime.now().isoformat(timespec='seconds')} =====\n"
                )
                self.log_file.flush()
            if self.file_handler:
                self.file_handler.flush()
        except Exception:
            pass
        finally:
            self._safe_restore_streams()
            self._safe_close_handler()
            self._safe_close_file()
            self.started = False

    def snapshot(self) -> dict:
        """Return a bounded excerpt and a reference to the complete job log."""
        try:
            if self.log_file:
                self.log_file.flush()
        except Exception:
            pass
        return build_log_snapshot(self.log_path)

    def _safe_restore_streams(self):
        if self.original_stdout is not None:
            sys.stdout = self.original_stdout
        if self.original_stderr is not None:
            sys.stderr = self.original_stderr

    def _safe_close_handler(self):
        if self.file_handler:
            try:
                logging.getLogger().removeHandler(self.file_handler)
                self.file_handler.close()
            except Exception:
                pass
            self.file_handler = None

    def _safe_close_file(self):
        if self.log_file:
            try:
                self.log_file.flush()
                self.log_file.close()
            except Exception:
                pass
            self.log_file = None


class TrackerAPIClient:
    def __init__(self, tracker_url="http://localhost:5000"):
        self.tracker_url = tracker_url
        self.config = get_config()
        self.service_name = f"creator-{self.config.get('application', {}).get('instance_id', '1')}"
        self.session = requests.Session()
        self.timeout = 30
        self._active_report_ids = {}
        self._active_attempts = {}
        self._progress_sequences = {}
        self._callback_state_lock = threading.Lock()
        
        self.capabilities = {
            'master': True,
            # Backward-compatible Tracker alias.  The queue payload remains
            # authoritative; Creator never rewrites every job to normal.
            'normal': True,
            'individual': True, 
            'both': True,
            'max_concurrent': 1,
            'version': self.config.get('application', {}).get('version', '1.0')
        }
        
        self.host_info = {
            'ip': '127.0.0.1',
            'port': 8001,
            'version': self.capabilities['version'],
            'config_loaded': True
        }
        
        print(f"🔗 Tracker API Client initialized")
        print(f"   Service: {self.service_name}")
        print(f"   Tracker: {self.tracker_url}")

    def _ensure_attempt(
        self,
        queue_id: int,
        report_id: Optional[str] = None,
        attempt_nonce: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return the immutable callback identity for one queue attempt."""
        if not hasattr(self, "_active_attempts"):
            self._active_attempts = {}
        if not hasattr(self, "_active_report_ids"):
            self._active_report_ids = {}
        if not hasattr(self, "_progress_sequences"):
            self._progress_sequences = {}

        existing = self._active_attempts.get(queue_id)
        if existing is not None:
            if report_id and report_id != existing["report_id"]:
                raise ValueError(f"report_id changed within queue attempt {queue_id}")
            if attempt_nonce and attempt_nonce != existing["attempt_nonce"]:
                raise ValueError(f"attempt_nonce changed within queue attempt {queue_id}")
            return existing

        state = {
            "report_id": report_id or str(uuid.uuid4()),
            "attempt_nonce": attempt_nonce or str(uuid.uuid4()),
            "next_sequence": 0,
        }
        self._active_attempts[queue_id] = state
        self._active_report_ids[queue_id] = state["report_id"]
        self._progress_sequences[queue_id] = 0
        return state

    def bind_job_report(
        self,
        queue_id: int,
        report_id: Optional[str] = None,
        attempt_nonce: Optional[str] = None,
    ) -> Dict[str, str]:
        """Bind immutable diagnostic identity and reset the shared sequence."""
        state = self._ensure_attempt(queue_id, report_id, attempt_nonce)
        return {
            "report_id": state["report_id"],
            "attempt_nonce": state["attempt_nonce"],
        }

    def unbind_job_report(self, queue_id: int) -> None:
        if hasattr(self, "_active_attempts"):
            self._active_attempts.pop(queue_id, None)
        if hasattr(self, "_active_report_ids"):
            self._active_report_ids.pop(queue_id, None)
        if hasattr(self, "_progress_sequences"):
            self._progress_sequences.pop(queue_id, None)

    def _next_progress_sequence(self, queue_id: int) -> int:
        state = self._ensure_attempt(queue_id)
        lock = getattr(self, "_callback_state_lock", None)
        if lock is None:
            lock = threading.Lock()
            self._callback_state_lock = lock
        with lock:
            sequence = state["next_sequence"]
            state["next_sequence"] = sequence + 1
            self._progress_sequences[queue_id] = state["next_sequence"]
        return sequence

    def _reserve_callback_sequence(self, queue_id: int, sequence: Optional[int]) -> int:
        """Reserve one monotonically increasing sequence shared by callbacks."""
        state = self._ensure_attempt(queue_id)
        lock = getattr(self, "_callback_state_lock", None)
        if lock is None:
            lock = threading.Lock()
            self._callback_state_lock = lock
        with lock:
            expected = state["next_sequence"]
            effective = expected if sequence is None else int(sequence)
            if effective < expected:
                raise ValueError(
                    f"sequence {effective} is stale for queue attempt {queue_id}; expected >= {expected}"
                )
            state["next_sequence"] = effective + 1
            self._progress_sequences[queue_id] = state["next_sequence"]
            return effective

    @staticmethod
    def _diagnostic_for_attempt(
        diagnostic_report: Optional[Dict],
        report_id: str,
        attempt_nonce: str,
    ) -> Optional[Dict]:
        if not isinstance(diagnostic_report, dict):
            return diagnostic_report
        normalized = dict(diagnostic_report)
        normalized["schema_version"] = TRACKER_REPORT_SCHEMA_VERSION
        normalized["report_id"] = report_id
        normalized["attempt_nonce"] = attempt_nonce
        return normalized

    @staticmethod
    def _log_callback_failure(callback_name: str, response) -> None:
        error_code, message = TrackerAPIClient._response_error(response)
        print(
            f"⚠️ {callback_name} callback failed: HTTP {getattr(response, 'status_code', 'unknown')}"
            f" error_code={error_code} message={message}"
        )

    @staticmethod
    def _payload_size_bytes(payload: Dict[str, Any]) -> int:
        """Return the UTF-8 JSON size used for callback size diagnostics."""

        encoded = json.dumps(
            payload,
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
        return len(encoded)

    @staticmethod
    def _normalise_transport_key(key: Any) -> str:
        return re.sub(r"[^a-z0-9]", "", str(key).lower())

    @classmethod
    def _strip_forbidden_transport_fields(cls, value: Any) -> Any:
        """Remove fields that can accidentally carry an unbounded log."""

        if isinstance(value, dict):
            cleaned = {}
            for key, entry in value.items():
                if cls._normalise_transport_key(key) in _TRANSPORT_FORBIDDEN_KEYS:
                    continue
                cleaned[str(key)] = cls._strip_forbidden_transport_fields(entry)
            return cleaned
        if isinstance(value, list):
            return [cls._strip_forbidden_transport_fields(entry) for entry in value]
        return value

    @classmethod
    def _strip_absolute_transport_paths(cls, value: Any) -> Any:
        """Remove local absolute paths from callback JSON."""

        if isinstance(value, dict):
            cleaned = {}
            for key, entry in value.items():
                normalized_key = cls._normalise_transport_key(key)
                if normalized_key in _TRANSPORT_PATH_KEYS and isinstance(entry, str):
                    is_windows_absolute = bool(re.match(r"^[A-Za-z]:[\\/]", entry))
                    is_unc = entry.startswith(("\\\\", "//"))
                    if os.path.isabs(entry) or is_windows_absolute or is_unc:
                        entry = os.path.basename(entry)
                cleaned[str(key)] = cls._strip_absolute_transport_paths(entry)
            return cleaned
        if isinstance(value, list):
            return [cls._strip_absolute_transport_paths(entry) for entry in value]
        return value

    @classmethod
    def _bound_transport_value(
        cls,
        value: Any,
        *,
        aggressive: bool = False,
        depth: int = 0,
    ) -> Any:
        """Bound arbitrary nested evidence before it reaches the wire.

        The diagnostic builder deliberately keeps useful structured evidence,
        but workflow evidence is extensible and can contain large arrays such
        as placement transactions or phase snapshots.  Removing only fields
        named ``full_log``/``stdout`` is therefore not a sufficient size
        guarantee.  This helper keeps the shape and the first/last entries of
        collections while bounding strings, dictionaries, and lists.
        """

        string_limit = 2 * 1024 if aggressive else 8 * 1024
        list_limit = 24 if aggressive else 64
        dict_limit = 64 if aggressive else 128

        if isinstance(value, str):
            return truncate_utf8_text(value, string_limit)
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if depth >= 8:
            if isinstance(value, dict):
                return {"_truncated": True, "field_count": len(value)}
            if isinstance(value, (list, tuple, set)):
                return {"_truncated": True, "item_count": len(value)}
            return str(value)
        if isinstance(value, dict):
            items = list(value.items())
            if len(items) > dict_limit:
                priority = {
                    "schema_version", "report_id", "attempt_nonce", "status",
                    "name", "required", "passed", "failure_code", "retryable",
                    "before", "after", "delta", "verified", "reason", "message",
                    "source_url", "project_slug", "file_name", "filename",
                    "relative_path", "size_bytes", "sha256",
                }
                prioritized = [
                    item for item in items
                    if cls._normalise_transport_key(item[0]) in {
                        cls._normalise_transport_key(key) for key in priority
                    }
                ]
                remainder = [item for item in items if item not in prioritized]
                keep_count = max(1, dict_limit - 1)
                items = (prioritized + remainder)[:keep_count]
                items.append(("_truncated_fields", len(value) - keep_count))
            return {
                str(key): cls._bound_transport_value(
                    entry, aggressive=aggressive, depth=depth + 1
                )
                for key, entry in items
            }
        if isinstance(value, (list, tuple, set)):
            entries = list(value)
            if len(entries) > list_limit:
                head_count = max(1, list_limit // 2)
                tail_count = list_limit - head_count
                entries = entries[:head_count] + entries[-tail_count:]
            return [
                cls._bound_transport_value(
                    entry, aggressive=aggressive, depth=depth + 1
                )
                for entry in entries
            ]
        return truncate_utf8_text(str(value), string_limit)

    @staticmethod
    def _compact_reference(reference: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Keep file identity metadata without transmitting a local path."""

        if not isinstance(reference, dict):
            return reference
        safe = json_safe(reference)
        filename = safe.get("file_name") or safe.get("filename")
        relative_path = safe.get("relative_path")
        if not relative_path and safe.get("path"):
            relative_path = os.path.basename(str(safe.get("path")))
        return {
            "kind": safe.get("kind"),
            "file_name": filename,
            "filename": filename,
            "relative_path": relative_path,
            "exists": bool(safe.get("exists", False)),
            "readable": bool(safe.get("readable", False)),
            "size_bytes": int(safe.get("size_bytes") or 0),
            "sha256": safe.get("sha256"),
            "access_mode": safe.get("access_mode") or "local_reference_only",
            "download_url": safe.get("download_url"),
        }

    @classmethod
    def _compact_diagnostic_report(
        cls,
        diagnostic_report: Optional[Dict[str, Any]],
        *,
        aggressive: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Build the bounded terminal diagnostic without dropping core sections."""

        if not isinstance(diagnostic_report, dict):
            return diagnostic_report

        compact = cls._strip_forbidden_transport_fields(json_safe(diagnostic_report))
        if not isinstance(compact, dict):
            compact = {}

        # These are duplicate, potentially very large snapshots.  The
        # required structured evidence remains in mod/phases/quality_gate.
        mod = compact.get("mod")
        if isinstance(mod, dict):
            mod.pop("input_snapshot", None)
        outputs = compact.get("outputs")
        if isinstance(outputs, dict):
            outputs.pop("manifest_snapshot", None)
        logs = compact_log_snapshot(
            compact.get("logs") if isinstance(compact.get("logs"), dict) else {},
            excerpt_bytes=48 * 1024 if aggressive else MAX_LOG_EXCERPT_BYTES,
        )
        # Bound every other nested evidence branch as well.  The log excerpt
        # is handled separately so it can retain the contract's 48--64 KiB
        # allowance instead of being reduced to the generic string limit.
        compact.pop("logs", None)
        compact = cls._bound_transport_value(compact, aggressive=aggressive)
        compact["logs"] = logs

        for required_section in (
            "mod",
            "summary",
            "timing",
            "phases",
            "quality_gate",
            "outputs",
            "logs",
        ):
            if not isinstance(compact.get(required_section), dict):
                compact[required_section] = {}
        return compact

    @classmethod
    def _compact_terminal_payload(
        cls,
        payload: Dict[str, Any],
        *,
        aggressive: bool = False,
    ) -> Dict[str, Any]:
        """Compact one terminal/status payload while preserving its identity."""

        compact = cls._strip_forbidden_transport_fields(json_safe(payload))
        if not isinstance(compact, dict):
            compact = {}

        diagnostic = cls._compact_diagnostic_report(
            compact.get("diagnostic_report"),
            aggressive=aggressive,
        )
        if isinstance(diagnostic, dict):
            # The outer callback identity is authoritative and must match the
            # copy inside diagnostic_report on every retry/replay.
            diagnostic["schema_version"] = TRACKER_REPORT_SCHEMA_VERSION
            diagnostic["report_id"] = compact.get("report_id")
            diagnostic["attempt_nonce"] = compact.get("attempt_nonce")
            diagnostic["status"] = compact.get("status")
            compact["diagnostic_report"] = diagnostic

        if "error_log" in compact:
            compact["error_log"] = cls._redact_callback_message(
                truncate_utf8_text(compact.get("error_log"), MAX_ERROR_LOG_BYTES)
            )
        if isinstance(compact.get("details"), (dict, list)):
            compact["details"] = cls._bound_transport_value(
                cls._strip_forbidden_transport_fields(compact["details"]),
                aggressive=aggressive,
            )
        if isinstance(compact.get("video_files"), (dict, list)):
            compact["video_files"] = cls._bound_transport_value(
                cls._strip_forbidden_transport_fields(compact["video_files"]),
                aggressive=aggressive,
            )
        if isinstance(compact.get("manifest_ref"), dict):
            compact["manifest_ref"] = cls._compact_reference(compact["manifest_ref"])

        # A top-level log_ref is optional transport convenience.  Keep only
        # metadata there; the bounded excerpt lives in diagnostic_report.logs.
        if isinstance(compact.get("log_ref"), dict):
            top_log_ref = compact_log_snapshot(compact["log_ref"], excerpt_bytes=0)
            top_log_ref.pop("excerpt", None)
            compact["log_ref"] = top_log_ref

        # Never send the full legacy workflow report in a terminal callback.
        compact.pop("workflow_report", None)
        return cls._strip_absolute_transport_paths(compact)

    @classmethod
    def _compact_evidence_summary(cls, evidence: Any) -> Dict[str, Any]:
        """Keep scalar evidence and bounded samples of large collections."""

        if not isinstance(evidence, dict):
            return {}
        compact: Dict[str, Any] = {}
        for key, value in evidence.items():
            normalized_key = cls._normalise_transport_key(key)
            if normalized_key in _TRANSPORT_FORBIDDEN_KEYS:
                continue
            if isinstance(value, (list, tuple, set)):
                entries = list(value)
                if len(entries) > 8:
                    compact[f"{key}Count"] = len(entries)
                    entries = entries[:4] + entries[-4:]
                compact[str(key)] = cls._bound_transport_value(
                    entries, aggressive=True
                )
            else:
                compact[str(key)] = cls._bound_transport_value(
                    value, aggressive=True
                )
        return compact

    @classmethod
    def _minimal_diagnostic_report(
        cls,
        diagnostic_report: Optional[Dict[str, Any]],
        *,
        log_excerpt_bytes: int = 48 * 1024,
    ) -> Dict[str, Any]:
        """Build a small, contract-complete diagnostic as a last resort.

        This path is used only when the normal bounded report is still above
        the Tracker limit.  It keeps the required sections and the scalar
        quality/phase evidence, while removing duplicate snapshots and large
        transaction/detail arrays.
        """

        source = diagnostic_report if isinstance(diagnostic_report, dict) else {}
        mod_source = source.get("mod") if isinstance(source.get("mod"), dict) else {}
        layout_source = mod_source.get("layout") if isinstance(mod_source.get("layout"), dict) else {}
        mod = {
            key: cls._bound_transport_value(mod_source.get(key), aggressive=True)
            for key in (
                "source_url",
                "domain",
                "project_slug",
                "page_title",
                "coverage",
                "game_settings",
            )
            if key in mod_source
        }
        mod["layout"] = {
            key: cls._bound_transport_value(layout_source.get(key), aggressive=True)
            for key in ("icon_count", "slot_count", "recording_area")
            if key in layout_source
        }

        phases_source = source.get("phases") if isinstance(source.get("phases"), dict) else {}
        phases: Dict[str, Any] = {}
        for key in (
            "statuses",
            "json_generation",
            "runtime_entry",
            "normal_gameplay",
            "horror",
            "placement",
            "cleanup",
            "identity",
            "attempt_output_references",
        ):
            if key not in phases_source:
                continue
            value = phases_source.get(key)
            if key in {"placement", "cleanup"} and isinstance(value, dict):
                value = {
                    field: value.get(field)
                    for field in (
                        "status",
                        "before",
                        "after",
                        "delta",
                        "verified",
                        "accepted_by_policy",
                        "final_cleanup_optional",
                    )
                    if field in value
                }
            phases[key] = cls._bound_transport_value(value, aggressive=True)

        phase_reports_source = phases_source.get("phase_reports")
        if isinstance(phase_reports_source, dict):
            phase_reports: Dict[str, Any] = {}
            for phase_name, phase in phase_reports_source.items():
                if not isinstance(phase, dict):
                    continue
                phase_reports[str(phase_name)] = {
                    field: cls._bound_transport_value(phase.get(field), aggressive=True)
                    for field in (
                        "mode",
                        "was_successful",
                        "failure_code",
                        "retryable",
                        "message",
                        "normal_icons_showcased",
                        "normal_icons_total",
                        "normal_remixes_played",
                        "normal_remixes_total",
                    )
                    if field in phase
                }
            phases["phase_reports"] = phase_reports

        gate_source = source.get("quality_gate") if isinstance(source.get("quality_gate"), dict) else {}
        gate: Dict[str, Any] = {
            key: cls._bound_transport_value(gate_source.get(key), aggressive=True)
            for key in (
                "passed",
                "status",
                "failure_code",
                "retryable",
                "coverage",
                "failed_checks",
            )
            if key in gate_source
        }
        checks = gate_source.get("checks")
        if isinstance(checks, list):
            compact_checks = []
            for check in checks[:64]:
                if not isinstance(check, dict):
                    continue
                compact_checks.append(
                    {
                        key: cls._bound_transport_value(check.get(key), aggressive=True)
                        for key in (
                            "name",
                            "status",
                            "required",
                            "passed",
                            "accepted_by_policy",
                            "failure_code",
                        )
                        if key in check
                    }
                    | {
                        "evidence": cls._compact_evidence_summary(
                            check.get("evidence")
                        )
                    }
                )
            gate["checks"] = compact_checks
        gate["evidence"] = cls._compact_evidence_summary(gate_source.get("evidence"))

        outputs_source = source.get("outputs") if isinstance(source.get("outputs"), dict) else {}
        outputs: Dict[str, Any] = {}
        if isinstance(outputs_source.get("manifest_ref"), dict):
            outputs["manifest_ref"] = cls._compact_reference(outputs_source["manifest_ref"])
        if "video_files" in outputs_source:
            outputs["video_files"] = cls._bound_transport_value(
                outputs_source.get("video_files"), aggressive=True
            )
        if "attempt_output_references" in outputs_source:
            outputs["attempt_output_references"] = cls._bound_transport_value(
                outputs_source.get("attempt_output_references"), aggressive=True
            )

        logs = compact_log_snapshot(
            source.get("logs") if isinstance(source.get("logs"), dict) else {},
            excerpt_bytes=max(0, int(log_excerpt_bytes)),
        )
        result = {
            "schema_version": source.get("schema_version", TRACKER_REPORT_SCHEMA_VERSION),
            "report_id": source.get("report_id"),
            "attempt_nonce": source.get("attempt_nonce"),
            "generated_at": cls._bound_transport_value(source.get("generated_at"), aggressive=True),
            "status": source.get("status"),
            "job": cls._bound_transport_value(source.get("job") or {}, aggressive=True),
            "mod": mod,
            "summary": cls._bound_transport_value(source.get("summary") or {}, aggressive=True),
            "timing": cls._bound_transport_value(source.get("timing") or {}, aggressive=True),
            "phases": phases,
            "quality_gate": gate,
            "attempts": cls._bound_transport_value(source.get("attempts") or [], aggressive=True),
            "outputs": outputs,
            "logs": logs,
            "tracker_ui": cls._bound_transport_value(source.get("tracker_ui") or {}, aggressive=True),
        }
        for required_section in (
            "mod",
            "summary",
            "timing",
            "phases",
            "quality_gate",
            "outputs",
            "logs",
        ):
            if not isinstance(result.get(required_section), dict):
                result[required_section] = {}
        return cls._strip_absolute_transport_paths(result)

    @classmethod
    def _fit_terminal_payload_to_limit(
        cls,
        payload: Dict[str, Any],
        *,
        max_bytes: int = TERMINAL_MAX_REQUEST_BYTES,
    ) -> Dict[str, Any]:
        """Return a terminal payload that is safely below Tracker's limit."""

        compact = cls._compact_terminal_payload(payload, aggressive=True)
        if cls._payload_size_bytes(compact) <= max_bytes:
            return compact

        # These fields are useful during ordinary progress reporting but are
        # optional for a terminal contract.  Remove them before reducing the
        # required diagnostic sections.
        for key in (
            "details",
            "video_files",
            "error_log",
            "manifest_ref",
            "log_ref",
            "stage",
            "worker_id",
        ):
            compact.pop(key, None)
            if cls._payload_size_bytes(compact) <= max_bytes:
                return compact

        diagnostic = compact.get("diagnostic_report")
        compact["diagnostic_report"] = cls._minimal_diagnostic_report(
            diagnostic,
            log_excerpt_bytes=min(48 * 1024, max(8 * 1024, max_bytes // 16)),
        )
        if cls._payload_size_bytes(compact) <= max_bytes:
            return compact

        # Last bounded fallback: retain only the fields Tracker uses to bind
        # and validate a terminal event.  The full log remains on disk and is
        # referenced by the compact logs section.
        minimal_diagnostic = compact["diagnostic_report"]
        minimal_attempt_refs = (
            minimal_diagnostic.get("outputs", {}).get("attempt_output_references", {})
            if isinstance(minimal_diagnostic.get("outputs"), dict)
            else {}
        )
        minimal_diagnostic["outputs"] = {
            "video_files": {},
            "attempt_output_references": minimal_attempt_refs,
        }
        minimal_diagnostic["attempts"] = []
        minimal_diagnostic["logs"] = compact_log_snapshot(
            minimal_diagnostic.get("logs"), excerpt_bytes=8 * 1024
        )
        minimal_diagnostic["logs"]["excerpt"] = truncate_utf8_text(
            minimal_diagnostic["logs"].get("excerpt"), 8 * 1024
        )
        return {
            "status": compact.get("status"),
            "schema_version": TRACKER_REPORT_SCHEMA_VERSION,
            "report_id": compact.get("report_id"),
            "attempt_nonce": compact.get("attempt_nonce"),
            "sequence": compact.get("sequence"),
            "final": True,
            "diagnostic_report": minimal_diagnostic,
        }

    def _tracker_outbox_directory(self) -> str:
        configured = getattr(self, "tracker_outbox_directory", None)
        if not configured:
            config = getattr(self, "config", None)
            getter = getattr(config, "get", None)
            if callable(getter):
                try:
                    configured = getter(
                        "logging.tracker_outbox_directory",
                        os.path.join("logs", "video_jobs", "tracker_outbox"),
                    )
                except Exception:
                    configured = None
        configured = configured or os.path.join(
            PROJECT_DIR, "logs", "video_jobs", "tracker_outbox"
        )
        if not os.path.isabs(str(configured)):
            configured = os.path.join(PROJECT_DIR, str(configured))
        return os.path.abspath(str(configured))

    def _tracker_outbox_path(self, queue_id: int) -> str:
        return os.path.join(
            self._tracker_outbox_directory(), f"job-{int(queue_id)}-terminal.json"
        )

    def _tracker_terminal_archive_path(self, queue_id: int) -> str:
        """Return the durable compact report path used for later replay."""

        archive_directory = os.path.join(self._tracker_outbox_directory(), "archive")
        return os.path.join(
            archive_directory, f"job-{int(queue_id)}-terminal.json"
        )

    def _save_terminal_outbox(self, queue_id: int, payload: Dict[str, Any]) -> Optional[str]:
        """Persist only the compact resendable payload, never the full log."""

        path = self._tracker_outbox_path(queue_id)
        temporary_path = f"{path}.tmp"
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(temporary_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, path)
            return path
        except Exception as exc:
            try:
                if os.path.exists(temporary_path):
                    os.remove(temporary_path)
            except OSError:
                pass
            print(f"[Tracker] Could not persist terminal outbox for queue {queue_id}: {exc}")
            return None

    def _save_terminal_archive(self, queue_id: int, payload: Dict[str, Any]) -> Optional[str]:
        """Keep a compact terminal report even after a successful callback."""

        path = self._tracker_terminal_archive_path(queue_id)
        temporary_path = f"{path}.tmp"
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(temporary_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, path)
            return path
        except Exception as exc:
            try:
                if os.path.exists(temporary_path):
                    os.remove(temporary_path)
            except OSError:
                pass
            print(f"[Tracker] Could not archive terminal report for queue {queue_id}: {exc}")
            return None

    def _remove_terminal_outbox(self, queue_id: int) -> None:
        try:
            path = self._tracker_outbox_path(queue_id)
            if os.path.exists(path):
                os.remove(path)
        except OSError as exc:
            print(f"[Tracker] Could not remove terminal outbox for queue {queue_id}: {exc}")

    def _load_terminal_outbox(self, queue_id: int) -> Optional[Dict[str, Any]]:
        path = self._tracker_outbox_path(queue_id)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            return payload if isinstance(payload, dict) else None
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            print(f"[Tracker] Could not load terminal outbox for queue {queue_id}: {exc}")
            return None

    def _load_terminal_replay(self, queue_id: int) -> Optional[Dict[str, Any]]:
        """Load pending outbox first, then a retained compact terminal report."""

        pending = self._load_terminal_outbox(queue_id)
        if pending is not None:
            return pending
        path = self._tracker_terminal_archive_path(queue_id)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            return payload if isinstance(payload, dict) else None
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            print(f"[Tracker] Could not load archived terminal report for queue {queue_id}: {exc}")
            return None

    @staticmethod
    def _response_error(response) -> Tuple[str, str]:
        status_code = getattr(response, "status_code", "unknown")
        body = getattr(response, "text", "") or ""
        data = None
        try:
            data = response.json()
        except Exception:
            try:
                data = json.loads(body)
            except Exception:
                data = None
        if isinstance(data, dict):
            error_code = data.get("error_code") or data.get("code") or f"http_{status_code}"
            message = data.get("message") or data.get("error") or ""
        else:
            error_code = f"http_{status_code}"
            message = body
        return str(error_code), TrackerAPIClient._redact_callback_message(message)

    @staticmethod
    def _redact_callback_message(message: Any) -> str:
        safe_message = re.sub(
            r"(?i)(token|secret|password|authorization|api[_-]?key)(\s*[:=]\s*)[^\s,;]+",
            r"\1\2[REDACTED]",
            str(message),
        )
        return truncate_utf8_text(safe_message, 512)

    @staticmethod
    def _retryable_http_status(status_code: Any) -> bool:
        try:
            value = int(status_code)
        except (TypeError, ValueError):
            return False
        return value in {408, 413, 425, 429} or 500 <= value <= 599

    def _record_callback_metrics(
        self,
        *,
        queue_id: int,
        endpoint: str,
        status_code: Any,
        request_bytes: int,
        retry_count: int,
        error_code: Optional[str],
        original_request_bytes: Optional[int] = None,
    ) -> None:
        self.last_callback_metrics = {
            "queue_id": queue_id,
            "endpoint": endpoint,
            "http_status": status_code,
            "request_bytes": int(request_bytes),
            "retry_count": int(retry_count),
            "error_code": error_code,
            "original_request_bytes": original_request_bytes,
        }

    def _send_callback_with_retry(
        self,
        *,
        queue_id: int,
        method: str,
        endpoint: str,
        payload: Dict[str, Any],
        final: bool,
        original_request_bytes: Optional[int] = None,
    ) -> bool:
        """Send a callback with bounded retries and durable terminal replay."""

        current_payload = (
            self._fit_terminal_payload_to_limit(payload)
            if final
            else self._compact_terminal_payload(payload, aggressive=False)
        )
        retry_count = 0
        compacted_after_413 = False
        outbox_saved = False
        sleep_fn = getattr(self, "_sleep", time.sleep)
        max_retries = int(getattr(self, "callback_max_retries", CALLBACK_MAX_RETRIES))
        retry_delays = tuple(
            getattr(self, "callback_retry_delays", CALLBACK_RETRY_DELAYS_SECONDS)
        )
        if final:
            self._save_terminal_archive(queue_id, current_payload)
            outbox_saved = self._save_terminal_outbox(queue_id, current_payload) is not None

        while True:
            request_bytes = self._payload_size_bytes(current_payload)
            url = f"{self.tracker_url}{endpoint}"
            print(
                f"[Tracker] Sending callback: queue_id={queue_id} endpoint={endpoint} "
                f"request_bytes={request_bytes} retry_count={retry_count}"
            )
            try:
                if method.upper() == "PUT":
                    response = self.session.put(
                        url, json=current_payload, timeout=self.timeout
                    )
                else:
                    response = self.session.post(
                        url, json=current_payload, timeout=self.timeout
                    )
            except Exception as exc:
                error_code = "network_error"
                message = self._redact_callback_message(str(exc))
                self._record_callback_metrics(
                    queue_id=queue_id,
                    endpoint=endpoint,
                    status_code="network_error",
                    request_bytes=request_bytes,
                    retry_count=retry_count,
                    error_code=error_code,
                    original_request_bytes=original_request_bytes,
                )
                print(
                    f"[Tracker] Callback failed: queue_id={queue_id} endpoint={endpoint} "
                    f"HTTP network_error request_bytes={request_bytes} "
                    f"retry_count={retry_count} error_code={error_code} message={message}"
                )
                if retry_count < max_retries:
                    delay = retry_delays[min(retry_count, len(retry_delays) - 1)]
                    sleep_fn(delay)
                    retry_count += 1
                    continue
                if final and not outbox_saved:
                    self._save_terminal_outbox(queue_id, current_payload)
                return False

            status_code = getattr(response, "status_code", None)
            if isinstance(status_code, int) and 200 <= status_code < 300:
                self._record_callback_metrics(
                    queue_id=queue_id,
                    endpoint=endpoint,
                    status_code=status_code,
                    request_bytes=request_bytes,
                    retry_count=retry_count,
                    error_code=None,
                    original_request_bytes=original_request_bytes,
                )
                print(
                    f"[Tracker] Callback accepted: queue_id={queue_id} endpoint={endpoint} "
                    f"HTTP {status_code} request_bytes={request_bytes} retry_count={retry_count}"
                )
                if final:
                    self._remove_terminal_outbox(queue_id)
                return True

            error_code, message = self._response_error(response)
            try:
                numeric_status = int(status_code)
            except (TypeError, ValueError):
                numeric_status = 0
            self._record_callback_metrics(
                queue_id=queue_id,
                endpoint=endpoint,
                status_code=status_code,
                request_bytes=request_bytes,
                retry_count=retry_count,
                error_code=error_code,
                original_request_bytes=original_request_bytes,
            )
            print(
                f"[Tracker] Callback failed: queue_id={queue_id} endpoint={endpoint} "
                f"HTTP {status_code} request_bytes={request_bytes} "
                f"retry_count={retry_count} error_code={error_code} message={message}"
            )

            if (
                final
                and numeric_status == 413
                and not compacted_after_413
            ):
                before_compact_bytes = request_bytes
                current_payload = self._fit_terminal_payload_to_limit(
                    current_payload,
                    max_bytes=min(
                        TERMINAL_MAX_REQUEST_BYTES,
                        TERMINAL_413_TARGET_BYTES,
                    ),
                )
                compacted_after_413 = True
                compacted_bytes = self._payload_size_bytes(current_payload)
                print(
                    f"[Tracker] HTTP 413 compacted terminal payload: "
                    f"queue_id={queue_id} before_bytes={before_compact_bytes} "
                    f"after_bytes={compacted_bytes}"
                )
                # Replace the pending resend artifact with the payload that
                # will actually be retried; it must never retain a larger
                # pre-413 representation.
                self._save_terminal_archive(queue_id, current_payload)
                outbox_saved = self._save_terminal_outbox(
                    queue_id, current_payload
                ) is not None

            if self._retryable_http_status(status_code) and retry_count < max_retries:
                delay = retry_delays[min(retry_count, len(retry_delays) - 1)]
                sleep_fn(delay)
                retry_count += 1
                continue

            if final and not outbox_saved:
                self._save_terminal_outbox(queue_id, current_payload)
            return False

    def resend_terminal_callback(
        self,
        queue_id: int,
        payload: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Replay a compact terminal callback from the durable outbox.

        This intentionally sends the stored sequence/identity verbatim.  It
        does not reserve a new sequence, so a retry after a lost HTTP response
        remains idempotent for Tracker.  The default target is job 3118 when
        the caller invokes ``resend_terminal_callback(3118)``.
        """

        replay = payload or self._load_terminal_replay(queue_id)
        if not isinstance(replay, dict):
            print(f"[Tracker] No terminal callback outbox found for queue {queue_id}")
            return False
        if replay.get("final") is not True:
            print(f"[Tracker] Terminal outbox for queue {queue_id} is not marked final")
            return False
        required = ("status", "schema_version", "report_id", "attempt_nonce", "sequence", "diagnostic_report")
        if any(key not in replay for key in required):
            print(f"[Tracker] Terminal outbox for queue {queue_id} is missing required fields")
            return False
        compact = self._compact_terminal_payload(replay, aggressive=True)
        compact["final"] = True
        # Keep the exact stored sequence and immutable callback identity.
        compact["sequence"] = replay["sequence"]
        compact["report_id"] = replay["report_id"]
        compact["attempt_nonce"] = replay["attempt_nonce"]
        compact["diagnostic_report"]["report_id"] = replay["report_id"]
        compact["diagnostic_report"]["attempt_nonce"] = replay["attempt_nonce"]
        self._ensure_attempt(
            queue_id,
            report_id=str(replay["report_id"]),
            attempt_nonce=str(replay["attempt_nonce"]),
        )
        try:
            sequence = int(replay["sequence"])
            state = self._active_attempts[queue_id]
            state["next_sequence"] = max(int(state.get("next_sequence", 0)), sequence + 1)
        except (KeyError, TypeError, ValueError):
            pass
        return self._send_callback_with_retry(
            queue_id=queue_id,
            method="PUT",
            endpoint=f"/api/queue/status/{queue_id}",
            payload=compact,
            final=True,
            original_request_bytes=self._payload_size_bytes(replay),
        )
    
    def register_service(self) -> bool:
        """Register this Creator instance with Tracker"""
        try:
            response = self.session.post(
                f"{self.tracker_url}/api/creators/register", 
                json={
                    'service_name': self.service_name,
                    'host_info': self.host_info,
                    'capabilities': self.capabilities
                },
                timeout=self.timeout
            )
            
            if response.status_code == 200:
                print(f"✅ Registered with Tracker: {self.service_name}")
                return True
            else:
                print(f"❌ Registration failed: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            print(f"❌ Registration error: {e}")
            return False
    
    def send_heartbeat(self, status="online", current_job_id=None, performance_stats=None) -> bool:
        """Send heartbeat to Tracker"""
        try:
            payload = {
                'service_name': self.service_name,
                'status': status,
                'current_job_id': current_job_id,
                'performance_stats': performance_stats
            }
            
            response = self.session.post(
                f"{self.tracker_url}/api/creators/heartbeat", 
                json=payload,
                timeout=self.timeout
            )
            return response.status_code == 200
            
        except Exception as e:
            print(f"⚠️ Heartbeat error: {e}")
            return False
    
    def get_next_job(self) -> Optional[Dict]:
        """Get next job from Tracker queue"""
        try:
            response = self.session.post(
                f"{self.tracker_url}/api/queue/next", 
                json={
                    'service_name': self.service_name,
                    'capabilities': self.capabilities
                },
                timeout=self.timeout
            )
            
            if response.status_code == 200:
                data = response.json()
                if data.get('status') == 'success' and data.get('job'):
                    return data['job']
                else:
                    return None
            else:
                print(f"⚠️ Get job failed: {response.status_code}")
                return None
                
        except Exception as e:
            print(f"❌ Get job error: {e}")
            return None
    
    def update_job_status(self, queue_id: int, status: str, details: Optional[Dict] = None,
                         video_files: Optional[Dict] = None, error_log: Optional[str] = None,
                         workflow_report: Optional[Dict] = None,
                         diagnostic_report: Optional[Dict] = None,
                         manifest_ref: Optional[Dict] = None,
                         log_ref: Optional[Dict] = None,
                         report_id: Optional[str] = None,
                         final: bool = False,
                         sequence: Optional[int] = None,
                         attempt_nonce: Optional[str] = None,
                         stage: Optional[str] = None,
                         worker_id: Optional[str] = None) -> bool:
        """Send a status callback using the current queue-attempt identity."""
        try:
            state = self._ensure_attempt(queue_id, report_id, attempt_nonce)
            effective_report_id = state["report_id"]
            effective_attempt_nonce = state["attempt_nonce"]
            normalized_diagnostic = self._diagnostic_for_attempt(
                diagnostic_report,
                effective_report_id,
                effective_attempt_nonce,
            )
            if final and not isinstance(normalized_diagnostic, dict):
                print(
                    f"⚠️ Terminal status callback for queue {queue_id} requires diagnostic_report."
                )
                return False
            if final and isinstance(normalized_diagnostic, dict):
                normalized_diagnostic["status"] = status
            effective_sequence = self._reserve_callback_sequence(queue_id, sequence)
            raw_payload = {
                'status': status,
                'details': details,
                'video_files': video_files,
                'error_log': error_log,
                'workflow_report': workflow_report,
                'schema_version': TRACKER_REPORT_SCHEMA_VERSION,
                'diagnostic_report': normalized_diagnostic,
                'manifest_ref': manifest_ref,
                'log_ref': log_ref,
                'report_id': effective_report_id,
                'attempt_nonce': effective_attempt_nonce,
                'stage': stage or (details or {}).get('stage'),
                'worker_id': worker_id or getattr(self, 'service_name', 'creator'),
                'final': bool(final),
                'sequence': effective_sequence,
            }
            original_request_bytes = self._payload_size_bytes(json_safe(raw_payload))
            if final:
                payload = self._fit_terminal_payload_to_limit(raw_payload)
            else:
                payload = self._compact_terminal_payload(
                    raw_payload,
                    aggressive=original_request_bytes > TERMINAL_MAX_REQUEST_BYTES,
                )
            result = self._send_callback_with_retry(
                queue_id=queue_id,
                method="PUT",
                endpoint=f"/api/queue/status/{queue_id}",
                payload=payload,
                final=bool(final),
                original_request_bytes=original_request_bytes,
            )
            if result:
                print(f"[Tracker] Updated job {queue_id} status: {status}")
            return result
        except Exception as e:
            print(f"❌ Status update error: {e}")
            return False

    def update_job_progress(self, queue_id: int, stage: str, substep: Optional[str] = None,
                        progress_percent: float = 0.0, status: str = 'active',
                        metadata: Optional[Dict] = None, error_details: Optional[str] = None,
                        report_id: Optional[str] = None, sequence: Optional[int] = None,
                        final: bool = False,
                        attempt_nonce: Optional[str] = None,
                        worker_id: Optional[str] = None) -> bool:
        """Send a progress callback using the current queue-attempt identity."""
        try:
            state = self._ensure_attempt(queue_id, report_id, attempt_nonce)
            effective_report_id = state["report_id"]
            effective_attempt_nonce = state["attempt_nonce"]
            effective_sequence = self._reserve_callback_sequence(queue_id, sequence)
            raw_payload = {
                'stage': stage,
                'substep': substep,
                'progress_percent': progress_percent,
                'status': status,
                'metadata': metadata or {},
                'error_details': error_details,
                'schema_version': TRACKER_REPORT_SCHEMA_VERSION,
                'report_id': effective_report_id,
                'attempt_nonce': effective_attempt_nonce,
                'worker_id': worker_id or getattr(self, 'service_name', 'creator'),
                'sequence': effective_sequence,
                'final': bool(final),
            }
            original_request_bytes = self._payload_size_bytes(json_safe(raw_payload))
            payload = self._strip_forbidden_transport_fields(json_safe(raw_payload))
            if isinstance(payload.get("error_details"), str):
                payload["error_details"] = truncate_utf8_text(
                    payload["error_details"], MAX_ERROR_LOG_BYTES
                )
            result = self._send_callback_with_retry(
                queue_id=queue_id,
                method="POST",
                endpoint=f"/api/queue/progress/{queue_id}",
                payload=payload,
                final=False,
                original_request_bytes=original_request_bytes,
            )
            if result:
                # Optional: Only log important milestones to reduce noise
                if status in ['completed', 'failed'] or progress_percent in [0.0, 25.0, 50.0, 75.0, 100.0]:
                    print(f"[Tracker] Progress {queue_id}: {stage}/{substep} -> {progress_percent}% ({status})")
            return result
        except Exception as e:
            print(f"⚠️ Progress update error: {e}")
            return False

    def get_job_progress(self, queue_id: int) -> dict:
        """Get detailed progress for job - PHASE 1"""
        try:
            response = self.session.get(
                f"{self.tracker_url}/api/queue/progress/{queue_id}",
                timeout=self.timeout
            )
            
            if response.status_code == 200:
                data = response.json()
                return data.get('progress', {})
            else:
                print(f"⚠️ Get progress failed: {response.status_code}")
                return {}
            
        except Exception as e:
            print(f"❌ Get progress error: {e}")
            return {}

    def update_mod_production_status(self, mod_id: int, manifest_data: Dict) -> bool:
        """Deprecated legacy endpoint; terminal state belongs to queue status."""
        try:
            summary = manifest_data.get("processingSummary", {})
            requested_status = str(summary.get('overallStatus') or '').strip().lower()
            if requested_status in {
                'success', 'successful', 'completed', 'complete',
                'failed', 'failure', 'partial_success', 'partial-success',
                'cancelled', 'canceled',
            }:
                print(
                    f"⚠️ Skipping legacy production-status callback for terminal status "
                    f"{requested_status!r}; use /api/queue/status/{{queue_id}}."
                )
                return False
            requested_mode = summary.get('mode')
            effective_mode = normalize_production_mode(requested_mode, allow_skip=True)
            payload = {
                'status': summary.get('overallStatus'),
                # ``skip`` is local policy state and must not be forwarded to
                # Tracker, even through this deprecated non-terminal path.
                'mode': None if effective_mode == 'skip' else requested_mode,
                'output_path': manifest_data.get("projectInfo", {}).get("outputDirectory"),
                'produced_at': summary.get('endTime'),
                'production_details': summary.get('statusDetails'),
                'quality_gates': manifest_data.get('qualityGates'),
                'coverage': summary.get('coverage'),
                'manifest_snapshot': manifest_data,
            }
            
            response = self.session.put(
                f"{self.tracker_url}/api/mods/{mod_id}/production-status",
                json=payload,
                timeout=self.timeout
            )
            if response.status_code != 200:
                self._log_callback_failure("Legacy production-status", response)
            return response.status_code == 200
        except Exception as e:
            print(f"❌ Error updating mod production status: {e}")
            return False

class CreatorService:
    """
    Creator Service - API-driven replacement for tkinter GUI
    Polls Tracker for jobs and processes them using existing UnifiedProcessingManager
    """
    
    def __init__(self, tracker_url="http://localhost:5000"):
        self.api_client = TrackerAPIClient(tracker_url)

        self.config_manager = get_config()
        self.config = self.config_manager.config

        self.running = False
        self.processing_thread = None
        self.heartbeat_thread = None
        self.service_stop_event = threading.Event()        
        self.current_job = None
        self._watchdog_failures = {}
        self._watchdog_failures_lock = threading.Lock()
        self._watchdog_states = {}
        self._watchdog_states_lock = threading.RLock()
        self.watchdog_settings = load_watchdog_settings(self.config_manager.get)
        
        self.game_player = None
        self.obs_controller = None
        
        from sprunki_ultimate_master import UnifiedProcessingManager
        self.processing_manager = UnifiedProcessingManager(master_controller=self)

        self.logger = logging.getLogger(self.__class__.__name__)
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

        # 🆕 PHASE 2: Initialize Smart Retry Manager (API-based, no database import)
        self.retry_manager = SmartRetryManager(self.api_client)

        # 🆕 PHASE 3: Initialize File Quality Validator
        self.file_validator = FileQualityValidator()

        # 🆕 PHASE 4: Initialize Performance Monitoring
        self.performance_analyzer = performance_analyzer

        # --- FIX: Initialize the processing config for the manager ---
        self._initialize_processing_config()
        self._stop_event = threading.Event() # [NEW] Global stop event
        
        # Ensure manual json directory exists
        os.makedirs(MANUAL_JSON_DIR, exist_ok=True)
        
        print("🤖 Creator Service initialized with API-based Smart Retry Manager")
        print(f"📂 Manual JSON Override directory: {os.path.abspath(MANUAL_JSON_DIR)}")

    # --- FIX: New method to create and set the ProcessingConfig ---
    def _initialize_processing_config(self):
        """Create and set the ProcessingConfig for the manager from the main config file."""
        try:
            print("⚙️ Initializing processing configuration for service mode...")
            # Use the config loaded by the api_client's config_manager
            main_config = self.api_client.config
            
            processing_config_data = main_config.get('processing', {})
            output_config = main_config.get('output', {})
            
            # Create the specific ProcessingConfig object
            processing_config = ProcessingConfig(
                output_base_dir=output_config.get('base_directory', r"C:\Users\dinht\Videos\SPRUNKI"),
                max_retries=processing_config_data.get('max_retries', 2),
                auto_skip_on_error=processing_config_data.get('auto_skip_on_error', True),
                pause_between_items=processing_config_data.get('pause_between_items', 5)
            )
            
            # Assign it to the manager
            self.processing_manager.config = processing_config
            print(f"✅ Processing config set. Output directory: {processing_config.output_base_dir}")
        except Exception as e:
            print(f"❌ CRITICAL: Failed to initialize processing config: {e}")
            self.processing_manager.config = None

    def _create_job_log_capture(self, queue_id: int) -> JobLogCapture:
        enabled = self.config_manager.get('logging.enable_job_log_capture', True)
        log_directory = self.config_manager.get('logging.job_log_directory', 'logs/video_jobs')
        if not os.path.isabs(log_directory):
            log_directory = os.path.join(PROJECT_DIR, log_directory)
        return JobLogCapture(queue_id=queue_id, log_directory=log_directory, enabled=enabled)

    def _snapshot_job_log(self, job_log_capture: Any) -> Dict[str, Any]:
        """Capture log evidence while remaining compatible with test/dummy captures."""
        snapshotter = getattr(job_log_capture, "snapshot", None)
        if callable(snapshotter):
            try:
                return snapshotter()
            except Exception as exc:
                self.logger.warning("Could not snapshot job log: %s", exc)
        return build_log_snapshot(getattr(job_log_capture, "log_path", None))

    @property
    def root(self):
        """
        Dummy 'root' property to ensure compatibility with UnifiedProcessingManager,
        which was designed with a Tkinter root in mind.
        """
        class DummyRoot:
            def after(self, ms, func, *args):
                # In service mode, we don't use Tkinter's event loop.
                # This no-op function prevents crashes if manager's queue logic is ever called.
                pass
        return DummyRoot()

    def _initialize_components(self, config_data: Dict[str, Any], job_stop_event: threading.Event) -> bool:
        """
        [REVISED] Initialize game player and OBS controller for a specific job,
        passing the job-specific stop event.
        """
        try:
            print("⚙️ Initializing components for new job...")

            layout_quality = validate_playable_layout(config_data)
            if not layout_quality.passed:
                print(f"🛑 Component initialization rejected: {layout_quality.failure_code}")
                return False
            
            if hasattr(self, 'obs_controller') and self.obs_controller:
                print("🧹 Cleaning up previous OBS controller...")
                self.obs_controller.comprehensive_obs_cleanup()
                self.obs_controller = None
                time.sleep(1)
            
            # Khởi tạo GamePlayer VÀ TRUYỀN CỜ DỪNG CỦA JOB VÀO
            self.game_player = SprunkiGamePlayer(stop_event=job_stop_event)
            self.game_player.watchdog_progress_callback = (
                self._watchdog_progress_callback
            )
            self.game_player.config = config_data
            
            current_url = config_data.get('currentUrl', '').lower()
            if 'cocrea.world' not in current_url:
                print(f"❌ Unsupported Domain: {current_url}")
                return False

            layout_data = config_data.get('layout', {})
            total_slots = len(layout_data.get('slots', []))
            
            if total_slots > 0:
                print(f"✅ Detected {total_slots} slots. Generating dynamic remix patterns...")
                self.game_player.generate_dynamic_patterns(total_slots)
            else:
                print("⚠️ Warning: No slots detected in JSON. Remixes may not be generated correctly.")
            
            game_settings = config_data.get('gameSettings', {})
            has_horror = game_settings.get('hasHorror', False)
            
            self.game_player.remix_library = self.game_player.generate_random_remixes(avoid_horror_icon=has_horror)
            self.game_player.current_remix_index = 0
            
            print("🎥 Initializing OBS controller for service mode...")
            self.obs_controller = SprunkiController()
            self.game_player.obs_controller = self.obs_controller

            if not self.obs_controller.load_config_from_dict(config_data):
                print("❌ OBS Error: Could not load recording area config!")
                return False
            
            self.config = config_data
            print("✅ Components initialized successfully.")
            return True
            
        except Exception as e:
            print(f"❌ Error initializing components: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def start(self):
        """Start Creator service"""
        print("🚀 Starting Creator Service...")
        
        if not self.api_client.register_service():
            print("❌ Failed to register with Tracker")
            return False
        
        self.running = True
        self.service_stop_event.clear()

        # 🆕 PHASE 4: Start performance monitoring
        self.performance_analyzer.start_monitoring()
        
        self.processing_thread = threading.Thread(target=self._processing_loop, daemon=True)
        self.processing_thread.start()
        
        self.heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self.heartbeat_thread.start()
        
        print("✅ Creator Service started successfully with performance monitoring")
        return True
    
    def stop(self):
        """Stop Creator service with performance cleanup - PHASE 4 Enhanced"""
        print("🛑 Stopping Creator Service...")
        self.running = False
        self.service_stop_event.set()
        # Manual service cancellation is the one path that is allowed to
        # abort both production and cleanup.  A watchdog timeout uses a
        # separate cleanup event and never clears the workflow stop signal.
        try:
            self.processing_manager.stop_event.set()
            self.processing_manager.cleanup_abort_event.set()
        except Exception:
            pass

        # 🆕 PHASE 4: Stop performance monitoring
        self.performance_analyzer.stop_monitoring()

        # Thêm logic dọn dẹp toàn diện khi dừng service
        try:
            print("🧹 Performing comprehensive cleanup on service stop...")
            if hasattr(self, 'obs_controller') and self.obs_controller:
                print("   -> Cleaning up OBS controller...")
                self.obs_controller.comprehensive_obs_cleanup()
            if hasattr(self, 'game_player') and self.game_player:
                print("   -> Cleaning up game player (closing browser)...")
                self.game_player.close_game_window()
        except Exception as e:
            print(f"⚠️ Error during final cleanup: {e}")
        
        if self.processing_thread:
            self.processing_thread.join(timeout=5)
        if self.heartbeat_thread:
            self.heartbeat_thread.join(timeout=5)
        
        print("✅ Creator Service stopped")

    def _processing_loop(self):
        """Main processing loop - polls for jobs"""
        print("🔄 Processing loop started")
        
        # SỬA ĐỔI: Vòng lặp while chỉ phụ thuộc vào cờ dừng của service
        while not self.service_stop_event.is_set():
            try:
                # Không cần kiểm tra cờ stop ở đây nữa.
                
                job = self.api_client.get_next_job()
                
                if job:
                    self.current_job = job
                    
                    print(f"📥 Received job: {job.get('mod_title', 'Unknown Title')} ({job.get('production_mode', 'N/A')})")
                    
                    # _process_job sẽ tự quản lý cờ stop của riêng nó
                    success = self._process_job(job)
                    
                    if success:
                        print(f"✅ Job completed: {job['id']}")
                    else:
                        print(f"❌ Job failed: {job['id']}")
                    
                    self.current_job = None
                else:
                    if self.processing_manager.config:
                        pause_duration = self.processing_manager.config.pause_between_items
                        print(f"No jobs available. Pausing for {pause_duration}s...")
                        # Sử dụng wait() của Event để có thể ngắt ngay lập tức
                        # thay vì sleep cứng
                        self.service_stop_event.wait(timeout=pause_duration)
                    else:
                        self.service_stop_event.wait(timeout=30)
                
            except Exception as e:
                print(f"❌ Processing loop error: {e}")
                self.service_stop_event.wait(timeout=10) # Chờ 10s nếu có lỗi
        
        # Log khi vòng lặp kết thúc
        print("🔄 Processing loop has finished.")
    
    def _heartbeat_loop(self):
        """Heartbeat loop - reports status to Tracker"""
        # SỬA ĐỔI: Chỉ phụ thuộc vào cờ dừng của service
        while not self.service_stop_event.is_set():
            try:
                status = "busy" if self.current_job else "online"
                current_job_id = self.current_job['id'] if self.current_job else None
                
                performance_stats = self.performance_analyzer.get_performance_summary()
                
                self.api_client.send_heartbeat(
                    status=status, 
                    current_job_id=current_job_id,
                    performance_stats=performance_stats
                )

                # Sử dụng wait() để có thể dừng ngay lập tức
                self.service_stop_event.wait(timeout=60)
                
            except Exception as e:
                print(f"⚠️ Heartbeat error: {e}")
                # Chờ một khoảng ngắn hơn nếu có lỗi
                if not self.service_stop_event.is_set():
                    self.service_stop_event.wait(timeout=30)
        
        # Log khi vòng lặp kết thúc
        print("❤️ Heartbeat loop has finished.")

    def _ensure_watchdog_runtime(self) -> None:
        """Initialize watchdog fields for legacy/test-created service objects."""

        if not hasattr(self, "watchdog_settings"):
            config_manager = getattr(self, "config_manager", None)
            getter = getattr(config_manager, "get", None)
            if not callable(getter):
                getter = lambda key, default=None: default
            self.watchdog_settings = load_watchdog_settings(getter)
        if not hasattr(self, "service_stop_event"):
            self.service_stop_event = threading.Event()
        if not hasattr(self, "logger"):
            self.logger = logging.getLogger(self.__class__.__name__)
        if not hasattr(self, "_watchdog_states"):
            self._watchdog_states = {}
        if not hasattr(self, "_watchdog_states_lock"):
            self._watchdog_states_lock = threading.RLock()

    def _register_watchdog_state(self, queue_id: int, state: WatchdogState) -> None:
        if not hasattr(self, "_watchdog_states"):
            self._watchdog_states = {}
        if not hasattr(self, "_watchdog_states_lock"):
            self._watchdog_states_lock = threading.RLock()
        with self._watchdog_states_lock:
            self._watchdog_states[queue_id] = state

    def _get_watchdog_state(self, queue_id: int) -> Optional[WatchdogState]:
        states = getattr(self, "_watchdog_states", None)
        if not isinstance(states, dict):
            return None
        lock = getattr(self, "_watchdog_states_lock", None)
        if lock is None:
            return states.get(queue_id)
        with lock:
            state = states.get(queue_id)
        return state

    def _remove_watchdog_state(self, queue_id: int) -> None:
        states = getattr(self, "_watchdog_states", None)
        if not isinstance(states, dict):
            return
        lock = getattr(self, "_watchdog_states_lock", None)
        if lock is None:
            states.pop(queue_id, None)
            return
        with lock:
            states.pop(queue_id, None)

    def _touch_watchdog(
        self,
        queue_id: int,
        phase: str,
        substep: Optional[str] = None,
        **metadata: Any,
    ) -> None:
        state = self._get_watchdog_state(queue_id)
        if state is not None:
            state.touch(phase, substep, **metadata)

    def _watchdog_progress_callback(
        self,
        phase: str,
        substep: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        queue_id = getattr(self, "_active_watchdog_queue_id", None)
        if queue_id is not None:
            self._touch_watchdog(queue_id, phase, substep, **(metadata or {}))

    def _job_watchdog(
        self,
        state: WatchdogState,
        workflow_stop_event: threading.Event,
    ) -> None:
        """Monitor hard and idle deadlines without sending terminal status."""

        queue_id = state.queue_id
        while not state.finished_event.is_set():
            if self.service_stop_event.is_set():
                self.logger.info(
                    "🐶 Watchdog for job %s: service is stopping. Exiting.",
                    queue_id,
                )
                return

            snapshot = state.snapshot()
            reason = None
            if snapshot["remaining_seconds"] <= 0:
                reason = "hard_timeout"
            elif snapshot["last_progress_age_seconds"] >= state.idle_timeout_seconds:
                reason = "idle_timeout"

            if reason:
                event = state.mark_timeout(reason)
                if event is not None:
                    workflow_stop_event.set()
                    # A number of legacy workflow loops still consult the
                    # boolean flag in addition to the shared Event.  Set it
                    # for the production stop, then cleanup will temporarily
                    # clear it while using its independent abort Event.
                    self.processing_manager.should_stop = True
                    if getattr(self, "game_player", None):
                        self.game_player.should_stop = True
                    self.logger.error(
                        "🚨 [WATCHDOG] Job %s %s at %.1fs; phase=%s/%s",
                        queue_id,
                        reason,
                        event.get("elapsed_seconds", 0.0),
                        event.get("phase"),
                        event.get("substep"),
                    )
                return

            wait_seconds = min(
                1.0,
                max(
                    0.05,
                    min(
                        snapshot["remaining_seconds"],
                        state.idle_timeout_seconds
                        - snapshot["last_progress_age_seconds"],
                    ),
                ),
            )
            state.finished_event.wait(timeout=wait_seconds)

    def _watchdog_failure_from_event(
        self,
        event: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        if not event:
            return None
        reason = event.get("reason", "watchdog_timeout")
        message = (
            f"WATCHDOG: {reason} after "
            f"{float(event.get('elapsed_seconds', 0.0)):.1f} seconds."
        )
        return {
            "failure_code": "watchdog_timeout",
            "message": message,
            "evidence": {
                "watchdog": True,
                **event,
            },
            "status": "UNKNOWN",
            "retryable": reason != "service_cancelled",
        }

    def _take_watchdog_failure(self, queue_id: int) -> Optional[Dict[str, Any]]:
        """Consume the current watchdog event exactly once."""
        state = self._get_watchdog_state(queue_id)
        if state is None:
            return None
        return self._watchdog_failure_from_event(state.take_timeout())

    def _set_workflow_stop_event(self, stop_event: threading.Event) -> None:
        """Propagate the workflow event, with compatibility for test adapters."""

        manager = getattr(self, "processing_manager", None)
        setter = getattr(manager, "set_stop_event", None)
        if callable(setter):
            setter(stop_event)
            return
        if manager is not None:
            manager.stop_event = stop_event
            manager.workflow_stop_event = stop_event
        player = getattr(self, "game_player", None)
        if player is not None:
            player.stop_event = stop_event

    def _set_watchdog_progress_callback(self) -> None:
        manager = getattr(self, "processing_manager", None)
        setter = getattr(manager, "set_watchdog_progress_callback", None)
        if callable(setter):
            setter(self._watchdog_progress_callback)

    def _set_workflow_attempt(self, attempt: int) -> None:
        manager = getattr(self, "processing_manager", None)
        setter = getattr(manager, "set_workflow_attempt", None)
        if callable(setter):
            setter(attempt)
        elif manager is not None:
            manager.workflow_attempt_index = max(1, int(attempt or 1))

    def _enter_cleanup_mode(self, cleanup_abort_event: threading.Event) -> None:
        """Use the manager's independent cleanup event when available."""

        manager = getattr(self, "processing_manager", None)
        enter = getattr(manager, "enter_cleanup_mode", None)
        if callable(enter):
            enter(cleanup_abort_event)
            return
        if manager is not None:
            manager._creator_fallback_workflow_event = getattr(
                manager, "workflow_stop_event", getattr(manager, "stop_event", None)
            )
            manager.stop_event = cleanup_abort_event
            manager.workflow_stop_event = manager._creator_fallback_workflow_event
            manager.should_stop = False
        player = getattr(self, "game_player", None)
        if player is not None:
            player.stop_event = cleanup_abort_event
            player.should_stop = False

    def _exit_cleanup_mode(self) -> None:
        manager = getattr(self, "processing_manager", None)
        exit_cleanup = getattr(manager, "exit_cleanup_mode", None)
        if callable(exit_cleanup):
            exit_cleanup()
            return
        workflow_event = getattr(manager, "_creator_fallback_workflow_event", None)
        if manager is not None and workflow_event is not None:
            manager.stop_event = workflow_event
            manager.workflow_stop_event = workflow_event
            manager.should_stop = workflow_event.is_set()
            manager._creator_fallback_workflow_event = None
        player = getattr(self, "game_player", None)
        if player is not None and workflow_event is not None:
            player.stop_event = workflow_event
            player.should_stop = workflow_event.is_set()

    def _start_job_watchdog(
        self,
        state: WatchdogState,
        workflow_stop_event: threading.Event,
    ) -> threading.Thread:
        thread = threading.Thread(
            target=self._job_watchdog,
            args=(state, workflow_stop_event),
            daemon=True,
            name=f"creator-watchdog-{state.queue_id}",
        )
        thread.start()
        return thread

    @staticmethod
    def _append_unique_output(item: Any, output_path: Optional[str]) -> bool:
        if not output_path:
            return False
        try:
            normalized = os.path.abspath(str(output_path))
            if not os.path.isfile(normalized) or os.path.getsize(normalized) <= 0:
                return False
        except (OSError, TypeError, ValueError):
            return False
        if normalized not in getattr(item, "output_files", []):
            item.output_files.append(normalized)
            return True
        return False

    def _collect_attempt_outputs(
        self,
        item: Any,
        attempt: int,
        *,
        attempt_started_wall_time: Optional[float] = None,
    ) -> list:
        """Collect stopped OBS output and recent video artifacts for an attempt."""
        collected = []
        controller = getattr(self, "obs_controller", None)
        last_output = getattr(controller, "last_stopped_output_path", None)
        if self._append_unique_output(item, last_output):
            collected.append(os.path.abspath(str(last_output)))

        project_folder = getattr(controller, "current_project_folder", None)
        if project_folder and os.path.isdir(project_folder):
            cutoff = (attempt_started_wall_time or time.time()) - 15.0
            try:
                candidates = []
                for entry in os.scandir(project_folder):
                    if not entry.is_file():
                        continue
                    name = entry.name.lower()
                    if not name.endswith((".mkv", ".mp4", ".webm")):
                        continue
                    try:
                        stat = entry.stat()
                    except OSError:
                        continue
                    if stat.st_size <= 0 or stat.st_mtime < cutoff:
                        continue
                    candidates.append((stat.st_mtime, entry.path))
                for _, candidate in sorted(candidates):
                    if self._append_unique_output(item, candidate):
                        collected.append(os.path.abspath(candidate))
            except OSError as exc:
                self.logger.warning(
                    "⚠️ Could not scan output folder for queue %s attempt %s: %s",
                    getattr(item, "project_slug", "unknown"),
                    attempt,
                    exc,
                )

        if not isinstance(getattr(item, "attempt_outputs", None), dict):
            item.attempt_outputs = {}
        attempt_references = item.attempt_outputs.setdefault(int(attempt), [])
        # A successful operation records its path immediately, while an
        # emergency OBS stop may only expose it through the controller. Keep
        # both sources in the bounded per-attempt diagnostic reference.
        for path in list(getattr(item, "output_files", []) or []):
            try:
                normalized = os.path.abspath(str(path))
                if os.path.isfile(normalized) and os.path.getsize(normalized) > 0:
                    if normalized not in attempt_references:
                        attempt_references.append(normalized)
            except (OSError, TypeError, ValueError):
                continue
        if collected:
            for path in collected:
                if path not in attempt_references:
                    attempt_references.append(path)
        return collected

    def _reset_item_for_watchdog_retry(self, item: Any, attempt: int) -> None:
        """Reset only per-workflow result state; retain source JSON and identity."""
        item.workflow_attempt = int(attempt)
        item.status = "processing"
        item.error_message = None
        item.failure_code = None
        item.output_files = []
        item.start_time = datetime.now()
        item.end_time = None
        item.master_mode_status = "not_started"
        item.individual_mode_status = "not_started"
        item.workflow_report = None
        item.recording_area = None
        item.recorded_identity_status = "not_started"
        item.recorded_identity_reason = None
        item.recorded_identity_retryable = False
        item.recorded_identity_files = []
        self._manual_json_failure = None
        self.processing_manager.should_stop = False
        if getattr(self, "game_player", None):
            self.game_player.should_stop = False
            self.game_player.is_playing = False

    def _send_watchdog_recovery_progress(
        self,
        queue_id: int,
        event: Dict[str, Any],
        *,
        retrying: bool,
        cleanup: Optional[Dict[str, Any]] = None,
    ) -> None:
        cleanup_metadata = dict(cleanup or {})
        if isinstance(cleanup_metadata.get("output_files"), list):
            cleanup_metadata["output_files"] = [
                os.path.basename(str(path))
                for path in cleanup_metadata["output_files"][:32]
            ]
        metadata = {
            "final": False,
            "watchdog": event,
            "retrying": bool(retrying),
            "cleanup": cleanup_metadata,
        }
        self.api_client.update_job_progress(
            queue_id,
            "watchdog",
            "watchdog_recovery",
            0.0,
            "active",
            metadata=metadata,
            error_details=None,
            final=False,
        )

    @staticmethod
    def _run_bounded_cleanup_call(
        operation: Callable[[], Any],
        cleanup_abort_event: threading.Event,
        deadline: float,
    ) -> Tuple[bool, Any, Optional[BaseException]]:
        """Run one cleanup operation without extending the grace budget."""

        remaining = max(0.0, deadline - time.monotonic())
        if remaining <= 0:
            cleanup_abort_event.set()
            return False, None, TimeoutError("cleanup_grace_expired")

        holder: Dict[str, Any] = {}

        def invoke() -> None:
            try:
                holder["value"] = operation()
            except BaseException as exc:  # preserve operation failures for evidence
                holder["error"] = exc

        worker = threading.Thread(
            target=invoke,
            name="creator-watchdog-cleanup-call",
            daemon=True,
        )
        worker.start()
        worker.join(timeout=remaining)
        if worker.is_alive():
            cleanup_abort_event.set()
            return False, None, TimeoutError("cleanup_grace_expired")
        if "error" in holder:
            return True, None, holder["error"]
        return True, holder.get("value"), None

    def _run_watchdog_cleanup(
        self,
        queue_id: int,
        item: Any,
        attempt: int,
        cleanup_abort_event: threading.Event,
    ) -> Dict[str, Any]:
        """Stop recording, recover the board, close runtime components, and bound cleanup."""
        started = time.monotonic()
        deadline = started + float(self.watchdog_settings["cleanup_grace_seconds"])
        result: Dict[str, Any] = {
            "status": "started",
            "attempt": attempt,
            "started_at_monotonic": started,
            "deadline_seconds": self.watchdog_settings["cleanup_grace_seconds"],
            "output_files": [],
        }

        def expired() -> bool:
            if self.service_stop_event.is_set() or time.monotonic() >= deadline:
                cleanup_abort_event.set()
                result["status"] = "cleanup_incomplete"
                result["reason"] = (
                    "service_stop" if self.service_stop_event.is_set()
                    else "cleanup_grace_expired"
                )
                return True
            return False

        self._enter_cleanup_mode(cleanup_abort_event)
        try:
            controller = getattr(self, "obs_controller", None)
            if controller and not expired():
                completed, stop_result, stop_error = self._run_bounded_cleanup_call(
                    controller.stop_recording,
                    cleanup_abort_event,
                    deadline,
                )
                if not completed:
                    result["status"] = "cleanup_incomplete"
                    result["reason"] = "recording_stop_grace_expired"
                    return result
                try:
                    if stop_error:
                        raise stop_error
                    stop_success, output_path = stop_result
                    if output_path:
                        self._append_unique_output(item, output_path)
                    result["recording_stop"] = {
                        "success": bool(stop_success),
                        "output_path_present": bool(output_path),
                    }
                    if not stop_success:
                        result["status"] = "failed"
                        result["reason"] = "recording_stop_failed"
                except Exception as exc:
                    result["recording_stop"] = {"success": False, "error": str(exc)}
                    result["status"] = "failed"
                    result["reason"] = "recording_stop_failed"

            self._collect_attempt_outputs(
                item,
                attempt,
                attempt_started_wall_time=time.time() - max(0.0, time.monotonic() - started),
            )
            if expired():
                return result

            player = getattr(self, "game_player", None)
            if player and hasattr(player, "_run_cleanup_once") and not expired():
                if hasattr(player, "_reset_cleanup_guard"):
                    player._reset_cleanup_guard()
                completed, cleanup_operation, cleanup_error = self._run_bounded_cleanup_call(
                    lambda: player._run_cleanup_once(
                        use_cdp=False,
                        context={
                            "workflow": "watchdog_recovery",
                            "queue_id": queue_id,
                            "attempt": attempt,
                        },
                        obs_controller=controller,
                        force_retry=True,
                    ),
                    cleanup_abort_event,
                    deadline,
                )
                if not completed:
                    result["status"] = "cleanup_incomplete"
                    result["reason"] = "board_cleanup_grace_expired"
                    return result
                try:
                    if cleanup_error:
                        raise cleanup_error
                    cleanup_status = getattr(
                        getattr(cleanup_operation, "status", None),
                        "value",
                        str(getattr(cleanup_operation, "status", "unknown")),
                    )
                    result["board_cleanup"] = {
                        "status": cleanup_status,
                        "message": getattr(cleanup_operation, "message", ""),
                    }
                    if cleanup_status not in {"VERIFIED", "ALREADY_EMPTY"}:
                        result["status"] = "failed"
                        result["reason"] = "board_cleanup_not_verified"
                except Exception as exc:
                    result["board_cleanup"] = {"status": "failed", "error": str(exc)}
                    result["status"] = "failed"
                    result["reason"] = "board_cleanup_failed"

            if controller and not expired():
                completed, obs_cleanup, cleanup_error = self._run_bounded_cleanup_call(
                    controller.comprehensive_obs_cleanup,
                    cleanup_abort_event,
                    deadline,
                )
                if not completed:
                    result["status"] = "cleanup_incomplete"
                    result["reason"] = "obs_cleanup_grace_expired"
                    return result
                try:
                    if cleanup_error:
                        raise cleanup_error
                    result["obs_cleanup"] = bool(obs_cleanup)
                    if not result["obs_cleanup"]:
                        result["status"] = "failed"
                        result["reason"] = "obs_cleanup_failed"
                except Exception as exc:
                    result["obs_cleanup"] = False
                    result["obs_cleanup_error"] = str(exc)
                    result["status"] = "failed"
                    result["reason"] = "obs_cleanup_failed"

            if player and not expired():
                completed, browser_cleanup, cleanup_error = self._run_bounded_cleanup_call(
                    player.close_game_window,
                    cleanup_abort_event,
                    deadline,
                )
                if not completed:
                    result["status"] = "cleanup_incomplete"
                    result["reason"] = "browser_cleanup_grace_expired"
                    return result
                try:
                    if cleanup_error:
                        raise cleanup_error
                    result["browser_cleanup"] = bool(browser_cleanup)
                    if not result["browser_cleanup"]:
                        result["status"] = "failed"
                        result["reason"] = "browser_cleanup_failed"
                except Exception as exc:
                    result["browser_cleanup"] = False
                    result["browser_cleanup_error"] = str(exc)
                    result["status"] = "failed"
                    result["reason"] = "browser_cleanup_failed"

            if result.get("status") == "started":
                if expired():
                    return result
                result["status"] = "completed"
        finally:
            self._exit_cleanup_mode()
            result["elapsed_seconds"] = round(time.monotonic() - started, 3)
            if result.get("status") == "started":
                result["status"] = "completed"
        result["output_files"] = list(item.attempt_outputs.get(int(attempt), []))
        return result

    @staticmethod
    def _has_recorded_video_output(
        item: Any,
        cleanup: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Require a real, non-empty video artifact for idle-timeout success."""

        candidates = list(getattr(item, "output_files", []) or [])
        cleanup = cleanup if isinstance(cleanup, dict) else {}
        candidates.extend(cleanup.get("output_files", []) or [])
        video_extensions = {".mkv", ".mp4", ".webm", ".mov", ".avi"}
        seen = set()
        for candidate in candidates:
            try:
                path = os.path.abspath(str(candidate))
                if path in seen or os.path.splitext(path)[1].lower() not in video_extensions:
                    continue
                seen.add(path)
                if os.path.isfile(path) and os.path.getsize(path) > 0:
                    return True
            except (OSError, TypeError, ValueError):
                continue
        return False

    def _accept_idle_timeout_partial_success(
        self,
        item: Any,
        timeout_event: Dict[str, Any],
        cleanup: Optional[Dict[str, Any]],
    ) -> bool:
        """Promote a productive idle timeout after bounded cleanup.

        This method is called only after the watchdog has stopped new workflow
        actions and attempted its bounded cleanup.  It never handles manual
        cancellation or the hard deadline, and it does not accept a report
        merely because a file exists: the shared quality helper still requires
        the core runtime, placement, icon, and remix evidence.
        """

        report = getattr(item, "workflow_report", None)
        if report is None:
            return False

        timeout_reason = timeout_event.get("reason") if isinstance(timeout_event, dict) else None
        output_recorded = self._has_recorded_video_output(item, cleanup)
        cleanup_status = (
            cleanup.get("status")
            if isinstance(cleanup, dict)
            else None
        )
        gate = evaluate_idle_timeout_partial_success(
            report,
            timeout_reason=timeout_reason,
            output_recorded=output_recorded,
            cleanup_status=cleanup_status,
        )
        if gate is None:
            return False

        watchdog_report_evidence = report.evidence.get("watchdog")
        if not isinstance(watchdog_report_evidence, dict):
            watchdog_report_evidence = {}
            report.evidence["watchdog"] = watchdog_report_evidence
        watchdog_report_evidence["partialAcceptance"] = {
            "reason": "idle_timeout",
            "outputRecorded": output_recorded,
            "cleanupStatus": cleanup_status,
            "cleanupNonBlocking": True,
        }
        watchdog_evidence = getattr(item, "watchdog_evidence", None)
        if isinstance(watchdog_evidence, dict):
            watchdog_evidence["partial_acceptance"] = {
                "status": "accepted",
                "reason": "idle_timeout",
                "output_recorded": output_recorded,
                "cleanup_status": cleanup_status,
                "completed_remixes": report.normal_remixes_played,
                "attempted_remixes": report.normal_remixes_total,
            }

        # _process_with_smart_retry may have recorded workflow_stopped while
        # unwinding.  Clear that transient failure so finalization can publish
        # the accepted report and its current-attempt video artifact.
        item.status = "processing"
        item.failure_code = None
        item.error_message = None
        if hasattr(item, "master_mode_status"):
            item.master_mode_status = "success"
        return True

    def _run_workflow_with_watchdog(
        self,
        item: Any,
        queue_id: int,
        state: WatchdogState,
        *,
        initial_workflow_stop_event: Optional[threading.Event] = None,
        initial_watcher_thread: Optional[threading.Thread] = None,
    ) -> Tuple[bool, Optional[Dict[str, Any]], Optional[threading.Thread]]:
        """Run a bounded workflow and at most one early-idle full retry."""
        attempt = 1
        watcher_thread = None
        final_timeout = None
        self._active_watchdog_queue_id = queue_id
        self._set_watchdog_progress_callback()

        if not isinstance(getattr(item, "attempt_started_wall_times", None), dict):
            item.attempt_started_wall_times = {}
        if not isinstance(getattr(item, "watchdog_evidence", None), dict):
            item.watchdog_evidence = {}

        while True:
            is_initial_attempt = attempt == 1 and initial_workflow_stop_event is not None
            workflow_stop_event = (
                initial_workflow_stop_event
                if is_initial_attempt
                else threading.Event()
            )
            cleanup_abort_event = threading.Event()
            self._set_workflow_stop_event(workflow_stop_event)
            self.processing_manager.cleanup_abort_event = cleanup_abort_event
            self._set_workflow_attempt(attempt)
            self._touch_watchdog(queue_id, "workflow", "attempt_start", attempt=attempt)
            attempt_started_wall_time = time.time()
            item.attempt_started_wall_times[int(attempt)] = attempt_started_wall_time
            if is_initial_attempt and initial_watcher_thread is not None:
                watcher_thread = initial_watcher_thread
            else:
                watcher_thread = self._start_job_watchdog(state, workflow_stop_event)

            job_successful = self._process_with_smart_retry(item, queue_id)
            timeout_event = state.take_timeout()
            # Close the small race where the workflow returns at/after the
            # hard deadline before the monitor thread gets scheduled.
            if timeout_event is None and state.snapshot()["remaining_seconds"] <= 0:
                timeout_event = state.mark_timeout("hard_timeout")
                if timeout_event is None:
                    timeout_event = state.take_timeout()
            if timeout_event is None:
                state.touch("workflow", "returned", success=bool(job_successful))
                return bool(job_successful), final_timeout, watcher_thread

            final_timeout = timeout_event
            failure = self._watchdog_failure_from_event(timeout_event) or {}
            item.watchdog_evidence.setdefault("timeouts", []).append(timeout_event)
            self._send_watchdog_recovery_progress(
                queue_id,
                timeout_event,
                retrying=False,
            )
            self._collect_attempt_outputs(
                item,
                attempt,
                attempt_started_wall_time=attempt_started_wall_time,
            )
            cleanup = self._run_watchdog_cleanup(
                queue_id,
                item,
                attempt,
                cleanup_abort_event,
            )
            item.watchdog_evidence.setdefault("cleanup", []).append(cleanup)
            failure["evidence"] = {
                **(failure.get("evidence") or {}),
                "cleanup": cleanup,
            }

            # A productive idle timeout is a terminal content decision, not a
            # reason to launch another full workflow.  The bounded cleanup has
            # already stopped recording and attempted board/runtime recovery;
            # if the report passes the normal Master icon/remix thresholds and
            # has a real video, accept it.  Hard timeout and manual
            # cancellation never enter this branch because the shared helper
            # only accepts the exact ``idle_timeout`` reason.
            if self._accept_idle_timeout_partial_success(item, timeout_event, cleanup):
                item.watchdog_evidence.setdefault("retry_decisions", []).append(
                    {
                        "attempt": attempt,
                        "timeout_reason": timeout_event.get("reason"),
                        "decision": "accept_partial_success",
                        "remaining_seconds": state.remaining_seconds(),
                        "cleanup_status": cleanup.get("status"),
                    }
                )
                self._send_watchdog_recovery_progress(
                    queue_id,
                    timeout_event,
                    retrying=False,
                    cleanup=cleanup,
                )
                return True, None, watcher_thread

            can_retry = (
                timeout_event.get("reason") == "idle_timeout"
                and attempt <= int(self.watchdog_settings["max_full_retries"])
                and state.can_retry(self.watchdog_settings["retry_min_remaining_seconds"])
                and cleanup.get("status") == "completed"
                and not self.service_stop_event.is_set()
            )
            item.watchdog_evidence.setdefault("retry_decisions", []).append(
                {
                    "attempt": attempt,
                    "timeout_reason": timeout_event.get("reason"),
                    "decision": "retry" if can_retry else "terminal",
                    "max_full_retries": int(self.watchdog_settings["max_full_retries"]),
                    "remaining_seconds": state.remaining_seconds(),
                    "minimum_remaining_seconds": self.watchdog_settings[
                        "retry_min_remaining_seconds"
                    ],
                    "cleanup_status": cleanup.get("status"),
                    "cleanup_grace_seconds": self.watchdog_settings[
                        "cleanup_grace_seconds"
                    ],
                }
            )
            if not can_retry:
                item.failure_code = "watchdog_timeout"
                item.error_message = failure.get("message")
                return False, failure, watcher_thread

            attempt += 1
            item.watchdog_evidence.setdefault("retries", []).append(
                {
                    "from_attempt": attempt - 1,
                    "to_attempt": attempt,
                    "reason": timeout_event.get("reason"),
                    "remaining_seconds": state.remaining_seconds(),
                }
            )
            self._reset_item_for_watchdog_retry(item, attempt)
            if not state.begin_retry(attempt):
                item.failure_code = "watchdog_timeout"
                item.error_message = "Watchdog retry could not start within the hard deadline."
                return False, failure, watcher_thread
            self._send_watchdog_recovery_progress(
                queue_id,
                timeout_event,
                retrying=True,
                cleanup=cleanup,
            )
            final_timeout = None

    def _process_job(self, job: Dict) -> bool:
        """
        [REVISED & WATCHDOG ENABLED] Orchestrates job processing with a job-specific stop event.
        """
        queue_id = job['id']
        self._ensure_watchdog_runtime()
        start_time = time.monotonic()
        attempt_started_wall_time = time.time()
        job_log_capture = self._create_job_log_capture(queue_id)
        job_log_capture.start()
        item = None
        job_successful = False
        final_success = False
        terminal_callback_ok = True
        terminal_error = None
        # These two identifiers are immutable for the lifetime of this queue
        # attempt.  Tracker does not provide attempt_nonce in /queue/next.
        report_id = str(uuid.uuid4())
        attempt_nonce = str(uuid.uuid4())
        attempt_bound = False

        # The hard deadline starts at queue-job orchestration time.  Tracker
        # binding is part of that budget and must not postpone the watchdog.
        watchdog_state = WatchdogState(
            queue_id,
            hard_timeout_seconds=self.watchdog_settings["hard_timeout_seconds"],
            idle_timeout_seconds=self.watchdog_settings["idle_timeout_seconds"],
        )
        self._register_watchdog_state(queue_id, watchdog_state)
        job_stop_event = threading.Event()
        initial_watchdog_thread = self._start_job_watchdog(
            watchdog_state,
            job_stop_event,
        )
        watchdog_thread = initial_watchdog_thread
        watchdog_failure = None

        bind_report = getattr(self.api_client, "bind_job_report", None)
        if callable(bind_report):
            try:
                bind_report(queue_id, report_id, attempt_nonce)
            except TypeError:
                # Keep test doubles/older local adapters callable while the
                # real Tracker client always receives both identifiers.
                bind_report(queue_id, report_id)
        
        try:
            # Bind the attempt before doing any work.  Processing is allowed
            # to continue only after Tracker accepts sequence 0.
            attempt_bound = self.api_client.update_job_status(
                queue_id,
                'processing',
                {
                    'stage': 'starting',
                    'started_by': getattr(self.api_client, 'service_name', 'creator'),
                },
                report_id=report_id,
                attempt_nonce=attempt_nonce,
                final=False,
                sequence=0,
                stage='video_processing',
                worker_id=getattr(self.api_client, 'service_name', 'creator'),
            )
            if not attempt_bound:
                terminal_error = "Tracker rejected the attempt-binding callback; job processing was not started."
                self.logger.error(
                    "❌ Cannot start job %s because Tracker did not accept the sequence-0 binding callback.",
                    queue_id,
                )
                return False

            # The initial watchdog was started before binding so the deadline
            # covers the whole queue attempt, not only gameplay.
            watchdog_thread = initial_watchdog_thread
            self._set_workflow_stop_event(job_stop_event)
            self._set_watchdog_progress_callback()
            self.logger.info(
                "🐶 Watchdog started for job %s: hard=%.1fs idle=%.1fs",
                queue_id,
                watchdog_state.hard_timeout_seconds,
                watchdog_state.idle_timeout_seconds,
            )
            
            item = self._convert_job_to_processing_item(job)
            if getattr(item, "start_time", None) is None:
                item.start_time = datetime.now()

            self.logger.info(
                "🎛️ Production mode for queue %s: requested=%s effective=%s",
                queue_id,
                getattr(item, "requested_production_mode", None),
                item.mode,
            )
            print(
                f"🎛️ Effective production mode: {item.mode} "
                f"(requested={getattr(item, 'requested_production_mode', None)})"
            )
            
            # --- HIỂN THỊ SLUG ĐỂ USER DỄ COPY ---
            print(f"🔑 Project Slug: {item.project_slug}")
            # ---------------------------------------

            self.performance_analyzer.record_job_start(queue_id, job)

            # The workflow cooperatively stops when the watchdog sets the
            # attempt-specific event.  An early idle timeout gets one fresh
            # workflow attempt within the same hard deadline.
            job_successful, watchdog_failure, watchdog_thread = (
                self._run_workflow_with_watchdog(
                    item,
                    queue_id,
                    watchdog_state,
                    initial_workflow_stop_event=job_stop_event,
                    initial_watcher_thread=initial_watchdog_thread,
                )
            )
            if watchdog_failure:
                terminal_error = watchdog_failure.get("message") or terminal_error

        except Exception as e:
            error_msg = f"Critical job processing error: {str(e)}"
            terminal_error = error_msg
            self.logger.error(f"❌ Unhandled exception in job {queue_id}: {error_msg}", exc_info=True)
            if item:
                self.processing_manager._record_quality_failure(
                    item,
                    getattr(item, "failure_code", None) or "runtime_verification_unknown",
                    error_msg,
                    evidence={"error": error_msg, "queueId": queue_id},
                    status="UNKNOWN",
                    retryable=True,
                )
            job_successful = False
            if attempt_bound:
                self.api_client.update_job_progress(queue_id, 'critical_failure', str(e), 0.0, 'failed')
        
        finally:
            # Stop the monitor before finalization.  Cleanup has its own
            # bounded abort event and must not clear workflow cancellation.
            job_stop_event.set()
            # The current attempt may use a fresh event after watchdog
            # recovery; stop the manager's active workflow event as well.
            try:
                self.processing_manager.stop_event.set()
            except Exception:
                pass
            watchdog_state.mark_finished()
            if watchdog_thread is not None:
                watchdog_thread.join(timeout=2.0)

            if watchdog_failure is None:
                watchdog_failure = self._take_watchdog_failure(queue_id)
            if watchdog_failure:
                terminal_error = watchdog_failure.get("message") or terminal_error
                if item:
                    self.processing_manager._record_quality_failure(
                        item,
                        watchdog_failure.get("failure_code", "runtime_verification_unknown"),
                        watchdog_failure.get("message", "Watchdog stopped the workflow."),
                        evidence=watchdog_failure.get("evidence", {}),
                        status=watchdog_failure.get("status", "UNKNOWN"),
                        retryable=watchdog_failure.get("retryable", True),
                    )

            manifest_data = None
            video_files_payload = {}
            workflow_report_obj = getattr(item, "workflow_report", None) if item else None

            if item:
                # Recover paths from an emergency OBS stop before generating
                # the manifest.  This is intentionally attempt-scoped so a
                # retry never republishes files from attempt 1.
                current_attempt = int(getattr(item, "workflow_attempt", 1) or 1)
                current_attempt_started = getattr(
                    item, "attempt_started_wall_times", {}
                ).get(current_attempt, attempt_started_wall_time)
                self._collect_attempt_outputs(
                    item,
                    current_attempt,
                    attempt_started_wall_time=current_attempt_started,
                )
                if not job_successful:
                    # Failed/partial attempt artifacts remain on disk and in
                    # the bounded attempt reference, but are never treated
                    # as publishable manifest/video outputs.
                    item.output_files = []
                if workflow_report_obj is None:
                    self.processing_manager._record_quality_failure(
                        item,
                        "runtime_verification_unknown",
                        "Workflow ended without a quality report.",
                        evidence={"queueId": queue_id},
                        status="UNKNOWN",
                        retryable=True,
                    )
                    workflow_report_obj = getattr(item, "workflow_report", None)

                # Align the manifest status with the same final authority as
                # the Tracker status.  A false workflow return must not leave
                # an item looking successful merely because an older phase
                # had a passing gate.
                if not job_successful and item.status != "failed":
                    preserved_watchdog_failure = bool(
                        watchdog_failure
                        and getattr(item, "failure_code", None) == "watchdog_timeout"
                    )
                    self.processing_manager._record_quality_failure(
                        item,
                        getattr(item, "failure_code", None) or "workflow_stopped",
                        getattr(item, "error_message", None) or "Workflow returned failure before finalization.",
                        evidence={
                            "jobSuccessful": False,
                            "watchdog": preserved_watchdog_failure,
                        },
                        retryable=(
                            bool(watchdog_failure.get("retryable", True))
                            if preserved_watchdog_failure
                            else False
                        ),
                    )
                    workflow_report_obj = getattr(item, "workflow_report", None)

                item.end_time = datetime.now()
                try:
                    manifest_data = self.processing_manager._generate_manifest_for_item(item)
                except Exception as manifest_error:
                    self.logger.warning(
                        "⚠️ Manifest generation failed for %s; keeping core workflow result: %s",
                        queue_id,
                        manifest_error,
                        exc_info=True,
                    )
                if manifest_data is None:
                    self.logger.warning(
                        "⚠️ Manifest generation returned no data for %s; keeping core workflow result.",
                        queue_id,
                    )

                try:
                    video_files_payload = self._organize_output_files(item.output_files)
                except Exception as output_error:
                    self.logger.warning(
                        "⚠️ Output organization failed for %s; sending empty video_files payload: %s",
                        queue_id,
                        output_error,
                        exc_info=True,
                    )

            # Dọn dẹp các component của job hiện tại trước khi chụp log cuối.
            final_cleanup_abort_event = threading.Event()
            watchdog_cleanup_attempted = bool(
                isinstance(getattr(item, "watchdog_evidence", None), dict)
                and any(
                    isinstance(entry, dict)
                    for entry in item.watchdog_evidence.get("cleanup", [])
                )
            )
            if not watchdog_cleanup_attempted:
                self._enter_cleanup_mode(final_cleanup_abort_event)
                try:
                    if hasattr(self, 'obs_controller') and self.obs_controller:
                        self.obs_controller.comprehensive_obs_cleanup()
                    if hasattr(self, 'game_player') and self.game_player:
                        self.game_player.close_game_window()
                except Exception as cleanup_error:
                    self.logger.error(f"Error during post-job cleanup for {queue_id}: {cleanup_error}")
                finally:
                    self._exit_cleanup_mode()
            else:
                self.logger.info(
                    "✅ Watchdog recovery cleanup was already attempted for queue %s; "
                    "skipping unbounded duplicate physical cleanup (status=%s).",
                    queue_id,
                    item.watchdog_evidence.get("cleanup", [{}])[-1].get("status")
                    if item and item.watchdog_evidence.get("cleanup")
                    else "unknown",
                )
            job_log_capture.stop()

            # The final status is calculated only after all evidence-producing
            # steps above have had a chance to record their own failures.
            if not attempt_bound:
                # The queue attempt was never accepted by Tracker, so do not
                # emit a terminal callback that Tracker cannot bind.
                unbind_report = getattr(self.api_client, "unbind_job_report", None)
                if callable(unbind_report):
                    unbind_report(queue_id)
                duration = time.monotonic() - start_time
                self.performance_analyzer.record_job_completion(queue_id, False, {
                    "report_id": report_id,
                    "attempt_nonce": attempt_nonce,
                    "duration_seconds": duration,
                    "attempt_bound": False,
                })
                self._remove_watchdog_state(queue_id)
                return False

            if item:
                workflow_report_obj = getattr(item, "workflow_report", None)
                quality_passed = (
                    workflow_report_obj is not None
                    and getattr(getattr(workflow_report_obj, "quality_gate", None), "passed", False) is True
                )
                final_status = "completed" if job_successful and quality_passed and item.status != "failed" else "failed"
                final_success = final_status == "completed"
                if final_status != "completed":
                    # Failed attempts remain on disk for diagnostics, but are
                    # never published as Tracker video_files.
                    video_files_payload = self._organize_output_files([])
                self.logger.info(f"Finalizing job {queue_id} with status: {final_status.upper()}")

                diagnostic_report = build_tracker_diagnostic_report(
                    job,
                    item,
                    workflow_report_obj,
                    manifest_data=manifest_data,
                    log_snapshot=self._snapshot_job_log(job_log_capture),
                    output_files_payload=video_files_payload,
                    report_id=report_id,
                    attempt_nonce=attempt_nonce,
                    max_attempts=getattr(getattr(self.processing_manager, "config", None), "max_retries", None),
                    terminal_status=final_status,
                    terminal_error=terminal_error,
                )
                terminal_callback_ok = bool(self.api_client.update_job_status(
                    queue_id,
                    status=final_status,
                    details={
                        "stage": diagnostic_report["summary"].get("blocking_stage"),
                        "failure_code": diagnostic_report["summary"].get("failure_code"),
                        "retryable": diagnostic_report["summary"].get("retryable"),
                        "report_id": report_id,
                    },
                    video_files=video_files_payload,
                    error_log=(
                        item.error_message
                        or diagnostic_report["summary"].get("root_cause")
                        or None
                    ),
                    diagnostic_report=diagnostic_report,
                    manifest_ref=diagnostic_report["outputs"].get("manifest_ref"),
                    log_ref=diagnostic_report["logs"],
                    report_id=report_id,
                    attempt_nonce=attempt_nonce,
                    final=True,
                    sequence=None,
                ))
                if not terminal_callback_ok:
                    self.logger.error(
                        "❌ Tracker terminal callback not accepted for queue %s; "
                        "compact payload remains in outbox for replay.",
                        queue_id,
                    )
            else:
                final_status = "failed"
                final_success = False
                diagnostic_report = build_tracker_diagnostic_report(
                    job,
                    None,
                    None,
                    log_snapshot=self._snapshot_job_log(job_log_capture),
                    report_id=report_id,
                    attempt_nonce=attempt_nonce,
                    terminal_status=final_status,
                    terminal_error=terminal_error or "Job ended before a ProcessingItem was created.",
                )
                terminal_callback_ok = bool(self.api_client.update_job_status(
                    queue_id,
                    status=final_status,
                    details={
                        "stage": diagnostic_report["summary"].get("blocking_stage"),
                        "failure_code": diagnostic_report["summary"].get("failure_code"),
                        "retryable": diagnostic_report["summary"].get("retryable"),
                        "report_id": report_id,
                    },
                    error_log=diagnostic_report["summary"].get("root_cause"),
                    diagnostic_report=diagnostic_report,
                    manifest_ref=diagnostic_report["outputs"].get("manifest_ref"),
                    log_ref=diagnostic_report["logs"],
                    report_id=report_id,
                    attempt_nonce=attempt_nonce,
                    final=True,
                    sequence=None,
                ))
                if not terminal_callback_ok:
                    self.logger.error(
                        "❌ Tracker terminal callback not accepted for queue %s; "
                        "compact payload remains in outbox for replay.",
                        queue_id,
                    )

            unbind_report = getattr(self.api_client, "unbind_job_report", None)
            if callable(unbind_report):
                unbind_report(queue_id)

            # A local workflow result is not a delivered terminal result.  Do
            # not let the Creator loop print COMPLETED while Tracker still
            # needs the compact outbox replay.
            if not terminal_callback_ok:
                final_success = False

            duration = time.monotonic() - start_time
            self.performance_analyzer.record_job_completion(queue_id, final_success, {
                "report_id": report_id,
                "attempt_nonce": attempt_nonce,
                "duration_seconds": duration,
                "terminal_callback_ok": terminal_callback_ok,
            })
            self._remove_watchdog_state(queue_id)
        # The orchestration result must agree with the status sent to the
        # tracker.  A workflow return value alone is not sufficient: the
        # shared quality gate is the final authority.
        return final_success

    def _check_for_manual_json_override(self, slug: str, job_id: int) -> Tuple[Optional[Dict], Optional[str]]:
        """
        Check if a manual JSON file exists for this job in the manual_jsons directory.
        Checks by Job ID first, then by Slug.
        """
        self._manual_json_failure = None
        try:
            files_to_check = []
            if job_id:
                files_to_check.append(f"{job_id}.json")
            if slug:
                files_to_check.append(f"{slug}.json")
            
            for filename in files_to_check:
                filepath = os.path.join(MANUAL_JSON_DIR, filename)
                if os.path.exists(filepath):
                    self.logger.info(f"🔍 Checking manual JSON: {filepath} ... FOUND!")
                    try:
                        with open(filepath, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                        layout_quality = validate_playable_layout(data)
                        if layout_quality.passed:
                            self._manual_json_failure = None
                            return data, filepath
                        else:
                            self._manual_json_failure = {
                                "path": filepath,
                                "failure_code": layout_quality.failure_code or "manual_json_invalid",
                                "message": layout_quality.message,
                                "evidence": layout_quality.evidence,
                            }
                            self.logger.warning(
                                f"⚠️ Manual JSON {filename} rejected: {layout_quality.failure_code}"
                            )
                    except Exception as e:
                        self.logger.error(f"❌ Error reading manual JSON {filename}: {e}")
                        self._manual_json_failure = {
                            "path": filepath,
                            "failure_code": "manual_json_invalid",
                            "message": f"Manual JSON could not be read: {e}",
                            "evidence": {"error": str(e)},
                        }
                        return None, None
            
            return None, None
        except Exception as e:
            self.logger.error(f"❌ Error in override check: {e}")
            self._manual_json_failure = {
                "failure_code": "manual_json_invalid",
                "message": str(e),
            }
            return None, None

    def _build_manual_profile_failure_details(self, url: str, error_message: Optional[str] = None) -> Dict:
        url = (url or "").strip()
        details = {
            "manualProfileRequired": True,
            "sourceUrl": url,
            "urlKey": url_key_from_url(url),
            "suggestedManualProfilePath": suggested_profile_path_for_url(url),
        }
        if error_message:
            details["reason"] = error_message
        return details

    def _process_with_smart_retry(self, item, queue_id: int) -> bool:
        """Process with smart retry for individual steps - PHASE 2"""
        try:
            # --- STEP 0: CHECK MANUAL OVERRIDE ---
            manual_data, manual_path = self._check_for_manual_json_override(item.project_slug, queue_id)

            if getattr(self, "_manual_json_failure", None):
                failure = self._manual_json_failure
                item.json_generation_status = "failure"
                self.processing_manager._record_quality_failure(
                    item,
                    failure.get("failure_code", "manual_json_invalid"),
                    failure.get("message", "Manual JSON failed static playability validation."),
                    evidence={
                        "manualJson": failure,
                        "path": failure.get("path"),
                    },
                )
                self.api_client.update_job_progress(
                    queue_id,
                    "json_generation",
                    "manual_override",
                    0.0,
                    "failed",
                    metadata=failure,
                    error_details=item.error_message,
                )
                return False
            
            if manual_data:
                print(f"🟢 MANUAL OVERRIDE DETECTED! Using local JSON file: {manual_path}")
                item.json_data = manual_data
                item.json_path = manual_path
                item.json_generation_status = "success"
                # Đánh dấu là JSON đã có sẵn để bỏ qua bước sinh tự động
                self.api_client.update_job_progress(queue_id, 'json_generation', 'manual_override', 100.0, 'completed',
                    metadata={'override_file': manual_path})
            
            # --- Step 1: JSON Generation (ONLY if not overridden) ---
            if item.source_url and not item.json_data:
                success = self._execute_with_retry(
                    queue_id, 'json_generation',
                    lambda: self.processing_manager._generate_json_for_item(item),
                    context={'url': item.source_url, 'item': item}
                )
                
                if not success:
                    return False
                self._touch_watchdog(queue_id, "json_generation", "completed")
            
            # --- Step 2: Video Processing ---
            success = self._execute_with_retry(
                queue_id, 'video_processing',
                lambda: self.processing_manager._process_video_for_item(item),
                context={'mode': item.mode, 'project_slug': item.project_slug, 'item': item}
            )
            
            return success
            
        except Exception as e:
            print(f"❌ Smart retry processing error: {e}")
            self.processing_manager._record_quality_failure(
                item,
                getattr(item, "failure_code", None) or "runtime_verification_unknown",
                f"Smart retry processing error: {e}",
                evidence={"error": str(e)},
                status="UNKNOWN",
                retryable=True,
            )
            return False

    def _execute_with_retry(self, queue_id: int, operation_name: str, operation_function: Callable, 
                        context: Optional[dict] = None, max_step_retries: int = 3) -> bool:
        """Execute operation with smart retry logic - PHASE 2 (API-based)"""

        def record_retry_evidence(
            attempt: int,
            *,
            error: str,
            exception: bool = False,
            status: Optional[str] = None,
            retryable: Optional[bool] = None,
            started_at: Optional[datetime] = None,
        ) -> None:
            failed_item = (context or {}).get('item')
            report = getattr(failed_item, 'workflow_report', None)
            if report is None:
                return
            ended_at = datetime.now()
            duration_ms = (
                max(0, int((ended_at - started_at).total_seconds() * 1000))
                if isinstance(started_at, datetime)
                else None
            )
            attempts = report.evidence.setdefault('retryAttempts', [])
            attempts.append({
                'operation': operation_name,
                'attempt': attempt,
                'error': error,
                'exception': exception,
                'failureCode': getattr(failed_item, 'failure_code', None),
                'failure_code': getattr(failed_item, 'failure_code', None),
                'status': status or ('FAIL' if exception else 'UNKNOWN'),
                'retryable': retryable,
                'started_at': started_at.isoformat() if isinstance(started_at, datetime) else None,
                'ended_at': ended_at.isoformat(),
                'duration_ms': duration_ms,
            })
        
        for step_attempt in range(1, max_step_retries + 1):
            # A watchdog/manual stop must prevent the smart step retry layer
            # from starting a fresh drag/showcase/remix after production has
            # been cancelled.  Cleanup uses a separate event and does not
            # enter this function.
            if getattr(getattr(self, "processing_manager", None), "stop_event", None) is not None \
                    and self.processing_manager.stop_event.is_set():
                return False
            attempt_started_at = datetime.now()
            try:
                print(f"🔧 {operation_name} - Step attempt {step_attempt}/{max_step_retries}")
                
                # Execute the operation
                result = operation_function()
                
                if result:
                    record_retry_evidence(
                        step_attempt,
                        error=f"{operation_name} succeeded",
                        status="PASS",
                        retryable=False,
                        started_at=attempt_started_at,
                    )
                    print(f"✅ {operation_name} succeeded on attempt {step_attempt}")
                    return True
                else:
                    failed_item = (context or {}).get('item')
                    failure_code = getattr(failed_item, 'failure_code', None)
                    failed_gate = getattr(getattr(failed_item, 'workflow_report', None), 'quality_gate', None)
                    failure_evidence = getattr(failed_gate, "evidence", {}) or {}
                    failure_retryable = (
                        True
                        if not failure_code
                        else failure_is_retryable(failure_code, failure_evidence)
                    )
                    error_msg = (
                        f"{operation_name} failed with {failure_code}"
                        if failure_code
                        else f"{operation_name} returned False"
                    )
                    print(f"❌ {operation_name} failed on attempt {step_attempt}: {error_msg}")
                    record_retry_evidence(
                        step_attempt,
                        error=error_msg,
                        status="FAIL",
                        retryable=failure_retryable,
                        started_at=attempt_started_at,
                    )

                    if (
                        failure_code
                        and not failure_retryable
                    ) or (
                        failed_gate is not None and not getattr(failed_gate, "retryable", True)
                    ):
                        self.logger.error(
                            "🚫 Deterministic quality failure is not retryable: %s",
                            failure_code,
                        )
                        self.api_client.update_job_progress(
                            queue_id,
                            operation_name,
                            'quality_gate_failed',
                            0.0,
                            'failed',
                            error_details=error_msg,
                            metadata={'failure_code': failure_code},
                        )
                        return False
                    
                    # Prepare context with attempt number
                    retry_context = {**(context or {}), 'operation': operation_name, 'attempt': step_attempt}

                    if getattr(getattr(self, "processing_manager", None), "stop_event", None) is not None \
                            and self.processing_manager.stop_event.is_set():
                        return False
                    
                    # Use smart retry logic
                    retry_decision = self.retry_manager.should_retry(queue_id, error_msg, retry_context)
                    
                    if not retry_decision['should_retry']:
                        print(f"🚫 Not retrying {operation_name}: {retry_decision['reason']}")

                        failed_item = (context or {}).get('item')
                        
                        # Handle special cases
                        if retry_decision.get('requires_manual'):
                            if failed_item is not None:
                                self.processing_manager._record_quality_failure(
                                    failed_item,
                                    "manual_json_invalid"
                                    if operation_name == "json_generation"
                                    else (getattr(failed_item, "failure_code", None) or "workflow_stopped"),
                                    f"{operation_name} requires manual intervention: {error_msg}",
                                    evidence={"retryDecision": retry_decision},
                                    retryable=False,
                                )
                            details = None
                            if operation_name == 'json_generation':
                                details = self._build_manual_profile_failure_details((context or {}).get('url', ''), error_msg)
                            self.api_client.update_job_progress(
                                queue_id,
                                operation_name,
                                "manual_intervention",
                                0.0,
                                "failed",
                                metadata={
                                    "final": False,
                                    "details": details,
                                    "failure_code": getattr(failed_item, "failure_code", None)
                                    if failed_item is not None else "manual_json_invalid",
                                },
                                error_details=f"{operation_name} requires manual intervention: {error_msg}",
                            )
                        elif retry_decision.get('should_skip'):
                            if failed_item is not None:
                                self.processing_manager._record_quality_failure(
                                    failed_item,
                                    "skipped_by_policy",
                                    f"{operation_name} was skipped by retry policy: {error_msg}",
                                    evidence={"retryDecision": retry_decision},
                                    retryable=False,
                                )
                            details = None
                            if operation_name == 'json_generation':
                                details = self._build_manual_profile_failure_details((context or {}).get('url', ''), error_msg)
                            self.api_client.update_job_progress(
                                queue_id,
                                operation_name,
                                "skipped_by_policy",
                                0.0,
                                "failed",
                                metadata={
                                    "final": False,
                                    "details": details,
                                    "failure_code": "skipped_by_policy",
                                },
                                error_details=f"{operation_name} should be skipped: {error_msg}",
                            )
                        
                        return False
                    
                    # Execute retry if this isn't the last attempt
                    if step_attempt < max_step_retries:
                        retry_success = self.retry_manager.execute_retry(
                            queue_id, retry_decision, operation_function
                        )
                        
                        if retry_success:
                            record_retry_evidence(
                                retry_decision.get("retry_attempt", step_attempt + 1),
                                error=f"{operation_name} retry succeeded",
                                status="PASS",
                                retryable=False,
                            )
                            print(f"✅ {operation_name} retry succeeded")
                            return True
                        else:
                            record_retry_evidence(
                                retry_decision.get("retry_attempt", step_attempt + 1),
                                error=f"{operation_name} retry failed",
                                status="FAIL",
                                retryable=True,
                            )
                            print(f"❌ {operation_name} retry failed, will try again")
                            continue
                    else:
                        print(f"❌ {operation_name} exhausted all {max_step_retries} attempts")
                        return False
                        
            except Exception as e:
                failed_item = (context or {}).get('item')
                failure_code = getattr(failed_item, 'failure_code', None)
                failed_gate = getattr(getattr(failed_item, 'workflow_report', None), 'quality_gate', None)
                failure_evidence = getattr(failed_gate, "evidence", {}) or {}
                failure_retryable = (
                    True
                    if not failure_code
                    else failure_is_retryable(failure_code, failure_evidence)
                )
                error_msg = f"{operation_name} exception: {str(e)}"
                print(f"❌ {operation_name} exception on attempt {step_attempt}: {e}")
                record_retry_evidence(
                    step_attempt,
                    error=error_msg,
                    exception=True,
                    status="FAIL",
                    retryable=failure_retryable,
                    started_at=attempt_started_at,
                )

                if (
                    failure_code
                    and not failure_retryable
                ) or (
                    failed_gate is not None and not getattr(failed_gate, "retryable", True)
                ):
                    self.logger.error(
                        "🚫 Deterministic quality exception is not retryable: %s",
                        failure_code,
                    )
                    return False
                
                # Prepare context with attempt number and exception flag
                if getattr(getattr(self, "processing_manager", None), "stop_event", None) is not None \
                        and self.processing_manager.stop_event.is_set():
                    return False
                retry_context = {
                    **(context or {}), 
                    'operation': operation_name, 
                    'attempt': step_attempt, 
                    'exception': True
                }
                
                # Use smart retry for exceptions too
                retry_decision = self.retry_manager.should_retry(queue_id, error_msg, retry_context)
                
                if not retry_decision['should_retry'] or step_attempt == max_step_retries:
                    print(f"🚫 Not retrying {operation_name} exception: {retry_decision.get('reason', 'Max attempts reached')}")
                    
                    # Final failure
                    self.api_client.update_job_progress(
                        queue_id, operation_name, 'exception', 0.0, 'failed',
                        metadata=self._build_manual_profile_failure_details((context or {}).get('url', ''), error_msg) if operation_name == 'json_generation' else None,
                        error_details=error_msg
                    )
                    # 🆕 PHASE 4: Track performance
                    self.performance_analyzer.record_job_progress(queue_id, 'retry', 0.0)                    
                    return False
                
                # Execute retry for exception
                if step_attempt < max_step_retries:
                    print(f"🔄 Retrying {operation_name} after exception...")
                    retry_success = self.retry_manager.execute_retry(
                        queue_id, retry_decision, operation_function
                    )
                    
                    if retry_success:
                        record_retry_evidence(
                            retry_decision.get("retry_attempt", step_attempt + 1),
                            error=f"{operation_name} retry succeeded after exception",
                            exception=True,
                            status="PASS",
                            retryable=False,
                        )
                        return True
                    record_retry_evidence(
                        retry_decision.get("retry_attempt", step_attempt + 1),
                        error=f"{operation_name} retry failed after exception",
                        exception=True,
                        status="FAIL",
                        retryable=True,
                    )
        
        return False
    
    def _convert_job_to_processing_item(self, job: Dict):
        """[FIXED] Convert Tracker job to ProcessingItem using URL for slug."""
        from sprunki_ultimate_master import ProcessingItem
        
        game_url = job.get('mod_game_url')
        
        # [FIX] Đảm bảo game_url luôn là một chuỗi để tránh lỗi type hint.
        # Nếu không có URL, một slug dựa trên thời gian sẽ được tạo ra bên trong _create_slug_from_url.
        url_for_slug = game_url or ""

        requested_mode = job.get('production_mode')
        effective_mode = normalize_production_mode(requested_mode, allow_skip=True)
        item = ProcessingItem(
            source_url=game_url,
            mode=effective_mode,
            requested_production_mode=requested_mode,
            # SỬA ĐỔI QUAN TRỌNG: Gọi hàm mới và truyền URL vào
            project_slug=self._create_slug_from_url(url_for_slug)
        )
        return item
    
    def _create_slug_from_url(self, url: str) -> str:
        """
        [NEW & FIXED] Creates a unique URL slug by combining author and project name.
        Example: /@author/project-name -> author-project-name
        This ensures consistency with the GUI mode.
        """
        try:
            from urllib.parse import urlparse
            import re
            
            if not url:
                return f"video-job-{int(time.time())}"

            parsed_url = urlparse(url)
            path_parts = [part for part in parsed_url.path.strip('/').split('/') if part]
            
            slug_base = ""
            if len(path_parts) >= 2 and path_parts[0].startswith('@'):
                author = path_parts[0][1:]
                project_name = path_parts[1]
                slug_base = f"{author}_{project_name}"
            elif len(path_parts) > 0:
                slug_base = "_".join(path_parts)
            else:
                domain = parsed_url.netloc.lower().replace('www.', '')
                slug_base = domain.split('.')[0]
            
            slug = re.sub(r'[^a-zA-Z0-9\-_]', '-', slug_base)
            slug = re.sub(r'[-_]+', '-', slug)
            slug = slug.strip('-')
            slug = slug[:70]
            
            return slug or f"video-job-{int(time.time())}"
            
        except Exception:
            return f"video-job-{int(time.time())}"
    
    def _generate_json_and_process(self, item, queue_id: int) -> bool:
        """Generate JSON then process video"""
        try:
            self.api_client.update_job_status(queue_id, 'processing', {
                'stage': 'json_generation'
            })
            
            success = self.processing_manager._generate_json_for_item(item)
            
            if not success:
                self.api_client.update_job_progress(
                    queue_id,
                    "json_generation",
                    "failed",
                    0.0,
                    "failed",
                    metadata={
                        "final": False,
                        "details": self._build_manual_profile_failure_details(
                            item.source_url or "", "JSON generation failed"
                        ),
                    },
                    error_details="JSON generation failed",
                )
                return False
            
            return self._process_video_for_item(item, queue_id)
            
        except Exception as e:
            print(f"❌ JSON generation error: {e}")
            return False
    
    def _process_video_for_item(self, item, queue_id: int) -> bool:
            """
            [REVISED & FIXED] Process video with detailed progress reporting and correct
            stop_event propagation.
            """
            try:
                item.mode = normalize_production_mode(item.mode, allow_skip=True)
                service_logger = getattr(self, "logger", None)
                if service_logger is not None:
                    service_logger.info(
                        "🎛️ Effective production mode for %s: %s (requested=%s)",
                        item.project_slug,
                        item.mode,
                        getattr(item, "requested_production_mode", None),
                    )
                if item.mode == "skip":
                    self.processing_manager._record_quality_failure(
                        item,
                        "skipped_by_policy",
                        "Item was skipped by policy and did not run gameplay verification.",
                    )
                    self.api_client.update_job_progress(
                        queue_id,
                        "video_processing",
                        "skipped_by_policy",
                        0.0,
                        "failed",
                        error_details=item.error_message,
                    )
                    return False

                # === START: PRE-FLIGHT CHECK ===
                if not item.json_data:
                    error_msg = "Cannot process video: item.json_data is missing."
                    self.logger.error(error_msg)
                    self.processing_manager._record_quality_failure(
                        item,
                        "manual_json_invalid",
                        error_msg,
                    )
                    self.api_client.update_job_progress(
                        queue_id,
                        "preflight",
                        "missing_json",
                        0.0,
                        "failed",
                        metadata={"final": False, "failure_code": "manual_json_invalid"},
                        error_details=error_msg,
                    )
                    return False
                # === END: PRE-FLIGHT CHECK ===

                layout_quality = validate_playable_layout(item.json_data)
                if not layout_quality.passed:
                    self.processing_manager._record_quality_failure(
                        item,
                        layout_quality.failure_code or "manual_json_invalid",
                        layout_quality.message,
                        evidence=layout_quality.evidence,
                    )
                    return False

                # --- Initialize progress tracking ---
                self.api_client.update_job_progress(
                    queue_id, 'initialization', 'starting', 0.0, 'active',
                    metadata={'item_mode': item.mode, 'project_slug': item.project_slug}
                )
                self._touch_watchdog(queue_id, "initialization", "starting")
                self.performance_analyzer.record_job_progress(queue_id, 'initialization', 0.0)
                
                self.logger.info(f"🎥 Processing video for item: {item.project_slug} (Mode: {item.mode})")

                # --- Component initialization (FIXED CALL) ---
                self.api_client.update_job_progress(
                    queue_id, 'initialization', 'components', 10.0, 'active'
                )
                self._touch_watchdog(queue_id, "initialization", "components")
                
                # === BẮT ĐẦU SỬA ĐỔI ===
                # Lấy cờ stop của job hiện tại từ processing_manager
                job_stop_event = self.processing_manager.stop_event 

                # Truyền cờ stop vào hàm _initialize_components
                if not self._initialize_components(item.json_data, job_stop_event):
                # === KẾT THÚC SỬA ĐỔI ===
                    error_details = "Failed to initialize components (GamePlayer/OBS)."
                    self.api_client.update_job_progress(
                        queue_id, 'initialization', 'components', 0.0, 'failed',
                        error_details=error_details
                    )
                    self.processing_manager._record_quality_failure(
                        item,
                        getattr(item, "failure_code", None) or "runtime_verification_unknown",
                        error_details,
                        status="UNKNOWN",
                        retryable=True,
                    )
                    return False
                
                # --- OBS connection test ---
                self.api_client.update_job_progress(
                    queue_id, 'initialization', 'obs_test', 25.0, 'active'
                )

                if hasattr(self, 'obs_controller') and self.obs_controller:
                    self.logger.info("🔍 Testing OBS connection...")
                    test_ws = self.obs_controller.connect_obs_fast()
                    if test_ws:
                        self.logger.info("✅ OBS connection test successful")
                        self.api_client.update_job_progress(queue_id, 'initialization', 'obs_test', 40.0, 'completed')
                        self._touch_watchdog(queue_id, "initialization", "obs_ready")
                        test_ws.close()
                    else:
                        error_details = "OBS connection test failed before video processing."
                        self.logger.error(f"❌ {error_details}")
                        self.api_client.update_job_progress(
                            queue_id, 'initialization', 'obs_test', 0.0, 'failed',
                            error_details=error_details
                        )
                        self.processing_manager._record_quality_failure(
                            item,
                            "runtime_verification_unknown",
                            error_details,
                            status="UNKNOWN",
                            retryable=True,
                        )
                        return False
                
                # --- Start main processing ---
                self.api_client.update_job_progress(
                    queue_id, 'video_processing', 'starting', 50.0, 'active'
                )
                self._touch_watchdog(queue_id, "video_processing", "starting")

                success = self._process_with_progress_callbacks(item, queue_id)
                
                # --- Early exit on core workflow failure ---
                if not success:
                    self.logger.error(f"❌ Video processing workflow failed for item {item.project_slug} (mode: {item.mode}). Skipping file validation.")
                    self.processing_manager._record_quality_failure(
                        item,
                        getattr(item, "failure_code", None) or "placement_not_verified",
                        getattr(item, "error_message", None) or "Core workflow failed.",
                    )
                    return False

                project_folder = getattr(self.obs_controller, 'current_project_folder', None)
                if item.mode != "skip":
                    try:
                        identity_ok = bool(
                            item.json_data
                            and project_folder
                            and self.processing_manager._run_recorded_identity_resolver(item, project_folder)
                        )
                        if not identity_ok:
                            self.logger.warning(
                                "⚠️ Recorded identity is non-blocking for %s: status=%s reason=%s",
                                item.project_slug,
                                item.recorded_identity_status,
                                item.recorded_identity_reason,
                            )
                    except Exception as identity_error:
                        # Identity is evidence/tagging only.  Preserve the
                        # real resolver state when available and never turn a
                        # resolver exception into a core workflow failure.
                        item.recorded_identity_status = "unavailable"
                        item.recorded_identity_reason = str(identity_error)
                        self.logger.warning(
                            "⚠️ Recorded identity resolver failed non-blockingly for %s: %s",
                            item.project_slug,
                            identity_error,
                        )

                if not self.processing_manager._finalize_item_quality(item):
                    self.logger.error(
                        "❌ Shared quality gate failed for %s: %s",
                        item.project_slug,
                        item.failure_code,
                    )
                    return False
                
                # Output organization is transport/observability only.  It
                # must not decide completion because Syncthing or a downstream
                # consumer may move files before this code runs.
                self.logger.info(
                    "✅ Core gameplay workflow passed for %s. Collecting optional output transport metadata.",
                    item.project_slug,
                )
                video_files = {}
                validation_summary = {}
                try:
                    self.api_client.update_job_progress(
                        queue_id,
                        'validation',
                        'transport_metadata',
                        95.0,
                        'active',
                        metadata={'output_files_count': len(item.output_files)},
                    )
                    video_files = self._organize_output_files(item.output_files) or {}
                    validation_summary = video_files.get('validation_summary', {}) if isinstance(video_files, dict) else {}
                except Exception as output_error:
                    self.logger.warning(
                        "⚠️ Optional output transport metadata failed for %s; continuing with empty video_files: %s",
                        item.project_slug,
                        output_error,
                        exc_info=True,
                    )
                    video_files = {}
                    validation_summary = {}

                item.status = "completed"
                item.end_time = datetime.now()
                self.api_client.update_job_progress(
                    queue_id,
                    'completion',
                    'finalizing',
                    100.0,
                    'completed',
                    metadata={
                        'validation_summary': validation_summary,
                        'total_valid_files': validation_summary.get('valid_files', 0),
                        'quality_gate_only': True,
                    },
                )
                self._touch_watchdog(queue_id, "completion", "workflow_completed")
                return True
                        
            except Exception as e:
                error_msg = f"Critical exception in _process_video_for_item: {str(e)}"
                self.logger.error(error_msg, exc_info=True)
                
                self.api_client.update_job_progress(
                    queue_id, 'error', 'exception', 0.0, 'failed',
                    error_details=error_msg
                )
                self.processing_manager._record_quality_failure(
                    item,
                    getattr(item, "failure_code", None) or "runtime_verification_unknown",
                    error_msg,
                    evidence={"error": error_msg},
                    status="UNKNOWN",
                    retryable=True,
                )
                return False

    def _process_with_progress_callbacks(self, item, queue_id: int) -> bool:
        """Enhanced processing with progress callbacks - PHASE 1"""
        try:
            mode = normalize_production_mode(item.mode, allow_skip=True)
            item.mode = mode

            if mode == "skip":
                self.processing_manager._record_quality_failure(
                    item,
                    "skipped_by_policy",
                    "Item was skipped by policy and did not run gameplay verification.",
                )
                self.api_client.update_job_progress(
                    queue_id,
                    'video_processing',
                    'skipped_by_policy',
                    0.0,
                    'failed',
                    error_details=item.error_message,
                )
                return False

            if mode not in CREATOR_PRODUCTION_MODES:
                error_details = f"Unsupported production mode: {mode}"
                self.processing_manager._record_quality_failure(
                    item,
                    "unsupported_production_mode",
                    error_details,
                    evidence={
                        "requestedMode": getattr(item, "requested_production_mode", None),
                        "effectiveMode": mode,
                    },
                    retryable=False,
                )
                self.api_client.update_job_progress(
                    queue_id,
                    'video_processing',
                    'unsupported_mode',
                    0.0,
                    'failed',
                    error_details=error_details,
                )
                return False
            
            if mode == "master":
                self._touch_watchdog(queue_id, "video_processing", "master_mode")
                self.api_client.update_job_progress(
                    queue_id, 'video_processing', 'master_mode', 60.0, 'active'
                )

                # 🆕 PHASE 4: Track performance
                self.performance_analyzer.record_job_progress(queue_id, 'retry', 0.0)

                success = self.processing_manager._process_master_mode(item)
                
            elif mode == "individual":
                self._touch_watchdog(queue_id, "video_processing", "individual_mode")
                self.api_client.update_job_progress(
                    queue_id, 'video_processing', 'individual_mode', 60.0, 'active'
                )

                # 🆕 PHASE 4: Track performance
                self.performance_analyzer.record_job_progress(queue_id, 'retry', 0.0)

                success = self.processing_manager._process_individual_mode(item)
                
            elif mode == "both":
                self._touch_watchdog(queue_id, "video_processing", "both_mode")
                # Master phase
                self.api_client.update_job_progress(
                    queue_id, 'video_processing', 'both_master_phase', 60.0, 'active'
                )

                # 🆕 PHASE 4: Track performance
                self.performance_analyzer.record_job_progress(queue_id, 'retry', 0.0)

                # Note: Individual phase progress will be updated inside _process_both_mode_enhanced
                self.api_client.update_job_progress(
                    queue_id, 'video_processing', 'both_individual_phase', 80.0, 'active'
                )

                # 🆕 PHASE 4: Track performance
                self.performance_analyzer.record_job_progress(queue_id, 'retry', 0.0)

                success = self.processing_manager._process_both_mode_enhanced(item)
            else:
                # Unknown mode
                self.api_client.update_job_progress(
                    queue_id, 'video_processing', 'unknown_mode', 0.0, 'failed',
                    error_details=f"Unknown processing mode: {mode}"
                )

                # 🆕 PHASE 4: Track performance
                self.performance_analyzer.record_job_progress(queue_id, 'retry', 0.0)

                return False
            
            if success:
                self.api_client.update_job_progress(
                    queue_id, 'video_processing', 'completed', 95.0, 'completed'
                )

                # 🆕 PHASE 4: Track performance
                self.performance_analyzer.record_job_progress(queue_id, 'retry', 0.0)

            else:
                self.api_client.update_job_progress(
                    queue_id, 'video_processing', 'failed', 0.0, 'failed',
                    error_details=f"Mode {mode} processing failed"
                )

                # 🆕 PHASE 4: Track performance
                self.performance_analyzer.record_job_progress(queue_id, 'retry', 0.0)

            return success
            
        except Exception as e:
            print(f"❌ Process with callbacks error: {e}")
            self.api_client.update_job_progress(
                queue_id, 'video_processing', 'exception', 0.0, 'failed',
                error_details=f"Processing callback exception: {str(e)}"
            )

            # 🆕 PHASE 4: Track performance
            self.performance_analyzer.record_job_progress(queue_id, 'retry', 0.0)

            return False

    def _organize_output_files(self, output_files: list) -> dict:
        """
        [FIXED] Organize and validate output files - PHASE 3 Enhanced.
        Ensures consistent structure and handles single-file cases correctly.
        """
        self.logger.info(f"📁 Organizing and validating {len(output_files)} output files...")
        
        # --- START OF FIX ---
        # Luôn khởi tạo cấu trúc đầy đủ, ngay cả khi không có file nào.
        final_payload = {
            'master': [],
            'individual': [],
            'validation_summary': {
                'total_files': 0, 'valid_files': 0, 'invalid_files': 0,
                'success_rate': 0.0, 'master_files': 0, 'individual_files': 0
            }
        }
        # --- END OF FIX ---
        
        if not output_files:
            self.logger.warning("⚠️ No output files to organize or validate.")
            return final_payload # Trả về cấu trúc rỗng nhưng hợp lệ
        
        master_files = []
        individual_files = []
        
        for file_path in output_files:
            filename = os.path.basename(file_path).lower()
            
            # Cải thiện logic phân loại để bắt cả "showcase"
            if 'showcase' in filename or 'master' in filename:
                master_files.append(file_path)
            elif any(keyword in filename for keyword in ['individual', 'n-', 'h-', 'remix']):
                individual_files.append(file_path)
            else:
                # Nếu không xác định được, giả sử là file individual
                self.logger.warning(f"Unknown file type for '{filename}', classifying as individual.")
                individual_files.append(file_path)
        
        # Validate master files
        if master_files:
            self.logger.info(f"🎬 Validating {len(master_files)} master file(s)...")
            master_validation = self.file_validator.validate_batch(master_files, 'video')
            final_payload['master'] = self._process_validation_results(master_validation)
            self.logger.info(f"   Master validation: {master_validation['summary']['valid_files']}/{master_validation['summary']['total_files']} valid")
        
        # Validate individual files
        if individual_files:
            self.logger.info(f"🎭 Validating {len(individual_files)} individual file(s)...")
            individual_validation = self.file_validator.validate_batch(individual_files, 'video')
            final_payload['individual'] = self._process_validation_results(individual_validation)
            self.logger.info(f"   Individual validation: {individual_validation['summary']['valid_files']}/{individual_validation['summary']['total_files']} valid")
        
        # Create combined validation summary
        total_files = len(master_files) + len(individual_files)
        # Lấy số file hợp lệ từ payload đã xử lý
        total_valid = len(final_payload['master']) + len(final_payload['individual'])
        
        final_payload['validation_summary'] = {
            'total_files': total_files,
            'valid_files': total_valid,
            'invalid_files': total_files - total_valid,
            'success_rate': round((total_valid / max(total_files, 1)) * 100, 1),
            'master_files': len(final_payload['master']),
            'individual_files': len(final_payload['individual'])
        }
        
        self.logger.info(f"✅ File organization completed: {total_valid}/{total_files} files validated successfully")
        
        return final_payload

    def _process_validation_results(self, validation_results: dict) -> list:
        """Process validation results and return only valid files with metadata"""
        valid_files = []
        
        for result in validation_results.get('individual_results', []):
            if result.get('is_valid', False):
                # Add validation metadata to file info
                file_info = {
                    'path': result['file_path'],
                    'size_mb': result.get('size_mb', 0),
                    'quality_score': result.get('quality_score', 0),
                    'quality_grade': result.get('quality_grade', 'unknown'),
                    'validation_time': result.get('validation_time', 0)
                }
                
                # Add technical details if available
                tech = result.get('technical_analysis', {})
                if tech.get('available'):
                    file_info.update({
                        'duration_seconds': tech.get('duration_seconds', 0),
                        'resolution': tech.get('video_resolution', 'unknown'),
                        'bitrate_kbps': tech.get('bitrate_kbps', 0),
                        'codec': tech.get('video_codec', 'unknown')
                    })
                
                valid_files.append(file_info)
            else:
                # Log invalid file details
                filename = os.path.basename(result.get('file_path', 'unknown'))
                errors = result.get('validation_errors', [])
                print(f"❌ Invalid file: {filename}")
                for error in errors[:3]:  # Show first 3 errors
                    print(f"   - {error}")
        
        return valid_files

    def _check_validation_requirements(self, validation_summary: dict, item) -> bool:
        """
        Legacy auxiliary output summary helper.

        This method is retained for source compatibility only.  It is not
        called by the completion path; Creator completion is decided solely by
        the core gameplay quality gate so moved/missing local files cannot
        block the job.
        """
        try:
            mode = normalize_production_mode(item.mode, allow_skip=True)
            json_data = item.json_data
            if not json_data:
                self.logger.error("Validation check failed: Input JSON data is missing.")
                return False

            workflow_report = getattr(item, "workflow_report", None)
            quality_gate = getattr(workflow_report, "quality_gate", None)
            if (
                workflow_report is None
                or quality_gate is None
                or getattr(quality_gate, "passed", False) is not True
            ):
                self.logger.error(
                    "Validation rejected because shared quality gate did not pass: %s",
                    getattr(item, "failure_code", None),
                )
                return False

            # --- Lấy số lượng file THỰC TẾ từ validation_summary ---
            master_files_actual = validation_summary.get('master_files', 0)
            # Giả định file individual được đặt tên là n-xx.mp4 hoặc h-xx.mp4
            # Logic này cần sự nhất quán từ sprunki_ultimate_master.py -> _process_individual_mode
            individual_files_actual = validation_summary.get('individual_files', 0)
            
            # --- Áp dụng quy tắc cho từng chế độ ---
            if mode == "master":
                # Master mode: Phải có đúng 1 video master và nó phải hợp lệ.
                return master_files_actual >= 1

            elif mode == "individual":
                # Gameplay thresholds are already evaluated by the shared
                # WorkflowReport. File validation remains an auxiliary
                # artifact check and must not recalculate coverage from raw
                # JSON that may contain policy-skipped horror content.
                self.logger.info("Individual Mode Validation: shared quality gate passed")
                return individual_files_actual >= 1

            elif mode == "both":
                # Both mode: both phase gates passed before this check; require
                # at least one valid artifact from each output family.
                master_requirement_met = (master_files_actual >= 1)

                self.logger.info(f"Both Mode Validation:")
                self.logger.info(f"  - Master: {master_files_actual} found. Met threshold? {master_requirement_met}")
                self.logger.info(f"  - Individual artifacts: {individual_files_actual} found")
                
                return master_requirement_met and individual_files_actual >= 1
            
            else:
                return False # Chế độ không xác định
                
        except Exception as e:
            self.logger.error(f"❌ CRITICAL: Error checking DYNAMIC validation requirements: {e}")
            return False

def main():
    """Main entry point for Creator Service"""
    print("🤖 Sprunki Creator Service")
    print("🔗 API-driven video production service")
    print("="*50)
    
    service = CreatorService()
    
    try:
        if service.start():
            print("✅ Service running. Press Ctrl+C to stop.")
            
            while service.running:
                time.sleep(1)
        else:
            print("❌ Failed to start service")
            
    except KeyboardInterrupt:
        print("\n🛑 Shutdown requested")
    finally:
        service.stop()

if __name__ == "__main__":
    main()

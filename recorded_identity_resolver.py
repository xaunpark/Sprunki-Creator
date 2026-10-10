import argparse
import hashlib
import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from identity_review_artifact import write_recorded_identity_review


OUTPUT_FILENAME = "recorded_character_identity.json"
OVERRIDES_FILENAME = "recorded_identity_overrides.json"
RULE_ID = "source_json_timestamps_coordinate_alignment"
SIDECAR_SUFFIX = ".icon_identity.json"

RETRYABLE_REASONS = {
    "resolver_timeout",
    "headless_browser_launch_failed",
    "chrome_debug_unavailable",
    "page_load_timeout",
    "game_load_timeout",
    "game_vm_not_ready",
    "missing_vm_runtime_temporarily",
    "network_timeout",
    "cocrea_load_timeout",
    "headless_vm_costume_evidence_failed",
}

NON_RETRYABLE_REASONS = {
    "missing_source_json",
    "missing_timestamps",
    "source_json_hash_mismatch",
    "sidecar_source_hash_mismatch",
    "insufficient_standard_icons",
    "non_standard_costume_names",
    "coordinate_grid_mismatch",
    "no_timestamp_icon_join",
    "missing_costume_evidence",
}


def reason_key(value: Any) -> str:
    return str(value or "").split(":", 1)[0]


def is_retryable_identity_result(status: str, reason: Optional[str], errors: List[str], warnings: List[str]) -> bool:
    if status == "verified":
        return False
    all_reasons = [reason_key(reason)] + [reason_key(item) for item in errors] + [reason_key(item) for item in warnings]
    if any(item in NON_RETRYABLE_REASONS for item in all_reasons):
        return False
    if any(item in RETRYABLE_REASONS for item in all_reasons):
        return True
    return False

CANONICAL_CHARACTERS: List[Tuple[str, str]] = [
    ("oren", "Oren"),
    ("raddy", "Raddy"),
    ("clukr", "Clukr"),
    ("fun_bot", "Fun Bot"),
    ("vineria", "Vineria"),
    ("gray", "Gray"),
    ("brud", "Brud"),
    ("garnold", "Garnold"),
    ("owakcx", "OWAKCX"),
    ("sky", "Sky"),
    ("mr_sun", "Mr. Sun"),
    ("durple", "Durple"),
    ("mr_tree", "Mr. Tree"),
    ("simon", "Simon"),
    ("tunner", "Tunner"),
    ("mr_fun_computer", "Mr. Fun Computer"),
    ("wenda", "Wenda"),
    ("pinki", "Pinki"),
    ("jevin", "Jevin"),
    ("black", "Black"),
]

CANONICAL_BY_NUMBER = {
    index + 1: {"canonicalId": canonical_id, "displayName": display_name}
    for index, (canonical_id, display_name) in enumerate(CANONICAL_CHARACTERS)
}


def read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def file_sha256(path: Optional[Path]) -> Optional[str]:
    if not path or not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_icon_number(costume_name: Optional[str]) -> Optional[int]:
    match = re.match(r"^(\d{1,2})-a(?:\b|-|_)?", costume_name or "", re.I)
    return int(match.group(1)) if match else None


def classify_costume(costume_name: Optional[str]) -> Dict[str, Any]:
    icon_number = parse_icon_number(costume_name)
    canonical = CANONICAL_BY_NUMBER.get(icon_number)
    if canonical:
        return {
            "iconNumber": icon_number,
            "canonicalId": canonical["canonicalId"],
            "displayName": canonical["displayName"],
            "status": "matched",
        }
    return {
        "iconNumber": icon_number,
        "canonicalId": None,
        "displayName": None,
        "status": "extra" if icon_number is not None else "unknown",
    }


def project_slug_from_manifest(manifest: Dict[str, Any], project: Path) -> str:
    return manifest.get("projectInfo", {}).get("projectSlug") or project.name


def source_url_from_manifest(manifest: Dict[str, Any]) -> Optional[str]:
    source = manifest.get("source") or {}
    return source.get("value") or manifest.get("projectInfo", {}).get("sourceUrl")


def candidate_source_json_paths(project: Path, manifest: Dict[str, Any]) -> Iterable[Path]:
    output_value = manifest.get("outputs", {}).get("sourceJson")
    filename = output_value.get("filename") if isinstance(output_value, dict) else output_value
    if filename:
        filename = str(filename)
        configured_json_dir = os.getenv("SPRUNKIHUB_JSON_DIR") or os.getenv("SPRUNKI_JSON_DIR")
        candidates = [
            project / filename,
            project.parent / "JSON" / filename,
            Path(r"G:\SprunkiOUTPUT\JSON") / filename,
            Path.cwd() / "JSON" / filename,
        ]
        if configured_json_dir:
            candidates.append(Path(configured_json_dir) / filename)
        for path in candidates:
            yield path

    slug = project_slug_from_manifest(manifest, project)
    for root in [project, project.parent / "JSON", Path(r"G:\SprunkiOUTPUT\JSON"), Path.cwd() / "JSON"]:
        if root.exists():
            yield from sorted(root.glob(f"*{slug}*.json"), key=lambda p: p.stat().st_mtime, reverse=True)


def find_source_json(project: Path, explicit: Optional[str]) -> Optional[Path]:
    if explicit:
        path = Path(explicit)
        return path if path.exists() else None
    manifest_path = project / "manifest.json"
    manifest = read_json(manifest_path) if manifest_path.exists() else {}
    for path in candidate_source_json_paths(project, manifest):
        if path.exists() and path.name != OUTPUT_FILENAME and not path.name.endswith(SIDECAR_SUFFIX):
            return path
    return None


def version_score(path: Path, project_name: str) -> int:
    match = re.search(rf"^{re.escape(project_name)}-showcase\s\((\d+)\)", path.name)
    if match:
        return int(match.group(1))
    return -1 if path.name.startswith(f"{project_name}-showcase") else -2


def _timestamp_path_from_video_filename(project: Path, filename: Any) -> Optional[Path]:
    if not filename:
        return None
    name = str(filename).replace("\\", "/").rsplit("/", 1)[-1]
    candidate_name = name if name.endswith(".timestamps.json") else f"{Path(name).stem}.timestamps.json"
    candidate = project / candidate_name
    return candidate if candidate.is_file() else None


def _md5_file(path: Path) -> Optional[str]:
    digest = hashlib.md5()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _cache_timestamp_hashes(project: Path) -> set[str]:
    hashes = set()
    for relative_path in (
        "preprocessed_assets/comparison_assets.json",
        "preprocessed_assets_short_comparison/short_comparison_assets.json",
    ):
        manifest_path = project / relative_path
        if not manifest_path.is_file():
            continue
        try:
            manifest = read_json(manifest_path)
            value = manifest.get("source_timestamps_hash") if isinstance(manifest, dict) else None
        except (OSError, ValueError, AttributeError, json.JSONDecodeError):
            continue
        if value:
            hashes.add(str(value).lower())
    return hashes


def find_timestamps(
    project: Path,
    explicit: Optional[str],
    manifest: Optional[Dict[str, Any]] = None,
) -> Optional[Path]:
    if explicit:
        path = Path(explicit)
        return path if path.exists() else None

    outputs = (manifest or {}).get("outputs") or {}
    master_video = outputs.get("masterVideo") or {}
    manifest_candidate = None
    if isinstance(master_video, dict):
        manifest_candidate = _timestamp_path_from_video_filename(
            project,
            master_video.get("filename") or master_video.get("path"),
        )

    candidates = list(project.glob("*.timestamps.json"))
    if manifest_candidate:
        candidates.append(manifest_candidate)
    candidates = list(dict.fromkeys(candidates))
    if not candidates:
        return None

    cache_hashes = _cache_timestamp_hashes(project)
    if cache_hashes:
        # Different cache profiles referring to different recordings are
        # ambiguous. Do not silently select the manifest/latest timestamps.
        if len(cache_hashes) != 1:
            return None
        matching = [path for path in candidates if _md5_file(path) in cache_hashes]
        if matching:
            return max(
                matching,
                key=lambda path: (version_score(path, project.name), path.stat().st_mtime_ns),
            )
        # A cache exists but none of the local timestamp files match it. A
        # manifest/latest fallback here could create an identity for a
        # different retry recording.
        return None

    if manifest_candidate:
        return manifest_candidate

    return max(candidates, key=lambda path: (version_score(path, project.name), path.stat().st_mtime_ns))


def extract_icon_index_segments(timestamps: Dict[str, Any]) -> Dict[int, str]:
    out: Dict[int, str] = {}
    last_icon_index: Optional[int] = None
    for event in sorted(timestamps.get("events", []), key=lambda row: row.get("timestamp", 0)):
        event_type = event.get("event")
        metadata = event.get("metadata") or {}
        if event_type in {"successful_drag_started", "start_drag"} and metadata.get("icon_index") is not None:
            last_icon_index = int(metadata["icon_index"])
        if event_type != "start_listen":
            continue
        icon_index = metadata.get("icon_index")
        if icon_index is None:
            icon_index = last_icon_index
        if icon_index is None:
            continue
        icon_index = int(icon_index)
        segment_key = metadata.get("segmentKey") or f"n-{icon_index + 1}"
        out[icon_index] = str(segment_key)
        last_icon_index = None
    return out


def extract_icon_segment_metadata(timestamps: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    out: Dict[int, Dict[str, Any]] = {}
    last_drag_by_icon: Dict[int, Dict[str, Any]] = {}
    last_drop_by_icon: Dict[int, Dict[str, Any]] = {}
    last_icon_index: Optional[int] = None
    assigned_segments = set()
    for event in sorted(timestamps.get("events", []), key=lambda row: row.get("timestamp", 0)):
        event_type = event.get("event")
        metadata = event.get("metadata") or {}
        if event_type in {"successful_drag_started", "start_drag"} and metadata.get("icon_index") is not None:
            icon_index = int(metadata["icon_index"])
            last_icon_index = icon_index
            last_drag_by_icon[icon_index] = dict(metadata)
            continue
        if event_type == "icon_dropped_successfully" and metadata.get("icon_index") is not None:
            icon_index = int(metadata["icon_index"])
            last_drop_by_icon[icon_index] = dict(metadata)
            continue
        if event_type != "start_listen":
            continue
        icon_index = metadata.get("icon_index")
        if icon_index is None:
            icon_index = last_icon_index
        if icon_index is None:
            continue
        icon_index = int(icon_index)
        row = out.setdefault(icon_index, {})
        segment_key = metadata.get("segmentKey") or f"n-{icon_index + 1}"
        if segment_key in assigned_segments:
            last_icon_index = None
            continue
        row.update(last_drag_by_icon.get(icon_index, {}))
        row.update(last_drop_by_icon.get(icon_index, {}))
        row.update({key: value for key, value in metadata.items() if value is not None})
        row["segmentKey"] = segment_key
        assigned_segments.add(segment_key)
        last_icon_index = None
    return out


def source_icons_by_index(source_json: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    icons = source_json.get("layout", {}).get("icons") or []
    out = {}
    for fallback_index, icon in enumerate(icons):
        index = int(icon.get("index", fallback_index))
        out[index] = {
            "iconIndex": index,
            "sourceX": icon.get("x"),
            "sourceY": icon.get("y"),
        }
    return out


def locate_sidecar(source_json_path: Optional[Path], explicit: Optional[str]) -> Optional[Path]:
    if explicit:
        path = Path(explicit)
        return path if path.exists() else None
    if not source_json_path:
        return None
    path = source_json_path.with_suffix(SIDECAR_SUFFIX)
    if path.exists():
        return path
    return None


def load_sidecar_icon_map(sidecar_path: Optional[Path], source_json_hash: Optional[str]) -> Tuple[Dict[int, Dict[str, Any]], List[str], Optional[str]]:
    if not sidecar_path or not sidecar_path.exists():
        return {}, [], None
    sidecar = read_json(sidecar_path)
    warnings = []
    expected_hash = sidecar.get("sourceJsonHash")
    if expected_hash and source_json_hash and expected_hash != source_json_hash:
        warnings.append("sidecar_source_json_hash_mismatch")
    rows = sidecar.get("iconIdentities") or sidecar.get("icons") or []
    out = {}
    for row in rows:
        if row.get("iconIndex") is None:
            continue
        costume_name = row.get("costumeName") or row.get("costume")
        if not costume_name:
            continue
        out[int(row["iconIndex"])] = {
            "costumeName": costume_name,
            "identityEvidence": "sidecar",
            "gridOrder": row.get("gridOrder"),
        }
    return out, warnings, str(sidecar_path)


def smoke_artifact_icon_map(project: Path) -> Tuple[Dict[int, Dict[str, Any]], Optional[str]]:
    candidates = [
        project / "character_identity_map.coordinate_smoke.headless_grid_order.json",
        project / "character_identity_map.coordinate_smoke.json",
        project / "character_identity_map.coordinate_smoke.headless_aggregate_order.json",
    ]
    for path in candidates:
        if not path.exists():
            continue
        data = read_json(path)
        rows = data.get("matches") or data.get("segmentIdentities") or []
        out = {}
        for row in rows:
            if row.get("iconIndex") is None:
                continue
            costume_name = row.get("costumeName") or row.get("costume")
            if not costume_name:
                continue
            out[int(row["iconIndex"])] = {
                "costumeName": costume_name,
                "identityEvidence": path.name,
                "gridOrder": row.get("gridOrder"),
            }
        if out:
            return out, str(path)
    return {}, None


def nearest_runtime_icon(source_icon: Dict[str, Any], runtime_icons: List[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], float]:
    source_x = source_icon.get("sourceX")
    source_y = source_icon.get("sourceY")
    if source_x is None or source_y is None:
        return None, math.inf
    best = None
    best_distance = math.inf
    for icon in runtime_icons:
        icon_x = icon.get("x")
        icon_y = icon.get("y")
        if icon_x is None or icon_y is None:
            continue
        distance = math.hypot(float(icon_x) - float(source_x), float(icon_y) - float(source_y))
        if distance < best_distance:
            best = icon
            best_distance = distance
    return best, best_distance


def headless_vm_icon_map(
    project: Path,
    manifest: Dict[str, Any],
    source_icons: Dict[int, Dict[str, Any]],
) -> Tuple[Dict[int, Dict[str, Any]], Optional[str], List[str]]:
    warnings: List[str] = []
    if not source_icons:
        return {}, None, ["headless_skipped_no_source_icons"]
    source_url = source_url_from_manifest(manifest)
    if not source_url:
        return {}, None, ["headless_skipped_missing_source_url"]
    try:
        from headless_coordinate_identity_smoke import open_headless_snapshot
    except Exception as exc:
        return {}, None, [f"headless_unavailable:{type(exc).__name__}:{str(exc)[:160]}"]

    cookie_file = os.getenv("RECORDED_IDENTITY_COOKIE_FILE") or str(Path(__file__).resolve().parent / "cocrea_cookies.json")
    timeout = int(os.getenv("RECORDED_IDENTITY_HEADLESS_TIMEOUT", "140"))
    threshold = float(os.getenv("RECORDED_IDENTITY_COORDINATE_THRESHOLD", "8"))
    min_runtime_icons = max(1, len(source_icons))
    try:
        print(f"[recorded_identity_resolver] Headless VM costume evidence: {source_url}")
        snapshot = open_headless_snapshot(
            source_url=source_url,
            cookie_file=cookie_file,
            timeout=timeout,
            min_runtime_icons=min_runtime_icons,
            click_center=True,
        )
    except Exception as exc:
        return {}, None, [f"headless_probe_failed:{type(exc).__name__}:{str(exc)[:180]}"]

    evidence_path = project / "recorded_identity_headless_evidence.json"
    try:
        write_json(evidence_path, {
            "schemaVersion": 1,
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "source": "headless_vm_coordinate_costume_evidence",
            "sourceUrl": source_url,
            "snapshotPhase": snapshot.get("phase"),
            "ok": snapshot.get("ok"),
            "title": snapshot.get("title"),
            "href": snapshot.get("href"),
            "visibleIconCount": snapshot.get("visibleIconCount"),
            "icons": snapshot.get("icons") or [],
            "error": snapshot.get("error") or snapshot.get("lastError"),
        })
    except Exception as exc:
        warnings.append(f"headless_evidence_write_failed:{type(exc).__name__}")

    if not snapshot.get("ok"):
        reason = snapshot.get("error") or snapshot.get("lastError") or "unknown"
        return {}, str(evidence_path), [*warnings, f"headless_probe_unusable:{str(reason)[:160]}"]

    runtime_icons = [
        icon for icon in snapshot.get("icons", [])
        if icon.get("filteredByJsonGenerator") and icon.get("costumeName")
    ]
    if not runtime_icons:
        return {}, str(evidence_path), [*warnings, "headless_probe_no_runtime_icons"]
    if len(runtime_icons) < min_runtime_icons:
        return {}, str(evidence_path), [
            *warnings,
            f"headless_insufficient_runtime_icons:{len(runtime_icons)}/{min_runtime_icons}",
        ]

    out: Dict[int, Dict[str, Any]] = {}
    unmatched = []
    for icon_index, source_icon in sorted(source_icons.items()):
        match, distance = nearest_runtime_icon(source_icon, runtime_icons)
        if not match or distance > threshold:
            unmatched.append(str(icon_index))
            continue
        out[icon_index] = {
            "costumeName": match.get("costumeName"),
            "identityEvidence": "headless_vm_coordinate_match",
            "gridOrder": match.get("originalIndexInVisibleList"),
            "coordinateDistance": round(distance, 3),
            "iconTargetName": match.get("iconTargetName"),
            "currentCostume": match.get("currentCostume"),
        }
    if unmatched:
        warnings.append(f"headless_unmatched_icon_indexes:{','.join(unmatched[:12])}")
    if out:
        warnings.append("used_headless_vm_costume_evidence")
    return out, str(evidence_path), warnings


def legacy_identity_icon_map(project: Path, source_icons: Dict[int, Dict[str, Any]]) -> Tuple[Dict[int, Dict[str, Any]], Optional[str], List[str]]:
    path = project / "character_identity_map.json"
    if not path.exists():
        return {}, None, ["missing_costume_evidence"]
    identity = read_json(path)
    standard = identity.get("standardCharacters") or []
    excluded = list((identity.get("excludedSegments") or {}).values())
    rows = [
        row
        for row in [*standard, *excluded]
        if row.get("costumeName") and parse_icon_number(row.get("costumeName")) is not None
    ]
    source_count = len(source_icons)
    warnings = []
    if source_count > len(rows):
        return {}, str(path), ["insufficient_costume_evidence"]
    if source_count != len(rows):
        warnings.append("legacy_costume_count_differs_from_source_icon_count")

    # This legacy fallback is intentionally narrow. It is safe for standard layouts
    # and for mods where extras trail the standard set; it refuses to invent direct
    # evidence for more complex layouts.
    standard_sorted = sorted(standard, key=lambda row: int(row.get("iconNumber") or 999))
    excluded_sorted = sorted(excluded, key=lambda row: int(row.get("iconNumber") or 999))
    ordered = [*standard_sorted, *excluded_sorted]
    if source_count > 20 and excluded_sorted:
        first_extra_number = parse_icon_number(excluded_sorted[0].get("costumeName"))
        if first_extra_number and first_extra_number < 21:
            return {}, str(path), ["legacy_extra_order_ambiguous"]
    out = {}
    for icon_index in sorted(source_icons):
        if icon_index >= len(ordered):
            continue
        out[icon_index] = {
            "costumeName": ordered[icon_index].get("costumeName"),
            "identityEvidence": "legacy_identity_costume_list",
            "gridOrder": icon_index,
        }
    return out, str(path), warnings


def load_overrides(project: Path) -> Dict[str, Any]:
    path = project / OVERRIDES_FILENAME
    if not path.exists():
        return {}
    return read_json(path)


def apply_icon_overrides(icon_identity_map: Dict[int, Dict[str, Any]], overrides: Dict[str, Any]) -> List[str]:
    warnings = []
    for row in overrides.get("overrides") or []:
        if row.get("iconIndex") is None or not row.get("costumeName"):
            continue
        icon_index = int(row["iconIndex"])
        icon_identity_map[icon_index] = {
            **icon_identity_map.get(icon_index, {}),
            "costumeName": row["costumeName"],
            "identityEvidence": row.get("evidence") or "manual_override",
            "gridOrder": row.get("gridOrder", icon_identity_map.get(icon_index, {}).get("gridOrder")),
            "overrideReason": row.get("reason"),
        }
        warnings.append(f"manual_override_icon_index:{icon_index}")
    return warnings


def segment_overrides_by_key(overrides: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    if not overrides:
        return {}
    rows = overrides.get("segmentOverrides") or []
    out = {}
    for row in rows:
        segment_key = row.get("segmentKey")
        if segment_key:
            out[str(segment_key)] = row
    return out


def apply_segment_override_to_row(row: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    status = str(override.get("status") or "").lower()
    canonical_id = override.get("canonicalId")
    out = {**row}
    out["manualOverride"] = True
    out["overrideReason"] = override.get("reason") or "manual_review"
    out["identityEvidence"] = "manual_segment_override"
    out["identityUsable"] = status not in {"unknown"}
    out["identityUnusableReason"] = "manual_unknown" if status == "unknown" else None

    if status == "extra":
        out["canonicalId"] = None
        out["displayName"] = None
        out["status"] = "extra"
        return out

    if status == "unknown":
        out["canonicalId"] = None
        out["displayName"] = None
        out["status"] = "unknown"
        return out

    if canonical_id in {value for value, _label in CANONICAL_CHARACTERS}:
        display_name = next(
            (display for cid, display in CANONICAL_CHARACTERS if cid == canonical_id),
            str(canonical_id),
        )
        out["canonicalId"] = str(canonical_id)
        out["displayName"] = display_name
        out["status"] = "matched"
        out["identityUsable"] = True
        out["identityUnusableReason"] = None
    elif status == "matched":
        out["canonicalId"] = None
        out["displayName"] = None
        out["status"] = "unknown"
        out["identityUsable"] = False
        out["identityUnusableReason"] = "invalid_manual_canonical_id"
    return out


def build_payload(
    project: Path,
    source_json_path: Optional[Path],
    timestamps_path: Optional[Path],
    source_json: Optional[Dict[str, Any]],
    icon_segments: Dict[int, str],
    segment_metadata: Optional[Dict[int, Dict[str, Any]]],
    icon_identity_map: Dict[int, Dict[str, Any]],
    evidence_source: Optional[str],
    errors: List[str],
    warnings: List[str],
    overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    source_icons = source_icons_by_index(source_json or {})
    segment_identities = []
    standard_by_segment = {}
    segment_by_canonical = {}
    duplicate_segments_by_canonical: Dict[str, List[str]] = {}
    excluded_segments = {}
    unusable_segments = {}
    canonical_preference = (overrides or {}).get("segmentByCanonicalPreference") or {}
    segment_overrides = segment_overrides_by_key(overrides)
    segment_metadata = segment_metadata or {}
    manual_review_complete = (overrides or {}).get("manualReviewComplete") is True

    if manual_review_complete:
        override_rows = (overrides or {}).get("segmentOverrides") or []
        override_keys = [str(row.get("segmentKey") or "") for row in override_rows]
        duplicate_override_keys = sorted({key for key in override_keys if key and override_keys.count(key) > 1})
        if duplicate_override_keys:
            errors.append("duplicate_manual_override_segments:" + ",".join(duplicate_override_keys[:20]))
        expected_segment_keys = {str(value) for value in icon_segments.values()}
        missing_manual_segments = sorted(expected_segment_keys - set(segment_overrides))
        if missing_manual_segments:
            errors.append(
                "manual_review_incomplete_segments:"
                + ",".join(missing_manual_segments[:20])
            )
        standard_ids = {value for value, _label in CANONICAL_CHARACTERS}
        for segment_key, override in segment_overrides.items():
            override_status = str(override.get("status") or "").lower()
            if override_status not in {"matched", "extra", "unknown"}:
                errors.append(f"invalid_manual_override_status:{segment_key}")
            elif override_status == "matched" and override.get("canonicalId") not in standard_ids:
                errors.append(f"invalid_manual_override_character:{segment_key}")

    for icon_index, segment_key in sorted(icon_segments.items()):
        source_icon = source_icons.get(icon_index, {"iconIndex": icon_index})
        identity = icon_identity_map.get(icon_index, {})
        runtime = segment_metadata.get(icon_index, {})
        is_manual = identity.get("identityEvidence") in {"manual_override", "user_video_review"}
        has_pickup_coordinates = runtime.get("pickupX") is not None and runtime.get("pickupY") is not None
        costume_name = identity.get("costumeName")
        classified = classify_costume(costume_name)
        missing_runtime_pickup = (
            runtime.get("method") == "search_mode"
            and not has_pickup_coordinates
            and not is_manual
        )
        is_runtime_ambiguous = missing_runtime_pickup and not classified["canonicalId"]
        row = {
            "segmentKey": segment_key,
            "iconIndex": icon_index,
            "sourceX": source_icon.get("sourceX"),
            "sourceY": source_icon.get("sourceY"),
            "gridOrder": identity.get("gridOrder"),
            "costumeName": costume_name,
            "iconNumber": classified["iconNumber"],
            "canonicalId": classified["canonicalId"],
            "displayName": classified["displayName"],
            "status": classified["status"] if costume_name else "unknown",
            "identityEvidence": identity.get("identityEvidence"),
            "dragMethod": runtime.get("method"),
            "pickupIconOffsetIdx": runtime.get("pickupIconOffsetIdx"),
            "pickupX": runtime.get("pickupX"),
            "pickupY": runtime.get("pickupY"),
            "slotOffsetIdx": runtime.get("slotOffsetIdx"),
            "dropX": runtime.get("dropX"),
            "dropY": runtime.get("dropY"),
            "identityUsable": not is_runtime_ambiguous,
            "identityUnusableReason": "search_mode_without_runtime_coordinates" if is_runtime_ambiguous else None,
            "runtimeCoordinateWarning": "search_mode_without_runtime_coordinates" if missing_runtime_pickup else None,
        }
        row["autoValue"] = row["canonicalId"] or (
            "extra" if row["status"] == "extra" else "unknown"
        )
        override = segment_overrides.get(segment_key)
        if override:
            row = apply_segment_override_to_row(row, override)
            warnings.append(f"manual_override_segment:{segment_key}")
            is_runtime_ambiguous = row.get("identityUsable") is False
        segment_identities.append(row)
        if is_runtime_ambiguous:
            unusable_segments[segment_key] = row
        elif row["canonicalId"]:
            standard_by_segment[segment_key] = row
            duplicate_segments_by_canonical.setdefault(row["canonicalId"], []).append(segment_key)
            if row["canonicalId"] not in segment_by_canonical:
                segment_by_canonical[row["canonicalId"]] = segment_key
            if canonical_preference.get(row["canonicalId"]) == segment_key:
                segment_by_canonical[row["canonicalId"]] = segment_key
        else:
            excluded_segments[segment_key] = row

    if not icon_segments and "missing_timestamps" not in errors:
        errors.append("timestamps_have_no_icon_segments")
    missing_identity = [idx for idx in icon_segments if idx not in icon_identity_map]
    if missing_identity and not manual_review_complete:
        warnings.append(f"missing_identity_for_icon_indexes:{','.join(map(str, missing_identity[:12]))}")
    search_without_runtime_coordinates = sorted(
        row["segmentKey"]
        for row in segment_identities
        if row.get("runtimeCoordinateWarning") == "search_mode_without_runtime_coordinates"
    )
    if search_without_runtime_coordinates:
        warnings.append(
            "search_mode_without_runtime_coordinates:"
            + ",".join(search_without_runtime_coordinates[:12])
        )
    canonical_counts: Dict[str, int] = {}
    costume_counts: Dict[str, int] = {}
    for row in segment_identities:
        if row.get("canonicalId"):
            canonical_counts[row["canonicalId"]] = canonical_counts.get(row["canonicalId"], 0) + 1
        if row.get("costumeName"):
            costume_counts[row["costumeName"]] = costume_counts.get(row["costumeName"], 0) + 1
    duplicate_canonicals = sorted(key for key, count in canonical_counts.items() if count > 1)
    duplicate_costumes = sorted(key for key, count in costume_counts.items() if count > 1)
    allowed_duplicate_canonicals = set((overrides or {}).get("allowDuplicateCanonicals") or [])
    unexpected_duplicate_canonicals = [
        canonical_id for canonical_id in duplicate_canonicals
        if canonical_id not in allowed_duplicate_canonicals
    ]
    if duplicate_canonicals:
        warnings.append(f"duplicate_canonical_ids:{','.join(duplicate_canonicals)}")
    if duplicate_costumes:
        warnings.append(f"duplicate_costume_names:{','.join(duplicate_costumes[:12])}")

    status = "verified"
    standard_count = len(segment_by_canonical)
    if errors:
        status = "unavailable"
    elif standard_count < 5 or unexpected_duplicate_canonicals:
        status = "needs_review"
        if standard_count < 5:
            warnings.append("insufficient_standard_icons")
    elif standard_count < 20:
        warnings.append(f"partial_standard_icons:{standard_count}/20")

    review_reason = None
    if status != "verified":
        if errors:
            review_reason = errors[0]
        elif unexpected_duplicate_canonicals:
            review_reason = "duplicate_canonical_ids:" + ",".join(unexpected_duplicate_canonicals)
        elif standard_count < 5:
            review_reason = "insufficient_standard_icons"
        else:
            blocking_warnings = [
                warning for warning in warnings
                if not str(warning).startswith((
                    "used_headless_vm_costume_evidence",
                    "manual_override_segment:",
                    "duplicate_costume_names:",
                ))
            ]
            review_reason = blocking_warnings[0] if blocking_warnings else "identity_needs_review"
    retryable = is_retryable_identity_result(status, review_reason, errors, warnings)

    return {
        "schemaVersion": 2,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "needsReviewReason": review_reason,
        "retryable": retryable,
        "manualReviewComplete": manual_review_complete,
        "rule": {
            "id": RULE_ID,
            "description": "Join recorded timestamps to source JSON icon indexes, then map those icons to Scratch costume names and canonical Sprunki identities.",
        },
        "evidence": {
            "projectFolder": str(project),
            "sourceJson": str(source_json_path) if source_json_path else None,
            "sourceJsonHash": file_sha256(source_json_path),
            "timestamps": str(timestamps_path) if timestamps_path else None,
            "timestampsHash": file_sha256(timestamps_path),
            "iconIdentitySource": evidence_source,
        },
        "validation": {
            "errors": errors,
            "warnings": warnings,
            "sourceIconCount": len(source_icons),
            "timestampSegmentCount": len(icon_segments),
            "identityIconCount": len(icon_identity_map),
            "standardCharacterCount": standard_count,
            "excludedSegmentCount": len(excluded_segments),
            "unusableSegmentCount": len(unusable_segments),
        },
        "segmentIdentities": segment_identities,
        "segmentByCanonicalId": segment_by_canonical,
        "standardBySegment": standard_by_segment,
        "duplicateSegmentsByCanonicalId": {
            canonical_id: segments
            for canonical_id, segments in duplicate_segments_by_canonical.items()
            if len(segments) > 1
        },
        "excludedSegments": excluded_segments,
        "unusableSegments": unusable_segments,
    }


def resolve(project: Path, source_json_arg: Optional[str], timestamps_arg: Optional[str], sidecar_arg: Optional[str]) -> Dict[str, Any]:
    errors: List[str] = []
    warnings: List[str] = []
    manifest_path = project / "manifest.json"
    manifest = read_json(manifest_path) if manifest_path.exists() else {}
    source_json_path = find_source_json(project, source_json_arg)
    timestamps_path = find_timestamps(project, timestamps_arg, manifest)

    source_json = None
    timestamps = None
    if not source_json_path:
        errors.append("missing_source_json")
    else:
        source_json = read_json(source_json_path)
    if not timestamps_path:
        errors.append("missing_timestamps")
    else:
        timestamps = read_json(timestamps_path)

    segment_metadata = extract_icon_segment_metadata(timestamps or {})
    icon_segments = {
        icon_index: str(metadata.get("segmentKey") or f"n-{icon_index + 1}")
        for icon_index, metadata in segment_metadata.items()
    }
    if not icon_segments:
        icon_segments = extract_icon_index_segments(timestamps or {})
    source_icons = source_icons_by_index(source_json or {})
    source_hash = file_sha256(source_json_path)

    overrides = load_overrides(project)
    manual_review_complete = overrides.get("manualReviewComplete") is True
    icon_map: Dict[int, Dict[str, Any]] = {}
    evidence_source = None

    if manual_review_complete:
        evidence_source = "manual_segment_review"
    else:
        sidecar_path = locate_sidecar(source_json_path, sidecar_arg)
        icon_map, sidecar_warnings, sidecar_source = load_sidecar_icon_map(sidecar_path, source_hash)
        warnings.extend(sidecar_warnings)
        if icon_map:
            evidence_source = sidecar_source

        if not icon_map:
            icon_map, evidence_source = smoke_artifact_icon_map(project)

        if not icon_map and not errors:
            icon_map, evidence_source, headless_warnings = headless_vm_icon_map(project, manifest, source_icons)
            warnings.extend(headless_warnings)

        if not icon_map:
            icon_map, evidence_source, legacy_warnings = legacy_identity_icon_map(project, source_icons)
            warnings.extend(legacy_warnings)

        if not icon_map and "missing_costume_evidence" not in warnings:
            warnings.append("missing_costume_evidence")

        if overrides:
            warnings.extend(apply_icon_overrides(icon_map, overrides))

    return build_payload(
        project=project,
        source_json_path=source_json_path,
        timestamps_path=timestamps_path,
        source_json=source_json,
        icon_segments=icon_segments,
        segment_metadata=segment_metadata,
        icon_identity_map=icon_map,
        evidence_source=evidence_source,
        errors=errors,
        warnings=warnings,
        overrides=overrides,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve recorded Sprunki character identity from source JSON, timestamps, and coordinate/costume evidence.")
    parser.add_argument("--project", required=True, help="Project folder containing manifest/timestamps/video.")
    parser.add_argument("--source-json", help="Explicit source JSON path.")
    parser.add_argument("--timestamps", help="Explicit timestamps JSON path.")
    parser.add_argument("--sidecar", help="Explicit icon identity sidecar path.")
    parser.add_argument("--out", help="Output path. Defaults to <project>/recorded_character_identity.json.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing output.")
    args = parser.parse_args()

    project = Path(args.project)
    out_path = Path(args.out) if args.out else project / OUTPUT_FILENAME
    if out_path.exists() and not args.force:
        payload = read_json(out_path)
        review_paths = {}
        try:
            review_paths = write_recorded_identity_review(project, payload)
        except Exception as exc:
            payload.setdefault("validation", {}).setdefault("warnings", []).append(f"review_artifact_failed:{exc}")
        print(json.dumps({
            "status": "skipped",
            "reason": "existing_recorded_identity",
            "identityPath": str(out_path),
            "identityStatus": payload.get("status"),
            "reviewHtml": str(review_paths.get("html")) if review_paths else None,
            "reviewJson": str(review_paths.get("json")) if review_paths else None,
        }, ensure_ascii=False))
        return 0

    payload = resolve(project, args.source_json, args.timestamps, args.sidecar)
    write_json(out_path, payload)
    review_paths = {}
    try:
        review_paths = write_recorded_identity_review(project, payload)
    except Exception as exc:
        payload.setdefault("validation", {}).setdefault("warnings", []).append(f"review_artifact_failed:{exc}")
    print(json.dumps({
        "status": payload.get("status"),
        "identityPath": str(out_path),
        "reviewHtml": str(review_paths.get("html")) if review_paths else None,
        "reviewJson": str(review_paths.get("json")) if review_paths else None,
        "standardCharacterCount": payload.get("validation", {}).get("standardCharacterCount"),
        "needsReviewReason": payload.get("needsReviewReason"),
        "retryable": payload.get("retryable", False),
        "errors": payload.get("validation", {}).get("errors") or [],
        "warnings": payload.get("validation", {}).get("warnings") or [],
    }, ensure_ascii=False))
    return 0 if payload.get("status") == "verified" else 2


if __name__ == "__main__":
    raise SystemExit(main())

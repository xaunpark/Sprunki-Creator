import argparse
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


OUTPUT_FILENAME = "recorded_character_identity.json"
OVERRIDES_FILENAME = "recorded_identity_overrides.json"
RULE_ID = "source_json_timestamps_coordinate_alignment"
SIDECAR_SUFFIX = ".icon_identity.json"

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


def find_timestamps(project: Path, explicit: Optional[str]) -> Optional[Path]:
    if explicit:
        path = Path(explicit)
        return path if path.exists() else None
    candidates = []
    for path in list(project.glob("*showcase*.timestamps.json")) + list(project.glob("*.timestamps.json")):
        video_stem = path.name.replace(".timestamps.json", "")
        if (project / f"{video_stem}.mp4").exists() or (project / f"{video_stem}.mkv").exists():
            candidates.append(path)
    if not candidates:
        candidates = list(project.glob("*.timestamps.json"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: (version_score(p, project.name), p.stat().st_mtime))


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
    canonical_preference = (overrides or {}).get("segmentByCanonicalPreference") or {}
    segment_metadata = segment_metadata or {}

    for icon_index, segment_key in sorted(icon_segments.items()):
        source_icon = source_icons.get(icon_index, {"iconIndex": icon_index})
        identity = icon_identity_map.get(icon_index, {})
        runtime = segment_metadata.get(icon_index, {})
        costume_name = identity.get("costumeName")
        classified = classify_costume(costume_name)
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
        }
        segment_identities.append(row)
        if row["canonicalId"]:
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
    if missing_identity:
        warnings.append(f"missing_identity_for_icon_indexes:{','.join(map(str, missing_identity[:12]))}")
    search_without_runtime_coordinates = []
    for row in segment_identities:
        is_manual = row.get("identityEvidence") in {"manual_override", "user_video_review"}
        has_pickup_coordinates = row.get("pickupX") is not None and row.get("pickupY") is not None
        if row.get("dragMethod") == "search_mode" and not has_pickup_coordinates and not is_manual:
            search_without_runtime_coordinates.append(row["segmentKey"])
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
    elif standard_count < 20 or unexpected_duplicate_canonicals or search_without_runtime_coordinates:
        status = "needs_review"
        if standard_count < 20:
            warnings.append("insufficient_standard_icons")

    return {
        "schemaVersion": 2,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "needsReviewReason": errors[0] if errors else (warnings[0] if status != "verified" and warnings else None),
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
    }


def resolve(project: Path, source_json_arg: Optional[str], timestamps_arg: Optional[str], sidecar_arg: Optional[str]) -> Dict[str, Any]:
    errors: List[str] = []
    warnings: List[str] = []
    source_json_path = find_source_json(project, source_json_arg)
    timestamps_path = find_timestamps(project, timestamps_arg)

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

    icon_map: Dict[int, Dict[str, Any]] = {}
    evidence_source = None
    sidecar_path = locate_sidecar(source_json_path, sidecar_arg)
    icon_map, sidecar_warnings, sidecar_source = load_sidecar_icon_map(sidecar_path, source_hash)
    warnings.extend(sidecar_warnings)
    if icon_map:
        evidence_source = sidecar_source

    if not icon_map:
        icon_map, evidence_source = smoke_artifact_icon_map(project)

    if not icon_map:
        icon_map, evidence_source, legacy_warnings = legacy_identity_icon_map(project, source_icons)
        warnings.extend(legacy_warnings)

    if not icon_map and "missing_costume_evidence" not in warnings:
        warnings.append("missing_costume_evidence")

    overrides = load_overrides(project)
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
        print(json.dumps({"status": "skipped", "reason": "existing_recorded_identity", "identityPath": str(out_path), "identityStatus": payload.get("status")}, ensure_ascii=False))
        return 0

    payload = resolve(project, args.source_json, args.timestamps, args.sidecar)
    write_json(out_path, payload)
    print(json.dumps({
        "status": payload.get("status"),
        "identityPath": str(out_path),
        "standardCharacterCount": payload.get("validation", {}).get("standardCharacterCount"),
        "needsReviewReason": payload.get("needsReviewReason"),
        "errors": payload.get("validation", {}).get("errors") or [],
        "warnings": payload.get("validation", {}).get("warnings") or [],
    }, ensure_ascii=False))
    return 0 if payload.get("status") == "verified" else 2


if __name__ == "__main__":
    raise SystemExit(main())

"""
Backfill per-segment recordingArea metadata for an already-recorded showcase.

This tool does not start OBS and does not record video. It reopens the mod,
replays only the icon drag/return steps needed to inspect runtime bounds, then
updates manifest.json and the showcase timestamps file with segment references.

Usage:
    python backfill_recording_areas.py
    python backfill_recording_areas.py "G:\\Sprunki OUTPUT\\MyProject"
    python backfill_recording_areas.py --project "G:\\Sprunki OUTPUT\\MyProject" --json "G:\\Sprunki OUTPUT\\JSON\\MyProject.json"
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from config_manager import get_config
from sprunki_game_player import SprunkiGamePlayer


def read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def backup_file(path: Path) -> Optional[Path]:
    if not path.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = path.with_name(f"{path.name}.bak-{stamp}")
    shutil.copy2(path, backup_path)
    return backup_path


def prompt_path(prompt: str, must_exist: bool = True) -> Path:
    while True:
        raw = input(prompt).strip().strip('"')
        if not raw:
            continue
        path = Path(raw)
        if not must_exist or path.exists():
            return path
        print(f"Path not found: {path}")


def find_manifest(project_dir: Path, explicit: Optional[str]) -> Path:
    if explicit:
        path = Path(explicit)
        if not path.exists():
            raise FileNotFoundError(f"Manifest not found: {path}")
        return path
    path = project_dir / "manifest.json"
    if not path.exists():
        raise FileNotFoundError(f"manifest.json not found in project folder: {project_dir}")
    return path


def find_timestamps(project_dir: Path, explicit: Optional[str]) -> Path:
    if explicit:
        path = Path(explicit)
        if not path.exists():
            raise FileNotFoundError(f"Timestamps file not found: {path}")
        return path

    candidates = sorted(project_dir.glob("*showcase.timestamps.json"))
    if not candidates:
        candidates = sorted(project_dir.glob("*.timestamps.json"))
    if not candidates:
        raise FileNotFoundError(f"No timestamps JSON found in project folder: {project_dir}")
    if len(candidates) > 1:
        print("Multiple timestamps files found. Using the newest by modified time:")
        candidates = sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def candidate_json_paths(project_dir: Path, manifest: Dict[str, Any]) -> List[Path]:
    paths: List[Path] = []
    source_json = manifest.get("outputs", {}).get("sourceJson")
    if source_json:
        source_json_name = source_json.get("filename") if isinstance(source_json, dict) else str(source_json)
        if source_json_name:
            paths.extend([
                project_dir / source_json_name,
                project_dir.parent / "JSON" / source_json_name,
                Path.cwd() / "JSON" / source_json_name,
            ])

            config = get_config()
            configured_json_dir = config.get("output.json_output_directory")
            if configured_json_dir:
                paths.append(Path(configured_json_dir) / source_json_name)

    project_slug = manifest.get("projectInfo", {}).get("projectSlug") or project_dir.name
    search_dirs = [project_dir.parent / "JSON", Path.cwd() / "JSON"]
    configured_json_dir = get_config().get("output.json_output_directory")
    if configured_json_dir:
        search_dirs.append(Path(configured_json_dir))
    for search_dir in search_dirs:
        if search_dir.exists():
            paths.extend(sorted(search_dir.glob(f"{project_slug}*.json")))

    deduped: List[Path] = []
    seen = set()
    for path in paths:
        resolved = str(path.resolve()) if path.exists() else str(path)
        if resolved not in seen:
            deduped.append(path)
            seen.add(resolved)
    return deduped


def find_source_json(project_dir: Path, manifest: Dict[str, Any], explicit: Optional[str]) -> Path:
    if explicit:
        path = Path(explicit)
        if not path.exists():
            raise FileNotFoundError(f"Source JSON not found: {path}")
        return path

    for path in candidate_json_paths(project_dir, manifest):
        if path.exists():
            return path

    print("Could not auto-find source JSON. Provide the old JSON file from the JSON folder.")
    return prompt_path("Source JSON path: ")


def event_icon_index(event: Dict[str, Any]) -> Optional[int]:
    metadata = event.get("metadata", {}) or {}
    for key in ("icon_index", "iconIndex"):
        value = metadata.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


def parse_normal_segments(timestamps: Dict[str, Any]) -> List[Dict[str, Any]]:
    events = sorted(timestamps.get("events", []), key=lambda e: e.get("timestamp", 0))
    segments: List[Dict[str, Any]] = []
    last_icon_index: Optional[int] = None
    inferred_index = 0

    for event in events:
        event_type = event.get("event")
        metadata = event.get("metadata", {}) or {}

        if event_type in {"trigger_horror_mode_started"}:
            break
        if event_type == "start_full_remix_performance":
            break

        if event_type == "successful_drag_started":
            icon_index = event_icon_index(event)
            if icon_index is not None:
                last_icon_index = icon_index
            continue

        if event_type != "start_listen":
            continue

        icon_index = event_icon_index(event)
        if icon_index is None:
            icon_index = last_icon_index
        if icon_index is None:
            icon_index = inferred_index

        segment_key = metadata.get("segmentKey") or metadata.get("recordingAreaId") or f"n-{icon_index + 1}"
        segments.append({
            "segmentKey": str(segment_key),
            "iconIndex": int(icon_index),
            "timestamp": event.get("timestamp"),
        })
        inferred_index = max(inferred_index + 1, int(icon_index) + 1)
        last_icon_index = None

    # Keep first occurrence per segment key in timeline order.
    unique: List[Dict[str, Any]] = []
    seen = set()
    for segment in segments:
        if segment["segmentKey"] in seen:
            continue
        unique.append(segment)
        seen.add(segment["segmentKey"])
    return unique


def find_icon_by_index(config: Dict[str, Any], icon_index: int) -> Optional[Dict[str, Any]]:
    for icon in config.get("layout", {}).get("icons", []):
        if icon.get("index") == icon_index:
            return icon
    return None


def update_timestamps_metadata(timestamps: Dict[str, Any], segments: List[Dict[str, Any]]) -> int:
    events = sorted(timestamps.get("events", []), key=lambda e: e.get("timestamp", 0))
    segment_by_icon = {segment["iconIndex"]: segment for segment in segments}
    segments_by_order = list(segments)
    last_icon_index: Optional[int] = None
    listen_order = 0
    updated = 0

    for event in events:
        event_type = event.get("event")
        if event_type == "successful_drag_started":
            last_icon_index = event_icon_index(event)
            continue
        if event_type != "start_listen":
            continue

        metadata = event.setdefault("metadata", {})
        icon_index = event_icon_index(event)
        if icon_index is None:
            icon_index = last_icon_index
        segment = segment_by_icon.get(icon_index) if icon_index is not None else None
        if segment is None and listen_order < len(segments_by_order):
            segment = segments_by_order[listen_order]
        listen_order += 1
        if not segment:
            continue
        if metadata.get("segmentKey") != segment["segmentKey"] or metadata.get("recordingAreaId") != segment["segmentKey"]:
            metadata["segmentKey"] = segment["segmentKey"]
            metadata["recordingAreaId"] = segment["segmentKey"]
            updated += 1
        last_icon_index = None

    return updated


def update_manifest(manifest: Dict[str, Any], recording_areas: Dict[str, Any]) -> None:
    layout_summary = manifest.setdefault("layoutSummary", {})
    legacy_area = layout_summary.pop("recordingArea", None)
    if legacy_area and "legacyRecordingArea" not in layout_summary:
        layout_summary["legacyRecordingArea"] = legacy_area
    layout_summary["recordingAreaMode"] = "per_showcase_segment"
    manifest["recordingAreas"] = recording_areas


def backfill(project_dir: Path, source_json_path: Path, manifest_path: Path, timestamps_path: Path, no_backup: bool, limit: Optional[int]) -> None:
    manifest = read_json(manifest_path)
    timestamps = read_json(timestamps_path)
    config = read_json(source_json_path)
    segments = parse_normal_segments(timestamps)
    if limit is not None:
        segments = segments[:limit]

    if not segments:
        raise RuntimeError("No normal start_listen segments found in timestamps.")

    print(f"Project: {project_dir}")
    print(f"Manifest: {manifest_path}")
    print(f"Timestamps: {timestamps_path}")
    print(f"Source JSON: {source_json_path}")
    print(f"Segments to backfill: {len(segments)}")
    for segment in segments:
        print(f"  {segment['segmentKey']}: iconIndex={segment['iconIndex']} timestamp={segment.get('timestamp')}")

    player = SprunkiGamePlayer()
    player.config = config
    recording_areas: Dict[str, Any] = {}

    try:
        if not player.setup_game_environment(first_setup=True):
            raise RuntimeError("Game environment setup failed")

        slots = config.get("layout", {}).get("slots", [])
        center_slot_index = player.calculate_center_slot_index(slots)
        target_slot = player._find_slot_by_index(center_slot_index)
        if not target_slot:
            raise RuntimeError(f"Center slot not found: index {center_slot_index}")

        for idx, segment in enumerate(segments, 1):
            icon = find_icon_by_index(config, segment["iconIndex"])
            if not icon:
                print(f"Skipping {segment['segmentKey']}: icon index {segment['iconIndex']} not found in JSON")
                continue

            print(f"\n[{idx}/{len(segments)}] Backfilling {segment['segmentKey']} from icon {segment['iconIndex']}")
            before_layout = player._inspect_runtime_layout_bounds()
            drag_success = player.corrected_drag_and_drop(icon, target_slot, is_first_icon=(idx == 1), use_cdp=True)
            if not drag_success:
                print(f"  Drag failed for {segment['segmentKey']}; no recordingArea written")
                continue

            area = player.calculate_runtime_recording_area_after_drop(
                before_layout if isinstance(before_layout, dict) else {},
                target_slot,
                segment["iconIndex"],
                segment["segmentKey"],
            )
            area["backfilledAt"] = datetime.now().isoformat()
            area["backfillSource"] = {
                "projectFolder": str(project_dir),
                "sourceJson": str(source_json_path),
                "timestamps": str(timestamps_path),
            }
            recording_areas[segment["segmentKey"]] = area

            player.corrected_return_icon(target_slot, use_cdp=True)
            time.sleep(0.2)

    finally:
        try:
            player.close_game_window()
        except Exception:
            pass

    if not recording_areas:
        raise RuntimeError("No recordingAreas were detected; files were not updated.")

    existing_areas = manifest.get("recordingAreas", {}) if isinstance(manifest.get("recordingAreas"), dict) else {}
    merged_areas = {**existing_areas, **recording_areas}
    update_manifest(manifest, merged_areas)
    updated_timestamp_events = update_timestamps_metadata(timestamps, segments)

    if not no_backup:
        manifest_backup = backup_file(manifest_path)
        timestamps_backup = backup_file(timestamps_path)
        print(f"Backups: {manifest_backup}, {timestamps_backup}")

    write_json(manifest_path, manifest)
    write_json(timestamps_path, timestamps)

    print("\nBackfill complete.")
    print(f"Recording areas written: {len(recording_areas)}")
    print(f"Manifest updated: {manifest_path}")
    print(f"Timestamps metadata events updated: {updated_timestamp_events}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill per-segment recordingArea metadata for old showcase recordings.")
    parser.add_argument("project", nargs="?", help="Project folder containing manifest.json and showcase timestamps")
    parser.add_argument("--project", dest="project_opt", help="Project folder containing manifest.json and showcase timestamps")
    parser.add_argument("--json", dest="json_path", help="Old source JSON path")
    parser.add_argument("--manifest", help="Manifest path; defaults to <project>/manifest.json")
    parser.add_argument("--timestamps", help="Timestamps path; defaults to newest *showcase.timestamps.json in project")
    parser.add_argument("--no-backup", action="store_true", help="Do not create .bak timestamp/manifest backups before writing")
    parser.add_argument("--limit", type=int, help="Backfill only the first N segments for testing")
    args = parser.parse_args()

    project_raw = args.project_opt or args.project
    project_dir = Path(project_raw) if project_raw else prompt_path("Project folder: ")
    if not project_dir.exists() or not project_dir.is_dir():
        raise FileNotFoundError(f"Project folder not found: {project_dir}")

    manifest_path = find_manifest(project_dir, args.manifest)
    manifest = read_json(manifest_path)
    timestamps_path = find_timestamps(project_dir, args.timestamps)
    source_json_path = find_source_json(project_dir, manifest, args.json_path)

    backfill(project_dir, source_json_path, manifest_path, timestamps_path, args.no_backup, args.limit)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.")
        raise SystemExit(130)
    except Exception as exc:
        print(f"ERROR: {exc}")
        raise SystemExit(1)

"""
Backfill the single fixed recordingArea for an already-recorded showcase.

Current production policy:
  - one recordingArea per mod, stored at manifest.layoutSummary.recordingArea
  - fixed size 700x480
  - bottom anchored to runtime Mute Buttons boundsBottomY, fallback 670
  - horizontally centered on the center slot when source JSON is available,
    otherwise centered on the default stage center X=999

This tool does not start OBS, does not record video, and does not drag icons.
It only opens the mod, injects/uses the runtime inspector, calculates bottomY,
then updates manifest.json back to the legacy-compatible single recordingArea
shape.

Usage:
    python backfill_recording_areas.py
    python backfill_recording_areas.py "G:\\SprunkiOUTPUT\\MyProject"
    python backfill_recording_areas.py --project "G:\\SprunkiOUTPUT\\MyProject" --json "G:\\SprunkiOUTPUT\\JSON\\MyProject.json"
    python backfill_recording_areas.py --batch-root "D:\\OUTPUT"
"""

from __future__ import annotations

import argparse
import json
import shutil
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


def has_manifest_backup(project_dir: Path) -> bool:
    return any(project_dir.glob("manifest.json.bak-*"))


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

            configured_json_dir = get_config().get("output.json_output_directory")
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
        key = str(path.resolve()) if path.exists() else str(path)
        if key not in seen:
            deduped.append(path)
            seen.add(key)
    return deduped


def find_source_json(project_dir: Path, manifest: Dict[str, Any], explicit: Optional[str]) -> Optional[Path]:
    if explicit:
        path = Path(explicit)
        if not path.exists():
            raise FileNotFoundError(f"Source JSON not found: {path}")
        return path

    for path in candidate_json_paths(project_dir, manifest):
        if path.exists():
            return path
    return None


def source_url_from_manifest(manifest: Dict[str, Any]) -> Optional[str]:
    candidates = [
        manifest.get("projectInfo", {}).get("sourceUrl"),
        manifest.get("source", {}).get("value"),
    ]
    for value in candidates:
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value
    return None


def make_runtime_config(manifest: Dict[str, Any], source_json: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if source_json:
        config = dict(source_json)
    else:
        config = {}

    source_url = config.get("currentUrl") or source_url_from_manifest(manifest)
    if not source_url:
        raise RuntimeError("Could not determine source URL from manifest or source JSON")

    config.setdefault("currentUrl", source_url)
    config.setdefault("layout", {})
    config.setdefault("gameSettings", {})
    config["gameSettings"]["hasHorror"] = False
    return config


def public_recording_area(area: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "x": int(area.get("x", 0)),
        "y": int(area.get("y", 0)),
        "width": int(area.get("width", 700)),
        "height": int(area.get("height", 480)),
        "aspectRatio": area.get("aspectRatio", "35:24"),
    }


def update_manifest(manifest: Dict[str, Any], area: Dict[str, Any]) -> None:
    layout_summary = manifest.setdefault("layoutSummary", {})
    layout_summary.pop("recordingAreaMode", None)
    layout_summary.pop("legacyRecordingArea", None)
    layout_summary["recordingArea"] = public_recording_area(area)
    manifest.pop("recordingAreas", None)

    processing = manifest.setdefault("processingSummary", {})
    processing["hasHorror"] = False
    processing.pop("effectiveMode", None)
    processing.pop("configuredHasHorror", None)


def backfill(project_dir: Path, manifest_path: Path, source_json_path: Optional[Path]) -> None:
    manifest = read_json(manifest_path)
    source_json = read_json(source_json_path) if source_json_path else None
    runtime_config = make_runtime_config(manifest, source_json)

    print(f"Project: {project_dir}")
    print(f"Manifest: {manifest_path}")
    print(f"Source JSON: {source_json_path or '(not found; using manifest URL and default center X)'}")
    print(f"URL: {runtime_config['currentUrl']}")

    player = SprunkiGamePlayer()
    player.config = runtime_config

    try:
        if not player.setup_game_environment(first_setup=True):
            raise RuntimeError("Game environment setup failed")

        slots = runtime_config.get("layout", {}).get("slots", [])
        target_slot = None
        if slots:
            center_slot_index = player.calculate_center_slot_index(slots)
            target_slot = player._find_slot_by_index(center_slot_index)
            if not target_slot:
                print(f"WARNING: center slot {center_slot_index} not found; using default center X")
        else:
            print("WARNING: source JSON has no slots; using default center X")

        area = player.calculate_fixed_runtime_recording_area(target_slot, width=700.0, height=480.0)
        debug = area.get("_runtimeDebug", {})
        print(f"Detected recordingArea: {public_recording_area(area)}")
        print(f"Bottom debug: {debug.get('bottomLine')}")

    finally:
        try:
            player.close_game_window()
        except Exception:
            pass

    update_manifest(manifest, area)

    manifest_backup = backup_file(manifest_path)
    print(f"Backup: {manifest_backup}")

    write_json(manifest_path, manifest)
    print("\nBackfill complete.")
    print(f"Manifest updated: {manifest_path}")


def iter_batch_projects(root_dir: Path) -> List[Path]:
    projects: List[Path] = []
    for child in sorted(root_dir.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir():
            continue
        if child.name.lower() == "json":
            continue
        if (child / "manifest.json").exists():
            projects.append(child)
    return projects


def batch_backfill(root_dir: Path, no_skip_existing_backup: bool = False) -> None:
    projects = iter_batch_projects(root_dir)
    if not projects:
        raise RuntimeError(f"No project folders with manifest.json found in {root_dir}")

    print(f"Batch root: {root_dir}")
    print(f"Projects found: {len(projects)}")

    summary = {
        "processed": [],
        "skipped": [],
        "failed": [],
    }

    for index, project_dir in enumerate(projects, 1):
        print("\n" + "=" * 80)
        print(f"[{index}/{len(projects)}] {project_dir.name}")

        if has_manifest_backup(project_dir) and not no_skip_existing_backup:
            print("Skipping: manifest.json backup already exists, assuming this project was already backfilled.")
            summary["skipped"].append(str(project_dir))
            continue

        try:
            manifest_path = find_manifest(project_dir, None)
            manifest = read_json(manifest_path)
            source_json_path = find_source_json(project_dir, manifest, None)
            backfill(project_dir, manifest_path, source_json_path)
            summary["processed"].append(str(project_dir))
        except Exception as exc:
            print(f"FAILED: {exc}")
            summary["failed"].append({"project": str(project_dir), "error": str(exc)})

    print("\n" + "=" * 80)
    print("Batch backfill complete.")
    print(f"Processed: {len(summary['processed'])}")
    print(f"Skipped: {len(summary['skipped'])}")
    print(f"Failed: {len(summary['failed'])}")
    if summary["failed"]:
        print("Failed projects:")
        for item in summary["failed"]:
            print(f"  - {item['project']}: {item['error']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill single fixed 700x480 recordingArea into manifest.json.")
    parser.add_argument("project", nargs="?", help="Project folder containing manifest.json")
    parser.add_argument("--project", dest="project_opt", help="Project folder containing manifest.json")
    parser.add_argument("--batch-root", help="Root output folder containing JSON and project subfolders, e.g. D:\\OUTPUT")
    parser.add_argument("--json", dest="json_path", help="Old source JSON path; optional but recommended for center slot X")
    parser.add_argument("--manifest", help="Manifest path; defaults to <project>/manifest.json")
    parser.add_argument("--no-skip-existing-backup", action="store_true", help="In batch mode, process even if manifest.json.bak-* already exists")
    args = parser.parse_args()

    if args.batch_root:
        root_dir = Path(args.batch_root)
        if not root_dir.exists() or not root_dir.is_dir():
            raise FileNotFoundError(f"Batch root folder not found: {root_dir}")
        batch_backfill(root_dir, no_skip_existing_backup=args.no_skip_existing_backup)
        return 0

    project_raw = args.project_opt or args.project
    project_dir = Path(project_raw) if project_raw else prompt_path("Project folder: ")
    if not project_dir.exists() or not project_dir.is_dir():
        raise FileNotFoundError(f"Project folder not found: {project_dir}")

    manifest_path = find_manifest(project_dir, args.manifest)
    manifest = read_json(manifest_path)
    source_json_path = find_source_json(project_dir, manifest, args.json_path)

    backfill(project_dir, manifest_path, source_json_path)
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

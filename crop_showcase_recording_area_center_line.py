"""
Crop showcase video frames by manifest.layoutSummary.recordingArea and draw a
vertical center line on the crop.

Usage:
    python crop_showcase_recording_area_center_line.py "G:\\SprunkiOUTPUT\\Project"
    python crop_showcase_recording_area_center_line.py --list projects.txt --segment n-1
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image, ImageDraw, ImageFont

MASTER_CROP_LEFT = 78.0
MASTER_CROP_TOP = 22.0
MASTER_CROP_RIGHT = 0.0
MASTER_CROP_BOTTOM = 22.0
SCREEN_W = 1920.0
SCREEN_H = 1080.0


def read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def find_video(project_dir: Path, manifest: Dict[str, Any]) -> Path:
    master = manifest.get("outputs", {}).get("masterVideo") or {}
    filename = master.get("filename") if isinstance(master, dict) else None
    if filename and (project_dir / filename).exists():
        return project_dir / filename
    candidates = list(project_dir.glob("*showcase.mkv")) + list(project_dir.glob("*showcase.mp4"))
    if not candidates:
        raise FileNotFoundError(f"No showcase video found in {project_dir}")
    return sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)[0]


def find_timestamps(project_dir: Path) -> Optional[Path]:
    candidates = list(project_dir.glob("*showcase.timestamps.json")) + list(project_dir.glob("*.timestamps.json"))
    if not candidates:
        return None
    return sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)[0]


def segment_timestamp(timestamps: Dict[str, Any], segment_key: str) -> Optional[float]:
    inferred = 0
    for event in sorted(timestamps.get("events", []), key=lambda e: e.get("timestamp", 0)):
        if event.get("event") == "start_full_remix_performance":
            break
        if event.get("event") != "start_listen":
            continue
        metadata = event.get("metadata", {}) or {}
        key = metadata.get("segmentKey") or metadata.get("recordingAreaId") or f"n-{inferred + 1}"
        if str(key) == segment_key:
            return float(event.get("timestamp", 0))
        inferred += 1
    return None


def extract_frame(video_path: Path, timestamp: float, output_path: Path) -> None:
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{timestamp:.3f}",
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(output_path),
    ]
    subprocess.run(cmd, check=True)


def font(size: int) -> ImageFont.ImageFont:
    for path in ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/segoeui.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()


def screen_area_to_video_area(area: Dict[str, Any]) -> Dict[str, Any]:
    """Convert live screen recordingArea coords to OBS master output video coords."""
    content_w = SCREEN_W - MASTER_CROP_LEFT - MASTER_CROP_RIGHT
    content_h = SCREEN_H - MASTER_CROP_TOP - MASTER_CROP_BOTTOM
    scale_x = SCREEN_W / content_w
    scale_y = SCREEN_H / content_h

    x = (float(area["x"]) - MASTER_CROP_LEFT) * scale_x
    y = (float(area["y"]) - MASTER_CROP_TOP) * scale_y
    width = float(area["width"]) * scale_x
    height = float(area["height"]) * scale_y

    converted = {
        "x": int(round(max(0.0, min(SCREEN_W - 1.0, x)))),
        "y": int(round(max(0.0, min(SCREEN_H - 1.0, y)))),
        "width": int(round(max(1.0, min(SCREEN_W, width)))),
        "height": int(round(max(1.0, min(SCREEN_H, height)))),
        "aspectRatio": area.get("aspectRatio", "35:24"),
    }
    if converted["x"] + converted["width"] > int(SCREEN_W):
        converted["width"] = int(SCREEN_W) - converted["x"]
    if converted["y"] + converted["height"] > int(SCREEN_H):
        converted["height"] = int(SCREEN_H) - converted["y"]
    converted["_transform"] = {
        "sourceCoordinateSpace": "screen_1920x1080_before_obs_master_crop_scale",
        "outputCoordinateSpace": "video_1920x1080_after_obs_master_crop_scale",
        "cropLeft": MASTER_CROP_LEFT,
        "cropTop": MASTER_CROP_TOP,
        "cropRight": MASTER_CROP_RIGHT,
        "cropBottom": MASTER_CROP_BOTTOM,
        "scaleX": scale_x,
        "scaleY": scale_y,
        "sourceArea": area,
    }
    return converted


def crop_with_center_line(frame_path: Path, output_path: Path, area: Dict[str, Any], label: str) -> Dict[str, Any]:
    image = Image.open(frame_path).convert("RGB")
    x = max(0, int(round(float(area["x"]))))
    y = max(0, int(round(float(area["y"]))))
    w = max(1, int(round(float(area["width"]))))
    h = max(1, int(round(float(area["height"]))))
    right = min(image.width, x + w)
    bottom = min(image.height, y + h)
    crop = image.crop((x, y, right, bottom))

    draw = ImageDraw.Draw(crop)
    center_x = crop.width / 2.0
    draw.line([center_x, 0, center_x, crop.height], fill="#00ddff", width=4)
    draw.rectangle([8, 8, min(crop.width - 8, 680), 68], fill="black", outline="#00ddff", width=1)
    draw.text((18, 14), label, fill="#ffffff", font=font(20))
    draw.text((18, 40), f"crop={crop.width}x{crop.height}, centerX={center_x:.1f}", fill="#dddddd", font=font(16))
    crop.save(output_path)
    return {
        "cropX": x,
        "cropY": y,
        "cropWidth": crop.width,
        "cropHeight": crop.height,
        "centerX": center_x,
    }


def draw_full_overlay(frame_path: Path, output_path: Path, area: Dict[str, Any], label: str) -> None:
    image = Image.open(frame_path).convert("RGB")
    draw = ImageDraw.Draw(image)
    x = int(round(float(area["x"])))
    y = int(round(float(area["y"])))
    w = int(round(float(area["width"])))
    h = int(round(float(area["height"])))
    cx = x + w / 2.0
    draw.rectangle([x, y, x + w, y + h], outline="#00ff66", width=5)
    draw.line([cx, y, cx, y + h], fill="#00ddff", width=4)
    draw.rectangle([12, 12, min(image.width - 12, 1100), 70], fill="black", outline="#00ff66", width=1)
    draw.text((24, 20), f"{label}: area x={x}, y={y}, w={w}, h={h}, centerX={cx:.1f}", fill="#ffffff", font=font(20))
    image.save(output_path)


def process_project(project_dir: Path, output_root: Path, segment: str, frame_offset: float, fallback_timestamp: float, convert_from_screen: bool) -> Dict[str, Any]:
    manifest_path = project_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"manifest.json not found: {manifest_path}")
    manifest = read_json(manifest_path)
    area = manifest.get("layoutSummary", {}).get("recordingArea")
    if not isinstance(area, dict):
        raise RuntimeError("manifest.layoutSummary.recordingArea missing")
    crop_area = screen_area_to_video_area(area) if convert_from_screen else area
    video_path = find_video(project_dir, manifest)
    timestamps_path = find_timestamps(project_dir)

    start = None
    if timestamps_path:
        start = segment_timestamp(read_json(timestamps_path), segment)
    timestamp = (start + frame_offset) if start is not None else fallback_timestamp

    out_dir = output_root / project_dir.name
    out_dir.mkdir(parents=True, exist_ok=True)
    frame_path = out_dir / f"{segment}_frame.jpg"
    crop_path = out_dir / f"{segment}_recording_area_crop_center_line.jpg"
    overlay_path = out_dir / f"{segment}_full_overlay.jpg"

    extract_frame(video_path, timestamp, frame_path)
    measurements = crop_with_center_line(frame_path, crop_path, crop_area, f"{project_dir.name} {segment}")
    draw_full_overlay(frame_path, overlay_path, crop_area, f"{project_dir.name} {segment}")
    return {
        "project": str(project_dir),
        "success": True,
        "video": str(video_path),
        "timestamps": str(timestamps_path) if timestamps_path else None,
        "segment": segment,
        "frameTimestamp": timestamp,
        "manifestRecordingArea": area,
        "cropRecordingArea": crop_area,
        "convertedFromScreenCoords": convert_from_screen,
        "measurements": measurements,
        "frame": str(frame_path),
        "cropCenterLine": str(crop_path),
        "fullOverlay": str(overlay_path),
    }


def read_project_list(path: Path) -> List[Path]:
    projects = []
    for line in path.read_text(encoding="utf-8").splitlines():
        value = line.strip().strip('"')
        if value:
            projects.append(Path(value))
    return projects


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crop showcase frames by recordingArea and draw crop center line.")
    parser.add_argument("projects", nargs="*", help="Project folders")
    parser.add_argument("--list", dest="list_path", help="Text file containing project folders")
    parser.add_argument("--segment", default="n-1", help="Segment key to extract, default n-1")
    parser.add_argument("--frame-offset", type=float, default=1.0, help="Seconds after segment start_listen")
    parser.add_argument("--fallback-timestamp", type=float, default=10.0, help="Used if timestamps are missing")
    parser.add_argument("--output-dir", default="showcase_recording_area_center_line_crops")
    parser.add_argument("--no-convert", action="store_true", help="Use manifest recordingArea directly without OBS master crop/scale conversion")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    projects = [Path(value.strip('"')) for value in args.projects]
    if args.list_path:
        projects.extend(read_project_list(Path(args.list_path)))
    if not projects:
        raise RuntimeError("Provide at least one project folder or --list")

    output_root = Path(args.output_dir) / datetime.now().strftime("%Y%m%d-%H%M%S")
    output_root.mkdir(parents=True, exist_ok=True)
    summary = {"generatedAt": datetime.now().isoformat(), "outputRoot": str(output_root), "projects": []}

    for index, project_dir in enumerate(projects, 1):
        print("\n" + "=" * 80)
        print(f"[{index}/{len(projects)}] {project_dir}")
        try:
            result = process_project(project_dir, output_root, args.segment, args.frame_offset, args.fallback_timestamp, not args.no_convert)
            summary["projects"].append(result)
            print(f"Crop: {result['cropCenterLine']}")
            print(f"Overlay: {result['fullOverlay']}")
        except Exception as exc:
            print(f"FAILED: {exc}")
            summary["projects"].append({"project": str(project_dir), "success": False, "error": str(exc)})

    summary_path = output_root / "result.json"
    write_json(summary_path, summary)
    print("\nDone.")
    print(f"Output: {output_root.resolve()}")
    print(f"Summary: {summary_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

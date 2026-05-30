"""
Draw a manifest recordingArea overlay on a real showcase video frame.

This is a diagnostic script for already-recorded Sprunki outputs. It reads
manifest.recordingAreas[segment], extracts a frame from the showcase video at
the segment timestamp, then writes:
  - full frame
  - full frame with recordingArea rectangle + bottomY line
  - cropped recordingArea frame

Usage:
    python draw_recording_area_overlay_from_project.py "G:\\SprunkiOUTPUT\\Project" --segment n-1
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from PIL import Image, ImageDraw, ImageFont


def read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def find_project_file(project_dir: Path, manifest: Dict[str, Any], kind: str) -> Path:
    if kind == "video":
        master = manifest.get("outputs", {}).get("masterVideo") or {}
        filename = master.get("filename") if isinstance(master, dict) else None
        if filename and (project_dir / filename).exists():
            return project_dir / filename
        candidates = list(project_dir.glob("*showcase.mkv")) + list(project_dir.glob("*showcase.mp4"))
    elif kind == "timestamps":
        candidates = list(project_dir.glob("*showcase.timestamps.json")) + list(project_dir.glob("*.timestamps.json"))
    else:
        raise ValueError(kind)

    if not candidates:
        raise FileNotFoundError(f"No {kind} file found in {project_dir}")
    return sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)[0]


def find_segment_timestamp(timestamps: Dict[str, Any], segment_key: str) -> Optional[float]:
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


def draw_overlay(frame_path: Path, output_path: Path, area: Dict[str, Any], segment_key: str) -> None:
    image = Image.open(frame_path).convert("RGB")
    draw = ImageDraw.Draw(image)
    label_font = font(22)
    small_font = font(18)

    x = int(round(float(area["x"])))
    y = int(round(float(area["y"])))
    w = int(round(float(area["width"])))
    h = int(round(float(area["height"])))
    bottom_y = float(area.get("bottomY", y + h))

    draw.rectangle([x, y, x + w, y + h], outline="#00ff66", width=5)
    draw.line([0, bottom_y, image.width, bottom_y], fill="#00ff66", width=3)

    info = (
        f"{segment_key}: recordingArea x={x}, y={y}, w={w}, h={h}, "
        f"right={x + w}, bottom={y + h}, bottomY={bottom_y:.2f}"
    )
    box_w = min(image.width - 24, 1180)
    draw.rectangle([12, 12, box_w, 78], fill="black", outline="#00ff66", width=1)
    draw.text((24, 22), info, fill="#00ff66", font=label_font)
    if area.get("source"):
        draw.text((24, 50), f"source: {area.get('source')}", fill="#cccccc", font=small_font)

    image.save(output_path)


def crop_area(frame_path: Path, output_path: Path, area: Dict[str, Any]) -> None:
    image = Image.open(frame_path).convert("RGB")
    x = max(0, int(round(float(area["x"]))))
    y = max(0, int(round(float(area["y"]))))
    w = max(1, int(round(float(area["width"]))))
    h = max(1, int(round(float(area["height"]))))
    right = min(image.width, x + w)
    bottom = min(image.height, y + h)
    image.crop((x, y, right, bottom)).save(output_path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Draw a manifest recordingArea overlay on a showcase video frame.")
    parser.add_argument("project", help="Project folder containing manifest.json, showcase video, and timestamps")
    parser.add_argument("--segment", default="n-1", help="Segment key, for example n-1")
    parser.add_argument("--frame-offset", type=float, default=1.0, help="Seconds after start_listen to extract")
    parser.add_argument("--output-dir", default=None, help="Output directory; default project/recording_area_overlays")
    args = parser.parse_args()

    project_dir = Path(args.project)
    manifest_path = project_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(manifest_path)

    manifest = read_json(manifest_path)
    areas = manifest.get("recordingAreas", {})
    if not isinstance(areas, dict) or args.segment not in areas:
        raise RuntimeError(f"manifest.recordingAreas.{args.segment} is missing")

    video_path = find_project_file(project_dir, manifest, "video")
    timestamps_path = find_project_file(project_dir, manifest, "timestamps")
    timestamps = read_json(timestamps_path)
    start_time = find_segment_timestamp(timestamps, args.segment)
    if start_time is None:
        raise RuntimeError(f"Could not find start_listen for {args.segment}")

    output_dir = Path(args.output_dir) if args.output_dir else project_dir / "recording_area_overlays"
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = start_time + args.frame_offset
    frame_path = output_dir / f"{args.segment}_frame.jpg"
    overlay_path = output_dir / f"{args.segment}_overlay.jpg"
    crop_path = output_dir / f"{args.segment}_crop.jpg"

    extract_frame(video_path, timestamp, frame_path)
    draw_overlay(frame_path, overlay_path, areas[args.segment], args.segment)
    crop_area(frame_path, crop_path, areas[args.segment])

    summary = {
        "project": str(project_dir),
        "video": str(video_path),
        "timestamps": str(timestamps_path),
        "segment": args.segment,
        "startListen": start_time,
        "frameTimestamp": timestamp,
        "recordingArea": areas[args.segment],
        "outputs": {
            "frame": str(frame_path),
            "overlay": str(overlay_path),
            "crop": str(crop_path),
        },
    }
    summary_path = output_dir / f"{args.segment}_overlay_summary.json"
    with summary_path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f"Frame: {frame_path.resolve()}")
    print(f"Overlay: {overlay_path.resolve()}")
    print(f"Crop: {crop_path.resolve()}")
    print(f"Summary: {summary_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

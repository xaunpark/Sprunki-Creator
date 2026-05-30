"""
Create visual mockups from per-segment recordingAreas in a Sprunki project.

For each normal segment n-*, this extracts a frame from the showcase video,
crops it using manifest.recordingAreas[segmentKey], then creates comparison
mockups for 2..10 mods by stretching that same cropped image into every cell.

Usage:
    python create_recording_area_comparison_mockups.py "G:\\SprunkiOUTPUT\\Project"
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

from PIL import Image, ImageDraw, ImageFont


TOTAL_WIDTH = 1920
TOTAL_HEIGHT = 1080
HEADER_HEIGHT = 250
VIDEO_HEIGHT = 830
BORDER = 10


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


def parse_segments(timestamps: Dict[str, Any]) -> List[Dict[str, Any]]:
    segments = []
    inferred = 0
    for event in sorted(timestamps.get("events", []), key=lambda e: e.get("timestamp", 0)):
        if event.get("event") == "start_full_remix_performance":
            break
        if event.get("event") != "start_listen":
            continue
        metadata = event.get("metadata", {}) or {}
        segment_key = metadata.get("segmentKey") or metadata.get("recordingAreaId") or f"n-{inferred + 1}"
        if not str(segment_key).startswith("n-"):
            continue
        icon_index = metadata.get("icon_index", metadata.get("iconIndex", inferred))
        try:
            icon_index = int(icon_index)
        except Exception:
            icon_index = inferred
        segments.append({
            "segmentKey": str(segment_key),
            "iconIndex": icon_index,
            "timestamp": float(event.get("timestamp", 0)),
            "label": event.get("label", str(segment_key)),
        })
        inferred += 1
    return segments


def comparison_layout(num_mods: int) -> Tuple[int, List[int]]:
    if num_mods in (2, 3):
        return 1, [num_mods]
    if num_mods == 4:
        return 1, [4]
    if num_mods == 5:
        return 1, [5]
    if num_mods == 6:
        return 2, [3, 3]
    if num_mods == 7:
        return 2, [4, 3]
    if num_mods == 8:
        return 2, [4, 4]
    if num_mods == 9:
        return 2, [5, 4]
    if num_mods == 10:
        return 2, [5, 5]
    raise ValueError(num_mods)


def cell_rects(num_mods: int) -> List[Tuple[int, int, int, int]]:
    rows, cols_per_row = comparison_layout(num_mods)
    max_cols = max(cols_per_row)
    cell_w = (TOTAL_WIDTH // max_cols // 2) * 2
    cell_h = (VIDEO_HEIGHT // rows // 2) * 2
    rects: List[Tuple[int, int, int, int]] = []
    for row, cols in enumerate(cols_per_row):
        row_w = cols * cell_w
        start_x = (TOTAL_WIDTH - row_w) // 2
        y = HEADER_HEIGHT + row * cell_h
        for col in range(cols):
            rects.append((start_x + col * cell_w, y, cell_w, cell_h))
    return rects[:num_mods]


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


def crop_area(image: Image.Image, area: Dict[str, Any]) -> Image.Image:
    x = max(0, int(round(area["x"])))
    y = max(0, int(round(area["y"])))
    w = max(1, int(round(area["width"])))
    h = max(1, int(round(area["height"])))
    right = min(image.width, x + w)
    bottom = min(image.height, y + h)
    return image.crop((x, y, right, bottom))


def font(size: int) -> ImageFont.ImageFont:
    for path in ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/segoeui.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()


def draw_mockup(crop: Image.Image, num_mods: int, segment_key: str, area: Dict[str, Any], output_path: Path) -> None:
    canvas = Image.new("RGB", (TOTAL_WIDTH, TOTAL_HEIGHT), (8, 8, 8))
    draw = ImageDraw.Draw(canvas)
    title_font = font(34)
    small_font = font(20)
    draw.text((32, 28), f"{segment_key} - recordingArea {area['width']}x{area['height']} @ ({area['x']},{area['y']})", fill=(245, 245, 245), font=title_font)
    draw.text((32, 78), f"Mock comparison: {num_mods} mods, same cropped frame stretched to every cell", fill=(180, 180, 180), font=small_font)

    for idx, (x, y, w, h) in enumerate(cell_rects(num_mods), 1):
        fitted = crop.resize((w, h), Image.Resampling.LANCZOS)
        canvas.paste(fitted, (x, y))
        draw.rectangle((x, y, x + w - 1, y + h - 1), outline=(235, 235, 235), width=2)
        draw.rectangle((x + 8, y + 8, x + 70, y + 38), fill=(0, 0, 0))
        draw.text((x + 16, y + 12), str(idx), fill=(255, 255, 255), font=small_font)

    canvas.save(output_path)


def create_contact_sheet(crop_paths: List[Path], output_path: Path) -> None:
    thumbs = []
    for path in crop_paths:
        img = Image.open(path).convert("RGB")
        img.thumbnail((220, 180), Image.Resampling.LANCZOS)
        thumbs.append((path.stem, img.copy()))
    if not thumbs:
        return
    cols = 5
    rows = (len(thumbs) + cols - 1) // cols
    cell_w, cell_h = 260, 230
    sheet = Image.new("RGB", (cols * cell_w, rows * cell_h), (20, 20, 20))
    draw = ImageDraw.Draw(sheet)
    label_font = font(18)
    for idx, (label, img) in enumerate(thumbs):
        col = idx % cols
        row = idx // cols
        x = col * cell_w + (cell_w - img.width) // 2
        y = row * cell_h + 34
        sheet.paste(img, (x, y))
        draw.text((col * cell_w + 16, row * cell_h + 8), label, fill=(245, 245, 245), font=label_font)
    sheet.save(output_path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Create recordingArea comparison mockups for 2..10 mods.")
    parser.add_argument("project", help="Project folder containing manifest.json, showcase video, and timestamps")
    parser.add_argument("--frame-offset", type=float, default=1.0, help="Seconds after start_listen to extract frame")
    parser.add_argument("--limit", type=int, default=None, help="Only process first N normal segments")
    args = parser.parse_args()

    project_dir = Path(args.project)
    manifest_path = project_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(manifest_path)

    manifest = read_json(manifest_path)
    areas = manifest.get("recordingAreas", {})
    if not isinstance(areas, dict) or not areas:
        raise RuntimeError("manifest.recordingAreas is missing or empty")

    video_path = find_project_file(project_dir, manifest, "video")
    timestamps_path = find_project_file(project_dir, manifest, "timestamps")
    timestamps = read_json(timestamps_path)
    segments = parse_segments(timestamps)
    if args.limit:
        segments = segments[:args.limit]

    output_dir = project_dir / "recording_area_mockups"
    frames_dir = output_dir / "frames"
    crops_dir = output_dir / "crops"
    mockups_dir = output_dir / "mockups"
    frames_dir.mkdir(parents=True, exist_ok=True)
    crops_dir.mkdir(parents=True, exist_ok=True)
    mockups_dir.mkdir(parents=True, exist_ok=True)

    crop_paths: List[Path] = []
    summary = {
        "project": str(project_dir),
        "video": str(video_path),
        "timestamps": str(timestamps_path),
        "frameOffset": args.frame_offset,
        "segments": [],
    }

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        for segment in segments:
            key = segment["segmentKey"]
            area = areas.get(key)
            if not area:
                print(f"Skipping {key}: no recordingArea")
                continue

            frame_ts = max(0.0, float(segment["timestamp"]) + args.frame_offset)
            temp_frame = tmp_dir / f"{key}.jpg"
            print(f"Extracting {key} at {frame_ts:.3f}s")
            extract_frame(video_path, frame_ts, temp_frame)

            frame = Image.open(temp_frame).convert("RGB")
            frame_path = frames_dir / f"{key}_frame.jpg"
            frame.save(frame_path, quality=92)

            crop = crop_area(frame, area)
            crop_path = crops_dir / f"{key}_crop.jpg"
            crop.save(crop_path, quality=95)
            crop_paths.append(crop_path)

            segment_outputs = []
            for num_mods in range(2, 11):
                output_path = mockups_dir / f"{key}_{num_mods}mods.png"
                draw_mockup(crop, num_mods, key, area, output_path)
                segment_outputs.append(str(output_path))

            summary["segments"].append({
                "segmentKey": key,
                "iconIndex": segment["iconIndex"],
                "frameTimestamp": frame_ts,
                "recordingArea": area,
                "frame": str(frame_path),
                "crop": str(crop_path),
                "mockups": segment_outputs,
            })

    create_contact_sheet(crop_paths, output_dir / "crop_contact_sheet.png")
    with (output_dir / "mockup_summary.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    print(f"\nDone. Output: {output_dir}")
    print(f"Segments processed: {len(summary['segments'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

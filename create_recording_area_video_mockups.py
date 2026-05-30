"""
Create real video comparison mockups from one segment in a showcase recording.

It reads:
  - manifest.recordingAreas[segmentKey]
  - *showcase.timestamps.json start/end times
  - showcase video

Then crops that segment and stretches the same cropped clip into every cell for
the requested comparison layouts.

Usage:
    python create_recording_area_video_mockups.py "G:\\SprunkiOUTPUT\\Project" --segment n-1 --layouts 2 3 4 5
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Tuple


TOTAL_W = 1920
TOTAL_H = 1080
HEADER_H = 250
VIDEO_H = 830


def read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def find_showcase_video(project_dir: Path, manifest: Dict[str, Any]) -> Path:
    master = manifest.get("outputs", {}).get("masterVideo") or {}
    filename = master.get("filename") if isinstance(master, dict) else None
    if filename and (project_dir / filename).exists():
        return project_dir / filename
    candidates = list(project_dir.glob("*showcase.mkv")) + list(project_dir.glob("*showcase.mp4"))
    if not candidates:
        raise FileNotFoundError(f"No showcase video found in {project_dir}")
    return sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)[0]


def find_timestamps(project_dir: Path) -> Path:
    candidates = list(project_dir.glob("*showcase.timestamps.json")) + list(project_dir.glob("*.timestamps.json"))
    if not candidates:
        raise FileNotFoundError(f"No timestamps file found in {project_dir}")
    return sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)[0]


def segment_times(timestamps: Dict[str, Any], segment_key: str) -> Tuple[float, float]:
    events = sorted(timestamps.get("events", []), key=lambda e: e.get("timestamp", 0))
    start = None
    for event in events:
        if event.get("event") != "start_listen":
            continue
        metadata = event.get("metadata", {}) or {}
        if metadata.get("segmentKey") == segment_key or metadata.get("recordingAreaId") == segment_key:
            start = float(event.get("timestamp", 0))
            break
    if start is None:
        # Backward-compatible fallback for n-1, n-2...
        try:
            index = int(segment_key.split("-", 1)[1]) - 1
        except Exception as exc:
            raise ValueError(f"Could not parse segment key: {segment_key}") from exc
        starts = [e for e in events if e.get("event") == "start_listen"]
        if index >= len(starts):
            raise ValueError(f"No start_listen for {segment_key}")
        start = float(starts[index].get("timestamp", 0))

    end = next(
        (
            float(e.get("timestamp", 0))
            for e in events
            if e.get("event") == "end_listen" and float(e.get("timestamp", 0)) > start
        ),
        start + 15.0,
    )
    return start, end


def layout_rects(num_mods: int) -> List[Tuple[int, int, int, int]]:
    if num_mods in (2, 3):
        rows, cols_per_row = 1, [num_mods]
    elif num_mods == 4:
        rows, cols_per_row = 1, [4]
    elif num_mods == 5:
        rows, cols_per_row = 1, [5]
    elif num_mods == 6:
        rows, cols_per_row = 2, [3, 3]
    elif num_mods == 7:
        rows, cols_per_row = 2, [4, 3]
    elif num_mods == 8:
        rows, cols_per_row = 2, [4, 4]
    elif num_mods == 9:
        rows, cols_per_row = 2, [5, 4]
    elif num_mods == 10:
        rows, cols_per_row = 2, [5, 5]
    else:
        raise ValueError(f"Unsupported layout: {num_mods}")

    max_cols = max(cols_per_row)
    cell_w = (TOTAL_W // max_cols // 2) * 2
    cell_h = (VIDEO_H // rows // 2) * 2
    rects: List[Tuple[int, int, int, int]] = []
    for row, cols in enumerate(cols_per_row):
        row_w = cols * cell_w
        start_x = (TOTAL_W - row_w) // 2
        y = HEADER_H + row * cell_h
        for col in range(cols):
            rects.append((start_x + col * cell_w, y, cell_w, cell_h))
    return rects[:num_mods]


def run_ffmpeg(cmd: List[str]) -> None:
    print(" ".join(cmd))
    subprocess.run(cmd, check=True)


def cut_crop_segment(video: Path, area: Dict[str, Any], start: float, end: float, output: Path) -> None:
    duration = max(0.1, end - start)
    crop = f"crop={int(area['width'])}:{int(area['height'])}:{int(area['x'])}:{int(area['y'])},setsar=1"
    cmd = [
        "ffmpeg", "-hide_banner", "-y",
        "-ss", f"{start:.3f}",
        "-i", str(video),
        "-t", f"{duration:.3f}",
        "-vf", crop,
        "-an",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
        str(output),
    ]
    run_ffmpeg(cmd)


def make_mockup_video(cropped_video: Path, num_mods: int, output: Path) -> None:
    rects = layout_rects(num_mods)
    inputs: List[str] = []
    for _ in rects:
        inputs.extend(["-i", str(cropped_video)])

    filters = [f"color=c=black:s={TOTAL_W}x{TOTAL_H}:r=30:d=15[base]"]
    overlay_chain = "[base]"
    for index, (x, y, w, h) in enumerate(rects):
        filters.append(f"[{index}:v]scale={w}:{h}:flags=lanczos,setsar=1[v{index}]")
        out_label = f"tmp{index}" if index < len(rects) - 1 else "vout"
        filters.append(f"{overlay_chain}[v{index}]overlay={x}:{y}:shortest=1[{out_label}]")
        overlay_chain = f"[{out_label}]"

    cmd = [
        "ffmpeg", "-hide_banner", "-y",
        *inputs,
        "-filter_complex", ";".join(filters),
        "-map", "[vout]",
        "-an",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
        "-shortest",
        str(output),
    ]
    run_ffmpeg(cmd)


def main() -> int:
    parser = argparse.ArgumentParser(description="Create real video mockups from one recordingArea segment.")
    parser.add_argument("project", help="Project folder")
    parser.add_argument("--segment", default="n-1", help="Segment key, default n-1")
    parser.add_argument("--layouts", nargs="+", type=int, default=[2, 3, 4, 5], help="Layouts to create")
    args = parser.parse_args()

    project_dir = Path(args.project)
    manifest = read_json(project_dir / "manifest.json")
    timestamps = read_json(find_timestamps(project_dir))
    video = find_showcase_video(project_dir, manifest)
    area = manifest.get("recordingAreas", {}).get(args.segment)
    if not area:
        raise RuntimeError(f"No recordingArea found for {args.segment}")

    start, end = segment_times(timestamps, args.segment)
    out_dir = project_dir / "recording_area_video_mockups" / args.segment
    out_dir.mkdir(parents=True, exist_ok=True)

    cropped = out_dir / f"{args.segment}_cropped.mp4"
    cut_crop_segment(video, area, start, end, cropped)

    outputs = []
    for layout in args.layouts:
        output = out_dir / f"{args.segment}_{layout}mods_video_mockup.mp4"
        make_mockup_video(cropped, layout, output)
        outputs.append(str(output))

    summary = {
        "project": str(project_dir),
        "segment": args.segment,
        "sourceVideo": str(video),
        "start": start,
        "end": end,
        "recordingArea": area,
        "croppedVideo": str(cropped),
        "mockups": outputs,
    }
    with (out_dir / "video_mockup_summary.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    print(f"Done: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
Open recorded project URLs, drag icon 0, and verify fixed recordingArea centering.

For each project folder:
  - read manifest.json and source JSON when available
  - open the mod URL
  - drag icon index 0 into the center slot
  - calculate the current fixed 700x480 runtime recordingArea
  - draw:
      green recordingArea
      cyan center-slot vertical line
      yellow left/right distance arrows from center slot to area edges

Usage:
    python test_project_urls_centered_recording_area.py "G:\\SprunkiOUTPUT\\ProjectA" "G:\\SprunkiOUTPUT\\ProjectB"
    python test_project_urls_centered_recording_area.py --list projects.txt
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pyautogui
import websocket
from PIL import Image, ImageDraw, ImageFont

from config_manager import get_config
from inspect_game_entry import complete_game_entry_sequence
from sprunki_game_player import SprunkiGamePlayer
from test_recording_area_scaled_comparison_mockups import (
    LAYOUT_AND_BOUNDS_JS,
    activate_browser_window,
    eval_js,
    slug_from_url,
)


def read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def font(size: int) -> ImageFont.ImageFont:
    for path in ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/segoeui.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()


def source_url_from_manifest(manifest: Dict[str, Any]) -> Optional[str]:
    candidates = [
        manifest.get("projectInfo", {}).get("sourceUrl"),
        manifest.get("source", {}).get("value"),
    ]
    for value in candidates:
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value
    return None


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


def find_source_json(project_dir: Path, manifest: Dict[str, Any]) -> Optional[Path]:
    for path in candidate_json_paths(project_dir, manifest):
        if path.exists():
            return path
    return None


def make_runtime_config(project_dir: Path) -> Dict[str, Any]:
    manifest_path = project_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"manifest.json not found: {manifest_path}")
    manifest = read_json(manifest_path)
    source_json_path = find_source_json(project_dir, manifest)
    config = read_json(source_json_path) if source_json_path else {}
    source_url = config.get("currentUrl") or source_url_from_manifest(manifest)
    if not source_url:
        raise RuntimeError(f"Could not find URL for {project_dir}")
    config.setdefault("currentUrl", source_url)
    config.setdefault("layout", {})
    config.setdefault("gameSettings", {})
    config["gameSettings"]["hasHorror"] = False
    return config


def normalize_runtime_icon(icon: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "index": int(icon.get("iconIndex", 0)),
        "x": float(icon["pixelX"]),
        "y": float(icon["pixelY"]),
        "name": icon.get("targetName") or icon.get("name") or f"Icon {icon.get('iconIndex')}",
    }


def normalize_runtime_slot(slot: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "index": int(slot.get("slotIndex", 0)),
        "x": float(slot["pixelX"]),
        "y": float(slot["pixelY"]),
        "name": slot.get("targetName") or slot.get("name") or f"Slot {slot.get('slotIndex')}",
    }


def cdp_click(ws: websocket.WebSocket, x: float, y: float) -> None:
    from test_recording_area_scaled_comparison_mockups import send_cdp

    send_cdp(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mouseMoved", "x": x, "y": y}}, timeout=10)
    time.sleep(0.08)
    send_cdp(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1}}, timeout=10)
    time.sleep(0.08)
    send_cdp(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1}}, timeout=10)


def draw_distance_arrow(draw: ImageDraw.ImageDraw, x1: float, x2: float, y: float, color: str, label: str, text_font: ImageFont.ImageFont) -> None:
    draw.line([x1, y, x2, y], fill=color, width=4)
    head = 10
    draw.polygon([(x1, y), (x1 + head, y - head / 2), (x1 + head, y + head / 2)], fill=color)
    draw.polygon([(x2, y), (x2 - head, y - head / 2), (x2 - head, y + head / 2)], fill=color)
    tx = min(x1, x2) + abs(x2 - x1) / 2 - 55
    draw.rectangle([tx - 6, y - 30, tx + 116, y - 5], fill="black")
    draw.text((tx, y - 29), label, fill=color, font=text_font)


def draw_overlay(frame_path: Path, output_path: Path, area: Dict[str, Any], target_slot: Dict[str, Any], project_name: str) -> Dict[str, Any]:
    image = Image.open(frame_path).convert("RGB")
    draw = ImageDraw.Draw(image)
    title_font = font(22)
    small_font = font(17)

    x = int(area["x"])
    y = int(area["y"])
    w = int(area["width"])
    h = int(area["height"])
    right = x + w
    bottom = y + h
    center_x = float(target_slot["x"])
    center_y = float(target_slot["y"])
    area_center_x = x + w / 2
    left_distance = center_x - x
    right_distance = right - center_x
    center_delta = area_center_x - center_x

    draw.rectangle([x, y, right, bottom], outline="#00ff66", width=5)
    draw.line([0, bottom, image.width, bottom], fill="#00ff66", width=3)
    draw.line([center_x, 0, center_x, image.height], fill="#00ddff", width=3)
    draw.line([area_center_x, 0, area_center_x, image.height], fill="#ff55ff", width=2)
    draw.line([center_x - 18, center_y, center_x + 18, center_y], fill="#00ddff", width=4)
    draw.line([center_x, center_y - 18, center_x, center_y + 18], fill="#00ddff", width=4)

    arrow_y = min(image.height - 60, bottom + 42)
    if arrow_y < bottom + 24:
        arrow_y = max(40, y - 42)
    draw_distance_arrow(draw, x, center_x, arrow_y, "#ffd400", f"L {left_distance:.1f}px", small_font)
    draw_distance_arrow(draw, center_x, right, arrow_y + 34, "#ffd400", f"R {right_distance:.1f}px", small_font)

    info1 = f"{project_name}: area x={x}, y={y}, w={w}, h={h}, right={right}, bottom={bottom}"
    info2 = f"slotCenterX={center_x:.2f}, areaCenterX={area_center_x:.2f}, delta={center_delta:.2f}, L={left_distance:.2f}, R={right_distance:.2f}"
    draw.rectangle([12, 12, min(image.width - 12, 1480), 92], fill="black", outline="#00ff66", width=1)
    draw.text((24, 20), info1, fill="#00ff66", font=title_font)
    draw.text((24, 52), info2, fill="#ffffff", font=small_font)
    image.save(output_path)

    return {
        "areaCenterX": area_center_x,
        "slotCenterX": center_x,
        "centerDelta": center_delta,
        "leftDistance": left_distance,
        "rightDistance": right_distance,
    }


def draw_crop_center_line(frame_path: Path, output_path: Path, area: Dict[str, Any], target_slot: Dict[str, Any], project_name: str) -> Dict[str, Any]:
    image = Image.open(frame_path).convert("RGB")
    x = int(area["x"])
    y = int(area["y"])
    w = int(area["width"])
    h = int(area["height"])
    crop = image.crop((x, y, min(image.width, x + w), min(image.height, y + h)))

    draw = ImageDraw.Draw(crop)
    title_font = font(20)
    small_font = font(16)
    crop_center_x = crop.width / 2.0
    slot_center_in_crop = float(target_slot["x"]) - x
    delta = slot_center_in_crop - crop_center_x

    draw.line([crop_center_x, 0, crop_center_x, crop.height], fill="#00ddff", width=4)
    draw.line([slot_center_in_crop, 0, slot_center_in_crop, crop.height], fill="#ff55ff", width=2)
    draw.rectangle([8, 8, min(crop.width - 8, 690), 76], fill="black", outline="#00ddff", width=1)
    draw.text((18, 14), f"{project_name}: crop {crop.width}x{crop.height}", fill="#ffffff", font=title_font)
    draw.text((18, 42), f"cyan=crop center {crop_center_x:.1f}, magenta=slot center {slot_center_in_crop:.1f}, delta={delta:.2f}px", fill="#dddddd", font=small_font)
    crop.save(output_path)

    return {
        "cropWidth": crop.width,
        "cropHeight": crop.height,
        "cropCenterX": crop_center_x,
        "slotCenterInCrop": slot_center_in_crop,
        "slotToCropCenterDelta": delta,
    }


def run_project(project_dir: Path, output_root: Path, wait_after_drag: float) -> Dict[str, Any]:
    config = make_runtime_config(project_dir)
    url = config["currentUrl"]
    project_output = output_root / project_dir.name
    frames_dir = project_output / "frames"
    overlays_dir = project_output / "overlays"
    crops_dir = project_output / "crops"
    frames_dir.mkdir(parents=True, exist_ok=True)
    overlays_dir.mkdir(parents=True, exist_ok=True)
    crops_dir.mkdir(parents=True, exist_ok=True)

    player = SprunkiGamePlayer()
    player.config = config
    ws: Optional[websocket.WebSocket] = None

    try:
        print(f"Opening: {url}")
        if not player.navigate_and_prepare_game(url):
            raise RuntimeError("Game setup failed")
        complete_game_entry_sequence(player)
        activate_browser_window()

        ws_url = player._get_websocket_url()
        if not ws_url:
            raise RuntimeError("Could not get Chrome DevTools websocket URL")
        ws = websocket.create_connection(ws_url, timeout=30)

        runtime = eval_js(ws, LAYOUT_AND_BOUNDS_JS, timeout=30)
        if not runtime.get("ok"):
            raise RuntimeError(f"Runtime inspect failed: {runtime}")

        icons = [normalize_runtime_icon(icon) for icon in runtime.get("icons", [])]
        slots = [normalize_runtime_slot(slot) for slot in runtime.get("slots", [])]
        if not icons:
            raise RuntimeError("No runtime icons found")
        if not slots:
            raise RuntimeError("No runtime slots found")

        icon = next((row for row in icons if row["index"] == 0), icons[0])
        center_index = (len(slots) - 1) // 2
        target_slot = next((slot for slot in slots if slot["index"] == center_index), slots[center_index])
        player.config = {
            **config,
            "layout": {"icons": icons, "slots": slots},
            "gameSettings": {"gameControlType": "click_drag", "hasHorror": False},
        }

        drag_success = player.corrected_drag_and_drop(icon, target_slot, False, True)
        if not drag_success:
            raise RuntimeError("Drag icon 0 failed")

        area = player.calculate_fixed_runtime_recording_area(target_slot, width=700.0, height=480.0)
        time.sleep(max(0.0, wait_after_drag))

        frame_path = frames_dir / "n-1_frame.png"
        overlay_path = overlays_dir / "n-1_center_distance_overlay.png"
        crop_path = crops_dir / "n-1_recording_area_crop_center_line.png"
        pyautogui.screenshot().save(frame_path)
        measurements = draw_overlay(frame_path, overlay_path, area, target_slot, project_dir.name)
        crop_measurements = draw_crop_center_line(frame_path, crop_path, area, target_slot, project_dir.name)

        cdp_click(ws, float(target_slot["x"]), float(target_slot["y"]))
        return {
            "project": str(project_dir),
            "url": url,
            "success": True,
            "recordingArea": {
                "x": area["x"],
                "y": area["y"],
                "width": area["width"],
                "height": area["height"],
                "aspectRatio": area.get("aspectRatio"),
            },
            "targetSlot": target_slot,
            "measurements": measurements,
            "cropMeasurements": crop_measurements,
            "frame": str(frame_path),
            "overlay": str(overlay_path),
            "cropCenterLine": str(crop_path),
        }
    finally:
        if ws:
            try:
                ws.close()
            except Exception:
                pass
        try:
            player.cleanup_chrome_complete()
        except Exception:
            pass


def read_project_list(path: Path) -> List[Path]:
    projects: List[Path] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        value = line.strip().strip('"')
        if value:
            projects.append(Path(value))
    return projects


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Draw center-slot distance diagnostics for fixed recordingArea.")
    parser.add_argument("projects", nargs="*", help="Project folders")
    parser.add_argument("--list", dest="list_path", help="Text file containing project folders, one per line")
    parser.add_argument("--output-dir", default="project_centered_recording_area_tests")
    parser.add_argument("--wait-after-drag", type=float, default=0.8)
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
    summary: Dict[str, Any] = {
        "generatedAt": datetime.now().isoformat(),
        "outputRoot": str(output_root),
        "projects": [],
    }

    for index, project_dir in enumerate(projects, 1):
        print("\n" + "=" * 80)
        print(f"[{index}/{len(projects)}] {project_dir}")
        try:
            result = run_project(project_dir, output_root, args.wait_after_drag)
            summary["projects"].append(result)
            print(f"Overlay: {result['overlay']}")
            print(f"Measurements: {result['measurements']}")
        except Exception as exc:
            print(f"FAILED: {exc}")
            summary["projects"].append({
                "project": str(project_dir),
                "success": False,
                "error": str(exc),
            })

    summary_path = output_root / "result.json"
    write_json(summary_path, summary)
    print("\nDone.")
    print(f"Output: {output_root.resolve()}")
    print(f"Summary: {summary_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

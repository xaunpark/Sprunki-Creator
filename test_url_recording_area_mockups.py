"""
Run recordingArea visual tests directly from a mod URL.

This is a diagnostic script, not part of the production pipeline. It opens the
game, drags icons into the center slot one by one, detects recordingArea from
runtime character bounds + Mute Buttons bottomY, then writes screenshots, crops,
overlays, and comparison mockups for 2..10 mods.

By default it compares multiple Mute Buttons bottomY selection strategies:
  - largest-cluster: current production/backfill behavior
  - closest-670: choose the Mute Buttons bottom cluster closest to Y=670
  - max-y: choose the lower Mute Buttons bottom cluster

Usage:
    python test_url_recording_area_mockups.py
    python test_url_recording_area_mockups.py "https://www.cocrea.world/@user/project" --limit 3
"""

from __future__ import annotations

import argparse
import json
import math
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pyautogui
import websocket

from sprunki_game_player import SprunkiGamePlayer
from inspect_game_entry import complete_game_entry_sequence
from test_recording_area_scaled_comparison_mockups import (
    LAYOUT_AND_BOUNDS_JS,
    activate_browser_window,
    calculate_recording_area,
    cdp_drag,
    cluster_values,
    create_mock_comparison,
    eval_js,
    likely_character_targets,
    median,
    slug_from_url,
    write_json,
)
from PIL import Image, ImageDraw, ImageFont


BOTTOM_STRATEGIES = ("largest-cluster", "closest-670", "max-y")


def detect_mute_bottom_y(layout: Dict[str, Any], strategy: str, fallback: float = 670.0) -> Dict[str, Any]:
    values = [
        float(target["boundsBottomY"])
        for target in layout.get("muteButtons", [])
        if isinstance(target.get("boundsBottomY"), (int, float)) and 600 <= float(target["boundsBottomY"]) <= 740
    ]
    clusters = cluster_values(values, threshold=4.0)
    if not clusters:
        return {
            "value": fallback,
            "method": f"{strategy}_fallback_fixed_670",
            "cluster": [],
            "clusters": [],
            "allValues": values,
            "fallbackUsed": True,
        }

    if strategy == "largest-cluster":
        best = max(clusters, key=lambda c: (len(c), -abs((median(c) or fallback) - fallback)))
    elif strategy == "closest-670":
        best = min(clusters, key=lambda c: abs((median(c) or fallback) - fallback))
    elif strategy == "max-y":
        best = max(clusters, key=lambda c: median(c) or fallback)
    else:
        raise ValueError(f"Unknown strategy: {strategy}")

    return {
        "value": float(median(best)),
        "method": f"mute_buttons_boundsBottomY_{strategy}",
        "cluster": best,
        "clusters": [{"median": median(c), "count": len(c), "values": c} for c in clusters],
        "allValues": values,
        "fallbackUsed": False,
    }


def cdp_click(ws: websocket.WebSocket, x: float, y: float) -> None:
    from test_recording_area_scaled_comparison_mockups import send_cdp

    send_cdp(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mouseMoved", "x": x, "y": y}}, timeout=10)
    time.sleep(0.08)
    send_cdp(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1}}, timeout=10)
    time.sleep(0.08)
    send_cdp(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1}}, timeout=10)


def safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in value)


def draw_overlay(
    screenshot_path: Path,
    output_path: Path,
    area: Dict[str, Any],
    candidate: Dict[str, Any],
    bottom: Dict[str, Any],
    label: str,
) -> None:
    image = Image.open(screenshot_path).convert("RGB")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("arial.ttf", 18)
    except Exception:
        font = ImageFont.load_default()

    ax, ay, aw, ah = area["x"], area["y"], area["width"], area["height"]
    draw.rectangle([ax, ay, ax + aw, ay + ah], outline="#00ff66", width=4)
    draw.line([0, area["bottomY"], image.width, area["bottomY"]], fill="#00ff66", width=3)

    bx = candidate.get("boundsX")
    by = candidate.get("boundsY")
    bw = candidate.get("boundsWidth")
    bh = candidate.get("boundsHeight")
    if all(isinstance(v, (int, float)) for v in (bx, by, bw, bh)):
        draw.rectangle([bx, by, bx + bw, by + bh], outline="#ff3333", width=3)

    text = f"{label}: area {aw}x{ah}, bottomY={area['bottomY']} ({bottom['method']})"
    draw.rectangle([70, 65, min(image.width - 20, 1200), 100], fill="black")
    draw.text((82, 72), text, fill="#00ff66", font=font)
    image.save(output_path)


def crop_from_area(screenshot_path: Path, area: Dict[str, Any], output_path: Path) -> Image.Image:
    screenshot = Image.open(screenshot_path).convert("RGB")
    x, y, w, h = area["x"], area["y"], area["width"], area["height"]
    crop = screenshot.crop((x, y, x + w, y + h))
    crop.save(output_path)
    return crop


def create_contact_sheet(crop_paths: List[Path], output_path: Path) -> None:
    if not crop_paths:
        return
    thumbs = []
    for path in crop_paths:
        image = Image.open(path).convert("RGB")
        image.thumbnail((220, 180), Image.Resampling.LANCZOS)
        thumbs.append((path.parent.name + "/" + path.stem, image.copy()))

    cols = 4
    rows = math.ceil(len(thumbs) / cols)
    cell_w, cell_h = 330, 230
    sheet = Image.new("RGB", (cols * cell_w, rows * cell_h), (18, 18, 18))
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("arial.ttf", 16)
    except Exception:
        font = ImageFont.load_default()

    for idx, (label, image) in enumerate(thumbs):
        col = idx % cols
        row = idx // cols
        base_x = col * cell_w
        base_y = row * cell_h
        draw.text((base_x + 12, base_y + 8), label, fill=(245, 245, 245), font=font)
        sheet.paste(image, (base_x + (cell_w - image.width) // 2, base_y + 38))
    sheet.save(output_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="URL recordingArea diagnostic mockup generator.")
    parser.add_argument("url", nargs="?", help="Cocrea/Sprunki mod URL")
    parser.add_argument("--limit", type=int, default=None, help="Number of icons to test; default all runtime icons")
    parser.add_argument("--start-icon", type=int, default=0, help="First runtime icon index to test")
    parser.add_argument("--wait-after-drag", type=float, default=1.0, help="Seconds to wait after drag before inspect/screenshot")
    parser.add_argument("--candidate-radius", type=float, default=360.0, help="Pixel radius around center slot for character candidate")
    parser.add_argument("--bottom-strategy", choices=[*BOTTOM_STRATEGIES, "all"], default="all")
    parser.add_argument("--output-dir", default="url_recording_area_mockup_tests")
    parser.add_argument("--keep-browser", action="store_true")
    return parser.parse_args()


def ensure_url(args: argparse.Namespace) -> str:
    if args.url:
        return args.url.strip()
    while True:
        raw = input("Nhập URL bản mod Cocrea/Sprunki: ").strip()
        if raw:
            return raw


def main() -> int:
    args = parse_args()
    url = ensure_url(args)
    if not url.startswith(("http://", "https://")):
        print("URL must start with http:// or https://")
        return 2

    strategies = list(BOTTOM_STRATEGIES) if args.bottom_strategy == "all" else [args.bottom_strategy]
    output_dir = Path(args.output_dir) / f"{slug_from_url(url)}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    output_dir.mkdir(parents=True, exist_ok=True)

    player = SprunkiGamePlayer()
    ws: Optional[websocket.WebSocket] = None
    summary: Dict[str, Any] = {
        "url": url,
        "generatedAt": datetime.now().isoformat(),
        "bottomStrategies": strategies,
        "waitAfterDrag": args.wait_after_drag,
        "candidateRadius": args.candidate_radius,
        "segments": [],
    }
    crop_paths: List[Path] = []

    try:
        print(f"Opening game: {url}")
        if not player.navigate_and_prepare_game(url):
            raise RuntimeError("Game setup failed")
        complete_game_entry_sequence(player)
        activate_browser_window()

        ws_url = player._get_websocket_url()
        if not ws_url:
            raise RuntimeError("Could not get Chrome DevTools websocket URL")
        ws = websocket.create_connection(ws_url, timeout=30)

        initial = eval_js(ws, LAYOUT_AND_BOUNDS_JS, timeout=30)
        if not initial.get("ok"):
            raise RuntimeError(f"Initial runtime inspect failed: {initial}")

        slots = initial.get("slots", [])
        icons = initial.get("icons", [])
        if not slots:
            raise RuntimeError("No slots found")
        if not icons:
            raise RuntimeError("No icons found")

        center_index = (len(slots) - 1) // 2
        center_slot = next((slot for slot in slots if slot.get("slotIndex") == center_index), slots[center_index])
        icons_to_test = [icon for icon in icons if int(icon.get("iconIndex", -1)) >= args.start_icon]
        if args.limit is not None:
            icons_to_test = icons_to_test[: args.limit]

        print(f"Icons to test: {len(icons_to_test)}")

        for order, icon in enumerate(icons_to_test, 1):
            icon_index = int(icon.get("iconIndex", order - 1))
            segment_key = f"n-{icon_index + 1}"
            print(f"\n[{order}/{len(icons_to_test)}] {segment_key}: drag icon {icon_index}")

            before = eval_js(ws, LAYOUT_AND_BOUNDS_JS, timeout=30)
            cdp_drag(ws, float(icon["pixelX"]), float(icon["pixelY"]), float(center_slot["pixelX"]), float(center_slot["pixelY"]))
            time.sleep(args.wait_after_drag)
            after = eval_js(ws, LAYOUT_AND_BOUNDS_JS, timeout=30)
            if not after.get("ok"):
                print(f"  inspect failed after drag: {after}")
                continue

            frame_dir = output_dir / "frames"
            frame_dir.mkdir(parents=True, exist_ok=True)
            frame_path = frame_dir / f"{segment_key}_frame.png"
            pyautogui.screenshot().save(frame_path)

            candidates = likely_character_targets(before, after, center_slot, float(args.candidate_radius))
            if not candidates:
                print(f"  no character candidate found for {segment_key}")
                cdp_click(ws, float(center_slot["pixelX"]), float(center_slot["pixelY"]))
                time.sleep(0.5)
                continue
            candidate = candidates[0]

            segment_result = {
                "segmentKey": segment_key,
                "iconIndex": icon_index,
                "frame": str(frame_path),
                "candidate": candidate,
                "strategyResults": {},
            }

            for strategy in strategies:
                strategy_dir = output_dir / strategy
                crops_dir = strategy_dir / "crops"
                overlays_dir = strategy_dir / "overlays"
                mockups_dir = strategy_dir / "mockups" / segment_key
                crops_dir.mkdir(parents=True, exist_ok=True)
                overlays_dir.mkdir(parents=True, exist_ok=True)
                mockups_dir.mkdir(parents=True, exist_ok=True)

                bottom = detect_mute_bottom_y(after, strategy=strategy, fallback=670.0)
                area, warnings = calculate_recording_area(candidate, float(bottom["value"]))
                crop_path = crops_dir / f"{segment_key}_crop.png"
                overlay_path = overlays_dir / f"{segment_key}_overlay.png"
                crop = crop_from_area(frame_path, area, crop_path)
                draw_overlay(frame_path, overlay_path, area, candidate, bottom, f"{segment_key} {strategy}")
                crop_paths.append(crop_path)

                mockup_paths = []
                for num_mods in range(2, 11):
                    mockup_path = mockups_dir / f"{segment_key}_{num_mods}mods.png"
                    create_mock_comparison(crop, num_mods, mockup_path)
                    mockup_paths.append(str(mockup_path))

                segment_result["strategyResults"][strategy] = {
                    "recordingArea": area,
                    "warnings": warnings,
                    "bottomDetection": bottom,
                    "crop": str(crop_path),
                    "overlay": str(overlay_path),
                    "mockups": mockup_paths,
                }
                print(f"  {strategy}: bottomY={area['bottomY']} area={area['width']}x{area['height']} warnings={len(warnings)}")

            summary["segments"].append(segment_result)

            cdp_click(ws, float(center_slot["pixelX"]), float(center_slot["pixelY"]))
            time.sleep(0.6)

        create_contact_sheet(crop_paths, output_dir / "crop_contact_sheet.png")
        write_json(output_dir / "result.json", summary)
        print(f"\nDone. Output: {output_dir.resolve()}")
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1
    finally:
        if ws:
            try:
                ws.close()
            except Exception:
                pass
        if not args.keep_browser:
            try:
                player.cleanup_chrome_complete()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())

"""
Test the current production recordingArea logic directly from a mod URL.

Unlike test_url_recording_area_mockups.py, this script does not reimplement the
area algorithm. It opens the game, drags an icon into the center slot, then calls
SprunkiGamePlayer.calculate_runtime_recording_area_after_drop(), the same method
used by the current showcase runtime.

Outputs:
  - screenshot after drag
  - overlay with the production recordingArea rectangle
  - crop from the production recordingArea
  - result.json with the raw production area data

Usage:
    python test_url_production_recording_area.py "https://www.cocrea.world/@user/mod" --start-icon 0 --limit 1
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

from inspect_game_entry import complete_game_entry_sequence
from sprunki_game_player import SprunkiGamePlayer
from test_recording_area_scaled_comparison_mockups import (
    LAYOUT_AND_BOUNDS_JS,
    activate_browser_window,
    eval_js,
    slug_from_url,
)


def write_json(path: Path, data: Dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def cdp_click(ws: websocket.WebSocket, x: float, y: float) -> None:
    from test_recording_area_scaled_comparison_mockups import send_cdp

    send_cdp(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mouseMoved", "x": x, "y": y}}, timeout=10)
    time.sleep(0.08)
    send_cdp(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1}}, timeout=10)
    time.sleep(0.08)
    send_cdp(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1}}, timeout=10)


def font(size: int) -> ImageFont.ImageFont:
    for path in ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/segoeui.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()


def draw_overlay(screenshot_path: Path, output_path: Path, area: Dict[str, Any]) -> None:
    image = Image.open(screenshot_path).convert("RGB")
    draw = ImageDraw.Draw(image)
    label_font = font(21)
    small_font = font(17)

    x = int(round(float(area["x"])))
    y = int(round(float(area["y"])))
    w = int(round(float(area["width"])))
    h = int(round(float(area["height"])))
    bottom_y = float(area.get("bottomY", y + h))
    candidate = ((area.get("source") or {}).get("characterCandidate") or {})
    bottom = ((area.get("source") or {}).get("bottomLine") or {})

    draw.rectangle([x, y, x + w, y + h], outline="#00ff66", width=5)
    draw.line([0, bottom_y, image.width, bottom_y], fill="#00ff66", width=3)

    bx = candidate.get("boundsX")
    by = candidate.get("boundsY")
    bw = candidate.get("boundsWidth")
    bh = candidate.get("boundsHeight")
    if all(isinstance(v, (int, float)) for v in (bx, by, bw, bh)):
        draw.rectangle([bx, by, bx + bw, by + bh], outline="#ff3333", width=3)

    label = (
        f"{area.get('segmentKey')}: production area x={x}, y={y}, w={w}, h={h}, "
        f"right={x + w}, bottom={y + h}, bottomY={bottom_y:.2f}"
    )
    method = f"bottom: {bottom.get('method', 'unknown')} fallback={area.get('fallbackUsed')}"
    candidate_name = f"candidate: {candidate.get('name')} idx={candidate.get('runtimeIndex')} score={candidate.get('score')}"

    draw.rectangle([12, 12, min(image.width - 12, 1280), 98], fill="black", outline="#00ff66", width=1)
    draw.text((24, 20), label, fill="#00ff66", font=label_font)
    draw.text((24, 48), method, fill="#dddddd", font=small_font)
    draw.text((24, 72), candidate_name, fill="#ff9999", font=small_font)
    image.save(output_path)


def crop_area(screenshot_path: Path, output_path: Path, area: Dict[str, Any]) -> None:
    image = Image.open(screenshot_path).convert("RGB")
    x = max(0, int(round(float(area["x"]))))
    y = max(0, int(round(float(area["y"]))))
    w = max(1, int(round(float(area["width"]))))
    h = max(1, int(round(float(area["height"]))))
    image.crop((x, y, min(image.width, x + w), min(image.height, y + h))).save(output_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="URL test for current production recordingArea logic.")
    parser.add_argument("url", nargs="?", help="Cocrea/Sprunki mod URL")
    parser.add_argument("--start-icon", type=int, default=0, help="First runtime icon index to test")
    parser.add_argument("--limit", type=int, default=1, help="Number of icons to test")
    parser.add_argument("--wait-after-drag", type=float, default=0.8, help="Seconds to wait before screenshot/return")
    parser.add_argument("--output-dir", default="url_production_recording_area_tests")
    parser.add_argument("--keep-browser", action="store_true")
    return parser.parse_args()


def ensure_url(args: argparse.Namespace) -> str:
    if args.url:
        return args.url.strip()
    while True:
        raw = input("Nhập URL bản mod Cocrea/Sprunki: ").strip()
        if raw:
            return raw


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


def main() -> int:
    args = parse_args()
    url = ensure_url(args)
    if not url.startswith(("http://", "https://")):
        print("URL must start with http:// or https://")
        return 2

    output_dir = Path(args.output_dir) / f"{slug_from_url(url)}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    output_dir.mkdir(parents=True, exist_ok=True)
    frames_dir = output_dir / "frames"
    overlays_dir = output_dir / "overlays"
    crops_dir = output_dir / "crops"
    frames_dir.mkdir(parents=True, exist_ok=True)
    overlays_dir.mkdir(parents=True, exist_ok=True)
    crops_dir.mkdir(parents=True, exist_ok=True)

    player = SprunkiGamePlayer()
    ws: Optional[websocket.WebSocket] = None
    summary: Dict[str, Any] = {
        "url": url,
        "generatedAt": datetime.now().isoformat(),
        "script": Path(__file__).name,
        "note": "Calls SprunkiGamePlayer.calculate_runtime_recording_area_after_drop directly.",
        "segments": [],
    }

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

        runtime = eval_js(ws, LAYOUT_AND_BOUNDS_JS, timeout=30)
        if not runtime.get("ok"):
            raise RuntimeError(f"Initial runtime inspect failed: {runtime}")
        runtime_icons = runtime.get("icons", [])
        runtime_slots = runtime.get("slots", [])
        if not runtime_icons:
            raise RuntimeError("No runtime icons found")
        if not runtime_slots:
            raise RuntimeError("No runtime slots found")

        icons = [normalize_runtime_icon(icon) for icon in runtime_icons]
        slots = [normalize_runtime_slot(slot) for slot in runtime_slots]
        center_index = (len(slots) - 1) // 2
        target_slot = next((slot for slot in slots if slot["index"] == center_index), slots[center_index])

        player.config = {
            "layout": {
                "icons": icons,
                "slots": slots,
            },
            "gameSettings": {
                "gameControlType": "click_drag",
                "hasHorror": False,
            },
        }

        icons_to_test = [icon for icon in icons if icon["index"] >= args.start_icon]
        icons_to_test = icons_to_test[: max(0, int(args.limit))]
        print(f"Icons to test: {len(icons_to_test)}")

        for order, icon in enumerate(icons_to_test, 1):
            segment_key = f"n-{icon['index'] + 1}"
            print(f"\n[{order}/{len(icons_to_test)}] {segment_key}: drag icon {icon['index']}")

            before_layout = player._inspect_runtime_layout_bounds()
            drag_success = player.corrected_drag_and_drop(icon, target_slot, False, True)
            if not drag_success:
                print(f"  drag failed for {segment_key}")
                summary["segments"].append({
                    "segmentKey": segment_key,
                    "iconIndex": icon["index"],
                    "dragSuccess": False,
                })
                continue

            area = player.calculate_runtime_recording_area_after_drop(
                before_layout if isinstance(before_layout, dict) else {},
                target_slot,
                icon["index"],
                segment_key,
            )

            time.sleep(max(0.0, float(args.wait_after_drag)))
            frame_path = frames_dir / f"{segment_key}_frame.png"
            overlay_path = overlays_dir / f"{segment_key}_overlay.png"
            crop_path = crops_dir / f"{segment_key}_crop.png"
            pyautogui.screenshot().save(frame_path)
            draw_overlay(frame_path, overlay_path, area)
            crop_area(frame_path, crop_path, area)

            summary["segments"].append({
                "segmentKey": segment_key,
                "iconIndex": icon["index"],
                "dragSuccess": True,
                "recordingArea": area,
                "outputs": {
                    "frame": str(frame_path),
                    "overlay": str(overlay_path),
                    "crop": str(crop_path),
                },
            })
            print(f"  production area: x={area['x']} y={area['y']} w={area['width']} h={area['height']} bottomY={area['bottomY']}")
            print(f"  overlay: {overlay_path.resolve()}")

            cdp_click(ws, float(target_slot["x"]), float(target_slot["y"]))
            time.sleep(0.6)

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

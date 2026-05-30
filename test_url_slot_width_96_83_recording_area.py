"""
Test a fixed-size recordingArea directly from a mod URL.

Algorithm under test:
  - bottom line: current production Mute Buttons boundsBottomY detection,
    fallback 670.
  - width/height: fixed size, default 700x480.
  - horizontal placement: centered on the center slot.
  - vertical placement: bottom anchored to detected bottomY.

Usage:
    python test_url_slot_width_96_83_recording_area.py "URL" --icons 0
    python test_url_slot_width_96_83_recording_area.py "URL" --icons 0,4,8
    python test_url_slot_width_96_83_recording_area.py "URL" --limit 5
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


def font(size: int) -> ImageFont.ImageFont:
    for path in ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/segoeui.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()


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


def parse_icon_indices(raw: Optional[str]) -> Optional[List[int]]:
    if not raw:
        return None
    values: List[int] = []
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        values.append(int(part))
    return values


def clamp_area(area: Dict[str, float], screen_w: int = 1920, screen_h: int = 1080) -> Dict[str, Any]:
    x = float(area["x"])
    y = float(area["y"])
    w = float(area["width"])
    h = float(area["height"])

    if x < 0:
        x = 0
    if x + w > screen_w:
        x = max(0.0, screen_w - w)
    if y < 0:
        y = 0
    if y + h > screen_h:
        y = max(0.0, screen_h - h)

    return {
        "x": int(round(x)),
        "y": int(round(y)),
        "width": int(round(min(w, screen_w))),
        "height": int(round(min(h, screen_h))),
    }


def calculate_fixed_size_area(
    center_slot: Dict[str, Any],
    bottom_y: float,
    fixed_width: float = 700.0,
    fixed_height: float = 480.0,
) -> Dict[str, Any]:
    width = float(fixed_width)
    height = float(fixed_height)
    center_x = float(center_slot["x"])
    bottom = float(bottom_y)

    unclamped = {
        "x": center_x - width / 2.0,
        "y": bottom - height,
        "width": width,
        "height": height,
    }
    clamped = clamp_area(unclamped)
    clamped.update({
        "bottomY": int(round(bottom)),
        "aspectRatio": "fixed",
        "sourceMethod": "fixed_700x480_plus_mute_buttons_bottom",
        "debug": {
            "centerSlotIndex": center_slot["index"],
            "centerSlotX": center_slot["x"],
            "fixedWidth": fixed_width,
            "fixedHeight": fixed_height,
            "unclamped": unclamped,
        },
    })
    return clamped


def draw_overlay(
    screenshot_path: Path,
    output_path: Path,
    area: Dict[str, Any],
    bottom_detection: Dict[str, Any],
    slots: List[Dict[str, Any]],
    center_slot: Dict[str, Any],
    label: str,
) -> None:
    image = Image.open(screenshot_path).convert("RGB")
    draw = ImageDraw.Draw(image)
    label_font = font(21)
    small_font = font(17)

    x = int(area["x"])
    y = int(area["y"])
    w = int(area["width"])
    h = int(area["height"])
    bottom_y = float(area["bottomY"])

    draw.rectangle([x, y, x + w, y + h], outline="#00ff66", width=5)
    draw.line([0, bottom_y, image.width, bottom_y], fill="#00ff66", width=3)

    center_index = int(center_slot["index"])
    for slot in slots:
        sx = float(slot["x"])
        sy = float(slot["y"])
        color = "#ffaa00" if int(slot["index"]) in (center_index - 1, center_index, center_index + 1) else "#555555"
        draw.line([sx, sy - 18, sx, sy + 18], fill=color, width=3)
        draw.line([sx - 18, sy, sx + 18, sy], fill=color, width=3)
        if int(slot["index"]) in (center_index - 1, center_index, center_index + 1):
            draw.text((sx + 8, sy - 24), f"slot {slot['index']}", fill=color, font=small_font)

    text = (
        f"{label}: area x={x}, y={y}, w={w}, h={h}, right={x + w}, "
        f"bottom={y + h}, bottomY={bottom_y:.2f}"
    )
    bottom_text = f"bottom source: {bottom_detection.get('method')} fallback={bottom_detection.get('fallbackUsed')}"
    width_text = f"fixed recordingArea size = {area['debug']['fixedWidth']:.0f}x{area['debug']['fixedHeight']:.0f}, centered on slot {center_index}"
    draw.rectangle([12, 12, min(image.width - 12, 1380), 102], fill="black", outline="#00ff66", width=1)
    draw.text((24, 20), text, fill="#00ff66", font=label_font)
    draw.text((24, 50), width_text, fill="#dddddd", font=small_font)
    draw.text((24, 74), bottom_text, fill="#dddddd", font=small_font)
    image.save(output_path)


def crop_area(screenshot_path: Path, output_path: Path, area: Dict[str, Any]) -> None:
    image = Image.open(screenshot_path).convert("RGB")
    x = max(0, int(area["x"]))
    y = max(0, int(area["y"]))
    w = max(1, int(area["width"]))
    h = max(1, int(area["height"]))
    image.crop((x, y, min(image.width, x + w), min(image.height, y + h))).save(output_path)


def cdp_click(ws: websocket.WebSocket, x: float, y: float) -> None:
    from test_recording_area_scaled_comparison_mockups import send_cdp

    send_cdp(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mouseMoved", "x": x, "y": y}}, timeout=10)
    time.sleep(0.08)
    send_cdp(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1}}, timeout=10)
    time.sleep(0.08)
    send_cdp(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1}}, timeout=10)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="URL test for fixed-size recordingArea.")
    parser.add_argument("url", nargs="?", help="Cocrea/Sprunki mod URL")
    parser.add_argument("--icons", default=None, help="Exact icon indices to test, e.g. 0 or 0,3,8")
    parser.add_argument("--start-icon", type=int, default=0, help="First icon index when --icons is not provided")
    parser.add_argument("--limit", type=int, default=1, help="Number of icons when --icons is not provided")
    parser.add_argument("--area-width", type=float, default=700.0, help="Fixed recordingArea width")
    parser.add_argument("--area-height", type=float, default=480.0, help="Fixed recordingArea height")
    parser.add_argument("--wait-after-drag", type=float, default=0.8, help="Seconds to wait before screenshot/return")
    parser.add_argument("--output-dir", default="url_slot_width_96_83_recording_area_tests")
    parser.add_argument("--keep-browser", action="store_true")
    return parser.parse_args()


def ensure_url(args: argparse.Namespace) -> str:
    if args.url:
        return args.url.strip()
    while True:
        raw = input("Nhap URL ban mod Cocrea/Sprunki: ").strip()
        if raw:
            return raw


def main() -> int:
    args = parse_args()
    url = ensure_url(args)
    if not url.startswith(("http://", "https://")):
        print("URL must start with http:// or https://")
        return 2

    output_dir = Path(args.output_dir) / f"{slug_from_url(url)}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    frames_dir = output_dir / "frames"
    overlays_dir = output_dir / "overlays"
    crops_dir = output_dir / "crops"
    for path in (frames_dir, overlays_dir, crops_dir):
        path.mkdir(parents=True, exist_ok=True)

    player = SprunkiGamePlayer()
    ws: Optional[websocket.WebSocket] = None
    summary: Dict[str, Any] = {
        "url": url,
        "generatedAt": datetime.now().isoformat(),
        "script": Path(__file__).name,
        "algorithm": {
            "bottom": "SprunkiGamePlayer._detect_recording_bottom_y(after_runtime_layout), fallback 670",
            "width": "fixed areaWidth",
            "height": "fixed areaHeight",
            "placement": "centered on center slot, bottom anchored to bottomY",
            "areaWidth": args.area_width,
            "areaHeight": args.area_height,
        },
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

        icons = [normalize_runtime_icon(icon) for icon in runtime.get("icons", [])]
        slots = [normalize_runtime_slot(slot) for slot in runtime.get("slots", [])]
        if not icons:
            raise RuntimeError("No runtime icons found")
        if not slots:
            raise RuntimeError("Need at least 1 slot")

        center_index = (len(slots) - 1) // 2
        target_slot = next((slot for slot in slots if slot["index"] == center_index), slots[center_index])

        player.config = {
            "layout": {"icons": icons, "slots": slots},
            "gameSettings": {"gameControlType": "click_drag", "hasHorror": False},
        }

        exact_indices = parse_icon_indices(args.icons)
        if exact_indices is not None:
            icons_to_test = [icon for idx in exact_indices for icon in icons if icon["index"] == idx]
            missing = [idx for idx in exact_indices if not any(icon["index"] == idx for icon in icons)]
            if missing:
                print(f"WARNING: missing icon indices: {missing}")
        else:
            icons_to_test = [icon for icon in icons if icon["index"] >= args.start_icon][: max(0, int(args.limit))]

        print(f"Center slot: {target_slot['index']} at ({target_slot['x']:.1f}, {target_slot['y']:.1f})")
        print(f"Icons to test: {[icon['index'] for icon in icons_to_test]}")

        for order, icon in enumerate(icons_to_test, 1):
            segment_key = f"n-{icon['index'] + 1}"
            print(f"\n[{order}/{len(icons_to_test)}] {segment_key}: drag icon {icon['index']}")
            drag_success = player.corrected_drag_and_drop(icon, target_slot, False, True)
            if not drag_success:
                print(f"  drag failed for {segment_key}")
                summary["segments"].append({"segmentKey": segment_key, "iconIndex": icon["index"], "dragSuccess": False})
                continue

            after_layout = player._inspect_runtime_layout_bounds()
            if not after_layout.get("ok"):
                print(f"  runtime inspect failed: {after_layout}")
                bottom_detection = {"value": 670.0, "method": "fallback_fixed_670_after_inspect_error", "fallbackUsed": True}
            else:
                bottom_detection = player._detect_recording_bottom_y(after_layout)

            area = calculate_fixed_size_area(
                center_slot=target_slot,
                bottom_y=float(bottom_detection.get("value", 670.0)),
                fixed_width=float(args.area_width),
                fixed_height=float(args.area_height),
            )
            area.update({
                "segmentKey": segment_key,
                "iconIndex": icon["index"],
                "source": {"bottomLine": bottom_detection},
                "fallbackUsed": bool(bottom_detection.get("fallbackUsed")),
                "warnings": [],
            })

            time.sleep(max(0.0, float(args.wait_after_drag)))
            frame_path = frames_dir / f"{segment_key}_frame.png"
            overlay_path = overlays_dir / f"{segment_key}_overlay.png"
            crop_path = crops_dir / f"{segment_key}_crop.png"
            pyautogui.screenshot().save(frame_path)
            draw_overlay(frame_path, overlay_path, area, bottom_detection, slots, target_slot, segment_key)
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
            print(f"  area: x={area['x']} y={area['y']} w={area['width']} h={area['height']} bottomY={area['bottomY']}")
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

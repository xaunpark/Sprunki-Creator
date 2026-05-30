"""
Track the production character candidate bounds over time after dragging an icon.

Purpose:
  - Open a Sprunki/Cocrea URL.
  - Drag one icon into the center slot.
  - Select the character candidate using the same production helper used for
    recordingArea.
  - Track that exact runtime target over time and record whether its bounds move.
  - Save screenshots with the target bounds rectangle for each sample.

Usage:
    python test_url_candidate_bounds_over_time.py "https://www.cocrea.world/@user/mod" --icon-index 0
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


def find_same_target(layout: Dict[str, Any], selected: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    targets = layout.get("targets", []) if isinstance(layout, dict) else []
    selected_id = selected.get("id")
    if selected_id:
        found = next((target for target in targets if target.get("id") == selected_id), None)
        if found:
            return found

    selected_runtime_index = selected.get("runtimeIndex")
    if isinstance(selected_runtime_index, int):
        found = next((target for target in targets if target.get("runtimeIndex") == selected_runtime_index), None)
        if found:
            return found

    selected_name = selected.get("name")
    if selected_name:
        candidates = [target for target in targets if target.get("name") == selected_name]
        if len(candidates) == 1:
            return candidates[0]

    return None


def bounds_tuple(target: Dict[str, Any]) -> Optional[Dict[str, float]]:
    keys = ("boundsX", "boundsY", "boundsWidth", "boundsHeight")
    if not all(isinstance(target.get(key), (int, float)) for key in keys):
        return None
    x = float(target["boundsX"])
    y = float(target["boundsY"])
    w = float(target["boundsWidth"])
    h = float(target["boundsHeight"])
    return {
        "x": x,
        "y": y,
        "width": w,
        "height": h,
        "right": x + w,
        "bottom": y + h,
        "centerX": x + w / 2,
        "centerY": y + h / 2,
    }


def draw_sample_overlay(
    screenshot_path: Path,
    output_path: Path,
    selected: Dict[str, Any],
    target: Optional[Dict[str, Any]],
    sample_index: int,
    elapsed: float,
) -> None:
    image = Image.open(screenshot_path).convert("RGB")
    draw = ImageDraw.Draw(image)
    label_font = font(22)
    small_font = font(17)

    current = bounds_tuple(target or {})
    initial = bounds_tuple(selected)

    if initial:
        draw.rectangle(
            [initial["x"], initial["y"], initial["right"], initial["bottom"]],
            outline="#00ff66",
            width=3,
        )
    if current:
        draw.rectangle(
            [current["x"], current["y"], current["right"], current["bottom"]],
            outline="#ff3333",
            width=4,
        )

    name = selected.get("name")
    runtime_index = selected.get("runtimeIndex")
    target_id = selected.get("id")
    label = f"sample {sample_index}, t={elapsed:.2f}s, target={name}, runtimeIndex={runtime_index}, id={target_id}"
    draw.rectangle([12, 12, min(image.width - 12, 1320), 94], fill="black", outline="#666666", width=1)
    draw.text((24, 20), label, fill="#ffffff", font=label_font)
    draw.text((24, 50), "green=initial candidate bounds, red=current same target bounds", fill="#cccccc", font=small_font)
    if not current:
        draw.text((24, 72), "same target not found or has no bounds", fill="#ff6666", font=small_font)

    image.save(output_path)


def cdp_click(ws: websocket.WebSocket, x: float, y: float) -> None:
    from test_recording_area_scaled_comparison_mockups import send_cdp

    send_cdp(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mouseMoved", "x": x, "y": y}}, timeout=10)
    time.sleep(0.08)
    send_cdp(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1}}, timeout=10)
    time.sleep(0.08)
    send_cdp(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1}}, timeout=10)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Track production character candidate bounds over time.")
    parser.add_argument("url", nargs="?", help="Cocrea/Sprunki mod URL")
    parser.add_argument("--icon-index", type=int, default=0, help="Runtime icon index to drag")
    parser.add_argument("--samples", type=int, default=20, help="Number of samples after candidate selection")
    parser.add_argument("--interval", type=float, default=0.25, help="Seconds between samples")
    parser.add_argument("--settle", type=float, default=0.4, help="Seconds after drag before selecting candidate")
    parser.add_argument("--output-dir", default="url_candidate_bounds_over_time_tests")
    parser.add_argument("--keep-browser", action="store_true")
    return parser.parse_args()


def ensure_url(args: argparse.Namespace) -> str:
    if args.url:
        return args.url.strip()
    while True:
        raw = input("Nhập URL bản mod Cocrea/Sprunki: ").strip()
        if raw:
            return raw


def summarize_movement(samples: List[Dict[str, Any]]) -> Dict[str, Any]:
    valid = [sample for sample in samples if sample.get("bounds")]
    if not valid:
        return {"validSamples": 0}

    fields = ["x", "y", "width", "height", "right", "bottom", "centerX", "centerY"]
    summary: Dict[str, Any] = {"validSamples": len(valid)}
    for field in fields:
        values = [float(sample["bounds"][field]) for sample in valid]
        summary[field] = {
            "min": min(values),
            "max": max(values),
            "delta": max(values) - min(values),
        }
    summary["maxCenterShiftFromInitial"] = max(
        math.hypot(
            float(sample["bounds"]["centerX"]) - float(valid[0]["bounds"]["centerX"]),
            float(sample["bounds"]["centerY"]) - float(valid[0]["bounds"]["centerY"]),
        )
        for sample in valid
    )
    return summary


def main() -> int:
    args = parse_args()
    url = ensure_url(args)
    if not url.startswith(("http://", "https://")):
        print("URL must start with http:// or https://")
        return 2

    output_dir = Path(args.output_dir) / f"{slug_from_url(url)}-icon{args.icon_index}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    frames_dir = output_dir / "frames"
    overlays_dir = output_dir / "overlays"
    frames_dir.mkdir(parents=True, exist_ok=True)
    overlays_dir.mkdir(parents=True, exist_ok=True)

    player = SprunkiGamePlayer()
    ws: Optional[websocket.WebSocket] = None

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
        icon = next((row for row in icons if row["index"] == args.icon_index), None)
        if not icon:
            raise RuntimeError(f"Icon index {args.icon_index} not found")

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

        print(f"Dragging icon {args.icon_index} to center slot {target_slot['index']}")
        before_layout = player._inspect_runtime_layout_bounds()
        drag_success = player.corrected_drag_and_drop(icon, target_slot, False, True)
        if not drag_success:
            raise RuntimeError("Drag failed")

        time.sleep(max(0.0, float(args.settle)))
        after_layout = player._inspect_runtime_layout_bounds()
        if not after_layout.get("ok"):
            raise RuntimeError(f"Runtime inspect after drag failed: {after_layout}")

        selected = player._find_runtime_character_candidate(
            before_layout if isinstance(before_layout, dict) else {},
            after_layout,
            target_slot,
        )
        if not selected:
            raise RuntimeError("No production character candidate found")

        print(f"Selected candidate: name={selected.get('name')} runtimeIndex={selected.get('runtimeIndex')} id={selected.get('id')}")
        print(f"Initial bounds: {bounds_tuple(selected)}")

        samples: List[Dict[str, Any]] = []
        start = time.monotonic()
        for index in range(max(1, int(args.samples))):
            if index > 0:
                time.sleep(max(0.0, float(args.interval)))
            elapsed = time.monotonic() - start
            layout = player._inspect_runtime_layout_bounds()
            target = find_same_target(layout, selected) if layout.get("ok") else None
            bounds = bounds_tuple(target or {})

            frame_path = frames_dir / f"sample_{index:03d}.png"
            overlay_path = overlays_dir / f"sample_{index:03d}.png"
            pyautogui.screenshot().save(frame_path)
            draw_sample_overlay(frame_path, overlay_path, selected, target, index, elapsed)

            samples.append({
                "sampleIndex": index,
                "elapsedSeconds": elapsed,
                "found": target is not None,
                "target": {
                    key: target.get(key)
                    for key in ("runtimeIndex", "id", "name", "currentCostumeIndex", "pixelX", "pixelY", "boundsX", "boundsY", "boundsWidth", "boundsHeight")
                } if target else None,
                "bounds": bounds,
                "frame": str(frame_path),
                "overlay": str(overlay_path),
            })

        movement = summarize_movement(samples)
        summary = {
            "url": url,
            "generatedAt": datetime.now().isoformat(),
            "iconIndex": args.icon_index,
            "settleSeconds": args.settle,
            "sampleCount": args.samples,
            "sampleIntervalSeconds": args.interval,
            "selectedCandidate": selected,
            "movementSummary": movement,
            "samples": samples,
        }
        write_json(output_dir / "result.json", summary)

        print("\nDone.")
        print(f"Output: {output_dir.resolve()}")
        print(f"Result: {(output_dir / 'result.json').resolve()}")
        print(f"Movement summary: {movement}")

        cdp_click(ws, float(target_slot["x"]), float(target_slot["y"]))
        time.sleep(0.4)
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

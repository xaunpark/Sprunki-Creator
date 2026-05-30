"""
Drag one icon into the center slot, compute a content recordingArea, crop it,
then create mock comparison images for 2..10 mods using SCALE/CONTAIN.

This script only reads the comparison layout rules from the current known
ComparisonService behavior and does not modify that project.

RecordingArea rule tested here:
  - bottomY = clustered Mute Buttons.boundsBottomY, fallback 670
  - bottom is fixed to bottomY, no bottom padding
  - candidate rank 0 after drag supplies character bounds
  - crop box is expanded 10px left/right/top around candidate bounds
  - crop box is clamped to the screen

Mock comparison rule tested here:
  - use the cropped still image as source content
  - for each comparison layout 2..10, stretch-scale source to exactly fill each video cell
  - do not crop the cropped source again

Usage:
    python test_recording_area_scaled_comparison_mockups.py

or:
    python test_recording_area_scaled_comparison_mockups.py "https://www.cocrea.world/@user/project" --icon-index 0

Outputs:
    recording_area_scaled_comparison_tests/<slug>-<timestamp>/
      - after_drag.png
      - recording_area_overlay.png
      - recording_area_crop.png
      - mock_compare_2mods.png ... mock_compare_10mods.png
      - result.json
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import pyautogui
import pygetwindow as gw
import websocket
from PIL import Image, ImageDraw, ImageFont

from inspect_game_entry import complete_game_entry_sequence
from sprunki_game_player import SprunkiGamePlayer


LAYOUT_AND_BOUNDS_JS = r"""
new Promise((resolve) => {
  try {
    function findVM() {
      if (window.__vm && window.__vm.runtime) return window.__vm;
      function getReactKey(el) { return Object.keys(el).find(k => k.startsWith('__react')); }
      const selectors = ['canvas', '.ccw-stage-wrapper', '#stage-canvas-wrapper', '.gandi_stage_stage_1fD7k'];
      for (const selector of selectors) {
        const elements = document.querySelectorAll(selector);
        for (const el of elements) {
          const reactKey = getReactKey(el);
          if (!reactKey) continue;
          let fiber = el[reactKey];
          let depth = 0;
          while (fiber && depth++ < 120) {
            const props = fiber.pendingProps || fiber.memoizedProps || fiber.props;
            if (props?.vm && props.vm.runtime) {
              window.__vm = props.vm;
              return props.vm;
            }
            fiber = fiber.return;
          }
        }
      }
      return null;
    }

    const screenW = 1920;
    const screenH = 1080;
    const stageW = 640;
    const stageH = 360;
    const menuWidth = 78;
    const viewportW = screenW - menuWidth;
    const scale = Math.min(viewportW / stageW, screenH / stageH);
    const canvasW = stageW * scale;
    const canvasH = stageH * scale;
    const leftOffset = menuWidth;
    const topOffset = (screenH - canvasH) / 2;
    const canvasCenterX = leftOffset + (canvasW / 2);
    const canvasCenterY = topOffset + (canvasH / 2);

    function toPixel(logicX, logicY) {
      if (logicX === null || logicY === null || logicX === undefined || logicY === undefined) return { x: null, y: null };
      return { x: canvasCenterX + (logicX * scale), y: canvasCenterY - (logicY * scale) };
    }

    function boundsToPixels(bounds) {
      if (!bounds) return null;
      const left = Number(bounds.left);
      const right = Number(bounds.right);
      const top = Number(bounds.top);
      const bottom = Number(bounds.bottom);
      if (![left, right, top, bottom].every(Number.isFinite)) return null;
      const pTopLeft = toPixel(left, top);
      const pBottomRight = toPixel(right, bottom);
      return {
        x: Math.min(pTopLeft.x, pBottomRight.x),
        y: Math.min(pTopLeft.y, pBottomRight.y),
        width: Math.abs(pBottomRight.x - pTopLeft.x),
        height: Math.abs(pBottomRight.y - pTopLeft.y),
        topPixelY: Math.min(pTopLeft.y, pBottomRight.y),
        bottomPixelY: Math.max(pTopLeft.y, pBottomRight.y),
        centerPixelX: (Math.min(pTopLeft.x, pBottomRight.x) + Math.max(pTopLeft.x, pBottomRight.x)) / 2,
        centerPixelY: (Math.min(pTopLeft.y, pBottomRight.y) + Math.max(pTopLeft.y, pBottomRight.y)) / 2,
        raw: { left, right, top, bottom }
      };
    }

    function serializeCostume(costume, index) {
      if (!costume) return null;
      return {
        index,
        name: costume.name,
        size: costume.size,
        rotationCenterX: costume.rotationCenterX,
        rotationCenterY: costume.rotationCenterY,
        assetId: costume.assetId,
        dataFormat: costume.dataFormat,
        md5ext: costume.md5ext
      };
    }

    function boundsCandidates(target, renderer) {
      const drawableID = target.drawableID ?? target._drawableID;
      const out = [];
      try {
        if (typeof target.getBounds === 'function') {
          const raw = target.getBounds();
          out.push({ source: 'target.getBounds', rawBounds: raw, pixelBounds: boundsToPixels(raw) });
        }
      } catch (e) {
        out.push({ source: 'target.getBounds', error: String(e) });
      }
      if (renderer && drawableID !== undefined && drawableID !== null) {
        for (const name of ['getBounds', 'getBoundsForDrawable']) {
          try {
            if (typeof renderer[name] === 'function') {
              const raw = renderer[name](drawableID);
              out.push({ source: `renderer.${name}`, rawBounds: raw, pixelBounds: boundsToPixels(raw) });
            }
          } catch (e) {
            out.push({ source: `renderer.${name}`, error: String(e) });
          }
        }
      }
      return out.filter(c => c.error || c.pixelBounds);
    }

    const vm = findVM();
    if (!vm) {
      resolve({ ok: false, error: 'Cannot find Scratch VM' });
      return;
    }

    const runtime = vm.runtime;
    const renderer = runtime.renderer || vm.renderer || null;
    const allTargets = runtime.targets || [];
    const targetRows = allTargets
      .filter(t => !t.isStage && t.visible)
      .map((target, runtimeIndex) => {
        const sprite = target.sprite || {};
        const name = sprite.name || target.getName?.() || '';
        const pixel = toPixel(target.x, target.y);
        const costumes = (sprite.costumes || []).map(serializeCostume);
        const currentCostume = costumes[target.currentCostume] || null;
        const candidates = boundsCandidates(target, renderer);
        const primary = candidates.find(c => c.pixelBounds)?.pixelBounds || null;
        return {
          runtimeIndex,
          id: target.id,
          name,
          visible: target.visible,
          drawableID: target.drawableID ?? target._drawableID ?? null,
          logicX: target.x,
          logicY: target.y,
          sizePercent: target.size,
          direction: target.direction,
          currentCostumeIndex: target.currentCostume,
          currentCostume,
          costumeCount: costumes.length,
          pixelX: pixel.x,
          pixelY: pixel.y,
          roundedPixelX: Math.round(pixel.x),
          roundedPixelY: Math.round(pixel.y),
          boundsTopY: primary?.topPixelY ?? null,
          boundsBottomY: primary?.bottomPixelY ?? null,
          boundsCenterX: primary?.centerPixelX ?? null,
          boundsCenterY: primary?.centerPixelY ?? null,
          boundsX: primary?.x ?? null,
          boundsY: primary?.y ?? null,
          boundsWidth: primary?.width ?? null,
          boundsHeight: primary?.height ?? null,
          boundsSources: candidates.filter(c => c.pixelBounds).map(c => c.source)
        };
      });

    const icons = targetRows
      .filter(t => t.name.includes('Icons') && t.roundedPixelY >= 540)
      .map((t, i) => ({ ...t, iconIndex: i }));

    const slots = targetRows
      .filter(t => /^Polo\s+\d+/i.test(t.name))
      .sort((a, b) => a.roundedPixelX - b.roundedPixelX)
      .map((t, i) => ({ ...t, slotIndex: i }));

    const muteButtons = targetRows
      .filter(t => /mute buttons/i.test(t.name));

    resolve({
      ok: true,
      title: document.title,
      currentUrl: window.location.href,
      capturedAt: new Date().toISOString(),
      screen: { width: screenW, height: screenH },
      canvas: { stageW, stageH, menuWidth, scale, canvasW, canvasH, leftOffset, topOffset, canvasCenterX, canvasCenterY },
      targetCount: allTargets.length,
      visibleTargetCount: targetRows.length,
      icons,
      slots,
      muteButtons,
      targets: targetRows
    });
  } catch (error) {
    resolve({ ok: false, error: String(error), stack: error?.stack });
  }
});
"""


TOTAL_WIDTH = 1920
VIDEO_HEIGHT = 830
HEADER_HEIGHT = 250
BORDER = 10


def slug_from_url(url: str) -> str:
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    candidate = parts[-1] if parts else parsed.netloc
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", candidate).strip("-")
    return slug[:80] or "sprunki-mod"


def send_cdp(ws: websocket.WebSocket, command: Dict[str, Any], timeout: float = 30) -> Optional[Dict[str, Any]]:
    command_id = int(time.time() * 1000) % 1_000_000
    command["id"] = command_id
    ws.send(json.dumps(command))
    start = time.time()
    while time.time() - start < timeout:
        response = json.loads(ws.recv())
        if response.get("id") == command_id:
            return response
    return None


def eval_js(ws: websocket.WebSocket, expression: str, timeout: float = 30) -> Dict[str, Any]:
    response = send_cdp(
        ws,
        {
            "method": "Runtime.evaluate",
            "params": {"expression": expression, "awaitPromise": True, "returnByValue": True},
        },
        timeout=timeout,
    )
    if not response:
        return {"ok": False, "error": "Runtime.evaluate timed out"}
    result = response.get("result", {}).get("result", {})
    if "value" in result:
        return result["value"]
    return {"ok": False, "error": "Runtime.evaluate returned no value", "raw": response}


def activate_browser_window() -> None:
    windows = gw.getWindowsWithTitle("cocrea.world") or gw.getWindowsWithTitle("Chrome")
    if not windows:
        return
    window = windows[0]
    try:
        window.activate()
        time.sleep(0.5)
        if not window.isMaximized:
            window.maximize()
            time.sleep(0.8)
    except Exception:
        pass


def cdp_drag(ws: websocket.WebSocket, start_x: float, start_y: float, end_x: float, end_y: float, steps: int = 24) -> None:
    send_cdp(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mouseMoved", "x": start_x, "y": start_y}})
    time.sleep(0.15)
    send_cdp(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mousePressed", "x": start_x, "y": start_y, "button": "left", "clickCount": 1}})
    time.sleep(0.2)
    for i in range(1, steps + 1):
        t = i / steps
        x = start_x + (end_x - start_x) * t
        y = start_y + (end_y - start_y) * t
        send_cdp(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mouseMoved", "x": x, "y": y, "button": "left", "buttons": 1}}, timeout=10)
        time.sleep(0.025)
    send_cdp(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mouseReleased", "x": end_x, "y": end_y, "button": "left", "clickCount": 1}})


def median(values: List[float]) -> Optional[float]:
    if not values:
        return None
    vals = sorted(values)
    mid = len(vals) // 2
    if len(vals) % 2:
        return vals[mid]
    return (vals[mid - 1] + vals[mid]) / 2


def cluster_values(values: List[float], threshold: float = 4.0) -> List[List[float]]:
    if not values:
        return []
    vals = sorted(values)
    clusters = [[vals[0]]]
    for value in vals[1:]:
        if abs(value - clusters[-1][-1]) <= threshold:
            clusters[-1].append(value)
        else:
            clusters.append([value])
    return clusters


def detect_bottom_y(layout: Dict[str, Any], fallback: float = 670.0) -> Dict[str, Any]:
    values = [
        float(target["boundsBottomY"])
        for target in layout.get("muteButtons", [])
        if isinstance(target.get("boundsBottomY"), (int, float)) and 600 <= float(target["boundsBottomY"]) <= 740
    ]
    clusters = cluster_values(values, threshold=4.0)
    if clusters:
        best = max(clusters, key=lambda c: (len(c), -abs((median(c) or fallback) - fallback)))
        return {
            "value": median(best),
            "method": "mute_buttons_boundsBottomY_cluster_median",
            "cluster": best,
            "allValues": values,
            "fallbackUsed": False,
        }
    return {"value": fallback, "method": "fallback_fixed_670", "cluster": [], "allValues": values, "fallbackUsed": True}


def distance_to_point(target: Dict[str, Any], x: float, y: float) -> float:
    values = []
    if isinstance(target.get("pixelX"), (int, float)) and isinstance(target.get("pixelY"), (int, float)):
        values.append(math.hypot(float(target["pixelX"]) - x, float(target["pixelY"]) - y))
    if isinstance(target.get("boundsCenterX"), (int, float)) and isinstance(target.get("boundsCenterY"), (int, float)):
        values.append(math.hypot(float(target["boundsCenterX"]) - x, float(target["boundsCenterY"]) - y))
    return min(values) if values else math.inf


def likely_character_targets(before: Dict[str, Any], after: Dict[str, Any], center_slot: Dict[str, Any], radius: float) -> List[Dict[str, Any]]:
    before_by_id = {target.get("id"): target for target in before.get("targets", [])}
    center_x = float(center_slot["pixelX"])
    center_y = float(center_slot["pixelY"])
    rows = []
    for target in after.get("targets", []):
        name = target.get("name", "")
        if "Icons" in name or re.match(r"^Polo\s+\d+", name, re.I) or re.search(r"button|mute|gallery|bars|preview", name, re.I):
            continue
        if distance_to_point(target, center_x, center_y) > radius:
            continue
        if not all(isinstance(target.get(k), (int, float)) for k in ("boundsX", "boundsY", "boundsWidth", "boundsHeight")):
            continue

        before_target = before_by_id.get(target.get("id"))
        changed = before_target is None
        if before_target:
            for key in ("pixelX", "pixelY", "boundsX", "boundsY", "boundsWidth", "boundsHeight", "currentCostumeIndex"):
                a = target.get(key)
                b = before_target.get(key)
                if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                    if abs(float(a) - float(b)) > 1:
                        changed = True
                        break
                elif a != b:
                    changed = True
                    break
        area = float(target["boundsWidth"]) * float(target["boundsHeight"])
        score = distance_to_point(target, center_x, center_y)
        if changed:
            score -= 150
        if 10_000 <= area <= 500_000:
            score -= 30
        elif area > 800_000:
            score += 200
        rows.append({**target, "distanceToCenterSlot": distance_to_point(target, center_x, center_y), "changedAfterDrag": changed, "score": score})
    rows.sort(key=lambda row: (row["score"], row["distanceToCenterSlot"], row.get("name") or ""))
    return rows


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(value, maximum))


def calculate_recording_area(candidate: Dict[str, Any], bottom_y: float, screen_w: int = 1920, screen_h: int = 1080, padding: float = 10.0) -> Tuple[Dict[str, Any], List[str]]:
    warnings = []
    bx = float(candidate["boundsX"])
    by = float(candidate["boundsY"])
    bw = float(candidate["boundsWidth"])
    bh = float(candidate["boundsHeight"])
    b_bottom = by + bh

    left = bx - padding
    right = bx + bw + padding
    top = by - padding
    bottom = bottom_y

    if b_bottom > bottom_y:
        warnings.append(f"candidate bounds bottom {b_bottom:.1f} exceeds fixed bottomY {bottom_y:.1f}; crop cannot fully contain candidate without violating bottom rule")
    if top >= bottom - 20:
        top = max(0, bottom - (bh + padding))
        warnings.append("top adjusted because fixed bottom produced invalid/too-small height")

    width = right - left
    height = bottom - top
    if width <= bw:
        warnings.append("recording width is not larger than candidate width")
    if height <= bh and b_bottom <= bottom_y:
        warnings.append("recording height is not larger than candidate height")

    if left < 0:
        right -= left
        left = 0
    if right > screen_w:
        shift = right - screen_w
        left = max(0, left - shift)
        right = screen_w
    top = clamp(top, 0, screen_h - 1)
    bottom = clamp(bottom, top + 1, screen_h)

    area = {
        "x": int(round(left)),
        "y": int(round(top)),
        "width": int(round(right - left)),
        "height": int(round(bottom - top)),
        "bottomY": int(round(bottom)),
        "aspectRatio": "native",
        "sourceMethod": "character_bounds_plus_mute_buttons_bottom",
    }
    return area, warnings


def comparison_layout(num_mods: int) -> Dict[str, Any]:
    if num_mods == 2:
        return {"type": "side_by_side_2", "rows": 1, "cols_per_row": [2]}
    if num_mods == 3:
        return {"type": "side_by_side_3", "rows": 1, "cols_per_row": [3]}
    if num_mods == 4:
        return {"type": "grid", "rows": 1, "cols_per_row": [4]}
    if num_mods == 5:
        return {"type": "grid", "rows": 1, "cols_per_row": [5]}
    if num_mods == 6:
        return {"type": "grid", "rows": 2, "cols_per_row": [3, 3]}
    if num_mods == 7:
        return {"type": "grid", "rows": 2, "cols_per_row": [4, 3]}
    if num_mods == 8:
        return {"type": "grid", "rows": 2, "cols_per_row": [4, 4]}
    if num_mods == 9:
        return {"type": "grid", "rows": 2, "cols_per_row": [5, 4]}
    if num_mods == 10:
        return {"type": "grid", "rows": 2, "cols_per_row": [5, 5]}
    raise ValueError(num_mods)


def cell_dimensions(num_mods: int) -> Tuple[Dict[str, Any], int, int, int, int, int]:
    layout = comparison_layout(num_mods)
    rows = layout["rows"]
    max_cols = max(layout["cols_per_row"])
    cell_w = (TOTAL_WIDTH // max_cols // 2) * 2
    video_h = (VIDEO_HEIGHT // rows // 2) * 2
    header_h = (HEADER_HEIGHT // rows // 2) * 2
    cell_total_h = header_h + video_h
    actual_w = max_cols * cell_w
    actual_h = rows * cell_total_h
    return layout, cell_w, video_h, header_h, actual_w, actual_h


def paste_stretch(canvas: Image.Image, src: Image.Image, box: Tuple[int, int, int, int], fill=(0, 0, 0)) -> Dict[str, Any]:
    x, y, w, h = box
    draw = ImageDraw.Draw(canvas)
    draw.rectangle([x, y, x + w, y + h], fill=fill)
    resized = src.resize((w, h), Image.Resampling.LANCZOS)
    canvas.paste(resized, (x, y))
    return {
        "x": x,
        "y": y,
        "width": w,
        "height": h,
        "scaleX": w / src.width,
        "scaleY": h / src.height,
        "preserveAspectRatio": False,
    }


def create_mock_comparison(crop_image: Image.Image, num_mods: int, output_path: Path) -> Dict[str, Any]:
    layout, cell_w, video_h, header_h, actual_w, actual_h = cell_dimensions(num_mods)
    image = Image.new("RGB", (actual_w, actual_h), (0, 0, 0))
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("arial.ttf", max(18, min(42, header_h // 3)))
        small_font = ImageFont.truetype("arial.ttf", max(14, min(26, header_h // 5)))
    except Exception:
        font = ImageFont.load_default()
        small_font = ImageFont.load_default()

    placements = []
    cursor = 0
    for row, cols in enumerate(layout["cols_per_row"]):
        row_w = cols * cell_w
        row_x = (actual_w - row_w) // 2
        for col in range(cols):
            idx = cursor + col
            x = row_x + col * cell_w
            y = row * (header_h + video_h)
            draw.rectangle([x, y, x + cell_w, y + header_h], fill=(0, 0, 0))
            draw.text((x + 18, y + 18), f"Mod {idx + 1}", fill=(255, 255, 255), font=font)
            draw.text((x + 18, y + 22 + font.size if hasattr(font, "size") else y + 48), "stretch scale, no recrop", fill=(120, 255, 120), font=small_font)
            draw.rectangle([x, y + header_h - BORDER, x + cell_w, y + header_h], fill=(0, 128, 0))

            video_y = y + header_h
            placement = paste_stretch(image, crop_image, (x, video_y, cell_w, video_h))
            placements.append({"modIndex": idx, "cell": {"x": x, "y": video_y, "width": cell_w, "height": video_h}, "placement": placement})

            if col > 0:
                draw.rectangle([x - BORDER // 2, y, x + BORDER // 2, y + header_h + video_h], fill=(245, 245, 245))
            if row > 0:
                draw.rectangle([0, y - BORDER // 2, actual_w, y + BORDER // 2], fill=(245, 245, 245))
        cursor += cols

    image.save(output_path)
    return {
        "numMods": num_mods,
        "layout": layout,
        "cellWidth": cell_w,
        "cellVideoHeight": video_h,
        "cellHeaderHeight": header_h,
        "outputWidth": actual_w,
        "outputHeight": actual_h,
        "placements": placements,
        "path": str(output_path),
    }


def draw_recording_overlay(screenshot_path: Path, output_path: Path, area: Dict[str, Any], candidate: Dict[str, Any], bottom: Dict[str, Any]) -> None:
    image = Image.open(screenshot_path).convert("RGB")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("arial.ttf", 18)
    except Exception:
        font = ImageFont.load_default()
    ax, ay, aw, ah = area["x"], area["y"], area["width"], area["height"]
    draw.rectangle([ax, ay, ax + aw, ay + ah], outline="#00ff66", width=4)
    draw.line([0, area["bottomY"], image.width, area["bottomY"]], fill="#00ff66", width=3)
    bx, by, bw, bh = candidate["boundsX"], candidate["boundsY"], candidate["boundsWidth"], candidate["boundsHeight"]
    draw.rectangle([bx, by, bx + bw, by + bh], outline="#ff3333", width=3)
    text = f"recordingArea {aw}x{ah}, bottomY={area['bottomY']} ({bottom['method']})"
    draw.rectangle([80, 72, min(image.width - 20, 980), 102], fill="black")
    draw.text((90, 78), text, fill="#00ff66", font=font)
    image.save(output_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Test character-bound recordingArea and scaled comparison mockups.")
    parser.add_argument("url", nargs="?", help="Cocrea/Sprunki mod URL")
    parser.add_argument("--icon-index", type=int, default=None, help="Icon index to drag; default 0")
    parser.add_argument("--wait-after-drag", type=float, default=None, help="Seconds to wait after drag before inspect")
    parser.add_argument("--candidate-radius", type=float, default=None, help="Pixel radius around center slot for candidate targets")
    parser.add_argument("--output-dir", default="recording_area_scaled_comparison_tests", help="Base output directory")
    parser.add_argument("--keep-browser", action="store_true", default=None, help="Leave Chrome open after the test")
    return parser.parse_args()


def ask_interactive_options(args: argparse.Namespace) -> argparse.Namespace:
    if not args.url:
        while True:
            args.url = input("Nhập URL bản mod Cocrea/Sprunki: ").strip()
            if args.url:
                break
            print("URL không được để trống.")
    if args.icon_index is None:
        raw = input("Icon index muốn kéo vào slot giữa [0]: ").strip()
        args.icon_index = int(raw) if raw else 0
    if args.wait_after_drag is None:
        raw = input("Chờ sau khi kéo icon bao nhiêu giây trước khi inspect? [2.0]: ").strip()
        args.wait_after_drag = float(raw) if raw else 2.0
    if args.candidate_radius is None:
        raw = input("Bán kính tìm bounds quanh slot giữa, pixel [360]: ").strip()
        args.candidate_radius = float(raw) if raw else 360.0
    if args.keep_browser is None:
        raw = input("Giữ Chrome mở sau khi test? [y/N]: ").strip().lower()
        args.keep_browser = raw in {"y", "yes", "1", "true"}
    raw_output = input(f"Thư mục output base [{args.output_dir}]: ").strip()
    if raw_output:
        args.output_dir = raw_output
    return args


def write_json(path: Path, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def main() -> int:
    args = ask_interactive_options(parse_args())
    url = args.url.strip()
    if not url.startswith(("http://", "https://")):
        print("URL must start with http:// or https://")
        return 2

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = Path(args.output_dir) / f"{slug_from_url(url)}-{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=True)

    player = SprunkiGamePlayer()
    ws = None
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

        before = eval_js(ws, LAYOUT_AND_BOUNDS_JS, timeout=30)
        if not before.get("ok"):
            raise RuntimeError(f"Could not inspect before drag: {before}")

        slots = before.get("slots", [])
        icons = before.get("icons", [])
        if not slots:
            raise RuntimeError("No slots found")
        if not icons:
            raise RuntimeError("No icons found")

        center_index = (len(slots) - 1) // 2
        center_slot = next((slot for slot in slots if slot.get("slotIndex") == center_index), slots[center_index])
        icon = next((item for item in icons if item.get("iconIndex") == args.icon_index), None)
        if not icon:
            raise RuntimeError(f"Icon index {args.icon_index} not found; available: {[i.get('iconIndex') for i in icons]}")

        print(f"Dragging icon {args.icon_index} -> center slot {center_index}")
        cdp_drag(ws, float(icon["pixelX"]), float(icon["pixelY"]), float(center_slot["pixelX"]), float(center_slot["pixelY"]))
        print(f"Waiting {args.wait_after_drag:.1f}s after drag...")
        time.sleep(float(args.wait_after_drag))

        after = eval_js(ws, LAYOUT_AND_BOUNDS_JS, timeout=30)
        if not after.get("ok"):
            raise RuntimeError(f"Could not inspect after drag: {after}")

        screenshot_path = output_dir / "after_drag.png"
        pyautogui.screenshot().save(screenshot_path)

        bottom = detect_bottom_y(after, fallback=670.0)
        candidates = likely_character_targets(before, after, center_slot, float(args.candidate_radius))
        if not candidates:
            raise RuntimeError("No likely character bounds found near center slot")
        candidate = candidates[0]
        area, warnings = calculate_recording_area(candidate, float(bottom["value"]))

        overlay_path = output_dir / "recording_area_overlay.png"
        crop_path = output_dir / "recording_area_crop.png"
        draw_recording_overlay(screenshot_path, overlay_path, area, candidate, bottom)

        screenshot = Image.open(screenshot_path).convert("RGB")
        crop_box = (area["x"], area["y"], area["x"] + area["width"], area["y"] + area["height"])
        crop_img = screenshot.crop(crop_box)
        crop_img.save(crop_path)

        mockups = []
        for num_mods in range(2, 11):
            output_path = output_dir / f"mock_compare_{num_mods}mods.png"
            mockups.append(create_mock_comparison(crop_img, num_mods, output_path))

        result = {
            "url": url,
            "generatedAt": datetime.now().isoformat(),
            "recordingArea": area,
            "recordingAreaWarnings": warnings,
            "bottomDetection": bottom,
            "bestCharacterCandidate": candidate,
            "candidateCount": len(candidates),
            "artifacts": {
                "afterDrag": str(screenshot_path),
                "recordingAreaOverlay": str(overlay_path),
                "recordingAreaCrop": str(crop_path),
            },
            "comparisonMockups": mockups,
            "comparisonLayoutSource": {
                "project": "G:\\VS-Project\\SprunkiComparisonService",
                "rules": {
                    "2": {"rows": 1, "cols_per_row": [2]},
                    "3": {"rows": 1, "cols_per_row": [3]},
                    "4": {"rows": 1, "cols_per_row": [4]},
                    "5": {"rows": 1, "cols_per_row": [5]},
                    "6": {"rows": 2, "cols_per_row": [3, 3]},
                    "7": {"rows": 2, "cols_per_row": [4, 3]},
                    "8": {"rows": 2, "cols_per_row": [4, 4]},
                    "9": {"rows": 2, "cols_per_row": [5, 4]},
                    "10": {"rows": 2, "cols_per_row": [5, 5]},
                },
                "currentServiceVideoPlacement": "scale force_original_aspect_ratio=increase + crop",
                "thisTestVideoPlacement": "stretch scale to cell width/height, no aspect preservation, no additional crop",
            },
        }
        json_path = output_dir / "result.json"
        write_json(json_path, result)

        print("\nScaled comparison mockup test complete.")
        print(f"Output directory: {output_dir.resolve()}")
        print(f"Recording crop: {crop_path.resolve()}")
        print(f"Overlay: {overlay_path.resolve()}")
        print(f"Result JSON: {json_path.resolve()}")
        if warnings:
            print("Warnings:")
            for warning in warnings:
                print(f"  - {warning}")
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
    sys.exit(main())

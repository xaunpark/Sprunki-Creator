"""
Compare multiple recordingArea detection methods for Cocrea/Sprunki mods.

Usage:
    python test_recording_area_methods.py "https://www.cocrea.world/@user/project"

Outputs are written to recording_area_tests/<slug>-<timestamp>/:
    - screenshot.png
    - results.json
    - overlay_all.png
    - overlay_<method>.png

The script intentionally does not change the main processing pipeline. It opens
the URL through the existing SprunkiGamePlayer setup, reads Scratch VM/runtime
data via CDP, captures a screenshot, and compares recordingArea candidates.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import cv2
import numpy as np
import pyautogui
import pygetwindow as gw
import websocket
from PIL import Image, ImageDraw, ImageFont

from inspect_game_entry import complete_game_entry_sequence
from sprunki_game_player import SprunkiGamePlayer


ASPECT_RATIO = 96 / 83
MENU_WIDTH = 78
STAGE_W = 640
STAGE_H = 360
DEFAULT_SCREEN_W = 1920
DEFAULT_SCREEN_H = 1080
TOP_LINE_RANGE_PX = (150, 250)
BOTTOM_LINE_RANGE_PX = (600, 750)
TOP_LINE_FALLBACK_Y = 170
BOTTOM_LINE_FALLBACK_Y = 670
CONTINUITY_CHECK_SLICE_HEIGHT = 10
CONTINUITY_THRESHOLD_RATIO = 0.85
PEAK_HEIGHT_THRESHOLD_RATIO = 0.10
PEAK_MIN_DISTANCE_RATIO = 0.05


COLORS = {
    "opencv_lines_current": "#ff3333",
    "hardcoded_line_fallback": "#ffaa00",
    "runtime_fixed_logic_region": "#2f80ff",
    "runtime_slot_band_heuristic": "#22aa66",
    "renderer_bounds_centerish": "#cc33ff",
}


def is_line_continuous(edges_slice: np.ndarray, width_threshold: float) -> bool:
    vertical_projection = np.sum(edges_slice, axis=0)
    non_empty_columns = np.count_nonzero(vertical_projection)
    continuity_ratio = non_empty_columns / edges_slice.shape[1]
    return continuity_ratio >= width_threshold


def find_local_peaks(values: np.ndarray, min_height: float, min_distance: int) -> Dict[int, float]:
    candidates: List[Tuple[int, float]] = []
    for y in range(1, len(values) - 1):
        value = float(values[y])
        if value < min_height:
            continue
        if value >= float(values[y - 1]) and value >= float(values[y + 1]):
            candidates.append((y, value))

    candidates.sort(key=lambda item: item[1], reverse=True)
    selected: List[Tuple[int, float]] = []
    for y, value in candidates:
        if all(abs(y - existing_y) >= min_distance for existing_y, _ in selected):
            selected.append((y, value))

    return dict(selected)


def find_dividing_lines_for_test(image_path: str) -> Tuple[Tuple[int, int], Any, Any, Any]:
    """
    Local copy of the current OpenCV idea without importing analyze_layout.py.

    analyze_layout.py imports matplotlib for its visualizer, which is not needed
    for this comparison script and may be absent in the test venv.
    """
    image = cv2.imread(image_path)
    if image is None:
        return (TOP_LINE_FALLBACK_Y, BOTTOM_LINE_FALLBACK_Y), None, None, None

    img_height, img_width, _ = image.shape
    gray_image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray_image, 50, 150)
    horizontal_projection = np.sum(edges, axis=1)

    all_candidate_peaks = find_local_peaks(
        horizontal_projection,
        min_height=img_width * PEAK_HEIGHT_THRESHOLD_RATIO,
        min_distance=max(1, int(img_height * PEAK_MIN_DISTANCE_RATIO)),
    )

    def choose_line(y_range: Tuple[int, int], fallback: int) -> int:
        candidates = {y: h for y, h in all_candidate_peaks.items() if y_range[0] <= y <= y_range[1]}
        if not candidates:
            return fallback

        continuous = {}
        for y, height in candidates.items():
            slice_start = max(0, y - CONTINUITY_CHECK_SLICE_HEIGHT // 2)
            slice_end = min(img_height, y + CONTINUITY_CHECK_SLICE_HEIGHT // 2)
            edge_slice = edges[slice_start:slice_end, :]
            if is_line_continuous(edge_slice, CONTINUITY_THRESHOLD_RATIO):
                continuous[y] = height

        pool = continuous if continuous else candidates
        return int(max(pool, key=pool.get))

    top_line_y = choose_line(TOP_LINE_RANGE_PX, TOP_LINE_FALLBACK_Y)
    bottom_line_y = choose_line(BOTTOM_LINE_RANGE_PX, BOTTOM_LINE_FALLBACK_Y)
    return (top_line_y, bottom_line_y), edges, horizontal_projection, list(all_candidate_peaks.keys())


RUNTIME_LAYOUT_JS = r"""
new Promise((resolve) => {
  try {
    function findVM() {
      if (window.__vm && window.__vm.runtime) return window.__vm;
      function getReactKey(el) {
        return Object.keys(el).find(k => k.startsWith('__react'));
      }
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
      if (logicX === null || logicY === null || logicX === undefined || logicY === undefined) {
        return { x: null, y: null };
      }
      return {
        x: Math.round(canvasCenterX + (logicX * scale)),
        y: Math.round(canvasCenterY - (logicY * scale))
      };
    }

    function boundsToPixels(bounds) {
      if (!bounds) return null;
      const left = Number(bounds.left);
      const right = Number(bounds.right);
      const top = Number(bounds.top);
      const bottom = Number(bounds.bottom);
      if (![left, right, top, bottom].every(Number.isFinite)) return null;
      const p1 = toPixel(left, top);
      const p2 = toPixel(right, bottom);
      return {
        x: Math.round(Math.min(p1.x, p2.x)),
        y: Math.round(Math.min(p1.y, p2.y)),
        width: Math.round(Math.abs(p2.x - p1.x)),
        height: Math.round(Math.abs(p2.y - p1.y)),
        raw: { left, right, top, bottom }
      };
    }

    const vm = findVM();
    if (!vm) {
      resolve({ ok: false, error: 'Cannot find Scratch VM' });
      return;
    }

    const renderer = vm.runtime.renderer || vm.renderer || null;
    const targets = vm.runtime.targets || [];

    function getBounds(target) {
      const drawableID = target.drawableID ?? target._drawableID;
      const attempts = [];
      if (renderer && drawableID !== undefined && drawableID !== null) {
        if (typeof renderer.getBounds === 'function') {
          attempts.push(() => renderer.getBounds(drawableID));
        }
        if (typeof renderer.getBoundsForDrawable === 'function') {
          attempts.push(() => renderer.getBoundsForDrawable(drawableID));
        }
        if (typeof renderer.getCurrentSkinSize === 'function') {
          attempts.push(() => {
            const size = renderer.getCurrentSkinSize(drawableID);
            if (!size || !Array.isArray(size) || size.length < 2) return null;
            const halfW = (size[0] * (target.size || 100) / 100) / 2;
            const halfH = (size[1] * (target.size || 100) / 100) / 2;
            return {
              left: target.x - halfW,
              right: target.x + halfW,
              top: target.y + halfH,
              bottom: target.y - halfH
            };
          });
        }
      }
      if (typeof target.getBounds === 'function') {
        attempts.push(() => target.getBounds());
      }

      for (const attempt of attempts) {
        try {
          const bounds = attempt();
          const px = boundsToPixels(bounds);
          if (px && px.width > 0 && px.height > 0) return px;
        } catch (e) {}
      }
      return null;
    }

    const rawTargets = targets
      .filter(t => !t.isStage)
      .map((t, idx) => {
        const name = t.sprite?.name || t.getName?.() || '';
        const pixel = toPixel(t.x, t.y);
        return {
          index: idx,
          id: t.id,
          name,
          visible: !!t.visible,
          x: t.x,
          y: t.y,
          pixelX: pixel.x,
          pixelY: pixel.y,
          size: t.size,
          currentCostume: t.currentCostume,
          drawableID: t.drawableID ?? t._drawableID ?? null,
          bounds: getBounds(t)
        };
      });

    const icons = rawTargets
      .filter(t => t.name.includes('Icons') && t.visible && t.pixelY >= 540)
      .map((t, i) => ({ x: t.pixelX, y: t.pixelY, index: i, name: t.name, bounds: t.bounds }));

    const slots = rawTargets
      .filter(t => /^Polo\s+\d+/i.test(t.name))
      .sort((a, b) => {
        const an = parseInt((a.name.match(/\d+/) || ['0'])[0], 10);
        const bn = parseInt((b.name.match(/\d+/) || ['0'])[0], 10);
        return an - bn;
      })
      .map(t => ({ x: t.pixelX, y: t.pixelY, originalSprite: t.name, bounds: t.bounds }))
      .sort((a, b) => a.x - b.x)
      .map((t, i) => ({ ...t, index: i }));

    resolve({
      ok: true,
      currentUrl: window.location.href,
      title: document.title,
      screen: { width: screenW, height: screenH },
      canvas: {
        stageW,
        stageH,
        menuWidth,
        scale,
        canvasW,
        canvasH,
        leftOffset,
        topOffset,
        canvasCenterX,
        canvasCenterY
      },
      icons,
      slots,
      targets: rawTargets
    });
  } catch (error) {
    resolve({ ok: false, error: String(error), stack: error?.stack });
  }
});
"""


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
            "params": {
                "expression": expression,
                "awaitPromise": True,
                "returnByValue": True,
            },
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


def calculate_center_slot(slots: List[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], Optional[int]]:
    if not slots:
        return None, None
    center_index = (len(slots) - 1) // 2
    center_slot = next((slot for slot in slots if slot.get("index") == center_index), None)
    return center_slot, center_index


def area_from_lines(
    top_y: float,
    bottom_y: float,
    center_x: float,
    screen_w: int,
    method: str,
    source: str,
) -> Optional[Dict[str, Any]]:
    height = bottom_y - top_y
    if height <= 0 or not math.isfinite(height):
        return None
    width = height * ASPECT_RATIO
    x = center_x - width / 2
    x = max(0, min(x, screen_w - width))
    return {
        "method": method,
        "source": source,
        "x": int(round(x)),
        "y": int(round(top_y)),
        "width": int(round(width)),
        "height": int(round(height)),
        "aspectRatio": "96:83",
        "debug": {
            "topY": round(top_y, 2),
            "bottomY": round(bottom_y, 2),
            "centerX": round(center_x, 2),
        },
    }


def logic_y_to_pixel_y(layout: Dict[str, Any], logic_y: float) -> float:
    canvas = layout["canvas"]
    return canvas["canvasCenterY"] - logic_y * canvas["scale"]


def area_from_box(
    box: Dict[str, float],
    screen_w: int,
    screen_h: int,
    method: str,
    source: str,
    pad_ratio: float = 0.18,
) -> Optional[Dict[str, Any]]:
    if not box:
        return None
    x = float(box["x"])
    y = float(box["y"])
    w = float(box["width"])
    h = float(box["height"])
    if w <= 0 or h <= 0:
        return None

    pad = max(w, h) * pad_ratio
    x -= pad
    y -= pad
    w += pad * 2
    h += pad * 2

    current_ratio = w / h
    if current_ratio < ASPECT_RATIO:
        new_w = h * ASPECT_RATIO
        x -= (new_w - w) / 2
        w = new_w
    else:
        new_h = w / ASPECT_RATIO
        y -= (new_h - h) / 2
        h = new_h

    x = max(0, min(x, screen_w - w))
    y = max(0, min(y, screen_h - h))
    return {
        "method": method,
        "source": source,
        "x": int(round(x)),
        "y": int(round(y)),
        "width": int(round(w)),
        "height": int(round(h)),
        "aspectRatio": "96:83",
        "debug": {"inputBox": box, "padRatio": pad_ratio},
    }


def union_boxes(boxes: List[Dict[str, Any]]) -> Optional[Dict[str, float]]:
    valid = [
        b
        for b in boxes
        if b
        and b.get("width", 0) > 0
        and b.get("height", 0) > 0
        and all(k in b for k in ("x", "y", "width", "height"))
    ]
    if not valid:
        return None
    left = min(float(b["x"]) for b in valid)
    top = min(float(b["y"]) for b in valid)
    right = max(float(b["x"]) + float(b["width"]) for b in valid)
    bottom = max(float(b["y"]) + float(b["height"]) for b in valid)
    return {"x": left, "y": top, "width": right - left, "height": bottom - top}


def calculate_methods(layout: Dict[str, Any], screenshot_path: Path, screen_size: Tuple[int, int]) -> List[Dict[str, Any]]:
    screen_w, screen_h = screen_size
    slots = layout.get("slots", [])
    icons = layout.get("icons", [])
    center_slot, center_index = calculate_center_slot(slots)
    if not center_slot or center_slot.get("x") is None:
        raise RuntimeError("Cannot calculate methods because center slot was not found.")

    center_x = float(center_slot["x"])
    areas: List[Dict[str, Any]] = []

    # 1. Existing OpenCV line method.
    opencv_result = find_dividing_lines_for_test(str(screenshot_path))
    if opencv_result and opencv_result[0] and len(opencv_result[0]) >= 2:
        top_y, bottom_y = opencv_result[0][0], opencv_result[0][1]
        area = area_from_lines(
            top_y,
            bottom_y,
            center_x,
            screen_w,
            "opencv_lines_current",
            "OpenCV Canny horizontal projection, same basic dependency as current pipeline",
        )
        if area:
            areas.append(area)

    # 2. Existing hard fallback lines.
    area = area_from_lines(
        TOP_LINE_FALLBACK_Y,
        BOTTOM_LINE_FALLBACK_Y,
        center_x,
        screen_w,
        "hardcoded_line_fallback",
        "Fixed fallback line values from analyze_layout.py",
    )
    if area:
        areas.append(area)

    # 3. Runtime fixed logical region. These defaults approximate the current
    # 170..670 fallback, but are converted through the stage scale.
    logic_top = 129
    logic_bottom = -45
    top_y = logic_y_to_pixel_y(layout, logic_top)
    bottom_y = logic_y_to_pixel_y(layout, logic_bottom)
    area = area_from_lines(
        top_y,
        bottom_y,
        center_x,
        screen_w,
        "runtime_fixed_logic_region",
        "Scratch logical Y region converted through VM stage scale",
    )
    if area:
        area["debug"].update({"logicTopY": logic_top, "logicBottomY": logic_bottom})
        areas.append(area)

    # 4. Runtime slot/icon band heuristic. This avoids image edges and derives
    # a band from VM coordinates. It is intentionally simple for comparison.
    canvas = layout["canvas"]
    slot_y_values = [float(s["y"]) for s in slots if s.get("y") is not None]
    icon_y_values = [float(i["y"]) for i in icons if i.get("y") is not None]
    canvas_top = float(canvas["topOffset"])
    canvas_h = float(canvas["canvasH"])
    median_slot_y = sorted(slot_y_values)[len(slot_y_values) // 2] if slot_y_values else center_slot["y"]
    median_icon_y = sorted(icon_y_values)[len(icon_y_values) // 2] if icon_y_values else None
    heuristic_top = canvas_top + canvas_h * 0.145
    if median_icon_y and median_icon_y > median_slot_y:
        heuristic_bottom = median_slot_y + (median_icon_y - median_slot_y) * 0.22
    else:
        heuristic_bottom = median_slot_y + canvas_h * 0.06
    area = area_from_lines(
        heuristic_top,
        heuristic_bottom,
        center_x,
        screen_w,
        "runtime_slot_band_heuristic",
        "VM slot/icon coordinates with fixed stage-relative padding",
    )
    if area:
        area["debug"].update(
            {
                "canvasTop": round(canvas_top, 2),
                "canvasH": round(canvas_h, 2),
                "medianSlotY": round(float(median_slot_y), 2),
                "medianIconY": round(float(median_icon_y), 2) if median_icon_y else None,
                "centerSlotIndex": center_index,
            }
        )
        areas.append(area)

    # 5. Renderer bounds near the center slot if Scratch renderer exposes them.
    centerish_targets = []
    for target in layout.get("targets", []):
        if not target.get("visible"):
            continue
        name = target.get("name", "")
        if "Icons" in name:
            continue
        px = target.get("pixelX")
        py = target.get("pixelY")
        bounds = target.get("bounds")
        if px is None or py is None or not bounds:
            continue
        if abs(float(px) - center_x) <= 260 and float(py) <= screen_h * 0.72:
            centerish_targets.append(target)

    bounds_union = union_boxes([target.get("bounds") for target in centerish_targets])
    area = area_from_box(
        bounds_union,
        screen_w,
        screen_h,
        "renderer_bounds_centerish",
        "Union of visible renderer bounds near center slot, padded and aspect-fitted",
    )
    if area:
        area["debug"].update(
            {
                "targetNames": [target.get("name") for target in centerish_targets[:30]],
                "targetCount": len(centerish_targets),
            }
        )
        areas.append(area)

    return areas


def draw_overlays(screenshot_path: Path, areas: List[Dict[str, Any]], output_dir: Path) -> None:
    base = Image.open(screenshot_path).convert("RGB")
    try:
        font = ImageFont.truetype("arial.ttf", 18)
    except Exception:
        font = ImageFont.load_default()

    def draw_area(draw: ImageDraw.ImageDraw, area: Dict[str, Any], color: str, label: str) -> None:
        x = area["x"]
        y = area["y"]
        w = area["width"]
        h = area["height"]
        for offset in range(3):
            draw.rectangle([x - offset, y - offset, x + w + offset, y + h + offset], outline=color)
        text = f"{label}: ({x},{y}) {w}x{h}"
        bbox = draw.textbbox((x, max(0, y - 24)), text, font=font)
        draw.rectangle(bbox, fill="black")
        draw.text((x, max(0, y - 24)), text, fill=color, font=font)

    all_img = base.copy()
    all_draw = ImageDraw.Draw(all_img)
    for area in areas:
        method = area["method"]
        draw_area(all_draw, area, COLORS.get(method, "#ffffff"), method)
    all_img.save(output_dir / "overlay_all.png")

    for area in areas:
        method = area["method"]
        img = base.copy()
        draw = ImageDraw.Draw(img)
        draw_area(draw, area, COLORS.get(method, "#ffffff"), method)
        img.save(output_dir / f"overlay_{method}.png")


def write_results(
    output_dir: Path,
    url: str,
    layout: Dict[str, Any],
    areas: List[Dict[str, Any]],
    screenshot_path: Path,
) -> None:
    center_slot, center_index = calculate_center_slot(layout.get("slots", []))
    data = {
        "url": url,
        "generatedAt": datetime.now().isoformat(),
        "screenshot": str(screenshot_path),
        "overlays": {
            "all": str(output_dir / "overlay_all.png"),
            "perMethodPattern": str(output_dir / "overlay_<method>.png"),
        },
        "summary": {
            "iconCount": len(layout.get("icons", [])),
            "slotCount": len(layout.get("slots", [])),
            "centerSlotIndex": center_index,
            "centerSlot": center_slot,
        },
        "areas": areas,
        "layout": {
            "currentUrl": layout.get("currentUrl"),
            "title": layout.get("title"),
            "canvas": layout.get("canvas"),
            "icons": layout.get("icons"),
            "slots": layout.get("slots"),
            "targetsWithBoundsCount": len([t for t in layout.get("targets", []) if t.get("bounds")]),
        },
    }
    with open(output_dir / "results.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Test recordingArea detection methods for a Sprunki mod URL.")
    parser.add_argument("url", nargs="?", help="Cocrea/Sprunki mod URL")
    parser.add_argument("--output-dir", default="recording_area_tests", help="Base output directory")
    parser.add_argument("--settle-seconds", type=float, default=None, help="Wait time after game setup before capture")
    parser.add_argument("--keep-browser", action="store_true", default=None, help="Leave Chrome open after the test")
    return parser.parse_args()


def ask_interactive_options(args: argparse.Namespace) -> argparse.Namespace:
    if not args.url:
        while True:
            args.url = input("Nhập URL bản mod Cocrea/Sprunki: ").strip()
            if args.url:
                break
            print("URL không được để trống.")

    if args.settle_seconds is None:
        raw = input("Chờ layout ổn định bao nhiêu giây trước khi chụp? [2.0]: ").strip()
        if raw:
            try:
                args.settle_seconds = float(raw)
            except ValueError:
                print("Giá trị không hợp lệ, dùng mặc định 2.0 giây.")
                args.settle_seconds = 2.0
        else:
            args.settle_seconds = 2.0

    if args.keep_browser is None:
        raw = input("Giữ Chrome mở sau khi test? [y/N]: ").strip().lower()
        args.keep_browser = raw in {"y", "yes", "1", "true"}

    raw_output = input(f"Thư mục output base [{args.output_dir}]: ").strip()
    if raw_output:
        args.output_dir = raw_output

    return args


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

        print(f"Waiting {args.settle_seconds:.1f}s for layout to settle...")
        time.sleep(args.settle_seconds)
        activate_browser_window()

        ws_url = player._get_websocket_url()
        if not ws_url:
            raise RuntimeError("Could not get Chrome DevTools websocket URL")

        ws = websocket.create_connection(ws_url, timeout=30)
        layout = eval_js(ws, RUNTIME_LAYOUT_JS, timeout=30)
        if not layout.get("ok"):
            raise RuntimeError(f"Could not extract runtime layout: {layout}")

        screenshot = pyautogui.screenshot()
        screenshot_path = output_dir / "screenshot.png"
        screenshot.save(screenshot_path)
        screen_size = screenshot.size

        areas = calculate_methods(layout, screenshot_path, screen_size)
        draw_overlays(screenshot_path, areas, output_dir)
        write_results(output_dir, url, layout, areas, screenshot_path)

        print("\nRecording area comparison complete.")
        print(f"Output directory: {output_dir.resolve()}")
        print(f"Results JSON: {(output_dir / 'results.json').resolve()}")
        print(f"Combined overlay: {(output_dir / 'overlay_all.png').resolve()}")
        print("\nMethods:")
        for area in areas:
            print(
                f"  - {area['method']}: "
                f"({area['x']}, {area['y']}) {area['width']}x{area['height']}"
            )
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

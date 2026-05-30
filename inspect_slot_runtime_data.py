"""
Collect detailed runtime data for Sprunki slot sprites.

Usage:
    python inspect_slot_runtime_data.py

or:
    python inspect_slot_runtime_data.py "https://www.cocrea.world/@user/project"

Outputs are written to slot_runtime_inspections/<slug>-<timestamp>/:
    - screenshot.png
    - slot_overlay.png
    - slot_inspection.json

This script focuses on discovering what "slot coordinates" mean in the VM:
center, foot, top, bounds, costume dimensions, renderer drawable bounds, etc.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import pyautogui
import pygetwindow as gw
import websocket
from PIL import Image, ImageDraw, ImageFont

from inspect_game_entry import complete_game_entry_sequence
from sprunki_game_player import SprunkiGamePlayer


SLOT_INSPECTION_JS = r"""
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
        x: canvasCenterX + (logicX * scale),
        y: canvasCenterY - (logicY * scale)
      };
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
        leftPixelX: Math.min(pTopLeft.x, pBottomRight.x),
        rightPixelX: Math.max(pTopLeft.x, pBottomRight.x),
        raw: { left, right, top, bottom }
      };
    }

    function shallowKeys(obj) {
      if (!obj) return [];
      try {
        return Object.keys(obj).slice(0, 80);
      } catch (e) {
        return [];
      }
    }

    function serializeCostume(costume, idx) {
      if (!costume) return null;
      const asset = costume.asset || null;
      const skin = costume.skin || null;
      return {
        index: idx,
        name: costume.name,
        rotationCenterX: costume.rotationCenterX,
        rotationCenterY: costume.rotationCenterY,
        bitmapResolution: costume.bitmapResolution,
        size: costume.size,
        assetId: costume.assetId,
        dataFormat: costume.dataFormat,
        md5ext: costume.md5ext,
        asset: asset ? {
          assetId: asset.assetId,
          dataFormat: asset.dataFormat,
          clean: asset.clean,
          decodeFormat: asset.decodeFormat,
        } : null,
        skinKeys: shallowKeys(skin),
      };
    }

    function callMaybe(obj, names, args) {
      const out = {};
      for (const name of names) {
        try {
          if (obj && typeof obj[name] === 'function') {
            const value = obj[name](...(args || []));
            out[name] = value;
          }
        } catch (e) {
          out[name] = { error: String(e) };
        }
      }
      return out;
    }

    function getDrawable(renderer, drawableID) {
      if (!renderer || drawableID === undefined || drawableID === null) return null;
      const drawables = renderer._allDrawables || renderer._drawables || null;
      if (!drawables) return null;
      return drawables[drawableID] || null;
    }

    function inspectBounds(target, renderer) {
      const drawableID = target.drawableID ?? target._drawableID;
      const result = {
        targetBounds: null,
        rendererBounds: {},
        rendererSkinSize: {},
        drawableKeys: [],
        drawable: null,
        pixelBoundsCandidates: []
      };

      try {
        if (typeof target.getBounds === 'function') {
          result.targetBounds = target.getBounds();
        }
      } catch (e) {
        result.targetBounds = { error: String(e) };
      }

      if (renderer && drawableID !== undefined && drawableID !== null) {
        result.rendererBounds = callMaybe(
          renderer,
          ['getBounds', 'getBoundsForDrawable', 'getFencedPositionOfDrawable'],
          [drawableID]
        );
        result.rendererSkinSize = callMaybe(
          renderer,
          ['getCurrentSkinSize'],
          [drawableID]
        );

        const drawable = getDrawable(renderer, drawableID);
        result.drawableKeys = shallowKeys(drawable);
        if (drawable) {
          result.drawable = {
            id: drawableID,
            position: drawable._position || drawable.position || null,
            scale: drawable._scale || drawable.scale || null,
            direction: drawable._direction || drawable.direction || null,
            visible: drawable._visible ?? drawable.visible ?? null,
            skinId: drawable._skinId ?? drawable.skinId ?? null,
            convexHullPointsLength: drawable._convexHullPoints ? drawable._convexHullPoints.length : null,
            transformedHullPointsLength: drawable._transformedHullPoints ? drawable._transformedHullPoints.length : null,
          };
        }
      }

      const candidates = [];
      if (result.targetBounds && !result.targetBounds.error) {
        candidates.push({ source: 'target.getBounds', bounds: result.targetBounds });
      }
      for (const [name, value] of Object.entries(result.rendererBounds || {})) {
        if (value && !value.error) {
          candidates.push({ source: `renderer.${name}`, bounds: value });
        }
      }

      result.pixelBoundsCandidates = candidates
        .map(c => ({ source: c.source, pixelBounds: boundsToPixels(c.bounds), rawBounds: c.bounds }))
        .filter(c => c.pixelBounds);

      return result;
    }

    const vm = findVM();
    if (!vm) {
      resolve({ ok: false, error: 'Cannot find Scratch VM' });
      return;
    }

    const runtime = vm.runtime;
    const renderer = runtime.renderer || vm.renderer || null;
    const allTargets = runtime.targets || [];

    const slots = allTargets
      .filter(t => !t.isStage && /^Polo\s+\d+/i.test(t.sprite?.name || t.getName?.() || ''))
      .map((target) => {
        const sprite = target.sprite || {};
        const name = sprite.name || target.getName?.() || '';
        const pixel = toPixel(target.x, target.y);
        const currentCostumeIndex = target.currentCostume;
        const costumes = (sprite.costumes || []).map(serializeCostume);
        const currentCostume = costumes[currentCostumeIndex] || null;
        const boundsInfo = inspectBounds(target, renderer);
        return {
          id: target.id,
          name,
          visible: target.visible,
          logic: {
            x: target.x,
            y: target.y,
            size: target.size,
            direction: target.direction,
            draggable: target.draggable,
            rotationStyle: target.rotationStyle,
            currentCostume: currentCostumeIndex,
          },
          pixel: {
            x: pixel.x,
            y: pixel.y,
            roundedX: Math.round(pixel.x),
            roundedY: Math.round(pixel.y),
          },
          drawableID: target.drawableID ?? target._drawableID ?? null,
          currentCostume,
          costumes,
          variables: Object.fromEntries(
            Object.values(target.variables || {}).map(v => [v.name, {
              id: v.id,
              name: v.name,
              value: v.value,
              type: v.type || ''
            }])
          ),
          lists: Object.fromEntries(
            Object.values(target.lists || {}).map(v => [v.name, {
              id: v.id,
              name: v.name,
              value: v.value,
              type: v.type || ''
            }])
          ),
          comments: target.comments || {},
          boundsInfo,
          targetKeys: shallowKeys(target),
          spriteKeys: shallowKeys(sprite),
        };
      })
      .sort((a, b) => a.pixel.x - b.pixel.x)
      .map((slot, index) => ({ ...slot, sortedIndex: index }));

    const icons = allTargets
      .filter(t => !t.isStage && (t.sprite?.name || '').includes('Icons') && t.visible)
      .map((target) => {
        const pixel = toPixel(target.x, target.y);
        return {
          name: target.sprite?.name || '',
          logic: { x: target.x, y: target.y, size: target.size, currentCostume: target.currentCostume },
          pixel: { x: pixel.x, y: pixel.y, roundedX: Math.round(pixel.x), roundedY: Math.round(pixel.y) },
          drawableID: target.drawableID ?? target._drawableID ?? null,
          boundsInfo: inspectBounds(target, renderer),
        };
      });

    const rendererKeys = shallowKeys(renderer);
    resolve({
      ok: true,
      currentUrl: window.location.href,
      title: document.title,
      capturedAt: new Date().toISOString(),
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
      runtimeSummary: {
        targetCount: allTargets.length,
        slotCount: slots.length,
        visibleIconCount: icons.length,
        rendererAvailable: !!renderer,
        rendererKeys,
      },
      slots,
      icons,
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


def draw_slot_overlay(screenshot_path: Path, data: Dict[str, Any], output_path: Path) -> None:
    image = Image.open(screenshot_path).convert("RGB")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("arial.ttf", 16)
    except Exception:
        font = ImageFont.load_default()

    for slot in data.get("slots", []):
        index = slot.get("sortedIndex")
        px = slot.get("pixel", {}).get("x")
        py = slot.get("pixel", {}).get("y")
        if px is None or py is None:
            continue
        px = float(px)
        py = float(py)
        draw.line([px - 10, py, px + 10, py], fill="#00ff66", width=3)
        draw.line([px, py - 10, px, py + 10], fill="#00ff66", width=3)
        label = f"{index}:{slot.get('name')}"
        draw.text((px + 8, py - 24), label, fill="#00ff66", font=font)

        candidates = slot.get("boundsInfo", {}).get("pixelBoundsCandidates", [])
        for candidate_idx, candidate in enumerate(candidates[:3]):
            bounds = candidate.get("pixelBounds")
            if not bounds:
                continue
            color = ["#ff3333", "#3388ff", "#ffcc00"][candidate_idx]
            x = float(bounds["x"])
            y = float(bounds["y"])
            w = float(bounds["width"])
            h = float(bounds["height"])
            draw.rectangle([x, y, x + w, y + h], outline=color, width=2)
            draw.line([x, bounds["bottomPixelY"], x + w, bounds["bottomPixelY"]], fill=color, width=2)

    image.save(output_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect detailed runtime slot data for a Sprunki mod URL.")
    parser.add_argument("url", nargs="?", help="Cocrea/Sprunki mod URL")
    parser.add_argument("--output-dir", default="slot_runtime_inspections", help="Base output directory")
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
        raw = input("Chờ layout ổn định bao nhiêu giây trước khi inspect? [2.0]: ").strip()
        if raw:
            try:
                args.settle_seconds = float(raw)
            except ValueError:
                print("Giá trị không hợp lệ, dùng mặc định 2.0 giây.")
                args.settle_seconds = 2.0
        else:
            args.settle_seconds = 2.0

    if args.keep_browser is None:
        raw = input("Giữ Chrome mở sau khi inspect? [y/N]: ").strip().lower()
        args.keep_browser = raw in {"y", "yes", "1", "true"}

    raw_output = input(f"Thư mục output base [{args.output_dir}]: ").strip()
    if raw_output:
        args.output_dir = raw_output

    return args


def write_json(path: Path, data: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def first_pixel_bounds(slot: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    candidates = slot.get("boundsInfo", {}).get("pixelBoundsCandidates", [])
    if not candidates:
        return None
    return candidates[0].get("pixelBounds")


def build_compact_summary(data: Dict[str, Any]) -> Dict[str, Any]:
    canvas = data.get("canvas", {})
    rows = []
    for slot in data.get("slots", []):
        logic = slot.get("logic", {})
        pixel = slot.get("pixel", {})
        costume = slot.get("currentCostume") or {}
        costume_size = costume.get("size") or [None, None]
        bounds = first_pixel_bounds(slot)

        pixel_y = pixel.get("y")
        bounds_top = bounds.get("topPixelY") if bounds else None
        bounds_bottom = bounds.get("bottomPixelY") if bounds else None

        row = {
            "sortedIndex": slot.get("sortedIndex"),
            "name": slot.get("name"),
            "logicX": logic.get("x"),
            "logicY": logic.get("y"),
            "pixelX": pixel.get("x"),
            "pixelY": pixel_y,
            "roundedPixelX": pixel.get("roundedX"),
            "roundedPixelY": pixel.get("roundedY"),
            "sizePercent": logic.get("size"),
            "costumeName": costume.get("name"),
            "costumeWidth": costume_size[0] if len(costume_size) > 0 else None,
            "costumeHeight": costume_size[1] if len(costume_size) > 1 else None,
            "rotationCenterX": costume.get("rotationCenterX"),
            "rotationCenterY": costume.get("rotationCenterY"),
            "boundsTopY": bounds_top,
            "boundsBottomY": bounds_bottom,
            "boundsHeight": bounds.get("height") if bounds else None,
            "boundsWidth": bounds.get("width") if bounds else None,
            "offsetPixelToBoundsTop": (pixel_y - bounds_top) if pixel_y is not None and bounds_top is not None else None,
            "offsetPixelToBoundsBottom": (bounds_bottom - pixel_y) if pixel_y is not None and bounds_bottom is not None else None,
        }
        rows.append(row)

    pixel_ys = [r["pixelY"] for r in rows if r.get("pixelY") is not None]
    bottom_offsets = [r["offsetPixelToBoundsBottom"] for r in rows if r.get("offsetPixelToBoundsBottom") is not None]
    top_offsets = [r["offsetPixelToBoundsTop"] for r in rows if r.get("offsetPixelToBoundsTop") is not None]

    def median(values: List[float]) -> Optional[float]:
        if not values:
            return None
        sorted_values = sorted(float(v) for v in values)
        mid = len(sorted_values) // 2
        if len(sorted_values) % 2:
            return sorted_values[mid]
        return (sorted_values[mid - 1] + sorted_values[mid]) / 2

    aggregate = {
        "slotCount": len(rows),
        "medianPixelY": median(pixel_ys),
        "medianOffsetPixelToBoundsTop": median(top_offsets),
        "medianOffsetPixelToBoundsBottom": median(bottom_offsets),
    }

    return {
        "currentUrl": data.get("currentUrl"),
        "title": data.get("title"),
        "capturedAt": data.get("capturedAt"),
        "canvas": canvas,
        "runtimeSummary": data.get("runtimeSummary"),
        "aggregate": aggregate,
        "slots": rows,
        "notes": {
            "pixelXY": "Sprite runtime coordinate converted to screen pixels. For these Polo slots it appears to be the costume rotation center.",
            "bounds": "First renderer/target pixel bounds candidate. It may include costume artwork below the desired recordingArea bottom line.",
        },
    }


def print_summary(data: Dict[str, Any]) -> None:
    print("\nSlot summary:")
    for slot in data.get("slots", []):
        pixel = slot.get("pixel", {})
        logic = slot.get("logic", {})
        candidates = slot.get("boundsInfo", {}).get("pixelBoundsCandidates", [])
        first_bounds = candidates[0].get("pixelBounds") if candidates else None
        bounds_text = "no bounds"
        if first_bounds:
            bounds_text = (
                f"bounds y={first_bounds.get('topPixelY'):.1f}.."
                f"{first_bounds.get('bottomPixelY'):.1f}, "
                f"h={first_bounds.get('height'):.1f}"
            )
        print(
            f"  - #{slot.get('sortedIndex')} {slot.get('name')}: "
            f"logic=({logic.get('x')},{logic.get('y')}), "
            f"pixel=({pixel.get('roundedX')},{pixel.get('roundedY')}), "
            f"size={logic.get('size')}, {bounds_text}"
        )


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
        data = eval_js(ws, SLOT_INSPECTION_JS, timeout=30)
        if not data.get("ok"):
            raise RuntimeError(f"Could not inspect slot runtime data: {data}")

        screenshot_path = output_dir / "screenshot.png"
        pyautogui.screenshot().save(screenshot_path)

        data["artifacts"] = {
            "screenshot": str(screenshot_path),
            "slotOverlay": str(output_dir / "slot_overlay.png"),
        }
        json_path = output_dir / "slot_inspection.json"
        compact_path = output_dir / "slot_compact_summary.json"
        write_json(json_path, data)
        write_json(compact_path, build_compact_summary(data))
        draw_slot_overlay(screenshot_path, data, output_dir / "slot_overlay.png")

        print_summary(data)
        print("\nSlot runtime inspection complete.")
        print(f"Output directory: {output_dir.resolve()}")
        print(f"JSON: {json_path.resolve()}")
        print(f"Compact JSON: {compact_path.resolve()}")
        print(f"Overlay: {(output_dir / 'slot_overlay.png').resolve()}")
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

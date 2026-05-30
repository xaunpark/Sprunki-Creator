"""
Inspect all visible runtime targets near the desired recordingArea bottom line.

Usage:
    python inspect_bottom_region_targets.py

or:
    python inspect_bottom_region_targets.py "https://www.cocrea.world/@user/project"

Outputs:
    bottom_region_inspections/<slug>-<timestamp>/
      - screenshot.png
      - bottom_region_overlay.png
      - bottom_region_targets.json
      - bottom_region_compact.json

The goal is to find runtime targets such as panels, masks, dividers, icon bars,
or background sprites whose bounds/top/bottom line up with the target bottom Y.
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
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import pyautogui
import pygetwindow as gw
import websocket
from PIL import Image, ImageDraw, ImageFont

from inspect_game_entry import complete_game_entry_sequence
from sprunki_game_player import SprunkiGamePlayer


INSPECT_TARGETS_JS = r"""
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

    function safeKeys(obj) {
      try {
        return obj ? Object.keys(obj).slice(0, 80) : [];
      } catch (e) {
        return [];
      }
    }

    function callBounds(target, renderer) {
      const drawableID = target.drawableID ?? target._drawableID;
      const candidates = [];

      try {
        if (typeof target.getBounds === 'function') {
          candidates.push({ source: 'target.getBounds', bounds: target.getBounds() });
        }
      } catch (e) {
        candidates.push({ source: 'target.getBounds', error: String(e) });
      }

      if (renderer && drawableID !== undefined && drawableID !== null) {
        for (const name of ['getBounds', 'getBoundsForDrawable']) {
          try {
            if (typeof renderer[name] === 'function') {
              candidates.push({ source: `renderer.${name}`, bounds: renderer[name](drawableID) });
            }
          } catch (e) {
            candidates.push({ source: `renderer.${name}`, error: String(e) });
          }
        }
      }

      return candidates
        .map(c => ({
          source: c.source,
          error: c.error || null,
          rawBounds: c.bounds || null,
          pixelBounds: c.bounds ? boundsToPixels(c.bounds) : null
        }))
        .filter(c => c.error || c.pixelBounds);
    }

    function serializeCostume(costume, index) {
      if (!costume) return null;
      return {
        index,
        name: costume.name,
        size: costume.size,
        rotationCenterX: costume.rotationCenterX,
        rotationCenterY: costume.rotationCenterY,
        bitmapResolution: costume.bitmapResolution,
        assetId: costume.assetId,
        dataFormat: costume.dataFormat,
        md5ext: costume.md5ext,
      };
    }

    const vm = findVM();
    if (!vm) {
      resolve({ ok: false, error: 'Cannot find Scratch VM' });
      return;
    }

    const runtime = vm.runtime;
    const renderer = runtime.renderer || vm.renderer || null;
    const targets = (runtime.targets || [])
      .filter(t => !t.isStage && t.visible)
      .map((target, runtimeIndex) => {
        const sprite = target.sprite || {};
        const name = sprite.name || target.getName?.() || '';
        const pixel = toPixel(target.x, target.y);
        const costumes = (sprite.costumes || []).map(serializeCostume);
        const currentCostume = costumes[target.currentCostume] || null;
        const boundsCandidates = callBounds(target, renderer);
        const primaryBounds = boundsCandidates.find(c => c.pixelBounds)?.pixelBounds || null;
        return {
          runtimeIndex,
          id: target.id,
          name,
          visible: target.visible,
          drawableID: target.drawableID ?? target._drawableID ?? null,
          logic: {
            x: target.x,
            y: target.y,
            size: target.size,
            direction: target.direction,
            currentCostume: target.currentCostume,
          },
          pixel: {
            x: pixel.x,
            y: pixel.y,
            roundedX: Math.round(pixel.x),
            roundedY: Math.round(pixel.y),
          },
          currentCostume,
          costumeCount: costumes.length,
          boundsCandidates,
          primaryBounds,
          variableNames: Object.values(target.variables || {}).map(v => v.name),
          listNames: Object.values(target.lists || {}).map(v => v.name),
          targetKeys: safeKeys(target),
          spriteKeys: safeKeys(sprite),
        };
      });

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
        targetCount: runtime.targets?.length || 0,
        visibleTargetCount: targets.length,
        rendererAvailable: !!renderer,
      },
      targets,
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


def distance_to_region(target: Dict[str, Any], target_y: float) -> float:
    values: List[float] = []
    pixel_y = target.get("pixel", {}).get("y")
    if pixel_y is not None:
        values.append(abs(float(pixel_y) - target_y))
    bounds = target.get("primaryBounds")
    if bounds:
        for key in ("topPixelY", "bottomPixelY", "y"):
            if bounds.get(key) is not None:
                values.append(abs(float(bounds[key]) - target_y))
    return min(values) if values else math.inf


def build_compact(data: Dict[str, Any], target_y: float, tolerance: float) -> Dict[str, Any]:
    rows = []
    for target in data.get("targets", []):
        bounds = target.get("primaryBounds")
        pixel = target.get("pixel", {})
        current_costume = target.get("currentCostume") or {}
        values = []
        if pixel.get("y") is not None:
            values.append(float(pixel["y"]))
        if bounds:
            values.extend(
                float(bounds[k])
                for k in ("topPixelY", "bottomPixelY")
                if bounds.get(k) is not None
            )
        if not values:
            continue

        nearest_distance = min(abs(v - target_y) for v in values)
        intersects_band = False
        if bounds:
            top = float(bounds.get("topPixelY", -math.inf))
            bottom = float(bounds.get("bottomPixelY", math.inf))
            intersects_band = top <= target_y + tolerance and bottom >= target_y - tolerance

        if nearest_distance <= tolerance or intersects_band:
            rows.append(
                {
                    "runtimeIndex": target.get("runtimeIndex"),
                    "name": target.get("name"),
                    "distanceToTargetY": nearest_distance,
                    "pixelX": pixel.get("x"),
                    "pixelY": pixel.get("y"),
                    "roundedPixelX": pixel.get("roundedX"),
                    "roundedPixelY": pixel.get("roundedY"),
                    "logicX": target.get("logic", {}).get("x"),
                    "logicY": target.get("logic", {}).get("y"),
                    "sizePercent": target.get("logic", {}).get("size"),
                    "currentCostumeIndex": target.get("logic", {}).get("currentCostume"),
                    "currentCostumeName": current_costume.get("name"),
                    "costumeWidth": (current_costume.get("size") or [None, None])[0],
                    "costumeHeight": (current_costume.get("size") or [None, None])[1],
                    "rotationCenterX": current_costume.get("rotationCenterX"),
                    "rotationCenterY": current_costume.get("rotationCenterY"),
                    "boundsTopY": bounds.get("topPixelY") if bounds else None,
                    "boundsBottomY": bounds.get("bottomPixelY") if bounds else None,
                    "boundsX": bounds.get("x") if bounds else None,
                    "boundsY": bounds.get("y") if bounds else None,
                    "boundsWidth": bounds.get("width") if bounds else None,
                    "boundsHeight": bounds.get("height") if bounds else None,
                    "boundsSources": [
                        c.get("source")
                        for c in target.get("boundsCandidates", [])
                        if c.get("pixelBounds")
                    ],
                    "variableNames": target.get("variableNames"),
                    "listNames": target.get("listNames"),
                }
            )

    rows.sort(key=lambda row: (row["distanceToTargetY"], row.get("name") or ""))
    return {
        "currentUrl": data.get("currentUrl"),
        "title": data.get("title"),
        "capturedAt": data.get("capturedAt"),
        "targetY": target_y,
        "tolerance": tolerance,
        "canvas": data.get("canvas"),
        "runtimeSummary": data.get("runtimeSummary"),
        "candidateCount": len(rows),
        "candidates": rows,
    }


def draw_overlay(screenshot_path: Path, compact: Dict[str, Any], output_path: Path) -> None:
    image = Image.open(screenshot_path).convert("RGB")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("arial.ttf", 15)
    except Exception:
        font = ImageFont.load_default()

    target_y = float(compact["targetY"])
    tolerance = float(compact["tolerance"])
    draw.line([0, target_y, image.width, target_y], fill="#00ff66", width=3)
    draw.line([0, target_y - tolerance, image.width, target_y - tolerance], fill="#555555", width=1)
    draw.line([0, target_y + tolerance, image.width, target_y + tolerance], fill="#555555", width=1)
    draw.text((90, max(0, target_y - 24)), f"targetY={target_y:g}", fill="#00ff66", font=font)

    colors = ["#ff3333", "#3388ff", "#ffcc00", "#cc33ff", "#00ddff"]
    for idx, target in enumerate(compact.get("candidates", [])[:80]):
        color = colors[idx % len(colors)]
        x = target.get("boundsX")
        y = target.get("boundsY")
        w = target.get("boundsWidth")
        h = target.get("boundsHeight")
        if x is not None and y is not None and w is not None and h is not None:
            draw.rectangle([x, y, x + w, y + h], outline=color, width=2)
            if target.get("boundsTopY") is not None:
                draw.line([x, target["boundsTopY"], x + w, target["boundsTopY"]], fill=color, width=2)
            if target.get("boundsBottomY") is not None:
                draw.line([x, target["boundsBottomY"], x + w, target["boundsBottomY"]], fill=color, width=2)

        px = target.get("pixelX")
        py = target.get("pixelY")
        if px is not None and py is not None:
            draw.line([px - 6, py, px + 6, py], fill=color, width=2)
            draw.line([px, py - 6, px, py + 6], fill=color, width=2)
            label = f"{idx}:{target.get('name')}"
            draw.text((px + 6, py - 18), label, fill=color, font=font)

    image.save(output_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect all visible runtime targets near a bottom Y line.")
    parser.add_argument("url", nargs="?", help="Cocrea/Sprunki mod URL")
    parser.add_argument("--output-dir", default="bottom_region_inspections", help="Base output directory")
    parser.add_argument("--settle-seconds", type=float, default=None, help="Wait time after game setup before capture")
    parser.add_argument("--target-y", type=float, default=None, help="Desired bottom Y to search around")
    parser.add_argument("--tolerance", type=float, default=None, help="Candidate tolerance in pixels")
    parser.add_argument("--keep-browser", action="store_true", default=None, help="Leave Chrome open after the test")
    return parser.parse_args()


def ask_interactive_options(args: argparse.Namespace) -> argparse.Namespace:
    if not args.url:
        while True:
            args.url = input("Nhập URL bản mod Cocrea/Sprunki: ").strip()
            if args.url:
                break
            print("URL không được để trống.")

    if args.target_y is None:
        raw = input("Y bottom line cần tìm quanh [670]: ").strip()
        args.target_y = float(raw) if raw else 670.0

    if args.tolerance is None:
        raw = input("Tolerance vùng tìm kiếm pixel [90]: ").strip()
        args.tolerance = float(raw) if raw else 90.0

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


def print_summary(compact: Dict[str, Any]) -> None:
    print(f"\nCandidates near Y={compact['targetY']} +/- {compact['tolerance']}:")
    for idx, row in enumerate(compact.get("candidates", [])[:40]):
        print(
            f"  {idx:02d}. {row.get('name')} "
            f"dist={row.get('distanceToTargetY'):.1f} "
            f"pixelY={row.get('pixelY'):.1f} "
            f"bounds={row.get('boundsTopY'):.1f}..{row.get('boundsBottomY'):.1f} "
            f"costume={row.get('currentCostumeName')}"
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
        data = eval_js(ws, INSPECT_TARGETS_JS, timeout=30)
        if not data.get("ok"):
            raise RuntimeError(f"Could not inspect runtime targets: {data}")

        screenshot_path = output_dir / "screenshot.png"
        pyautogui.screenshot().save(screenshot_path)

        compact = build_compact(data, target_y=float(args.target_y), tolerance=float(args.tolerance))
        compact["artifacts"] = {
            "screenshot": str(screenshot_path),
            "overlay": str(output_dir / "bottom_region_overlay.png"),
            "fullJson": str(output_dir / "bottom_region_targets.json"),
        }

        full_path = output_dir / "bottom_region_targets.json"
        compact_path = output_dir / "bottom_region_compact.json"
        overlay_path = output_dir / "bottom_region_overlay.png"
        write_json(full_path, data)
        write_json(compact_path, compact)
        draw_overlay(screenshot_path, compact, overlay_path)

        print_summary(compact)
        print("\nBottom-region target inspection complete.")
        print(f"Output directory: {output_dir.resolve()}")
        print(f"Compact JSON: {compact_path.resolve()}")
        print(f"Full JSON: {full_path.resolve()}")
        print(f"Overlay: {overlay_path.resolve()}")
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

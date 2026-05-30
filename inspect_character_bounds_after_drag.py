"""
Inspect runtime/render bounds after dragging an icon into the center slot.

Goal:
    Verify whether a newly performed character has a useful runtime/render
    bound around it. If such bounds exist, they may be a better source for
    recordingArea than layout lines or UI markers.

Usage:
    python inspect_character_bounds_after_drag.py

or:
    python inspect_character_bounds_after_drag.py "https://www.cocrea.world/@user/project" --icon-index 0

Outputs:
    character_bounds_after_drag/<slug>-<timestamp>/
      - before_drag.png
      - after_drag.png
      - after_drag_overlay.png
      - character_bounds_after_drag.json
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


LAYOUT_AND_BOUNDS_JS = r"""
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
        centerPixelX: (Math.min(pTopLeft.x, pBottomRight.x) + Math.max(pTopLeft.x, pBottomRight.x)) / 2,
        centerPixelY: (Math.min(pTopLeft.y, pBottomRight.y) + Math.max(pTopLeft.y, pBottomRight.y)) / 2,
        leftPixelX: Math.min(pTopLeft.x, pBottomRight.x),
        rightPixelX: Math.max(pTopLeft.x, pBottomRight.x),
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
        md5ext: costume.md5ext,
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
        try {
          if (typeof renderer.getCurrentSkinSize === 'function') {
            out.push({ source: 'renderer.getCurrentSkinSize', skinSize: renderer.getCurrentSkinSize(drawableID) });
          }
        } catch (e) {}
      }
      return out.filter(c => c.error || c.pixelBounds || c.skinSize);
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
          boundsSources: candidates.filter(c => c.pixelBounds).map(c => c.source),
          skinSizes: candidates.filter(c => c.skinSize).map(c => ({ source: c.source, skinSize: c.skinSize })),
        };
      });

    const icons = targetRows
      .filter(t => t.name.includes('Icons') && t.roundedPixelY >= 540)
      .map((t, i) => ({ ...t, iconIndex: i }));

    const slots = targetRows
      .filter(t => /^Polo\s+\d+/i.test(t.name))
      .sort((a, b) => a.roundedPixelX - b.roundedPixelX)
      .map((t, i) => ({ ...t, slotIndex: i }));

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
      targets: targetRows,
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


def cdp_drag(ws: websocket.WebSocket, start_x: float, start_y: float, end_x: float, end_y: float, steps: int = 24) -> None:
    send_cdp(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mouseMoved", "x": start_x, "y": start_y}})
    time.sleep(0.15)
    send_cdp(
        ws,
        {
            "method": "Input.dispatchMouseEvent",
            "params": {"type": "mousePressed", "x": start_x, "y": start_y, "button": "left", "clickCount": 1},
        },
    )
    time.sleep(0.2)
    for i in range(1, steps + 1):
        t = i / steps
        x = start_x + (end_x - start_x) * t
        y = start_y + (end_y - start_y) * t
        send_cdp(
            ws,
            {
                "method": "Input.dispatchMouseEvent",
                "params": {"type": "mouseMoved", "x": x, "y": y, "button": "left", "buttons": 1},
            },
            timeout=10,
        )
        time.sleep(0.025)
    send_cdp(
        ws,
        {
            "method": "Input.dispatchMouseEvent",
            "params": {"type": "mouseReleased", "x": end_x, "y": end_y, "button": "left", "clickCount": 1},
        },
    )


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
        if "Icons" in name or re.match(r"^Polo\s+\d+", name, re.I):
            continue
        if distance_to_point(target, center_x, center_y) > radius:
            continue

        before_target = before_by_id.get(target.get("id"))
        changed = False
        if not before_target:
            changed = True
        else:
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

        score = distance_to_point(target, center_x, center_y)
        if changed:
            score -= 150
        if isinstance(target.get("boundsWidth"), (int, float)) and isinstance(target.get("boundsHeight"), (int, float)):
            area = float(target["boundsWidth"]) * float(target["boundsHeight"])
            if area > 10000:
                score -= 30
            if area > 500000:
                score += 120
        rows.append({**target, "distanceToCenterSlot": distance_to_point(target, center_x, center_y), "changedAfterDrag": changed, "score": score})

    rows.sort(key=lambda row: (row["score"], row["distanceToCenterSlot"], row.get("name") or ""))
    return rows


def draw_overlay(
    screenshot_path: Path,
    output_path: Path,
    center_slot: Dict[str, Any],
    candidates: List[Dict[str, Any]],
    recording_area: Optional[Dict[str, Any]],
) -> None:
    image = Image.open(screenshot_path).convert("RGB")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("arial.ttf", 20)
        small_font = ImageFont.truetype("arial.ttf", 14)
    except Exception:
        font = ImageFont.load_default()
        small_font = ImageFont.load_default()

    cx = float(center_slot["pixelX"])
    cy = float(center_slot["pixelY"])
    draw.line([cx - 14, cy, cx + 14, cy], fill="#00ff66", width=3)
    draw.line([cx, cy - 14, cx, cy + 14], fill="#00ff66", width=3)
    draw.text((cx + 10, cy - 26), f"center slot {center_slot.get('slotIndex')}", fill="#00ff66", font=small_font)

    if recording_area:
        x = recording_area["x"]
        y = recording_area["y"]
        w = recording_area["width"]
        h = recording_area["height"]
        draw.rectangle([x, y, x + w, y + h], outline="#00ff66", width=4)
        draw.text((x, max(0, y - 26)), "aspect-fitted candidate", fill="#00ff66", font=small_font)

    colors = ["#ff3333", "#3388ff", "#ffcc00", "#cc33ff", "#00ddff"]
    legend = []
    for idx, target in enumerate(candidates[:20]):
        color = "#00ff66" if idx == 0 else colors[(idx - 1) % len(colors)]
        x = target.get("boundsX")
        y = target.get("boundsY")
        w = target.get("boundsWidth")
        h = target.get("boundsHeight")
        if x is not None and y is not None and w is not None and h is not None:
            draw.rectangle([x, y, x + w, y + h], outline=color, width=3 if idx == 0 else 2)
            draw.rectangle([x, y - 18, x + 22, y], fill="black", outline=color)
            draw.text((x + 4, y - 18), str(idx), fill=color, font=small_font)
        legend.append(
            {
                "color": color,
                "text": (
                    f"{idx}: {target.get('name')} d={target.get('distanceToCenterSlot'):.1f} "
                    f"changed={target.get('changedAfterDrag')} "
                    f"bounds={target.get('boundsWidth'):.1f}x{target.get('boundsHeight'):.1f}"
                    if isinstance(target.get("boundsWidth"), (int, float)) and isinstance(target.get("boundsHeight"), (int, float))
                    else f"{idx}: {target.get('name')} d={target.get('distanceToCenterSlot'):.1f}"
                ),
            }
        )

    if legend:
        row_h = 20
        legend_x = 88
        legend_y = image.height - min(len(legend), 20) * row_h - 28
        legend_y = max(74, legend_y)
        legend_w = min(image.width - legend_x - 16, 1080)
        legend_h = min(len(legend), 20) * row_h + 14
        draw.rectangle([legend_x - 8, legend_y - 8, legend_x + legend_w, legend_y + legend_h], fill="black")
        draw.rectangle([legend_x - 8, legend_y - 8, legend_x + legend_w, legend_y + legend_h], outline="#666666")
        for row_i, row in enumerate(legend[:20]):
            draw.text((legend_x, legend_y + row_i * row_h), row["text"], fill=row["color"], font=small_font)
    else:
        draw.rectangle([80, 80, 840, 116], fill="black")
        draw.text((90, 86), "No likely character target bounds near center slot", fill="#ff3333", font=font)

    image.save(output_path)


def aspect_fit_from_bounds(bounds_target: Dict[str, Any], pad_ratio: float = 0.12) -> Optional[Dict[str, Any]]:
    required = ("boundsX", "boundsY", "boundsWidth", "boundsHeight")
    if not all(isinstance(bounds_target.get(key), (int, float)) for key in required):
        return None
    x = float(bounds_target["boundsX"])
    y = float(bounds_target["boundsY"])
    w = float(bounds_target["boundsWidth"])
    h = float(bounds_target["boundsHeight"])
    if w <= 0 or h <= 0:
        return None

    pad = max(w, h) * pad_ratio
    x -= pad
    y -= pad
    w += pad * 2
    h += pad * 2

    aspect = 96 / 83
    if w / h < aspect:
        new_w = h * aspect
        x -= (new_w - w) / 2
        w = new_w
    else:
        new_h = w / aspect
        y -= (new_h - h) / 2
        h = new_h

    x = max(0, min(x, 1920 - w))
    y = max(0, min(y, 1080 - h))
    return {"x": round(x), "y": round(y), "width": round(w), "height": round(h), "aspectRatio": "96:83"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect character bounds after dragging an icon into center slot.")
    parser.add_argument("url", nargs="?", help="Cocrea/Sprunki mod URL")
    parser.add_argument("--icon-index", type=int, default=None, help="Icon index to drag; default 0")
    parser.add_argument("--wait-after-drag", type=float, default=None, help="Seconds to wait after drag before inspect")
    parser.add_argument("--candidate-radius", type=float, default=None, help="Pixel radius around center slot for candidate targets")
    parser.add_argument("--output-dir", default="character_bounds_after_drag", help="Base output directory")
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
        raw = input("Giữ Chrome mở sau khi inspect? [y/N]: ").strip().lower()
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

        before_path = output_dir / "before_drag.png"
        pyautogui.screenshot().save(before_path)

        print(
            f"Dragging icon {args.icon_index} from ({icon['pixelX']:.1f},{icon['pixelY']:.1f}) "
            f"to center slot {center_index} at ({center_slot['pixelX']:.1f},{center_slot['pixelY']:.1f})"
        )
        cdp_drag(ws, float(icon["pixelX"]), float(icon["pixelY"]), float(center_slot["pixelX"]), float(center_slot["pixelY"]))

        print(f"Waiting {args.wait_after_drag:.1f}s after drag...")
        time.sleep(float(args.wait_after_drag))

        after = eval_js(ws, LAYOUT_AND_BOUNDS_JS, timeout=30)
        if not after.get("ok"):
            raise RuntimeError(f"Could not inspect after drag: {after}")

        after_path = output_dir / "after_drag.png"
        overlay_path = output_dir / "after_drag_overlay.png"
        pyautogui.screenshot().save(after_path)

        candidates = likely_character_targets(before, after, center_slot, float(args.candidate_radius))
        recording_area = aspect_fit_from_bounds(candidates[0]) if candidates else None
        draw_overlay(after_path, overlay_path, center_slot, candidates, recording_area)

        result = {
            "url": url,
            "generatedAt": datetime.now().isoformat(),
            "iconIndex": args.icon_index,
            "centerSlotIndex": center_index,
            "drag": {
                "from": {"x": icon.get("pixelX"), "y": icon.get("pixelY"), "name": icon.get("name")},
                "to": {"x": center_slot.get("pixelX"), "y": center_slot.get("pixelY"), "name": center_slot.get("name")},
            },
            "artifacts": {
                "beforeDrag": str(before_path),
                "afterDrag": str(after_path),
                "overlay": str(overlay_path),
            },
            "summary": {
                "beforeVisibleTargetCount": before.get("visibleTargetCount"),
                "afterVisibleTargetCount": after.get("visibleTargetCount"),
                "candidateCount": len(candidates),
                "bestCandidate": candidates[0] if candidates else None,
                "aspectFitRecordingArea": recording_area,
            },
            "candidates": candidates[:80],
            "beforeLayout": {
                "icons": before.get("icons"),
                "slots": before.get("slots"),
            },
        }
        json_path = output_dir / "character_bounds_after_drag.json"
        write_json(json_path, result)

        print("\nCharacter bounds after drag inspection complete.")
        print(f"Output directory: {output_dir.resolve()}")
        print(f"JSON: {json_path.resolve()}")
        print(f"Overlay: {overlay_path.resolve()}")
        if candidates:
            best = candidates[0]
            print(
                f"Best candidate: {best.get('name')} "
                f"bounds=({best.get('boundsX')},{best.get('boundsY')}) "
                f"{best.get('boundsWidth')}x{best.get('boundsHeight')} "
                f"changed={best.get('changedAfterDrag')}"
            )
        else:
            print("No likely character bounds found near center slot.")
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

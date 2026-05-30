"""
Inspect only visible runtime targets named/containing "Bars".

Usage:
    python inspect_bars_bounds.py

or:
    python inspect_bars_bounds.py "https://www.cocrea.world/@user/project"

Outputs:
    bars_bounds_inspections/<slug>-<timestamp>/
      - bars_bounds.json
      - screenshot.png
      - bars_overlay.png
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import pyautogui
import pygetwindow as gw
import websocket
from PIL import Image, ImageDraw, ImageFont

from inspect_game_entry import complete_game_entry_sequence
from sprunki_game_player import SprunkiGamePlayer


BARS_JS = r"""
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
      return out;
    }

    const vm = findVM();
    if (!vm) {
      resolve({ ok: false, error: 'Cannot find Scratch VM' });
      return;
    }

    const runtime = vm.runtime;
    const renderer = runtime.renderer || vm.renderer || null;
    const targets = runtime.targets || [];
    const bars = targets
      .filter(t => !t.isStage && t.visible && /bars/i.test(t.sprite?.name || t.getName?.() || ''))
      .map((target, barsIndex) => {
        const sprite = target.sprite || {};
        const name = sprite.name || target.getName?.() || '';
        const pixel = toPixel(target.x, target.y);
        const costumes = (sprite.costumes || []).map(serializeCostume);
        const currentCostume = costumes[target.currentCostume] || null;
        const candidates = boundsCandidates(target, renderer).filter(c => c.error || c.pixelBounds);
        const primary = candidates.find(c => c.pixelBounds)?.pixelBounds || null;
        return {
          barsIndex,
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
          boundsX: primary?.x ?? null,
          boundsY: primary?.y ?? null,
          boundsWidth: primary?.width ?? null,
          boundsHeight: primary?.height ?? null,
          boundsSources: candidates.filter(c => c.pixelBounds).map(c => c.source),
          boundsCandidates: candidates,
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
      targetCount: targets.length,
      visibleBarsCount: bars.length,
      bars,
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


def draw_overlay(screenshot_path: Path, data: Dict[str, Any], output_path: Path) -> None:
    image = Image.open(screenshot_path).convert("RGB")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("arial.ttf", 16)
    except Exception:
        font = ImageFont.load_default()

    def draw_horizontal_indicator(y_value: float, color: str, label: str, width: int = 3) -> None:
        if 0 <= y_value <= image.height:
            draw.line([0, y_value, image.width, y_value], fill=color, width=width)
            text_y = max(0, min(image.height - 24, y_value - 22))
            draw.text((90, text_y), label, fill=color, font=font)
            return

        edge_y = 0 if y_value < 0 else image.height - 1
        triangle = (
            [(70, 6), (86, 6), (78, 22)]
            if y_value < 0
            else [(70, image.height - 7), (86, image.height - 7), (78, image.height - 23)]
        )
        draw.line([0, edge_y, image.width, edge_y], fill=color, width=width)
        draw.polygon(triangle, fill=color)
        text_y = 26 if y_value < 0 else image.height - 44
        draw.rectangle([88, text_y - 2, min(image.width - 10, 760), text_y + 22], fill="black")
        draw.text((94, text_y), f"{label} (offscreen)", fill=color, font=font)

    colors = ["#00ff66", "#ff3333", "#3388ff", "#ffcc00"]
    for idx, bar in enumerate(data.get("bars", [])):
        color = colors[idx % len(colors)]
        top = bar.get("boundsTopY")
        bottom = bar.get("boundsBottomY")
        if top is not None:
            draw_horizontal_indicator(float(top), color, f"Bars #{idx} topY={top:.2f}", width=3)
        if bottom is not None:
            draw_horizontal_indicator(float(bottom), color, f"Bars #{idx} bottomY={bottom:.2f}", width=2)

        x = bar.get("boundsX")
        y = bar.get("boundsY")
        w = bar.get("boundsWidth")
        h = bar.get("boundsHeight")
        if x is not None and y is not None and w is not None and h is not None:
            draw.rectangle([x, y, x + w, y + h], outline=color, width=2)

    image.save(output_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect Bars target bounds for a Sprunki mod URL.")
    parser.add_argument("url", nargs="?", help="Cocrea/Sprunki mod URL")
    parser.add_argument("--output-dir", default="bars_bounds_inspections", help="Base output directory")
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
        raw = input("Chờ layout ổn định bao nhiêu giây trước khi inspect Bars? [2.0]: ").strip()
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


def print_summary(data: Dict[str, Any]) -> None:
    print(f"\nVisible Bars count: {data.get('visibleBarsCount')}")
    for bar in data.get("bars", []):
        print(
            f"  - #{bar.get('barsIndex')} {bar.get('name')}: "
            f"topY={bar.get('boundsTopY')}, bottomY={bar.get('boundsBottomY')}, "
            f"pixel=({bar.get('roundedPixelX')},{bar.get('roundedPixelY')}), "
            f"costume={bar.get('currentCostume', {}).get('name') if bar.get('currentCostume') else None}, "
            f"size={bar.get('sizePercent')}"
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
        data = eval_js(ws, BARS_JS, timeout=30)
        if not data.get("ok"):
            raise RuntimeError(f"Could not inspect Bars target data: {data}")

        for bar in data.get("bars", []):
            top_y = bar.get("boundsTopY")
            bottom_y = bar.get("boundsBottomY")
            bar["bottomLineCandidate"] = {
                "source": "boundsTopY",
                "value": top_y,
                "usableAsBottomLine": isinstance(top_y, (int, float)) and 600 <= float(top_y) <= 750,
                "reason": (
                    "boundsTopY is inside expected 600..750 bottom-line band"
                    if isinstance(top_y, (int, float)) and 600 <= float(top_y) <= 750
                    else "boundsTopY is outside expected 600..750 bottom-line band"
                ),
                "boundsBottomY": bottom_y,
            }

        screenshot_path = output_dir / "screenshot.png"
        overlay_path = output_dir / "bars_overlay.png"
        json_path = output_dir / "bars_bounds.json"
        pyautogui.screenshot().save(screenshot_path)

        data["artifacts"] = {
            "screenshot": str(screenshot_path),
            "overlay": str(overlay_path),
        }
        write_json(json_path, data)
        draw_overlay(screenshot_path, data, overlay_path)

        print_summary(data)
        print("\nBars bounds inspection complete.")
        print(f"Output directory: {output_dir.resolve()}")
        print(f"JSON: {json_path.resolve()}")
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

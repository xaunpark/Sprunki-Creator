"""
Batch-test Bars.boundsTopY as the recordingArea bottom line.

Input:
    A .txt file containing one mod URL per line. Empty lines and lines starting
    with # are ignored.

Usage:
    python batch_test_bars_top_line.py

or:
    python batch_test_bars_top_line.py urls.txt --expected-min-y 600 --expected-max-y 750

Outputs:
    bars_top_line_tests/<timestamp>/
      - summary.json
      - summary.csv
      - <index>-<slug>/screenshot.png
      - <index>-<slug>/overlay_bars_top.png
      - <index>-<slug>/metadata.json
"""

from __future__ import annotations

import argparse
import csv
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
      return out.filter(c => c.error || c.pixelBounds);
    }

    const vm = findVM();
    if (!vm) {
      resolve({ ok: false, error: 'Cannot find Scratch VM' });
      return;
    }

    const runtime = vm.runtime;
    const renderer = runtime.renderer || vm.renderer || null;
    const targets = runtime.targets || [];
    const visibleTargets = targets.filter(t => !t.isStage && t.visible);
    const bars = visibleTargets
      .filter(t => /bars/i.test(t.sprite?.name || t.getName?.() || ''))
      .map((target, barsIndex) => {
        const sprite = target.sprite || {};
        const name = sprite.name || target.getName?.() || '';
        const pixel = toPixel(target.x, target.y);
        const costumes = (sprite.costumes || []).map(serializeCostume);
        const currentCostume = costumes[target.currentCostume] || null;
        const candidates = boundsCandidates(target, renderer);
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
        };
      });

    resolve({
      ok: true,
      title: document.title,
      currentUrl: window.location.href,
      capturedAt: new Date().toISOString(),
      screen: { width: screenW, height: screenH },
      canvas: { stageW, stageH, menuWidth, scale, canvasW, canvasH, leftOffset, topOffset, canvasCenterX, canvasCenterY },
      targetCount: targets.length,
      visibleTargetCount: visibleTargets.length,
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


def read_urls(path: Path) -> List[str]:
    urls = []
    with open(path, "r", encoding="utf-8-sig") as f:
        for line in f:
            value = line.strip()
            if not value or value.startswith("#"):
                continue
            urls.append(value)
    return urls


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


def select_best_bars_candidate(
    bars: List[Dict[str, Any]],
    expected_min_y: float,
    expected_max_y: float,
) -> Optional[Dict[str, Any]]:
    midpoint = (expected_min_y + expected_max_y) / 2
    candidates = [
        bar
        for bar in bars
        if isinstance(bar.get("boundsTopY"), (int, float))
        and expected_min_y <= float(bar["boundsTopY"]) <= expected_max_y
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda bar: abs(float(bar["boundsTopY"]) - midpoint))


def draw_overlay(
    screenshot_path: Path,
    output_path: Path,
    bars: List[Dict[str, Any]],
    selected: Optional[Dict[str, Any]],
    expected_min_y: float,
    expected_max_y: float,
) -> None:
    image = Image.open(screenshot_path).convert("RGB")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("arial.ttf", 20)
        small_font = ImageFont.truetype("arial.ttf", 15)
    except Exception:
        font = ImageFont.load_default()
        small_font = ImageFont.load_default()

    draw.line([0, expected_min_y, image.width, expected_min_y], fill="#555555", width=1)
    draw.line([0, expected_max_y, image.width, expected_max_y], fill="#555555", width=1)
    draw.text((90, max(0, expected_min_y - 24)), f"expected band {expected_min_y:g}..{expected_max_y:g}", fill="#bbbbbb", font=small_font)

    def draw_y(y: float, color: str, label: str, width: int = 2) -> None:
        if 0 <= y <= image.height:
            draw.line([0, y, image.width, y], fill=color, width=width)
            draw.rectangle([88, max(0, y - 30), min(image.width - 10, 680), max(24, y - 3)], fill="black")
            draw.text((94, max(0, y - 28)), label, fill=color, font=font if width >= 3 else small_font)
        else:
            edge_y = 0 if y < 0 else image.height - 1
            draw.line([0, edge_y, image.width, edge_y], fill=color, width=width)
            draw.text((94, 24 if y < 0 else image.height - 48), f"{label} offscreen", fill=color, font=small_font)

    for idx, bar in enumerate(bars):
        top = bar.get("boundsTopY")
        if not isinstance(top, (int, float)):
            continue
        color = "#00ff66" if selected and bar.get("barsIndex") == selected.get("barsIndex") else "#ff3333"
        width = 4 if selected and bar.get("barsIndex") == selected.get("barsIndex") else 2
        draw_y(float(top), color, f"Bars #{bar.get('barsIndex')} topY={float(top):.2f}", width=width)

    if not selected:
        draw.rectangle([80, 80, 720, 116], fill="black")
        draw.text((90, 86), "No Bars.boundsTopY inside expected band", fill="#ff3333", font=font)

    image.save(output_path)


def write_json(path: Path, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def write_summary_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    fields = [
        "index",
        "status",
        "url",
        "title",
        "outputDir",
        "screenshot",
        "overlay",
        "error",
        "visibleBarsCount",
        "selectedBarsIndex",
        "selectedBarsTopY",
        "usableBarsTopY",
        "allBarsTopY",
    ]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fields})


def process_url(
    index: int,
    total: int,
    url: str,
    run_dir: Path,
    expected_min_y: float,
    expected_max_y: float,
    settle_seconds: float,
) -> Dict[str, Any]:
    slug = slug_from_url(url)
    item_dir = run_dir / f"{index:03d}-{slug}"
    item_dir.mkdir(parents=True, exist_ok=True)

    player = SprunkiGamePlayer()
    ws = None
    metadata: Dict[str, Any] = {
        "index": index,
        "total": total,
        "url": url,
        "status": "failed",
        "outputDir": str(item_dir),
        "expectedMinY": expected_min_y,
        "expectedMaxY": expected_max_y,
        "startedAt": datetime.now().isoformat(),
    }

    try:
        print(f"\n[{index}/{total}] Opening: {url}")
        if not player.navigate_and_prepare_game(url):
            raise RuntimeError("Game setup failed")

        complete_game_entry_sequence(player)

        print(f"[{index}/{total}] Waiting {settle_seconds:.1f}s before Bars inspection...")
        time.sleep(settle_seconds)
        activate_browser_window()

        ws_url = player._get_websocket_url()
        if not ws_url:
            raise RuntimeError("Could not get Chrome DevTools websocket URL")

        ws = websocket.create_connection(ws_url, timeout=30)
        bars_data = eval_js(ws, BARS_JS, timeout=30)
        if not bars_data.get("ok"):
            raise RuntimeError(f"Bars inspect failed: {bars_data}")

        bars = bars_data.get("bars", [])
        selected = select_best_bars_candidate(bars, expected_min_y, expected_max_y)

        screenshot_path = item_dir / "screenshot.png"
        overlay_path = item_dir / "overlay_bars_top.png"
        pyautogui.screenshot().save(screenshot_path)
        draw_overlay(screenshot_path, overlay_path, bars, selected, expected_min_y, expected_max_y)

        all_top_y = [
            round(float(bar["boundsTopY"]), 3)
            for bar in bars
            if isinstance(bar.get("boundsTopY"), (int, float))
        ]

        metadata.update(
            bars_data,
            status="success",
            screenshot=str(screenshot_path),
            overlay=str(overlay_path),
            selectedBars=selected,
            selectedBarsIndex=selected.get("barsIndex") if selected else None,
            selectedBarsTopY=selected.get("boundsTopY") if selected else None,
            usableBarsTopY=selected is not None,
            allBarsTopY=all_top_y,
            finishedAt=datetime.now().isoformat(),
        )

        if selected:
            print(f"[{index}/{total}] Selected Bars #{selected.get('barsIndex')} topY={selected.get('boundsTopY'):.2f}")
        else:
            print(f"[{index}/{total}] No usable Bars.boundsTopY in {expected_min_y:g}..{expected_max_y:g}; all={all_top_y}")

    except Exception as exc:
        metadata.update({"error": str(exc), "finishedAt": datetime.now().isoformat()})
        print(f"[{index}/{total}] ERROR: {exc}")
    finally:
        if ws:
            try:
                ws.close()
            except Exception:
                pass
        try:
            player.cleanup_chrome_complete()
        except Exception:
            pass

    write_json(item_dir / "metadata.json", metadata)
    return metadata


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch screenshot mods with Bars.boundsTopY overlay.")
    parser.add_argument("url_file", nargs="?", help="Text file with one mod URL per line")
    parser.add_argument("--expected-min-y", type=float, default=None, help="Minimum usable Bars top Y")
    parser.add_argument("--expected-max-y", type=float, default=None, help="Maximum usable Bars top Y")
    parser.add_argument("--output-dir", default="bars_top_line_tests", help="Base output directory")
    parser.add_argument("--settle-seconds", type=float, default=None, help="Wait after game entry before screenshot")
    return parser.parse_args()


def ask_interactive_options(args: argparse.Namespace) -> argparse.Namespace:
    if not args.url_file:
        while True:
            args.url_file = input("Nhập đường dẫn file .txt chứa danh sách URL: ").strip().strip('"')
            if args.url_file:
                break
            print("Đường dẫn file không được để trống.")

    if args.expected_min_y is None:
        raw = input("Min Y hợp lệ cho Bars top [600]: ").strip()
        args.expected_min_y = float(raw) if raw else 600.0

    if args.expected_max_y is None:
        raw = input("Max Y hợp lệ cho Bars top [750]: ").strip()
        args.expected_max_y = float(raw) if raw else 750.0

    if args.settle_seconds is None:
        raw = input("Chờ layout ổn định bao nhiêu giây trước khi chụp mỗi mod? [2.0]: ").strip()
        if raw:
            try:
                args.settle_seconds = float(raw)
            except ValueError:
                print("Giá trị không hợp lệ, dùng mặc định 2.0 giây.")
                args.settle_seconds = 2.0
        else:
            args.settle_seconds = 2.0

    raw_output = input(f"Thư mục output base [{args.output_dir}]: ").strip()
    if raw_output:
        args.output_dir = raw_output

    return args


def main() -> int:
    args = ask_interactive_options(parse_args())
    url_file = Path(args.url_file)
    if not url_file.exists():
        print(f"URL file not found: {url_file}")
        return 2

    urls = read_urls(url_file)
    if not urls:
        print(f"No URLs found in: {url_file}")
        return 2

    if args.expected_min_y > args.expected_max_y:
        print("expected-min-y must be <= expected-max-y")
        return 2

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = Path(args.output_dir) / f"bars-top-{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"Loaded {len(urls)} URL(s).")
    print(f"Usable Bars top band: {args.expected_min_y:g}..{args.expected_max_y:g}")
    print(f"Output directory: {run_dir.resolve()}")

    rows: List[Dict[str, Any]] = []
    for idx, url in enumerate(urls, start=1):
        if not url.startswith(("http://", "https://")):
            row = {
                "index": idx,
                "url": url,
                "status": "failed",
                "error": "URL must start with http:// or https://",
            }
            rows.append(row)
            continue

        row = process_url(
            idx,
            len(urls),
            url,
            run_dir,
            float(args.expected_min_y),
            float(args.expected_max_y),
            float(args.settle_seconds),
        )
        rows.append(row)
        write_json(run_dir / "summary.json", rows)
        write_summary_csv(run_dir / "summary.csv", rows)

    write_json(run_dir / "summary.json", rows)
    write_summary_csv(run_dir / "summary.csv", rows)

    success_count = len([row for row in rows if row.get("status") == "success"])
    usable_count = len([row for row in rows if row.get("usableBarsTopY")])
    print("\nBatch Bars top-line test complete.")
    print(f"Success: {success_count}/{len(rows)}")
    print(f"Usable Bars topY: {usable_count}/{len(rows)}")
    print(f"Summary JSON: {(run_dir / 'summary.json').resolve()}")
    print(f"Summary CSV: {(run_dir / 'summary.csv').resolve()}")
    return 0 if success_count else 1


if __name__ == "__main__":
    sys.exit(main())

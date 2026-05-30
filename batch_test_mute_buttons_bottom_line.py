"""
Batch-test Mute Buttons.boundsBottomY against fixed Y=670.

Input:
    A .txt file containing one mod URL per line. Empty lines and lines starting
    with # are ignored.

Usage:
    python batch_test_mute_buttons_bottom_line.py

or:
    python batch_test_mute_buttons_bottom_line.py urls.txt --fixed-y 670 --tolerance 90

Outputs:
    mute_buttons_bottom_tests/<timestamp>/
      - summary.json
      - summary.csv
      - <index>-<slug>/screenshot.png
      - <index>-<slug>/overlay_mute_buttons_bottom.png
      - <index>-<slug>/metadata.json
"""

from __future__ import annotations

import argparse
import csv
import json
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


MUTE_BUTTONS_JS = r"""
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
    const muteButtons = targets
      .filter(t => !t.isStage && t.visible && /mute buttons/i.test(t.sprite?.name || t.getName?.() || ''))
      .map((target, muteIndex) => {
        const sprite = target.sprite || {};
        const name = sprite.name || target.getName?.() || '';
        const pixel = toPixel(target.x, target.y);
        const costumes = (sprite.costumes || []).map(serializeCostume);
        const currentCostume = costumes[target.currentCostume] || null;
        const candidates = boundsCandidates(target, renderer);
        const primary = candidates.find(c => c.pixelBounds)?.pixelBounds || null;
        return {
          muteIndex,
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
      targetCount: targets.length,
      visibleMuteButtonsCount: muteButtons.length,
      muteButtons,
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


def select_mute_button(buttons: List[Dict[str, Any]], fixed_y: float, tolerance: float) -> Optional[Dict[str, Any]]:
    candidates = [
        button
        for button in buttons
        if isinstance(button.get("boundsBottomY"), (int, float))
        and abs(float(button["boundsBottomY"]) - fixed_y) <= tolerance
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda button: abs(float(button["boundsBottomY"]) - fixed_y))


def draw_overlay(
    screenshot_path: Path,
    output_path: Path,
    fixed_y: float,
    selected: Optional[Dict[str, Any]],
) -> None:
    image = Image.open(screenshot_path).convert("RGB")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("arial.ttf", 24)
        small_font = ImageFont.truetype("arial.ttf", 18)
    except Exception:
        font = ImageFont.load_default()
        small_font = ImageFont.load_default()

    def draw_line(y: float, color: str, label: str, width: int) -> None:
        if 0 <= y <= image.height:
            draw.line([0, y, image.width, y], fill=color, width=width)
            label_y = max(0, min(image.height - 30, y - 34))
            draw.rectangle([88, label_y - 3, 780, label_y + 27], fill="black")
            draw.text((96, label_y), label, fill=color, font=font if width >= 4 else small_font)
        else:
            edge_y = 0 if y < 0 else image.height - 1
            draw.line([0, edge_y, image.width, edge_y], fill=color, width=width)
            draw.text((96, 24 if y < 0 else image.height - 48), f"{label} offscreen", fill=color, font=small_font)

    draw_line(fixed_y, "#00ff66", f"Fixed Y={fixed_y:g}", 4)

    if selected:
        y = float(selected["boundsBottomY"])
        distance = abs(y - fixed_y)
        draw_line(y, "#ff3333", f"Mute Buttons boundsBottomY={y:.2f} (diff {distance:.2f})", 4)

        x = selected.get("boundsX")
        by = selected.get("boundsY")
        w = selected.get("boundsWidth")
        h = selected.get("boundsHeight")
        if x is not None and by is not None and w is not None and h is not None:
            draw.rectangle([x, by, x + w, by + h], outline="#ff3333", width=3)
    else:
        draw.rectangle([88, 86, 820, 122], fill="black")
        draw.text((96, 92), "No Mute Buttons.boundsBottomY near fixed Y", fill="#ff3333", font=small_font)

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
        "fixedY",
        "visibleMuteButtonsCount",
        "selectedMuteIndex",
        "selectedBoundsBottomY",
        "diffFromFixedY",
        "usableMuteButtonsBottomY",
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
    fixed_y: float,
    tolerance: float,
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
        "fixedY": fixed_y,
        "tolerance": tolerance,
        "startedAt": datetime.now().isoformat(),
    }

    try:
        print(f"\n[{index}/{total}] Opening: {url}")
        if not player.navigate_and_prepare_game(url):
            raise RuntimeError("Game setup failed")

        complete_game_entry_sequence(player)

        print(f"[{index}/{total}] Waiting {settle_seconds:.1f}s before Mute Buttons inspection...")
        time.sleep(settle_seconds)
        activate_browser_window()

        ws_url = player._get_websocket_url()
        if not ws_url:
            raise RuntimeError("Could not get Chrome DevTools websocket URL")

        ws = websocket.create_connection(ws_url, timeout=30)
        data = eval_js(ws, MUTE_BUTTONS_JS, timeout=30)
        if not data.get("ok"):
            raise RuntimeError(f"Mute Buttons inspect failed: {data}")

        buttons = data.get("muteButtons", [])
        selected = select_mute_button(buttons, fixed_y, tolerance)

        screenshot_path = item_dir / "screenshot.png"
        overlay_path = item_dir / "overlay_mute_buttons_bottom.png"
        pyautogui.screenshot().save(screenshot_path)
        draw_overlay(screenshot_path, overlay_path, fixed_y, selected)

        selected_y = selected.get("boundsBottomY") if selected else None
        diff = abs(float(selected_y) - fixed_y) if isinstance(selected_y, (int, float)) else None

        metadata.update(
            data,
            status="success",
            screenshot=str(screenshot_path),
            overlay=str(overlay_path),
            selectedMuteButton=selected,
            selectedMuteIndex=selected.get("muteIndex") if selected else None,
            selectedBoundsBottomY=selected_y,
            diffFromFixedY=diff,
            usableMuteButtonsBottomY=selected is not None,
            allMuteButtonsBottomY=[
                round(float(button["boundsBottomY"]), 3)
                for button in buttons
                if isinstance(button.get("boundsBottomY"), (int, float))
            ],
            finishedAt=datetime.now().isoformat(),
        )

        if selected:
            print(
                f"[{index}/{total}] Selected Mute Buttons #{selected.get('muteIndex')} "
                f"bottomY={selected_y:.2f}, diff={diff:.2f}"
            )
        else:
            print(f"[{index}/{total}] No Mute Buttons.boundsBottomY within +/- {tolerance:g} of {fixed_y:g}")

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
    parser = argparse.ArgumentParser(description="Batch draw fixed Y and Mute Buttons.boundsBottomY.")
    parser.add_argument("url_file", nargs="?", help="Text file with one mod URL per line")
    parser.add_argument("--fixed-y", type=float, default=None, help="Fixed Y coordinate to compare against")
    parser.add_argument("--tolerance", type=float, default=None, help="Max distance for selecting Mute Buttons")
    parser.add_argument("--output-dir", default="mute_buttons_bottom_tests", help="Base output directory")
    parser.add_argument("--settle-seconds", type=float, default=None, help="Wait after game entry before screenshot")
    return parser.parse_args()


def ask_interactive_options(args: argparse.Namespace) -> argparse.Namespace:
    if not args.url_file:
        while True:
            args.url_file = input("Nhập đường dẫn file .txt chứa danh sách URL: ").strip().strip('"')
            if args.url_file:
                break
            print("Đường dẫn file không được để trống.")

    if args.fixed_y is None:
        raw = input("Fixed Y cần vẽ [670]: ").strip()
        args.fixed_y = float(raw) if raw else 670.0

    if args.tolerance is None:
        raw = input("Tolerance để chọn Mute Buttons.boundsBottomY [90]: ").strip()
        args.tolerance = float(raw) if raw else 90.0

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

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = Path(args.output_dir) / f"mute-bottom-{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"Loaded {len(urls)} URL(s).")
    print(f"Fixed Y: {args.fixed_y:g}; tolerance: +/- {args.tolerance:g}")
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
            float(args.fixed_y),
            float(args.tolerance),
            float(args.settle_seconds),
        )
        rows.append(row)
        write_json(run_dir / "summary.json", rows)
        write_summary_csv(run_dir / "summary.csv", rows)

    write_json(run_dir / "summary.json", rows)
    write_summary_csv(run_dir / "summary.csv", rows)

    success_count = len([row for row in rows if row.get("status") == "success"])
    usable_count = len([row for row in rows if row.get("usableMuteButtonsBottomY")])
    print("\nBatch Mute Buttons bottom-line test complete.")
    print(f"Success: {success_count}/{len(rows)}")
    print(f"Usable Mute Buttons bottomY: {usable_count}/{len(rows)}")
    print(f"Summary JSON: {(run_dir / 'summary.json').resolve()}")
    print(f"Summary CSV: {(run_dir / 'summary.csv').resolve()}")
    return 0 if success_count else 1


if __name__ == "__main__":
    sys.exit(main())

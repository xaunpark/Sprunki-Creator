"""
Batch-test whether a fixed screen Y coordinate matches the recordingArea bottom.

Input:
    A .txt file containing one mod URL per line. Empty lines and lines starting
    with # are ignored.

Usage:
    python batch_test_fixed_y_line.py

or:
    python batch_test_fixed_y_line.py urls.txt --y 670

Outputs:
    fixed_y_line_tests/<timestamp>/
      - summary.json
      - summary.csv
      - <index>-<slug>/screenshot.png
      - <index>-<slug>/overlay_y_670.png
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


PAGE_INFO_JS = r"""
(() => {
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

  const vm = findVM();
  const targets = vm?.runtime?.targets || [];
  const visibleTargets = targets.filter(t => !t.isStage && t.visible);
  const visibleBars = visibleTargets.filter(t => /bars/i.test(t.sprite?.name || t.getName?.() || ''));
  const visibleSlots = visibleTargets.filter(t => /^Polo\s+\d+/i.test(t.sprite?.name || t.getName?.() || ''));
  const visibleIcons = visibleTargets.filter(t => (t.sprite?.name || '').includes('Icons'));

  return {
    ok: true,
    title: document.title,
    currentUrl: window.location.href,
    hasVM: !!vm,
    targetCount: targets.length,
    visibleTargetCount: visibleTargets.length,
    visibleBarsCount: visibleBars.length,
    visibleSlotCount: visibleSlots.length,
    visibleIconTargetCount: visibleIcons.length,
  };
})();
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
            "params": {"expression": expression, "returnByValue": True},
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


def draw_y_overlay(screenshot_path: Path, output_path: Path, y: float, label: str) -> None:
    image = Image.open(screenshot_path).convert("RGB")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("arial.ttf", 22)
    except Exception:
        font = ImageFont.load_default()

    if 0 <= y <= image.height:
        for offset in range(-2, 3):
            draw.line([0, y + offset, image.width, y + offset], fill="#00ff66", width=1)
        draw.rectangle([80, max(0, y - 34), 420, max(28, y - 4)], fill="black")
        draw.text((90, max(0, y - 32)), label, fill="#00ff66", font=font)
    else:
        edge_y = 0 if y < 0 else image.height - 1
        draw.line([0, edge_y, image.width, edge_y], fill="#ff3333", width=4)
        draw.text((90, 24 if y < 0 else image.height - 48), f"{label} offscreen", fill="#ff3333", font=font)

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
        "hasVM",
        "visibleBarsCount",
        "visibleSlotCount",
        "visibleIconTargetCount",
    ]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fields})


def process_url(index: int, total: int, url: str, run_dir: Path, y: float, settle_seconds: float) -> Dict[str, Any]:
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
        "fixedY": y,
        "startedAt": datetime.now().isoformat(),
    }

    try:
        print(f"\n[{index}/{total}] Opening: {url}")
        if not player.navigate_and_prepare_game(url):
            raise RuntimeError("Game setup failed")

        complete_game_entry_sequence(player)

        print(f"[{index}/{total}] Waiting {settle_seconds:.1f}s before screenshot...")
        time.sleep(settle_seconds)
        activate_browser_window()

        ws_url = player._get_websocket_url()
        if ws_url:
            ws = websocket.create_connection(ws_url, timeout=30)
            metadata.update(eval_js(ws, PAGE_INFO_JS, timeout=30))

        screenshot_path = item_dir / "screenshot.png"
        overlay_path = item_dir / f"overlay_y_{int(y) if y == int(y) else y}.png"
        pyautogui.screenshot().save(screenshot_path)
        draw_y_overlay(screenshot_path, overlay_path, y, f"Y={y:g}")

        metadata.update(
            {
                "status": "success",
                "screenshot": str(screenshot_path),
                "overlay": str(overlay_path),
                "finishedAt": datetime.now().isoformat(),
            }
        )
        print(f"[{index}/{total}] Saved overlay: {overlay_path}")
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
    parser = argparse.ArgumentParser(description="Batch screenshot mods with a fixed horizontal Y line overlay.")
    parser.add_argument("url_file", nargs="?", help="Text file with one mod URL per line")
    parser.add_argument("--y", type=float, default=None, help="Screen Y coordinate to draw")
    parser.add_argument("--output-dir", default="fixed_y_line_tests", help="Base output directory")
    parser.add_argument("--settle-seconds", type=float, default=None, help="Wait after game entry before screenshot")
    return parser.parse_args()


def ask_interactive_options(args: argparse.Namespace) -> argparse.Namespace:
    if not args.url_file:
        while True:
            args.url_file = input("Nhập đường dẫn file .txt chứa danh sách URL: ").strip().strip('"')
            if args.url_file:
                break
            print("Đường dẫn file không được để trống.")

    if args.y is None:
        raw = input("Tọa độ Y cần kẻ [670]: ").strip()
        args.y = float(raw) if raw else 670.0

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
    run_dir = Path(args.output_dir) / f"y-{args.y:g}-{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"Loaded {len(urls)} URL(s).")
    print(f"Fixed Y line: {args.y:g}")
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
        row = process_url(idx, len(urls), url, run_dir, float(args.y), float(args.settle_seconds))
        rows.append(row)
        write_json(run_dir / "summary.json", rows)
        write_summary_csv(run_dir / "summary.csv", rows)

    write_json(run_dir / "summary.json", rows)
    write_summary_csv(run_dir / "summary.csv", rows)

    success_count = len([row for row in rows if row.get("status") == "success"])
    print("\nBatch fixed-Y test complete.")
    print(f"Success: {success_count}/{len(rows)}")
    print(f"Summary JSON: {(run_dir / 'summary.json').resolve()}")
    print(f"Summary CSV: {(run_dir / 'summary.csv').resolve()}")
    return 0 if success_count else 1


if __name__ == "__main__":
    sys.exit(main())

"""
Batch-test any visible runtime target near a desired Y coordinate.

This is broader than Bars-only inspection. For each mod it inspects all visible
targets, extracts pixelY/boundsTopY/boundsBottomY, scores line candidates near
the target Y, and draws the best candidate plus nearby alternatives.

Usage:
    python batch_test_any_target_near_y.py

or:
    python batch_test_any_target_near_y.py urls.txt --target-y 670 --tolerance 90

Outputs:
    any_target_near_y_tests/<timestamp>/
      - summary.json
      - summary.csv
      - <index>-<slug>/screenshot.png
      - <index>-<slug>/overlay_any_target_near_y.png
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


TARGETS_JS = r"""
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
    const visibleTargets = targets
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
      targets: visibleTargets,
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


def target_kind(name: str) -> str:
    lower = (name or "").lower()
    if "icons" in lower:
        return "icons"
    if re.match(r"^polo\s+\d+", name or "", re.I):
        return "slot"
    if "button" in lower or "mute" in lower or "gallery" in lower:
        return "button"
    if "bar" in lower or "panel" in lower or "mask" in lower:
        return "panel"
    if "preview" in lower:
        return "preview"
    return "other"


def line_candidates_for_target(target: Dict[str, Any], target_y: float, tolerance: float) -> List[Dict[str, Any]]:
    candidates = []
    kind = target_kind(target.get("name", ""))
    for source, value in (
        ("boundsTopY", target.get("boundsTopY")),
        ("boundsBottomY", target.get("boundsBottomY")),
        ("pixelY", target.get("pixelY")),
    ):
        if not isinstance(value, (int, float)):
            continue
        distance = abs(float(value) - target_y)
        if distance > tolerance:
            continue

        width = target.get("boundsWidth")
        height = target.get("boundsHeight")
        width_value = float(width) if isinstance(width, (int, float)) else 0.0
        height_value = float(height) if isinstance(height, (int, float)) else 0.0

        score = distance
        if source == "boundsTopY":
            score -= 30
        elif source == "boundsBottomY":
            score -= 10
        else:
            score += 20

        if width_value >= 1500:
            score -= 25
        elif width_value >= 600:
            score -= 12
        elif width_value <= 80:
            score += 35

        if kind == "panel":
            score -= 20
        elif kind == "button":
            score += 25
        elif kind == "icons":
            score += 15
        elif kind == "slot":
            score += 18
        elif kind == "preview":
            score += 30

        if height_value > 2500:
            score += 15

        candidates.append(
            {
                "targetName": target.get("name"),
                "runtimeIndex": target.get("runtimeIndex"),
                "lineSource": source,
                "lineY": float(value),
                "distanceToTargetY": distance,
                "score": score,
                "kind": kind,
                "pixelX": target.get("pixelX"),
                "pixelY": target.get("pixelY"),
                "boundsTopY": target.get("boundsTopY"),
                "boundsBottomY": target.get("boundsBottomY"),
                "boundsX": target.get("boundsX"),
                "boundsY": target.get("boundsY"),
                "boundsWidth": target.get("boundsWidth"),
                "boundsHeight": target.get("boundsHeight"),
                "currentCostumeName": (target.get("currentCostume") or {}).get("name"),
                "sizePercent": target.get("sizePercent"),
            }
        )
    return candidates


def select_candidates(targets: List[Dict[str, Any]], target_y: float, tolerance: float) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    for target in targets:
        candidates.extend(line_candidates_for_target(target, target_y, tolerance))
    candidates.sort(key=lambda row: (row["score"], row["distanceToTargetY"], row["targetName"] or ""))
    return candidates


def draw_overlay(
    screenshot_path: Path,
    output_path: Path,
    target_y: float,
    tolerance: float,
    candidates: List[Dict[str, Any]],
) -> None:
    image = Image.open(screenshot_path).convert("RGB")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("arial.ttf", 20)
        small_font = ImageFont.truetype("arial.ttf", 14)
    except Exception:
        font = ImageFont.load_default()
        small_font = ImageFont.load_default()

    draw.line([0, target_y, image.width, target_y], fill="#00ff66", width=2)
    draw.line([0, target_y - tolerance, image.width, target_y - tolerance], fill="#555555", width=1)
    draw.line([0, target_y + tolerance, image.width, target_y + tolerance], fill="#555555", width=1)
    draw.text((90, max(0, target_y - 28)), f"target Y={target_y:g}", fill="#00ff66", font=font)

    colors = ["#ff3333", "#3388ff", "#ffcc00", "#cc33ff", "#00ddff"]
    legend_rows = []
    for idx, candidate in enumerate(candidates[:25]):
        y = candidate["lineY"]
        color = "#00ff66" if idx == 0 else colors[(idx - 1) % len(colors)]
        width = 4 if idx == 0 else 2
        if 0 <= y <= image.height:
            draw.line([0, y, image.width, y], fill=color, width=width)
            marker_x = 74
            draw.rectangle([marker_x - 10, y - 8, marker_x + 10, y + 8], fill="black", outline=color)
            draw.text((marker_x - 5, y - 8), str(idx), fill=color, font=small_font)

        legend_rows.append(
            {
                "idx": idx,
                "color": color,
                "text": (
                    f"{idx}: {candidate['targetName']} {candidate['lineSource']}="
                    f"{y:.1f} d={candidate['distanceToTargetY']:.1f} "
                    f"s={candidate['score']:.1f} [{candidate['kind']}]"
                ),
            }
        )

        x = candidate.get("boundsX")
        by = candidate.get("boundsY")
        w = candidate.get("boundsWidth")
        h = candidate.get("boundsHeight")
        if x is not None and by is not None and w is not None and h is not None:
            draw.rectangle([x, by, x + w, by + h], outline=color, width=1 if idx else 3)

    if legend_rows:
        row_h = 20
        legend_x = 88
        legend_y = 74
        max_rows = min(len(legend_rows), 25)
        legend_w = min(image.width - legend_x - 16, 980)
        legend_h = max_rows * row_h + 14
        draw.rectangle([legend_x - 8, legend_y - 8, legend_x + legend_w, legend_y + legend_h], fill="black")
        draw.rectangle([legend_x - 8, legend_y - 8, legend_x + legend_w, legend_y + legend_h], outline="#666666")
        for row_i, row in enumerate(legend_rows[:max_rows]):
            y_text = legend_y + row_i * row_h
            draw.text((legend_x, y_text), row["text"], fill=row["color"], font=small_font)

    if not candidates:
        draw.rectangle([80, 80, 760, 116], fill="black")
        draw.text((90, 86), "No visible target line candidate near target Y", fill="#ff3333", font=font)

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
        "targetY",
        "tolerance",
        "candidateCount",
        "bestTargetName",
        "bestLineSource",
        "bestLineY",
        "bestDistanceToTargetY",
        "bestScore",
        "bestKind",
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
    target_y: float,
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
        "targetY": target_y,
        "tolerance": tolerance,
        "startedAt": datetime.now().isoformat(),
    }

    try:
        print(f"\n[{index}/{total}] Opening: {url}")
        if not player.navigate_and_prepare_game(url):
            raise RuntimeError("Game setup failed")

        complete_game_entry_sequence(player)

        print(f"[{index}/{total}] Waiting {settle_seconds:.1f}s before target inspection...")
        time.sleep(settle_seconds)
        activate_browser_window()

        ws_url = player._get_websocket_url()
        if not ws_url:
            raise RuntimeError("Could not get Chrome DevTools websocket URL")

        ws = websocket.create_connection(ws_url, timeout=30)
        data = eval_js(ws, TARGETS_JS, timeout=30)
        if not data.get("ok"):
            raise RuntimeError(f"Target inspect failed: {data}")

        candidates = select_candidates(data.get("targets", []), target_y, tolerance)
        best = candidates[0] if candidates else None

        screenshot_path = item_dir / "screenshot.png"
        overlay_path = item_dir / "overlay_any_target_near_y.png"
        pyautogui.screenshot().save(screenshot_path)
        draw_overlay(screenshot_path, overlay_path, target_y, tolerance, candidates)

        compact_candidates = candidates[:50]
        metadata.update(
            status="success",
            title=data.get("title"),
            currentUrl=data.get("currentUrl"),
            targetCount=data.get("targetCount"),
            visibleTargetCount=data.get("visibleTargetCount"),
            screenshot=str(screenshot_path),
            overlay=str(overlay_path),
            candidateCount=len(candidates),
            bestCandidate=best,
            candidates=compact_candidates,
            bestTargetName=best.get("targetName") if best else None,
            bestLineSource=best.get("lineSource") if best else None,
            bestLineY=best.get("lineY") if best else None,
            bestDistanceToTargetY=best.get("distanceToTargetY") if best else None,
            bestScore=best.get("score") if best else None,
            bestKind=best.get("kind") if best else None,
            finishedAt=datetime.now().isoformat(),
        )

        if best:
            print(
                f"[{index}/{total}] Best: {best['targetName']} "
                f"{best['lineSource']}={best['lineY']:.2f} "
                f"distance={best['distanceToTargetY']:.2f} score={best['score']:.2f}"
            )
        else:
            print(f"[{index}/{total}] No candidate near Y={target_y:g} +/- {tolerance:g}")

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
    parser = argparse.ArgumentParser(description="Batch inspect any visible runtime target near a fixed Y.")
    parser.add_argument("url_file", nargs="?", help="Text file with one mod URL per line")
    parser.add_argument("--target-y", type=float, default=None, help="Desired Y coordinate")
    parser.add_argument("--tolerance", type=float, default=None, help="Candidate tolerance in pixels")
    parser.add_argument("--output-dir", default="any_target_near_y_tests", help="Base output directory")
    parser.add_argument("--settle-seconds", type=float, default=None, help="Wait after game entry before screenshot")
    return parser.parse_args()


def ask_interactive_options(args: argparse.Namespace) -> argparse.Namespace:
    if not args.url_file:
        while True:
            args.url_file = input("Nhập đường dẫn file .txt chứa danh sách URL: ").strip().strip('"')
            if args.url_file:
                break
            print("Đường dẫn file không được để trống.")

    if args.target_y is None:
        raw = input("Tọa độ Y cần tìm quanh [670]: ").strip()
        args.target_y = float(raw) if raw else 670.0

    if args.tolerance is None:
        raw = input("Tolerance pixel [90]: ").strip()
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
    run_dir = Path(args.output_dir) / f"any-target-y-{args.target_y:g}-{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"Loaded {len(urls)} URL(s).")
    print(f"Target Y: {args.target_y:g} +/- {args.tolerance:g}")
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
            float(args.target_y),
            float(args.tolerance),
            float(args.settle_seconds),
        )
        rows.append(row)
        write_json(run_dir / "summary.json", rows)
        write_summary_csv(run_dir / "summary.csv", rows)

    write_json(run_dir / "summary.json", rows)
    write_summary_csv(run_dir / "summary.csv", rows)

    success_count = len([row for row in rows if row.get("status") == "success"])
    candidate_count = len([row for row in rows if row.get("candidateCount", 0) > 0])
    print("\nBatch any-target near-Y test complete.")
    print(f"Success: {success_count}/{len(rows)}")
    print(f"Mods with candidates: {candidate_count}/{len(rows)}")
    print(f"Summary JSON: {(run_dir / 'summary.json').resolve()}")
    print(f"Summary CSV: {(run_dir / 'summary.csv').resolve()}")
    return 0 if success_count else 1


if __name__ == "__main__":
    sys.exit(main())

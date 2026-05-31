"""
Test light runtime inspection for Mute Buttons boundsBottomY.

This script intentionally does NOT run the full game-entry flow:
  - no Run click sequence
  - no fullscreen button
  - no F11
  - no icon drag

It opens the URL in Chrome debug mode, waits for the Scratch VM/runtime targets,
then tries to read Mute Buttons boundsBottomY directly.

Usage:
    python test_light_runtime_inspect_mute_buttons.py "https://www.cocrea.world/@user/mod"
    python test_light_runtime_inspect_mute_buttons.py --url-file urls.txt
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import websocket

from sprunki_game_player import SprunkiGamePlayer
from test_recording_area_scaled_comparison_mockups import eval_js, slug_from_url


LIGHT_INSPECT_JS = r"""
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
          while (fiber && depth++ < 160) {
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
      const x = Math.min(pTopLeft.x, pBottomRight.x);
      const y = Math.min(pTopLeft.y, pBottomRight.y);
      const width = Math.abs(pBottomRight.x - pTopLeft.x);
      const height = Math.abs(pBottomRight.y - pTopLeft.y);
      return {
        x, y, width, height,
        topPixelY: y,
        bottomPixelY: y + height,
        centerPixelX: x + width / 2,
        centerPixelY: y + height / 2,
        raw: { left, right, top, bottom }
      };
    }

    function boundsCandidates(target, renderer) {
      const drawableID = target.drawableID ?? target._drawableID;
      const out = [];
      try {
        if (typeof target.getBounds === 'function') {
          const raw = target.getBounds();
          out.push({ source: 'target.getBounds', pixelBounds: boundsToPixels(raw) });
        }
      } catch (e) {}
      if (renderer && drawableID !== undefined && drawableID !== null) {
        for (const name of ['getBounds', 'getBoundsForDrawable']) {
          try {
            if (typeof renderer[name] === 'function') {
              const raw = renderer[name](drawableID);
              out.push({ source: `renderer.${name}`, pixelBounds: boundsToPixels(raw) });
            }
          } catch (e) {}
        }
      }
      return out.filter(c => c.pixelBounds);
    }

    const vm = findVM();
    if (!vm) {
      resolve({
        ok: false,
        ready: false,
        reason: 'vm_not_found',
        title: document.title,
        url: location.href,
        canvasCount: document.querySelectorAll('canvas').length
      });
      return;
    }

    const runtime = vm.runtime;
    const renderer = runtime.renderer || vm.renderer || null;
    const allTargets = runtime.targets || [];
    const targets = allTargets
      .filter(t => !t.isStage)
      .map((target, runtimeIndex) => {
        const sprite = target.sprite || {};
        const name = sprite.name || target.getName?.() || '';
        const pixel = toPixel(target.x, target.y);
        const candidates = boundsCandidates(target, renderer);
        const primary = candidates.find(c => c.pixelBounds)?.pixelBounds || null;
        return {
          runtimeIndex,
          id: target.id,
          name,
          visible: target.visible,
          logicX: target.x,
          logicY: target.y,
          sizePercent: target.size,
          currentCostumeIndex: target.currentCostume,
          pixelX: pixel.x,
          pixelY: pixel.y,
          boundsTopY: primary?.topPixelY ?? null,
          boundsBottomY: primary?.bottomPixelY ?? null,
          boundsX: primary?.x ?? null,
          boundsY: primary?.y ?? null,
          boundsWidth: primary?.width ?? null,
          boundsHeight: primary?.height ?? null,
          boundsSources: candidates.map(c => c.source)
        };
      });

    const muteButtons = targets.filter(t => /mute buttons/i.test(t.name));
    const values = muteButtons
      .map(t => t.boundsBottomY)
      .filter(v => Number.isFinite(v) && v >= 600 && v <= 740);

    resolve({
      ok: true,
      ready: true,
      title: document.title,
      url: location.href,
      targetCount: allTargets.length,
      visibleTargetCount: targets.filter(t => t.visible).length,
      muteButtons,
      usableBoundsBottomY: values,
      usable: values.length > 0,
      canvas: { screenW, screenH, stageW, stageH, menuWidth, scale, canvasW, canvasH, leftOffset, topOffset }
    });
  } catch (error) {
    resolve({ ok: false, ready: false, reason: 'exception', error: String(error), stack: error?.stack });
  }
});
"""


def write_json(path: Path, data: Dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def read_urls(path: Path) -> List[str]:
    urls: List[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        value = line.strip().strip('"')
        if value and not value.startswith("#"):
            urls.append(value)
    return urls


def median(values: List[float]) -> Optional[float]:
    if not values:
        return None
    vals = sorted(float(v) for v in values)
    mid = len(vals) // 2
    if len(vals) % 2:
        return vals[mid]
    return (vals[mid - 1] + vals[mid]) / 2


def cluster_values(values: List[float], threshold: float = 4.0) -> List[List[float]]:
    vals = sorted(float(v) for v in values)
    if not vals:
        return []
    clusters = [[vals[0]]]
    for value in vals[1:]:
        if abs(value - clusters[-1][-1]) <= threshold:
            clusters[-1].append(value)
        else:
            clusters.append([value])
    return clusters


def choose_bottom(values: List[float], fallback: float = 670.0) -> Dict[str, Any]:
    clusters = cluster_values(values)
    if not clusters:
        return {"value": fallback, "method": "fallback_fixed_670", "clusters": [], "fallbackUsed": True}
    best = max(clusters, key=lambda c: (len(c), -abs((median(c) or fallback) - fallback)))
    return {
        "value": float(median(best)),
        "method": "mute_buttons_boundsBottomY_cluster_median",
        "clusters": [{"median": median(c), "count": len(c), "values": c} for c in clusters],
        "fallbackUsed": False,
    }


def launch_url_light(player: SprunkiGamePlayer, url: str) -> bool:
    player.cleanup_chrome_complete()
    time.sleep(2)

    chrome_exe = next((path for path in player.chrome_paths if os.path.exists(path)), None)
    if not chrome_exe:
        raise RuntimeError("Google Chrome not found")

    chrome_cmd = [
        chrome_exe,
        f"--remote-debugging-port={player.debugging_port}",
        f"--user-data-dir={os.path.join(os.getcwd(), player.user_data_dir)}",
        "--no-first-run",
        "--incognito",
        "--window-size=1280,720",
        "--remote-allow-origins=*",
        "--use-fake-ui-for-media-stream",
        "--test-type",
        "--clear-token-service",
        "--clear-key-system-properties",
        "--no-default-browser-check",
        "--disable-client-side-phishing-detection",
        "--disable-popup-blocking",
        "--disable-prompt-on-repost",
        "--disable-hang-monitor",
        "--disable-session-crashed-bubble",
        "--disable-infobars",
        "--no-recovery-component",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--disable-background-timer-throttling",
        "--enable-extensions",
        "--load-plugins",
        url,
    ]
    player.chrome_process = subprocess.Popen(chrome_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(3)

    try:
        if player.inject_cookies_via_cdp():
            ws_url = player._get_websocket_url()
            if ws_url:
                ws = websocket.create_connection(ws_url, timeout=10)
                player._send_cdp_command(ws, {"method": "Page.reload"})
                ws.close()
    except Exception as exc:
        print(f"Cookie injection/reload warning: {exc}")

    time.sleep(max(1, getattr(player, "page_load_wait", 8)))
    return True


def inspect_url(url: str, timeout: float, interval: float, keep_browser: bool) -> Dict[str, Any]:
    player = SprunkiGamePlayer()
    ws: Optional[websocket.WebSocket] = None
    started = time.monotonic()
    samples: List[Dict[str, Any]] = []
    try:
        print(f"Opening URL only: {url}")
        if not launch_url_light(player, url):
            raise RuntimeError("Chrome debug launch failed")

        ws_url = None
        while time.monotonic() - started < timeout:
            ws_url = player._get_websocket_url()
            if ws_url:
                break
            time.sleep(0.5)
        if not ws_url:
            raise RuntimeError("Could not get Chrome DevTools websocket URL")

        ws = websocket.create_connection(ws_url, timeout=30)
        while time.monotonic() - started < timeout:
            elapsed = time.monotonic() - started
            data = eval_js(ws, LIGHT_INSPECT_JS, timeout=30)
            data["elapsedSeconds"] = elapsed
            samples.append(data)
            values = data.get("usableBoundsBottomY") or []
            if data.get("ok") and values:
                bottom = choose_bottom([float(v) for v in values])
                return {
                    "url": url,
                    "success": True,
                    "elapsedSeconds": elapsed,
                    "bottomDetection": bottom,
                    "latestInspect": data,
                    "samples": samples,
                }
            time.sleep(interval)

        latest = samples[-1] if samples else None
        return {
            "url": url,
            "success": False,
            "elapsedSeconds": time.monotonic() - started,
            "reason": "timeout_no_usable_mute_buttons",
            "latestInspect": latest,
            "samples": samples,
        }
    finally:
        if ws:
            try:
                ws.close()
            except Exception:
                pass
        if not keep_browser:
            try:
                player.cleanup_chrome_complete()
            except Exception:
                pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Test light runtime Mute Buttons inspect without game-entry flow.")
    parser.add_argument("url", nargs="?", help="Single Cocrea/Sprunki URL")
    parser.add_argument("--url-file", help="Text file with one URL per line")
    parser.add_argument("--timeout", type=float, default=45.0, help="Seconds to wait for usable Mute Buttons bounds")
    parser.add_argument("--interval", type=float, default=1.0, help="Seconds between inspect attempts")
    parser.add_argument("--output-dir", default="light_runtime_inspect_tests")
    parser.add_argument("--keep-browser", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    urls: List[str] = []
    if args.url:
        urls.append(args.url.strip())
    if args.url_file:
        urls.extend(read_urls(Path(args.url_file)))
    if not urls:
        raw = input("Nhap URL ban mod Cocrea/Sprunki: ").strip()
        if raw:
            urls.append(raw)
    if not urls:
        raise RuntimeError("No URLs provided")

    output_root = Path(args.output_dir) / datetime.now().strftime("%Y%m%d-%H%M%S")
    output_root.mkdir(parents=True, exist_ok=True)
    summary = {
        "generatedAt": datetime.now().isoformat(),
        "timeout": args.timeout,
        "interval": args.interval,
        "results": [],
    }

    for index, url in enumerate(urls, 1):
        print("\n" + "=" * 80)
        print(f"[{index}/{len(urls)}] {url}")
        try:
            result = inspect_url(url, args.timeout, args.interval, args.keep_browser)
            summary["results"].append(result)
            per_url_path = output_root / f"{index:03d}-{slug_from_url(url)}.json"
            write_json(per_url_path, result)
            if result.get("success"):
                bottom = result.get("bottomDetection", {})
                print(f"SUCCESS: bottomY={bottom.get('value')} method={bottom.get('method')} elapsed={result.get('elapsedSeconds'):.2f}s")
            else:
                print(f"FAILED: {result.get('reason')} elapsed={result.get('elapsedSeconds'):.2f}s")
            print(f"Result: {per_url_path.resolve()}")
        except Exception as exc:
            result = {"url": url, "success": False, "error": str(exc)}
            summary["results"].append(result)
            print(f"ERROR: {exc}")

    summary_path = output_root / "summary.json"
    write_json(summary_path, summary)
    success_count = sum(1 for row in summary["results"] if row.get("success"))
    print("\nDone.")
    print(f"Success: {success_count}/{len(summary['results'])}")
    print(f"Output: {output_root.resolve()}")
    print(f"Summary: {summary_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

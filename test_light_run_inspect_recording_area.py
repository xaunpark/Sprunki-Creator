"""
Test lightweight backfill inspect with Run click but without fullscreen/F11.

Flow:
  - open URL in Chrome debug mode
  - inject cookies/reload if available
  - wait for page
  - click Run via existing CDP helper
  - do NOT click fullscreen
  - do NOT press F11
  - inspect Mute Buttons boundsBottomY
  - calculate fixed recordingArea in video-output coordinates

Usage:
    python test_light_run_inspect_recording_area.py "https://www.cocrea.world/@user/mod"
    python test_light_run_inspect_recording_area.py --url-file urls.txt
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
from test_light_runtime_inspect_mute_buttons import LIGHT_INSPECT_JS, choose_bottom
from test_recording_area_scaled_comparison_mockups import eval_js, slug_from_url

RUN_CLICK_JS = r"""
new Promise((resolve) => {
  try {
    function visible(el) {
      const rect = el.getBoundingClientRect();
      const style = window.getComputedStyle(el);
      return rect.width > 8 && rect.height > 8 && style.visibility !== 'hidden' && style.display !== 'none' && style.opacity !== '0';
    }
    const candidates = [];
    const selectors = [
      '.style_run-button__VjFSY',
      'button[class*="run-button"]',
      'button[class*="Run"]',
      'button.MuiButton-contained',
      'button.MuiButton-root',
      'button',
      '[role="button"]'
    ];
    for (const selector of selectors) {
      for (const el of document.querySelectorAll(selector)) {
        const text = (el.textContent || el.getAttribute('aria-label') || el.title || '').trim().toLowerCase();
        const aria = (el.getAttribute('aria-label') || '').trim().toLowerCase();
        const cls = String(el.className || '').toLowerCase();
        const looksRun = text === 'run' || text.includes('run') || aria.includes('run') || cls.includes('run-button');
        if (looksRun && visible(el)) {
          candidates.push({ el, selector, text, aria, cls });
        }
      }
    }
    if (!candidates.length) {
      resolve({
        ok: false,
        reason: 'button_not_found',
        buttonTexts: Array.from(document.querySelectorAll('button,[role="button"]')).slice(0, 40).map(el => ({
          text: (el.textContent || '').trim(),
          aria: el.getAttribute('aria-label'),
          className: String(el.className || '').slice(0, 120),
          rect: (() => { const r = el.getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height }; })()
        })),
        title: document.title,
        url: location.href
      });
      return;
    }
    const target = candidates[0].el;
    target.scrollIntoView({ block: 'center', inline: 'center' });
    target.click();
    resolve({
      ok: true,
      strategy: candidates[0].selector,
      text: candidates[0].text,
      aria: candidates[0].aria,
      title: document.title,
      url: location.href
    });
  } catch (error) {
    resolve({ ok: false, reason: 'exception', error: String(error), stack: error?.stack });
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


def launch_url_light(player: SprunkiGamePlayer, url: str) -> None:
    player.game_url = url
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


def wait_for_websocket(player: SprunkiGamePlayer, timeout: float) -> str:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        ws_url = player._get_websocket_url()
        if ws_url:
            return ws_url
        time.sleep(0.5)
    raise RuntimeError("Could not get Chrome DevTools websocket URL")


def click_run_with_wait(ws: websocket.WebSocket, timeout: float = 45.0, interval: float = 1.0) -> Dict[str, Any]:
    started = time.monotonic()
    last: Dict[str, Any] = {}
    while time.monotonic() - started < timeout:
        result = eval_js(ws, RUN_CLICK_JS, timeout=30)
        last = result
        if result.get("ok"):
            result["elapsedSeconds"] = time.monotonic() - started
            return result
        time.sleep(interval)
    return {
        "ok": False,
        "reason": "timeout_waiting_for_run_button",
        "elapsedSeconds": time.monotonic() - started,
        "lastResult": last,
    }


def screen_area_to_master_video_area(area: Dict[str, Any]) -> Dict[str, Any]:
    screen_w = 1920.0
    screen_h = 1080.0
    crop_left = 78.0
    crop_top = 22.0
    crop_right = 0.0
    crop_bottom = 22.0
    content_w = screen_w - crop_left - crop_right
    content_h = screen_h - crop_top - crop_bottom
    scale_x = screen_w / content_w
    scale_y = screen_h / content_h
    x = (float(area["x"]) - crop_left) * scale_x
    y = (float(area["y"]) - crop_top) * scale_y
    width = float(area["width"]) * scale_x
    height = float(area["height"]) * scale_y
    out = {
        "x": int(round(max(0.0, min(screen_w - 1.0, x)))),
        "y": int(round(max(0.0, min(screen_h - 1.0, y)))),
        "width": int(round(max(1.0, min(screen_w, width)))),
        "height": int(round(max(1.0, min(screen_h, height)))),
        "aspectRatio": "35:24",
    }
    if out["x"] + out["width"] > int(screen_w):
        out["width"] = int(screen_w) - out["x"]
    if out["y"] + out["height"] > int(screen_h):
        out["height"] = int(screen_h) - out["y"]
    return out


def calculate_recording_area(bottom_y: float, center_x: float = 999.0) -> Dict[str, Any]:
    screen_area = {
        "x": int(round(center_x - 350.0)),
        "y": int(round(float(bottom_y) - 480.0)),
        "width": 700,
        "height": 480,
        "aspectRatio": "35:24",
    }
    video_area = screen_area_to_master_video_area(screen_area)
    return {
        "screenArea": screen_area,
        "videoArea": video_area,
    }


def inspect_after_run(url: str, timeout: float, interval: float, keep_browser: bool) -> Dict[str, Any]:
    player = SprunkiGamePlayer()
    ws: Optional[websocket.WebSocket] = None
    started = time.monotonic()
    samples: List[Dict[str, Any]] = []
    try:
        print(f"Opening URL: {url}")
        launch_url_light(player, url)
        ws_url = wait_for_websocket(player, timeout=20)
        ws = websocket.create_connection(ws_url, timeout=30)

        print("Waiting for and clicking Run via light CDP...")
        run_result = click_run_with_wait(ws, timeout=min(45.0, timeout), interval=1.0)
        if not run_result.get("ok"):
            return {
                "url": url,
                "success": False,
                "reason": "run_click_failed",
                "runClick": run_result,
                "elapsedSeconds": time.monotonic() - started,
            }

        time.sleep(max(0.5, getattr(player, "game_load_wait", 5)))

        while time.monotonic() - started < timeout:
            elapsed = time.monotonic() - started
            data = eval_js(ws, LIGHT_INSPECT_JS, timeout=30)
            data["elapsedSeconds"] = elapsed
            samples.append(data)
            values = data.get("usableBoundsBottomY") or []
            if data.get("ok") and values:
                bottom = choose_bottom([float(v) for v in values])
                area = calculate_recording_area(float(bottom["value"]))
                return {
                    "url": url,
                    "success": True,
                    "elapsedSeconds": elapsed,
                    "runClicked": True,
                    "runClick": run_result,
                    "bottomDetection": bottom,
                    "recordingArea": area["videoArea"],
                    "screenRecordingAreaDebug": area["screenArea"],
                    "latestInspect": data,
                    "samples": samples,
                }
            time.sleep(interval)

        return {
            "url": url,
            "success": False,
            "reason": "timeout_no_usable_mute_buttons_after_run",
            "elapsedSeconds": time.monotonic() - started,
            "runClicked": True,
            "runClick": run_result,
            "latestInspect": samples[-1] if samples else None,
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
    parser = argparse.ArgumentParser(description="Test light Run-only Mute Buttons inspect and recordingArea calculation.")
    parser.add_argument("url", nargs="?", help="Single Cocrea/Sprunki URL")
    parser.add_argument("--url-file", help="Text file with one URL per line")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--output-dir", default="light_run_inspect_recording_area_tests")
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
    summary = {"generatedAt": datetime.now().isoformat(), "results": []}

    for index, url in enumerate(urls, 1):
        print("\n" + "=" * 80)
        print(f"[{index}/{len(urls)}] {url}")
        try:
            result = inspect_after_run(url, args.timeout, args.interval, args.keep_browser)
            summary["results"].append(result)
            result_path = output_root / f"{index:03d}-{slug_from_url(url)}.json"
            write_json(result_path, result)
            if result.get("success"):
                print(f"SUCCESS: bottomY={result['bottomDetection']['value']} recordingArea={result['recordingArea']}")
            else:
                print(f"FAILED: {result.get('reason')}")
            print(f"Result: {result_path.resolve()}")
        except Exception as exc:
            print(f"ERROR: {exc}")
            summary["results"].append({"url": url, "success": False, "error": str(exc)})

    summary_path = output_root / "summary.json"
    write_json(summary_path, summary)
    print("\nDone.")
    print(f"Success: {sum(1 for row in summary['results'] if row.get('success'))}/{len(summary['results'])}")
    print(f"Output: {output_root.resolve()}")
    print(f"Summary: {summary_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

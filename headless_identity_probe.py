import argparse
import csv
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import requests
import websocket

from character_identity import (
    IDENTITY_MAP_FILENAME,
    SCRATCH_VM_ICON_STATE_JS,
    build_character_identity_map,
    classify_needs_review_reason,
    is_verified_identity_map,
    load_character_identity_map,
    load_page_title_from_manifest,
    load_source_url_from_manifest,
)


CLICK_RUN_JS = r"""
(() => {
  const candidates = Array.from(document.querySelectorAll('button, [role="button"], svg, div, span'))
    .filter(el => {
      const text = (el.innerText || el.textContent || el.getAttribute('aria-label') || el.getAttribute('title') || '').toLowerCase();
      const cls = (el.className && String(el.className).toLowerCase()) || '';
      return text.includes('run') || text.includes('play') || cls.includes('green-flag') || cls.includes('run-button') || cls.includes('stage-header_stage-button');
    });
  for (const el of candidates) {
    const rect = el.getBoundingClientRect();
    if (rect.width > 0 && rect.height > 0) {
      el.dispatchEvent(new MouseEvent('mousedown', {bubbles:true, clientX:rect.left+rect.width/2, clientY:rect.top+rect.height/2}));
      el.dispatchEvent(new MouseEvent('mouseup', {bubbles:true, clientX:rect.left+rect.width/2, clientY:rect.top+rect.height/2}));
      el.click();
      return {clicked:true};
    }
  }
  return {clicked:false, count:candidates.length};
})();
"""


CLICK_CENTER_JS = r"""
(() => {
  const x = Math.floor(window.innerWidth / 2);
  const y = Math.floor(window.innerHeight / 2);
  const el = document.elementFromPoint(x, y) || document.body;
  for (const type of ['pointerdown','mousedown','pointerup','mouseup','click']) {
    el.dispatchEvent(new MouseEvent(type, {bubbles:true, cancelable:true, clientX:x, clientY:y}));
  }
  return {clicked:true, x, y};
})();
"""


def find_chrome():
    candidates = [
        os.environ.get("CHROME_PATH"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        shutil.which("chrome"),
        shutil.which("chrome.exe"),
        shutil.which("msedge"),
        shutil.which("msedge.exe"),
    ]
    for path in candidates:
        if path and os.path.exists(path):
            return path
    raise FileNotFoundError("Chrome/Edge executable not found. Set CHROME_PATH if needed.")


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_debug(port, timeout=30):
    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        try:
            response = requests.get(f"http://127.0.0.1:{port}/json/version", timeout=1)
            if response.ok:
                return response.json()
        except Exception as exc:
            last_error = exc
        time.sleep(0.25)
    raise TimeoutError(f"Chrome debug endpoint not ready on {port}: {last_error}")


def cdp_send(ws, method, params=None, timeout=30):
    cdp_send.counter += 1
    cid = cdp_send.counter
    ws.send(json.dumps({"id": cid, "method": method, "params": params or {}}))
    deadline = time.time() + timeout
    while time.time() < deadline:
        message = json.loads(ws.recv())
        if message.get("id") == cid:
            if "error" in message:
                raise RuntimeError(f"{method}: {message['error']}")
            return message
    raise TimeoutError(method)


cdp_send.counter = 0


def cdp_eval(ws, expression, timeout=60):
    result = cdp_send(
        ws,
        "Runtime.evaluate",
        {
            "expression": expression,
            "awaitPromise": True,
            "returnByValue": True,
            "timeout": int(timeout * 1000),
        },
        timeout=timeout + 5,
    )
    return result.get("result", {}).get("result", {}).get("value")


def inject_cookies(ws, cookie_file):
    if not cookie_file or not os.path.exists(cookie_file):
        return {"attempted": False, "count": 0}
    with open(cookie_file, "r", encoding="utf-8") as handle:
        cookies = json.load(handle)
    count = 0
    for cookie in cookies:
        params = {
            "name": cookie.get("name"),
            "value": cookie.get("value", ""),
            "domain": cookie.get("domain", ".cocrea.world"),
            "path": cookie.get("path", "/"),
            "secure": bool(cookie.get("secure", False)),
            "httpOnly": bool(cookie.get("httpOnly", False)),
        }
        if cookie.get("expirationDate"):
            params["expires"] = float(cookie["expirationDate"])
        try:
            cdp_send(ws, "Network.setCookie", params, timeout=5)
            count += 1
        except Exception:
            pass
    return {"attempted": True, "count": count}


def slug_from_url(url):
    parsed = urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 2:
        return f"{parts[-2].lstrip('@')}-{parts[-1]}"
    return "sprunki-mod"


def wait_document_ready(ws, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            state = cdp_eval(ws, "document.readyState", timeout=5)
            if state in ("interactive", "complete"):
                return state
        except Exception:
            pass
        time.sleep(0.5)
    return None


def wait_for_identity(ws, output_folder, source_url, page_title, timeout, click_interval=2.0):
    deadline = time.time() + timeout
    last_state = None
    last_identity = None
    last_error = None
    next_click = 0
    while time.time() < deadline:
        now = time.time()
        if now >= next_click:
            try:
                cdp_eval(ws, CLICK_RUN_JS, timeout=5)
                cdp_eval(ws, CLICK_CENTER_JS, timeout=5)
            except Exception:
                pass
            next_click = now + click_interval
        try:
            state = cdp_eval(ws, SCRATCH_VM_ICON_STATE_JS, timeout=30)
            last_state = state
            if state and state.get("ok"):
                identity = build_character_identity_map(state, output_folder, source_url, page_title)
                last_identity = identity
                if identity.get("status") == "verified":
                    return identity, state
        except Exception as exc:
            last_error = str(exc) or repr(exc)
        time.sleep(1)

    if last_identity:
        return last_identity, last_state
    fallback_state = last_state if isinstance(last_state, dict) else {}
    error = fallback_state.get("error") or "timeout_no_verified_identity"
    if last_error and ("Runtime.evaluate" in last_error or "timeout" in last_error.lower()):
        error = "runtime_evaluate_timeout"
    fallback_state.update({"ok": False, "error": error, "lastError": last_error, "icons": fallback_state.get("icons") or []})
    return build_character_identity_map(fallback_state, output_folder, source_url, page_title), fallback_state


def write_identity(output_folder, identity):
    Path(output_folder).mkdir(parents=True, exist_ok=True)
    path = Path(output_folder) / IDENTITY_MAP_FILENAME
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(identity, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return path


def probe_one(item, args):
    started = time.perf_counter()
    output_folder = item["outputFolder"]
    existing_identity = load_character_identity_map(output_folder)
    if existing_identity and not args.force:
        existing_status = existing_identity.get("status")
        if is_verified_identity_map(existing_identity):
            identity_path = Path(output_folder) / IDENTITY_MAP_FILENAME
            validation = existing_identity.get("validation") or {}
            return {
                "outputFolder": output_folder,
                "sourceUrl": item.get("sourceUrl") or load_source_url_from_manifest(output_folder),
                "status": "skipped",
                "existingStatus": existing_status,
                "reason": "existing_verified_identity_map",
                "elapsedSeconds": round(time.perf_counter() - started, 2),
                "identityMap": str(identity_path),
                "identityMapBytes": os.path.getsize(identity_path) if identity_path.exists() else 0,
                "standardCharacterCount": validation.get("standardCharacterCount"),
                "excludedSegmentCount": validation.get("excludedSegmentCount"),
                "needsReviewReason": validation.get("needsReviewReason") or existing_identity.get("needsReviewReason"),
            }
        if args.skip_existing:
            identity_path = Path(output_folder) / IDENTITY_MAP_FILENAME
            return {
                "outputFolder": output_folder,
                "sourceUrl": item.get("sourceUrl") or load_source_url_from_manifest(output_folder),
                "status": "skipped",
                "existingStatus": existing_status,
                "reason": "existing_identity_map_not_forced",
                "elapsedSeconds": round(time.perf_counter() - started, 2),
                "identityMap": str(identity_path),
            }

    source_url = item.get("sourceUrl") or load_source_url_from_manifest(output_folder)
    page_title = item.get("pageTitle") or load_page_title_from_manifest(output_folder)
    if not source_url:
        return {
            "outputFolder": output_folder,
            "status": "failed",
            "error": "missing_source_url",
            "elapsedSeconds": round(time.perf_counter() - started, 2),
        }

    port = free_port()
    user_data_dir = tempfile.mkdtemp(prefix="sprunki-headless-")
    chrome = find_chrome()
    headless_arg = [] if args.no_headless else ["--headless=new"]
    cmd = [
        chrome,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
        "--remote-allow-origins=*",
        "--disable-gpu",
        "--no-first-run",
        "--no-default-browser-check",
        "--autoplay-policy=no-user-gesture-required",
        "--window-size=1920,1080",
        *headless_arg,
        "about:blank",
    ]

    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ws = None
    try:
        wait_debug(port, timeout=30)
        tabs = requests.get(f"http://127.0.0.1:{port}/json", timeout=5).json()
        page = next(tab for tab in tabs if tab.get("type") == "page")
        ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=30)
        cdp_send(ws, "Page.enable")
        cdp_send(ws, "Runtime.enable")
        cdp_send(ws, "Network.enable")
        cookie_result = inject_cookies(ws, args.cookie_file)
        cdp_send(ws, "Page.navigate", {"url": source_url})
        wait_document_ready(ws, timeout=60)
        time.sleep(2)

        identity, state = wait_for_identity(
            ws,
            output_folder=output_folder,
            source_url=source_url,
            page_title=page_title,
            timeout=args.timeout,
        )
        identity_path = write_identity(output_folder, identity)
        elapsed = round(time.perf_counter() - started, 2)
        validation = identity.get("validation", {})
        return {
            "outputFolder": output_folder,
            "sourceUrl": source_url,
            "status": identity.get("status"),
            "elapsedSeconds": elapsed,
            "identityMap": str(identity_path),
            "identityMapBytes": os.path.getsize(identity_path),
            "standardCharacterCount": validation.get("standardCharacterCount"),
            "excludedSegmentCount": validation.get("excludedSegmentCount"),
            "errors": validation.get("errors") or [],
            "needsReviewReason": validation.get("needsReviewReason") or identity.get("needsReviewReason"),
            "stateError": state.get("error") if isinstance(state, dict) else None,
            "cookieResult": cookie_result,
        }
    except Exception as exc:
        elapsed = round(time.perf_counter() - started, 2)
        identity = build_character_identity_map(
            {"ok": False, "error": str(exc), "href": source_url, "icons": []},
            output_folder,
            source_url,
            page_title,
        )
        if not identity.get("needsReviewReason"):
            reason = classify_needs_review_reason({"ok": False, "error": str(exc), "href": source_url, "icons": []})
            if reason:
                identity["needsReviewReason"] = reason
                identity.setdefault("validation", {})["needsReviewReason"] = reason
        identity_path = write_identity(output_folder, identity)
        validation = identity.get("validation", {})
        return {
            "outputFolder": output_folder,
            "sourceUrl": source_url,
            "status": "failed",
            "elapsedSeconds": elapsed,
            "identityMap": str(identity_path),
            "error": str(exc),
            "needsReviewReason": validation.get("needsReviewReason") or identity.get("needsReviewReason"),
        }
    finally:
        if ws:
            try:
                ws.close()
            except Exception:
                pass
        if not args.keep_browser:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            shutil.rmtree(user_data_dir, ignore_errors=True)


def read_input_list(path):
    input_path = Path(path)
    if input_path.suffix.lower() == ".csv":
        with open(input_path, "r", encoding="utf-8-sig", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    items = []
    with open(input_path, "r", encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def build_items(args):
    items = []
    if args.input_list:
        items.extend(read_input_list(args.input_list))
    for folder in args.folder:
        folder_path = Path(folder)
        if (folder_path / "manifest.json").exists():
            items.append({"outputFolder": str(folder_path)})
            continue

        child_folders = sorted(
            child
            for child in folder_path.iterdir()
            if child.is_dir() and (child / "manifest.json").exists()
        ) if folder_path.exists() and folder_path.is_dir() else []
        if child_folders:
            for child in child_folders:
                items.append({"outputFolder": str(child)})
        else:
            items.append({"outputFolder": str(folder_path)})
    for url in args.url:
        folder = args.output_folder or str(Path(args.out_dir) / slug_from_url(url))
        items.append({"outputFolder": folder, "sourceUrl": url})
    normalized = []
    for item in items:
        output_folder = item.get("outputFolder") or item.get("folder")
        if not output_folder:
            continue
        normalized.append(
            {
                "outputFolder": output_folder.strip(),
                "sourceUrl": (item.get("sourceUrl") or item.get("url") or "").strip() or None,
                "pageTitle": (item.get("pageTitle") or "").strip() or None,
            }
        )
    return normalized


def run_batch(items, args):
    results = []
    if args.concurrency <= 1:
        for index, item in enumerate(items, start=1):
            print(f"[{index}/{len(items)}] Probing {item['outputFolder']}")
            result = probe_one(item, args)
            print(json.dumps(result, ensure_ascii=False))
            if result.get("status") == "needs_review" and args.retry_needs_review:
                print(f"[{index}/{len(items)}] Retrying needs_review: {item['outputFolder']}")
                retry = probe_one(item, args)
                retry["retried"] = True
                print(json.dumps(retry, ensure_ascii=False))
                result = retry
            results.append(result)
        return results

    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = {executor.submit(probe_one, item, args): item for item in items}
        for future in as_completed(futures):
            result = future.result()
            print(json.dumps(result, ensure_ascii=False))
            results.append(result)
    return results


def main():
    parser = argparse.ArgumentParser(description="Headless Cocrea/Scratch identity probe. Writes only character_identity_map.json.")
    parser.add_argument("url", nargs="*", help="Optional source URL(s).")
    parser.add_argument("--folder", action="append", default=[], help="Existing mod output folder, or a root folder containing child mod folders with manifest.json.")
    parser.add_argument("--input-list", help="JSONL or CSV with outputFolder and optional sourceUrl/pageTitle.")
    parser.add_argument("--output-folder", help="Output folder for a single URL probe.")
    parser.add_argument("--out-dir", default=r"G:\VS-Project\Sprunki\temp\character_identity_probe", help="Base folder for URL-only probes.")
    parser.add_argument("--cookie-file", default="cocrea_cookies.json")
    parser.add_argument("--timeout", type=float, default=220)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--force", action="store_true", help="Re-probe even when character_identity_map.json already exists.")
    parser.add_argument("--skip-existing", action="store_true", help="Skip any existing character_identity_map.json, including needs_review/failed.")
    parser.add_argument("--retry-needs-review", action="store_true", default=True)
    parser.add_argument("--keep-browser", action="store_true")
    parser.add_argument("--no-headless", action="store_true")
    parser.add_argument("--summary", help="Optional JSON summary path.")
    args = parser.parse_args()

    if args.concurrency < 1:
        raise ValueError("--concurrency must be >= 1")
    items = build_items(args)
    if not items:
        raise SystemExit("No probes requested. Use URL positional args, --folder, or --input-list.")

    started = time.perf_counter()
    results = run_batch(items, args)
    summary = {
        "generatedAt": datetime.now().isoformat(),
        "elapsedSeconds": round(time.perf_counter() - started, 2),
        "total": len(results),
        "verified": len([row for row in results if row.get("status") == "verified"]),
        "skipped": len([row for row in results if row.get("status") == "skipped"]),
        "needsReview": len([row for row in results if row.get("status") == "needs_review"]),
        "failed": len([row for row in results if row.get("status") == "failed"]),
        "needsReviewReasons": {
            reason: len([row for row in results if row.get("needsReviewReason") == reason])
            for reason in sorted({row.get("needsReviewReason") for row in results if row.get("needsReviewReason")})
        },
        "results": results,
    }
    if args.summary:
        Path(args.summary).parent.mkdir(parents=True, exist_ok=True)
        with open(args.summary, "w", encoding="utf-8") as handle:
            json.dump(summary, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

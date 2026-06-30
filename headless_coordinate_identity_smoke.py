from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
import websocket

from headless_identity_probe import (
    CLICK_CENTER_JS,
    CLICK_RUN_JS,
    cdp_eval,
    cdp_send,
    find_chrome,
    free_port,
    inject_cookies,
    wait_debug,
    wait_document_ready,
)


VM_ICON_COORDINATE_SNAPSHOT_JS = r"""
new Promise((resolve) => {
  try {
    function findVM() {
      if (window.__vm && window.__vm.runtime) return window.__vm;
      function getReactKey(el) { return Object.keys(el).find(k => k.startsWith('__react')); }
      const selectors = ['canvas', '.ccw-stage-wrapper', '#stage-canvas-wrapper', '.gandi_stage_stage_1fD7k', 'body'];
      for (const selector of selectors) {
        for (const el of document.querySelectorAll(selector)) {
          const reactKey = getReactKey(el);
          if (!reactKey) continue;
          let fiber = el[reactKey];
          let depth = 0;
          while (fiber && depth++ < 180) {
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

    const toPixelWithMenu = (logicX, logicY) => {
      if (logicX === null || logicY === null || logicX === undefined || logicY === undefined) return { x: null, y: null };
      const stageW = 640;
      const stageH = 360;
      const screenW = 1920;
      const screenH = 1080;
      const menuWidth = 78;
      const viewportW = screenW - menuWidth;
      const scale = Math.min(viewportW / stageW, screenH / stageH);
      const canvasW = stageW * scale;
      const canvasH = stageH * scale;
      const leftOffset = menuWidth;
      const topOffset = (screenH - canvasH) / 2;
      const canvasCenterX = leftOffset + (canvasW / 2);
      const canvasCenterY = topOffset + (canvasH / 2);
      return {
        x: Math.round(canvasCenterX + (logicX * scale)),
        y: Math.round(canvasCenterY - (logicY * scale))
      };
    };

    const vm = findVM();
    if (!vm) return resolve({
      ok:false,
      error:'vm_not_found',
      title:document.title,
      href:location.href,
      readyState:document.readyState,
      canvasCount:document.querySelectorAll('canvas').length,
      bodyText:(document.body?.innerText || '').replace(/\s+/g, ' ').slice(0, 500)
    });

    const allTargets = vm.runtime.targets || [];
    const visibleIcons = allTargets.filter(t => t.sprite?.name?.includes('Icons') && t.visible);
    const icons = visibleIcons.map((target, originalIndexInVisibleList) => {
      const pixel = toPixelWithMenu(target.x, target.y);
      const costumes = target.sprite?.costumes || [];
      const costume = costumes[target.currentCostume] || {};
      return {
        originalIndexInVisibleList,
        runtimeIndex: allTargets.indexOf(target),
        targetId: target.id,
        iconTargetName: target.sprite?.name || null,
        visible: !!target.visible,
        vmX: target.x,
        vmY: target.y,
        x: pixel.x,
        y: pixel.y,
        currentCostume: target.currentCostume,
        costumeName: costume.name || null,
        costumeAssetId: costume.assetId || null,
        costumeMd5Ext: costume.md5ext || null,
        filteredByJsonGenerator: pixel.y !== null && pixel.y >= 540
      };
    });

    resolve({
      ok:true,
      title:document.title,
      href:location.href,
      capturedAt:new Date().toISOString(),
      targetCount:allTargets.length,
      visibleIconCount:visibleIcons.length,
      icons
    });
  } catch (e) {
    resolve({ok:false, error:String(e), stack:e.stack, title:document.title, href:location.href});
  }
});
"""


def read_json(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def source_json_from_manifest(project: Path) -> Path:
    manifest = read_json(project / "manifest.json")
    source_name = manifest.get("outputs", {}).get("sourceJson")
    if not source_name:
        raise ValueError("manifest.json does not contain outputs.sourceJson")
    candidates = [
        project / source_name,
        project.parent / "JSON" / source_name,
        Path(r"G:\SprunkiOUTPUT\JSON") / source_name,
        Path(r"G:\Sprunki OUTPUT\JSON") / source_name,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"Could not find sourceJson {source_name}")


def find_timestamps(project: Path) -> Path:
    candidates = sorted(project.glob("*showcase.timestamps.json")) + sorted(project.glob("*.timestamps.json"))
    if not candidates:
        raise FileNotFoundError(f"No timestamps JSON found in {project}")
    return candidates[0]


def source_url_from_manifest(project: Path) -> str:
    manifest = read_json(project / "manifest.json")
    source_url = manifest.get("source", {}).get("value") or manifest.get("projectInfo", {}).get("sourceUrl")
    if not source_url:
        raise ValueError("manifest.json does not contain source URL")
    return source_url


def load_identity_lookup(identity: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    lookup: Dict[str, Dict[str, Any]] = {}
    for row in identity.get("standardCharacters") or []:
        target_name = row.get("iconTargetName") or ""
        costume_name = row.get("costumeName") or ""
        if costume_name:
            lookup[f"{target_name}\0{costume_name}"] = row
            lookup[f"\0{costume_name}"] = row
    return lookup


def recorded_order(timestamps: Dict[str, Any]) -> Dict[int, str]:
    out: Dict[int, str] = {}
    events = sorted(timestamps.get("events", []), key=lambda item: item.get("timestamp", 0))
    for event in events:
        if event.get("event") != "start_listen":
            continue
        metadata = event.get("metadata") or {}
        if metadata.get("icon_index") is None or not metadata.get("segmentKey"):
            continue
        out[int(metadata["icon_index"])] = metadata["segmentKey"]
    return out


def nearest_icon(source_icon: Dict[str, Any], runtime_icons: List[Dict[str, Any]]) -> Dict[str, Any]:
    sx = float(source_icon["x"])
    sy = float(source_icon["y"])
    best: Optional[Dict[str, Any]] = None
    best_distance = math.inf
    for icon in runtime_icons:
        if icon.get("x") is None or icon.get("y") is None:
            continue
        distance = math.hypot(float(icon["x"]) - sx, float(icon["y"]) - sy)
        if distance < best_distance:
            best = icon
            best_distance = distance
    return {"icon": best, "distance": best_distance}


def open_headless_snapshot(
    source_url: str,
    cookie_file: str,
    timeout: float,
    min_runtime_icons: int,
    click_center: bool = False,
) -> Dict[str, Any]:
    port = free_port()
    user_data_dir = tempfile.mkdtemp(prefix="sprunki-headless-coordinate-")
    chrome = find_chrome()
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
        "--headless=new",
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
        cookie_result = inject_cookies(ws, cookie_file)
        def capture_snapshot(phase: str, eval_timeout: float = 30) -> Dict[str, Any]:
            snapshot = cdp_eval(ws, VM_ICON_COORDINATE_SNAPSHOT_JS, timeout=eval_timeout)
            if isinstance(snapshot, dict):
                snapshot["phase"] = phase
                snapshot["cookieResult"] = cookie_result
                return snapshot
            return {"ok": False, "phase": phase, "error": "snapshot_not_object", "cookieResult": cookie_result}

        cdp_send(ws, "Page.navigate", {"url": source_url})
        wait_document_ready(ws, timeout=60)
        time.sleep(4)

        phase_snapshots = []
        for phase in ("after_document_ready",):
            try:
                snapshot = capture_snapshot(phase)
                phase_snapshots.append(snapshot)
                runtime_icons = [icon for icon in snapshot.get("icons", []) if icon.get("filteredByJsonGenerator")]
                if snapshot.get("ok") and len(runtime_icons) >= min_runtime_icons:
                    snapshot["phaseSnapshots"] = phase_snapshots
                    return snapshot
            except Exception as exc:
                phase_snapshots.append({"ok": False, "phase": phase, "error": str(exc) or repr(exc), "cookieResult": cookie_result})

        try:
            cdp_eval(ws, CLICK_RUN_JS, timeout=5)
        except Exception:
            pass
        time.sleep(6)
        try:
            snapshot = capture_snapshot("after_run_click")
            phase_snapshots.append(snapshot)
            runtime_icons = [icon for icon in snapshot.get("icons", []) if icon.get("filteredByJsonGenerator")]
            if snapshot.get("ok") and len(runtime_icons) >= min_runtime_icons:
                snapshot["phaseSnapshots"] = phase_snapshots
                return snapshot
        except Exception as exc:
            phase_snapshots.append({"ok": False, "phase": "after_run_click", "error": str(exc) or repr(exc), "cookieResult": cookie_result})

        deadline = time.time() + timeout
        last_snapshot: Dict[str, Any] = {}
        while time.time() < deadline:
            try:
                cdp_eval(ws, CLICK_RUN_JS, timeout=5)
                if click_center:
                    cdp_eval(ws, CLICK_CENTER_JS, timeout=5)
            except Exception:
                pass
            try:
                snapshot = cdp_eval(ws, VM_ICON_COORDINATE_SNAPSHOT_JS, timeout=30)
                if isinstance(snapshot, dict):
                    snapshot["phase"] = "poll_after_run"
                    snapshot["cookieResult"] = cookie_result
                    snapshot["phaseSnapshots"] = phase_snapshots
                    last_snapshot = snapshot
                    runtime_icons = [icon for icon in snapshot.get("icons", []) if icon.get("filteredByJsonGenerator")]
                    if snapshot.get("ok") and len(runtime_icons) >= min_runtime_icons:
                        return snapshot
            except Exception as exc:
                last_snapshot = {
                    **(last_snapshot or {}),
                    "ok": False,
                    "phase": "poll_after_run",
                    "lastError": str(exc) or repr(exc),
                    "cookieResult": cookie_result,
                    "phaseSnapshots": phase_snapshots,
                }
            time.sleep(2)
        return last_snapshot or {"ok": False, "error": "timeout_no_runtime_icons", "cookieResult": cookie_result}
    finally:
        if ws:
            try:
                ws.close()
            except Exception:
                pass
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        shutil.rmtree(user_data_dir, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Headless coordinate smoke: source JSON icon x/y -> Scratch VM icon target.")
    parser.add_argument("project")
    parser.add_argument("--threshold", type=float, default=6.0)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--cookie-file", default=r"G:\VS-Project\Sprunki\cocrea_cookies.json")
    parser.add_argument("--click-center", action="store_true", help="Click center while waiting; disabled by default to avoid mutating icon state.")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    project = Path(args.project)
    source_json_path = source_json_from_manifest(project)
    timestamps_path = find_timestamps(project)
    identity_path = project / "character_identity_map.json"
    source_json = read_json(source_json_path)
    timestamps = read_json(timestamps_path)
    identity = read_json(identity_path)
    source_url = source_url_from_manifest(project)

    min_runtime_icons = len(source_json.get("layout", {}).get("icons", []))
    snapshot = open_headless_snapshot(source_url, args.cookie_file, args.timeout, min_runtime_icons, args.click_center)
    if not snapshot.get("ok"):
        out_path = Path(args.out) if args.out else project / "character_identity_map.coordinate_smoke.headless.failed.json"
        with open(out_path, "w", encoding="utf-8") as handle:
            json.dump({"status": "failed", "snapshot": snapshot}, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        raise RuntimeError(f"Headless VM snapshot failed: {snapshot}")

    identity_lookup = load_identity_lookup(identity)
    recorded_by_icon_index = recorded_order(timestamps)
    runtime_icons = [icon for icon in snapshot.get("icons", []) if icon.get("filteredByJsonGenerator")]

    matches: List[Dict[str, Any]] = []
    segment_by_canonical: Dict[str, str] = {}
    excluded: Dict[str, Any] = {}
    for source_icon in source_json.get("layout", {}).get("icons", []):
        match = nearest_icon(source_icon, runtime_icons)
        runtime_icon = match["icon"]
        distance = match["distance"]
        icon_index = int(source_icon["index"])
        segment_key = recorded_by_icon_index.get(icon_index)
        identity_row = None
        if runtime_icon:
            identity_row = identity_lookup.get(f"{runtime_icon.get('iconTargetName') or ''}\0{runtime_icon.get('costumeName') or ''}")
            if not identity_row:
                identity_row = identity_lookup.get(f"\0{runtime_icon.get('costumeName') or ''}")
        status = "matched" if runtime_icon and distance <= args.threshold else "unmatched"
        canonical_id = identity_row.get("canonicalId") if identity_row and status == "matched" else None
        row = {
            "iconIndex": icon_index,
            "segmentKey": segment_key,
            "sourcePixel": {"x": source_icon.get("x"), "y": source_icon.get("y")},
            "matchStatus": status if canonical_id else ("matched_no_identity" if status == "matched" else status),
            "distancePx": None if math.isinf(distance) else round(distance, 3),
            "matchedRuntimeIcon": runtime_icon,
            "identity": identity_row,
            "canonicalId": canonical_id,
            "displayName": identity_row.get("displayName") if identity_row and canonical_id else None,
        }
        matches.append(row)
        if segment_key and canonical_id:
            segment_by_canonical[canonical_id] = segment_key
        elif segment_key:
            excluded[segment_key] = {
                "iconIndex": icon_index,
                "reason": row["matchStatus"],
                "matchedRuntimeIcon": runtime_icon,
            }

    output = {
        "schemaVersion": 2,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "status": "coordinate_smoke_headless",
        "rule": {
            "id": "source_json_coordinate_to_vm_icon_match_headless",
            "description": "Headless Chrome match of json_generator layout.icons x/y back to Scratch VM Icons targets using json_generator coordinate conversion.",
        },
        "source": {
            "outputFolder": str(project),
            "sourceJson": str(source_json_path),
            "timestamps": str(timestamps_path),
            "identityMap": str(identity_path),
            "sourceUrl": source_url,
            "identityRule": identity.get("rule", {}).get("id"),
        },
        "validation": {
            "sourceIconCount": len(source_json.get("layout", {}).get("icons", [])),
            "runtimeFilteredIconCount": len(runtime_icons),
            "matchedCount": len([row for row in matches if row["matchStatus"] == "matched"]),
            "mappedCanonicalCount": len(segment_by_canonical),
            "excludedSegmentCount": len(excluded),
            "thresholdPx": args.threshold,
        },
        "runtimeSnapshotSummary": {
            "targetCount": snapshot.get("targetCount"),
            "visibleIconCount": snapshot.get("visibleIconCount"),
            "title": snapshot.get("title"),
            "href": snapshot.get("href"),
            "cookieResult": snapshot.get("cookieResult"),
        },
        "matches": matches,
        "segmentByCanonicalId": segment_by_canonical,
        "excludedSegments": excluded,
    }

    out_path = Path(args.out) if args.out else project / "character_identity_map.coordinate_smoke.headless.json"
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(output, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    print(out_path)
    for row in matches:
        runtime_icon = row.get("matchedRuntimeIcon") or {}
        print(
            f"{row['segmentKey'] or '-':>4} iconIndex={row['iconIndex']:>2} "
            f"src=({row['sourcePixel']['x']},{row['sourcePixel']['y']}) "
            f"dist={row['distancePx']} target={runtime_icon.get('iconTargetName')} "
            f"costume={runtime_icon.get('costumeName')} -> {row.get('canonicalId') or row['matchStatus']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())

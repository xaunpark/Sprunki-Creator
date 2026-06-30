from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import websocket

from inspect_character_bounds_after_drag import eval_js
from inspect_game_entry import complete_game_entry_sequence
from sprunki_game_player import SprunkiGamePlayer


VM_ICON_COORDINATE_SNAPSHOT_JS = r"""
new Promise((resolve) => {
  try {
    function findVM() {
      if (window.__vm && window.__vm.runtime) return window.__vm;
      function getReactKey(el) { return Object.keys(el).find(k => k.startsWith('__react')); }
      const selectors = ['canvas', '.ccw-stage-wrapper', '#stage-canvas-wrapper', '.gandi_stage_stage_1fD7k'];
      for (const selector of selectors) {
        for (const el of document.querySelectorAll(selector)) {
          const reactKey = getReactKey(el);
          if (!reactKey) continue;
          let fiber = el[reactKey];
          let depth = 0;
          while (fiber && depth++ < 140) {
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
      const pixelX = canvasCenterX + (logicX * scale);
      const pixelY = canvasCenterY - (logicY * scale);
      return { x: Math.round(pixelX), y: Math.round(pixelY) };
    };

    const vm = findVM();
    if (!vm) return resolve({ok:false, error:'vm_not_found', title:document.title, href:location.href});
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


def source_url_from_manifest(project: Path) -> str:
    manifest = read_json(project / "manifest.json")
    source_url = manifest.get("source", {}).get("value") or manifest.get("projectInfo", {}).get("sourceUrl")
    if not source_url:
        raise ValueError("manifest.json does not contain source URL")
    return source_url


def find_timestamps(project: Path) -> Path:
    candidates = sorted(project.glob("*showcase.timestamps.json")) + sorted(project.glob("*.timestamps.json"))
    if not candidates:
        raise FileNotFoundError(f"No timestamps JSON found in {project}")
    return candidates[0]


def identity_map_path(project: Path) -> Path:
    path = project / "character_identity_map.json"
    if not path.exists():
        raise FileNotFoundError(path)
    return path


def load_identity_lookup(identity: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    lookup: Dict[str, Dict[str, Any]] = {}
    for row in identity.get("standardCharacters") or []:
        target_name = row.get("iconTargetName") or ""
        costume_name = row.get("costumeName") or ""
        if costume_name:
            lookup[f"{target_name}\0{costume_name}"] = row
            lookup[f"\0{costume_name}"] = row
    return lookup


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test: match source JSON icon x/y back to Scratch VM icons.")
    parser.add_argument("project", help="Output project folder")
    parser.add_argument("--threshold", type=float, default=6.0)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    project = Path(args.project)
    source_json_path = source_json_from_manifest(project)
    timestamps_path = find_timestamps(project)
    identity_path = identity_map_path(project)
    source_json = read_json(source_json_path)
    timestamps = read_json(timestamps_path)
    identity = read_json(identity_path)
    source_url = source_url_from_manifest(project)

    player: Optional[SprunkiGamePlayer] = None
    ws: Optional[websocket.WebSocket] = None
    try:
        player = SprunkiGamePlayer()
        if not player.navigate_and_prepare_game(source_url):
            raise RuntimeError("Game setup failed")
        complete_game_entry_sequence(player)
        ws_url = player._get_websocket_url()
        if not ws_url:
            raise RuntimeError("Could not get Chrome DevTools websocket URL")
        ws = websocket.create_connection(ws_url, timeout=30)
        snapshot = eval_js(ws, VM_ICON_COORDINATE_SNAPSHOT_JS, timeout=30)
    finally:
        if ws:
            try:
                ws.close()
            except Exception:
                pass
        if player:
            try:
                player.cleanup_chrome_complete()
            except Exception:
                pass

    if not snapshot.get("ok"):
        raise RuntimeError(f"VM snapshot failed: {snapshot}")

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
        "status": "coordinate_smoke",
        "rule": {
            "id": "source_json_coordinate_to_vm_icon_match",
            "description": "Match json_generator layout.icons x/y back to Scratch VM Icons targets using json_generator's coordinate conversion; then join matched costume to current identity order.",
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
        },
        "matches": matches,
        "segmentByCanonicalId": segment_by_canonical,
        "excludedSegments": excluded,
    }

    out_path = Path(args.out) if args.out else project / "character_identity_map.coordinate_smoke.json"
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

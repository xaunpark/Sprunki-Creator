from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import websocket

from character_identity import CANONICAL_DISPLAY_NAMES
from inspect_character_bounds_after_drag import (
    LAYOUT_AND_BOUNDS_JS,
    cdp_drag,
    eval_js,
    likely_character_targets,
    send_cdp,
)
from inspect_game_entry import complete_game_entry_sequence
from sprunki_game_player import SprunkiGamePlayer


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
      return {ok:true, clicked:true};
    }
  }
  return {ok:false, clicked:false, count:candidates.length};
})();
"""


ALIASES: Dict[str, List[str]] = {
    "oren": ["oren", "orange"],
    "raddy": ["raddy", "red"],
    "clukr": ["clukr", "silver"],
    "fun_bot": ["fun bot", "funbot"],
    "vineria": ["vineria", "green"],
    "gray": ["gray", "grey"],
    "brud": ["brud", "brown"],
    "garnold": ["garnold", "gold"],
    "owakcx": ["owakcx", "lime"],
    "sky": ["sky", "sky blue", "skyblue"],
    "mr_sun": ["mr. sun", "mr sun"],
    "durple": ["durple", "purple"],
    "mr_tree": ["mr. tree", "mr tree", "tree"],
    "simon": ["simon", "yellow"],
    "tunner": ["tunner", "tan"],
    "mr_fun_computer": ["mr. fun computer", "mr fun computer", "fun computer", "computer"],
    "wenda": ["wenda"],
    "pinki": ["pinki"],
    "jevin": ["jevin"],
    "black": ["black"],
}


def read_json(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def find_timestamps(project: Path) -> Path:
    candidates = sorted(project.glob("*showcase.timestamps.json")) + sorted(project.glob("*.timestamps.json"))
    if not candidates:
        raise FileNotFoundError(f"No timestamps JSON found in {project}")
    return candidates[0]


def source_url_from_manifest(project: Path) -> str:
    manifest = read_json(project / "manifest.json")
    source_url = manifest.get("source", {}).get("value") or manifest.get("projectInfo", {}).get("sourceUrl")
    if not source_url:
        raise ValueError("manifest.json does not contain a source URL")
    return source_url


def recorded_segments(timestamps: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for event in sorted(timestamps.get("events", []), key=lambda item: item.get("timestamp", 0)):
        if event.get("event") != "start_listen":
            continue
        metadata = event.get("metadata") or {}
        segment_key = metadata.get("segmentKey")
        icon_index = metadata.get("icon_index")
        if segment_key is None or icon_index is None:
            continue
        rows.append(
            {
                "segmentKey": segment_key,
                "iconIndex": int(icon_index),
                "timestamp": event.get("timestamp"),
            }
        )
    return rows


def canonical_from_target_name(name: Optional[str]) -> Optional[str]:
    normalized = re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()
    if not normalized:
        return None
    padded = f" {normalized} "
    for canonical_id, aliases in ALIASES.items():
        for alias in aliases:
            alias_normalized = re.sub(r"[^a-z0-9]+", " ", alias.lower()).strip()
            if f" {alias_normalized} " in padded:
                return canonical_id
    return None


def is_non_character_target(name: Optional[str]) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()
    if not normalized:
        return True
    if re.search(r"\b(icon|polo|button|mute|gallery|bar|bars|preview|backdrop|menu|slider|control)\b", normalized):
        return True
    # Several mods have environmental sprites named simply "sun"; do not treat
    # that as Mr. Sun unless the target is explicitly named as the character.
    if normalized in {"sun", "moon", "sky", "bg", "background"}:
        return True
    return False


def open_prepared_game(url: str) -> tuple[SprunkiGamePlayer, websocket.WebSocket]:
    player = SprunkiGamePlayer()
    if not player.navigate_and_prepare_game(url):
        raise RuntimeError("Game setup failed")
    complete_game_entry_sequence(player)
    ws_url = player._get_websocket_url()
    if not ws_url:
        raise RuntimeError("Could not get Chrome DevTools websocket URL")
    ws = websocket.create_connection(ws_url, timeout=30)
    return player, ws


def reload_prepared_game(player: SprunkiGamePlayer, ws: websocket.WebSocket, url: str, settle_seconds: float) -> None:
    send_cdp(ws, {"method": "Page.navigate", "params": {"url": url}}, timeout=10)
    time.sleep(settle_seconds)
    for _ in range(4):
        result = eval_js(ws, CLICK_RUN_JS, timeout=8)
        if result.get("clicked"):
            break
        time.sleep(1.0)
    time.sleep(4.0)
    complete_game_entry_sequence(player)


def inspect_one(
    ws: websocket.WebSocket,
    segment_key: str,
    icon_index: int,
    wait_after_drag: float,
    radius: float,
) -> Dict[str, Any]:
    before = eval_js(ws, LAYOUT_AND_BOUNDS_JS, timeout=30)
    if not before.get("ok"):
        return {"segmentKey": segment_key, "iconIndex": icon_index, "status": "failed", "error": before.get("error")}

    icons = before.get("icons") or []
    slots = before.get("slots") or []
    if not slots:
        return {"segmentKey": segment_key, "iconIndex": icon_index, "status": "failed", "error": "no_slots"}
    if not icons:
        return {"segmentKey": segment_key, "iconIndex": icon_index, "status": "failed", "error": "no_icons"}

    icon = next((row for row in icons if row.get("iconIndex") == icon_index), None)
    if not icon:
        return {
            "segmentKey": segment_key,
            "iconIndex": icon_index,
            "status": "failed",
            "error": "icon_index_not_found",
            "availableIconIndexes": [row.get("iconIndex") for row in icons],
        }

    center_index = (len(slots) - 1) // 2
    center_slot = next((slot for slot in slots if slot.get("slotIndex") == center_index), slots[center_index])

    cdp_drag(
        ws,
        float(icon["pixelX"]),
        float(icon["pixelY"]),
        float(center_slot["pixelX"]),
        float(center_slot["pixelY"]),
    )
    time.sleep(wait_after_drag)

    after = eval_js(ws, LAYOUT_AND_BOUNDS_JS, timeout=30)
    if not after.get("ok"):
        return {"segmentKey": segment_key, "iconIndex": icon_index, "status": "failed", "error": after.get("error")}

    candidates = [
        row
        for row in likely_character_targets(before, after, center_slot, radius)
        if not is_non_character_target(row.get("name"))
        and row.get("changedAfterDrag") is True
        and isinstance(row.get("distanceToCenterSlot"), (int, float))
        and float(row["distanceToCenterSlot"]) <= 180.0
    ]
    best = candidates[0] if candidates else None
    canonical_id = canonical_from_target_name(best.get("name") if best else None)
    status = "identified" if canonical_id else "needs_review"
    return {
        "segmentKey": segment_key,
        "iconIndex": icon_index,
        "status": status,
        "canonicalId": canonical_id,
        "displayName": CANONICAL_DISPLAY_NAMES.get(canonical_id) if canonical_id else None,
        "detectedTargetName": best.get("name") if best else None,
        "bestCandidate": {
            key: best.get(key)
            for key in (
                "runtimeIndex",
                "id",
                "name",
                "distanceToCenterSlot",
                "changedAfterDrag",
                "score",
                "currentCostume",
                "boundsX",
                "boundsY",
                "boundsWidth",
                "boundsHeight",
            )
        }
        if best
        else None,
        "topCandidates": [
            {
                "name": row.get("name"),
                "distanceToCenterSlot": row.get("distanceToCenterSlot"),
                "changedAfterDrag": row.get("changedAfterDrag"),
                "score": row.get("score"),
                "currentCostume": row.get("currentCostume"),
            }
            for row in candidates[:8]
        ],
    }


def build_identity(project: Path, segments: List[Dict[str, Any]], rows: List[Dict[str, Any]], source_url: str) -> Dict[str, Any]:
    segment_by_canonical: Dict[str, str] = {}
    duplicates: Dict[str, List[str]] = {}
    segment_identities: Dict[str, Any] = {}
    for row in rows:
        segment_key = row["segmentKey"]
        canonical_id = row.get("canonicalId")
        segment_identities[segment_key] = {
            "canonicalId": canonical_id,
            "displayName": row.get("displayName"),
            "detectedTargetName": row.get("detectedTargetName"),
            "status": row.get("status"),
            "iconIndex": row.get("iconIndex"),
            "evidence": {
                "method": "runtime_target_after_recorded_icon_drag",
                "bestCandidate": row.get("bestCandidate"),
            },
        }
        if not canonical_id:
            continue
        if canonical_id in segment_by_canonical:
            duplicates.setdefault(canonical_id, [segment_by_canonical[canonical_id]]).append(segment_key)
            continue
        segment_by_canonical[canonical_id] = segment_key

    errors = []
    if duplicates:
        errors.append("duplicate canonical ids detected")
    identified = len(segment_by_canonical)
    status = "verified" if identified >= 20 and not errors else "needs_review"
    return {
        "schemaVersion": 2,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "needsReviewReason": None if status == "verified" else "runtime_active_probe_incomplete_or_ambiguous",
        "rule": {
            "id": "recorded_segment_active_probe",
            "description": "Read recorded n-* order from timestamps, replay each recorded icon index, and identify the changed Scratch runtime target after drop.",
        },
        "source": {
            "sourceUrl": source_url,
            "outputFolder": str(project),
        },
        "validation": {
            "errors": errors,
            "recordedSegmentCount": len(segments),
            "identifiedCanonicalCount": identified,
            "duplicates": duplicates,
        },
        "segmentByCanonicalId": segment_by_canonical,
        "segmentIdentities": segment_identities,
        "recordedSegments": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Runtime-only smoke test for recorded segment character identity.")
    parser.add_argument("project", help="Output project folder containing manifest.json and timestamps.")
    parser.add_argument("--limit", type=int, default=0, help="Probe only the first N recorded segments.")
    parser.add_argument("--start", type=int, default=0, help="Skip the first N recorded segments.")
    parser.add_argument("--wait-after-drag", type=float, default=2.0)
    parser.add_argument("--candidate-radius", type=float, default=360.0)
    parser.add_argument("--reload-settle", type=float, default=8.0)
    parser.add_argument("--out", default=None, help="Output JSON path.")
    args = parser.parse_args()

    project = Path(args.project)
    timestamps = read_json(find_timestamps(project))
    source_url = source_url_from_manifest(project)
    segments = recorded_segments(timestamps)
    if args.start:
        segments = segments[args.start :]
    if args.limit:
        segments = segments[: args.limit]
    if not segments:
        raise RuntimeError("No recorded start_listen segments found.")

    output_path = Path(args.out) if args.out else project / "character_identity_map.runtime_smoke.json"
    rows: List[Dict[str, Any]] = []
    player: Optional[SprunkiGamePlayer] = None
    ws: Optional[websocket.WebSocket] = None
    try:
        player, ws = open_prepared_game(source_url)
        for index, segment in enumerate(segments):
            if index > 0:
                reload_prepared_game(player, ws, source_url, args.reload_settle)
            print(f"Probing {segment['segmentKey']} icon_index={segment['iconIndex']} ({index + 1}/{len(segments)})")
            row = inspect_one(
                ws,
                segment["segmentKey"],
                int(segment["iconIndex"]),
                wait_after_drag=args.wait_after_drag,
                radius=args.candidate_radius,
            )
            rows.append(row)
            print(f"  -> {row.get('status')} {row.get('canonicalId')} target={row.get('detectedTargetName')}")
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

    identity = build_identity(project, segments, rows, source_url)
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(identity, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(f"Wrote {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

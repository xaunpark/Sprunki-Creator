import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


CANONICAL_CHARACTERS: List[Tuple[str, str]] = [
    ("oren", "Oren"),
    ("raddy", "Raddy"),
    ("clukr", "Clukr"),
    ("fun_bot", "Fun Bot"),
    ("vineria", "Vineria"),
    ("gray", "Gray"),
    ("brud", "Brud"),
    ("garnold", "Garnold"),
    ("owakcx", "OWAKCX"),
    ("sky", "Sky"),
    ("mr_sun", "Mr. Sun"),
    ("durple", "Durple"),
    ("mr_tree", "Mr. Tree"),
    ("simon", "Simon"),
    ("tunner", "Tunner"),
    ("mr_fun_computer", "Mr. Fun Computer"),
    ("wenda", "Wenda"),
    ("pinki", "Pinki"),
    ("jevin", "Jevin"),
    ("black", "Black"),
]

CANONICAL_DISPLAY_NAMES = dict(CANONICAL_CHARACTERS)
CANONICAL_ORDER = [canonical_id for canonical_id, _ in CANONICAL_CHARACTERS]
IDENTITY_MAP_FILENAME = "character_identity_map.json"
IDENTITY_RULE_ID = "icon_costume_contiguous_01_to_20"


SCRATCH_VM_ICON_STATE_JS = r"""
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
          let fiber = el[reactKey]; let depth = 0;
          while (fiber && depth++ < 240) {
            const props = fiber.pendingProps || fiber.memoizedProps || fiber.props;
            if (props?.vm && props.vm.runtime) { window.__vm = props.vm; return props.vm; }
            fiber = fiber.return;
          }
        }
      }
      return null;
    }
    const vm = findVM();
    if (!vm) return resolve({
      ok:false,
      error:'VM_NOT_FOUND',
      title: document.title,
      href: location.href,
      readyState: document.readyState,
      canvasCount: document.querySelectorAll('canvas').length,
      bodyText: (document.body?.innerText || '').replace(/\s+/g, ' ').slice(0, 500)
    });
    const allTargets = vm.runtime.targets || [];
    let iconTargets = allTargets.filter(t => {
      const costumes = t.sprite?.costumes || [];
      const costume = costumes[t.currentCostume] || {};
      return t.visible && (/^Icons/.test(t.sprite?.name || '') || /^\d{1,2}-a(?:\b|-|_)?/i.test(costume.name || ''));
    });
    let icons = iconTargets.map((t, order) => {
      const costumes = t.sprite.costumes || [];
      const costume = costumes[t.currentCostume] || {};
      return {
        order,
        segmentKey: `n-${order + 1}`,
        iconTargetName: t.sprite.name,
        runtimeIndex: allTargets.indexOf(t),
        currentCostume: t.currentCostume,
        costumeName: costume.name || null,
        costumeAssetId: costume.assetId || null,
        x: t.x,
        y: t.y,
        size: t.size,
        visible: !!t.visible
      };
    });
    if (icons.length < 20) {
      const aggregateCandidates = allTargets.map((t) => {
        const costumes = t.sprite?.costumes || [];
        const standardCostumes = costumes
          .map((costume, costumeIndex) => ({costume, costumeIndex}))
          .filter(({costume}) => /^\d{1,2}-a(?:\b|-|_)?/i.test(costume.name || ''));
        const standardNumbers = new Set(
          standardCostumes
            .map(({costume}) => Number((costume.name || '').match(/^(\d{1,2})-a/i)?.[1]))
            .filter(number => number >= 1 && number <= 20)
        );
        return {target: t, standardCostumes, standardCount: standardNumbers.size};
      })
      .filter(row => /^Icons/.test(row.target.sprite?.name || '') && row.standardCount >= 20)
      .sort((a, b) => b.standardCount - a.standardCount || b.standardCostumes.length - a.standardCostumes.length);

      if (aggregateCandidates.length) {
        const selected = aggregateCandidates[0];
        const byNumber = new Map();
        for (const row of selected.standardCostumes) {
          const number = Number((row.costume.name || '').match(/^(\d{1,2})-a/i)?.[1]);
          if (!byNumber.has(number)) byNumber.set(number, row);
        }
        icons = Array.from(byNumber.entries())
          .sort((a, b) => a[0] - b[0])
          .map(([number, row], order) => ({
            order,
            segmentKey: `n-${order + 1}`,
            iconNumberHint: number,
            iconTargetName: selected.target.sprite?.name || null,
            runtimeIndex: allTargets.indexOf(selected.target),
            currentCostume: row.costumeIndex,
            costumeName: row.costume.name || null,
            costumeAssetId: row.costume.assetId || null,
            x: selected.target.x,
            y: selected.target.y,
            size: selected.target.size,
            visible: !!selected.target.visible,
            source: 'aggregate_icon_costume_list'
          }));
      }
    }
    resolve({
      ok:true,
      title: document.title,
      href: location.href,
      targetCount: allTargets.length,
      capturedAt: new Date().toISOString(),
      icons
    });
  } catch (e) {
    resolve({ok:false, error:String(e), stack:e.stack, title: document.title, href: location.href});
  }
});
"""


def parse_icon_number(costume_name: Optional[str]) -> Optional[int]:
    match = re.match(r"^(\d{1,2})-a(?:\b|-|_)?", costume_name or "", re.I)
    return int(match.group(1)) if match else None


def _enrich_icons(raw_icons: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    icons = []
    for order, icon in enumerate(raw_icons):
        copied = dict(icon)
        copied.setdefault("order", order)
        copied.setdefault("segmentKey", f"n-{order + 1}")
        copied["iconNumber"] = parse_icon_number(copied.get("costumeName"))
        icons.append(copied)
    return icons


def _select_best_icon_group(icons: List[Dict[str, Any]]) -> Tuple[Optional[str], List[Dict[str, Any]], List[Dict[str, Any]]]:
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for icon in icons:
        groups.setdefault(icon.get("iconTargetName") or "", []).append(icon)

    best_group_name: Optional[str] = None
    best_score = (-1, -1)
    for group_name, group_icons in groups.items():
        numbers = {icon.get("iconNumber") for icon in group_icons if icon.get("iconNumber") is not None}
        standard_count = len([number for number in numbers if 1 <= int(number) <= 20])
        score = (standard_count, len(numbers))
        if score > best_score:
            best_group_name = group_name
            best_score = score

    if best_score[0] == 20:
        selected = [
            icon
            for icon in groups.get(best_group_name or "", [])
            if icon.get("iconNumber") is not None and 1 <= int(icon["iconNumber"]) <= 20
        ]
    else:
        by_number: Dict[int, Dict[str, Any]] = {}
        for icon in icons:
            number = icon.get("iconNumber")
            if number is not None and 1 <= int(number) <= 20 and int(number) not in by_number:
                by_number[int(number)] = icon
        selected = [by_number[number] for number in range(1, 21) if number in by_number]
        if len(selected) == 20:
            best_group_name = "__visible_icon_costumes__"

    selected_ids = {id(icon) for icon in selected}
    standard_icons = sorted(selected, key=lambda icon: int(icon["iconNumber"]))
    extras = [icon for icon in icons if id(icon) not in selected_ids]
    return best_group_name, standard_icons, extras


def classify_needs_review_reason(
    icon_state: Dict[str, Any],
    icons: Optional[List[Dict[str, Any]]] = None,
    standard_icons: Optional[List[Dict[str, Any]]] = None,
) -> Optional[str]:
    error = str(icon_state.get("error") or icon_state.get("lastError") or "")
    body_text = str(icon_state.get("bodyText") or "")
    if re.search(r"page not found|couldn.?t find the page|404", body_text, re.I):
        return "page_not_found"
    if re.search(r"runtime\.evaluate|timeout", error, re.I):
        return "runtime_evaluate_timeout"
    if error == "VM_NOT_FOUND":
        return "vm_not_found"
    if icon_state.get("ok") and icon_state.get("targetCount") is not None and not (icons or []):
        return "vm_found_no_icon_candidates"
    if standard_icons is not None and len(standard_icons) != 20:
        return "icon_rule_mismatch"
    return None


def build_character_identity_map(
    icon_state: Dict[str, Any],
    output_folder: Optional[str] = None,
    source_url: Optional[str] = None,
    page_title: Optional[str] = None,
) -> Dict[str, Any]:
    icons = _enrich_icons(icon_state.get("icons") or [])
    best_group_name, standard_icons, extras = _select_best_icon_group(icons)

    errors: List[str] = []
    if len(standard_icons) != 20:
        errors.append(f"expected 20 standard icons in best group, found {len(standard_icons)}")

    icon_numbers = [icon.get("iconNumber") for icon in standard_icons]
    if icon_numbers != list(range(1, 21)):
        errors.append(f"expected iconNumber 1..20, found {icon_numbers}")
    needs_review_reason = classify_needs_review_reason(icon_state, icons, standard_icons) if errors else None

    standard_characters = []
    for (canonical_id, display_name), icon in zip(CANONICAL_CHARACTERS, standard_icons):
        standard_characters.append(
            {
                "canonicalId": canonical_id,
                "displayName": display_name,
                "segmentKey": icon.get("segmentKey"),
                "iconNumber": icon.get("iconNumber"),
                "costumeName": icon.get("costumeName"),
                "iconTargetName": icon.get("iconTargetName"),
                "runtimeIndex": icon.get("runtimeIndex"),
                "confidence": "high" if not errors else "needs_review",
                "source": IDENTITY_RULE_ID,
            }
        )

    excluded_segments = {}
    for icon in extras:
        segment_key = icon.get("segmentKey")
        if not segment_key:
            continue
        excluded_segments[segment_key] = {
            "segmentKey": segment_key,
            "iconNumber": icon.get("iconNumber"),
            "costumeName": icon.get("costumeName"),
            "iconTargetName": icon.get("iconTargetName"),
            "runtimeIndex": icon.get("runtimeIndex"),
            "reason": "outside_standard_icon_range_01_to_20",
        }

    source = {
        "sourceUrl": source_url or icon_state.get("href"),
        "pageTitle": page_title or icon_state.get("title"),
        "outputFolder": output_folder,
        "targetCount": icon_state.get("targetCount"),
        "bestIconGroup": best_group_name,
    }
    payload = {
        "schemaVersion": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "status": "verified" if not errors else "needs_review",
        "needsReviewReason": needs_review_reason,
        "rule": {
            "id": IDENTITY_RULE_ID,
            "description": "Use the visible icon group containing costumeName 01-a through 20-a as the 20 canonical Sprunki characters; exclude icons outside that range.",
        },
        "source": source,
        "validation": {
            "errors": errors,
            "visibleIconCount": len(icons),
            "standardCharacterCount": len(standard_characters),
            "excludedSegmentCount": len(excluded_segments),
            "needsReviewReason": needs_review_reason,
        },
        "segmentByCanonicalId": {
            row["canonicalId"]: row["segmentKey"]
            for row in standard_characters
            if row.get("segmentKey")
        },
        "standardCharacters": standard_characters,
        "standardBySegment": {
            row["segmentKey"]: row
            for row in standard_characters
            if row.get("segmentKey")
        },
        "excludedSegments": excluded_segments,
    }
    if not needs_review_reason:
        payload.pop("needsReviewReason", None)
        payload["validation"].pop("needsReviewReason", None)
    return payload


def write_character_identity_map(
    output_folder: str,
    icon_state: Dict[str, Any],
    source_url: Optional[str] = None,
    page_title: Optional[str] = None,
) -> Dict[str, Any]:
    payload = build_character_identity_map(
        icon_state,
        output_folder=output_folder,
        source_url=source_url,
        page_title=page_title,
    )
    Path(output_folder).mkdir(parents=True, exist_ok=True)
    output_path = Path(output_folder) / IDENTITY_MAP_FILENAME
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return payload


def load_character_identity_map(project_folder: str) -> Optional[Dict[str, Any]]:
    path = Path(project_folder) / IDENTITY_MAP_FILENAME
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def is_verified_identity_map(identity_map: Optional[Dict[str, Any]]) -> bool:
    return bool(
        identity_map
        and identity_map.get("status") == "verified"
        and len(identity_map.get("segmentByCanonicalId") or {}) >= 20
    )


def load_source_url_from_manifest(project_folder: str) -> Optional[str]:
    path = Path(project_folder) / "manifest.json"
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
        return (
            manifest.get("source", {}).get("value")
            or manifest.get("projectInfo", {}).get("sourceUrl")
        )
    except Exception:
        return None


def load_page_title_from_manifest(project_folder: str) -> Optional[str]:
    path = Path(project_folder) / "manifest.json"
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
        return manifest.get("projectInfo", {}).get("pageTitle")
    except Exception:
        return None

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from headless_coordinate_identity_smoke import (
    open_headless_snapshot,
    read_json,
    recorded_order,
    source_json_from_manifest,
    source_url_from_manifest,
    find_timestamps,
)


def identity_rows(identity: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for row in identity.get("standardCharacters") or []:
        rows.append(
            {
                "canonicalId": row.get("canonicalId"),
                "displayName": row.get("displayName"),
                "costumeName": row.get("costumeName"),
                "iconNumber": int(row.get("iconNumber") or 0),
                "iconTargetName": row.get("iconTargetName"),
                "section": "standard",
            }
        )
    for prop_name, value in (identity.get("excludedSegments") or {}).items():
        rows.append(
            {
                "canonicalId": None,
                "displayName": None,
                "costumeName": value.get("costumeName"),
                "iconNumber": int(value.get("iconNumber") or 0),
                "iconTargetName": value.get("iconTargetName"),
                "section": "excluded",
            }
        )
    return rows


def load_visible_coordinate_result(project: Path) -> Dict[str, str]:
    path = project / "character_identity_map.coordinate_smoke.json"
    if not path.exists():
        return {}
    data = read_json(path)
    out: Dict[str, str] = {}
    for row in data.get("matches") or []:
        segment = row.get("segmentKey")
        costume = (row.get("matchedRuntimeIcon") or {}).get("costumeName")
        canonical = row.get("canonicalId")
        if segment:
            out[segment] = canonical or f"extra:{costume}"
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Headless aggregate-order smoke for recorded source JSON icon order.")
    parser.add_argument("project")
    parser.add_argument("--timeout", type=float, default=80.0)
    parser.add_argument("--cookie-file", default=r"G:\VS-Project\Sprunki\cocrea_cookies.json")
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

    snapshot = open_headless_snapshot(
        source_url=source_url,
        cookie_file=args.cookie_file,
        timeout=args.timeout,
        min_runtime_icons=1,
        click_center=False,
    )
    runtime_icons = list(snapshot.get("icons", []) or [])
    if not snapshot.get("ok") or not runtime_icons:
        raise RuntimeError(f"Headless snapshot did not expose aggregate icon: {snapshot}")

    leading_costume = runtime_icons[0].get("costumeName")
    rows = identity_rows(identity)
    by_costume = {row.get("costumeName"): row for row in rows if row.get("costumeName")}
    ordered: List[Dict[str, Any]] = []
    if leading_costume in by_costume:
        ordered.append(by_costume[leading_costume])
    ordered.extend(sorted([row for row in rows if row.get("costumeName") != leading_costume], key=lambda row: row["iconNumber"]))
    ordered = ordered[: len(source_json.get("layout", {}).get("icons") or [])]

    recorded = recorded_order(timestamps)
    visible = load_visible_coordinate_result(project)
    matches = []
    segment_by_canonical: Dict[str, str] = {}
    diffs = []
    for index, source_icon in enumerate(source_json.get("layout", {}).get("icons") or []):
        inferred = ordered[index] if index < len(ordered) else {}
        segment = recorded.get(int(source_icon["index"]))
        canonical = inferred.get("canonicalId")
        value = canonical or f"extra:{inferred.get('costumeName')}"
        visible_value = visible.get(segment)
        if visible_value is not None and visible_value != value:
            diffs.append({"segmentKey": segment, "headless": value, "visible": visible_value})
        if segment and canonical:
            segment_by_canonical[canonical] = segment
        matches.append(
            {
                "segmentKey": segment,
                "iconIndex": int(source_icon["index"]),
                "sourcePixel": {"x": source_icon.get("x"), "y": source_icon.get("y")},
                "costumeName": inferred.get("costumeName"),
                "iconNumber": inferred.get("iconNumber"),
                "canonicalId": canonical,
                "displayName": inferred.get("displayName"),
                "visibleCoordinateValue": visible_value,
                "comparisonWithVisible": "same" if visible_value == value else ("missing_visible" if visible_value is None else "different"),
            }
        )

    output = {
        "schemaVersion": 2,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "status": "headless_aggregate_order_smoke",
        "rule": {
            "id": "source_json_order_plus_headless_aggregate_costume_order",
            "description": "Headless VM exposes aggregate Icons costume state; use source JSON recorded order and aggregate costume order, anchored by the headless visible aggregate costume.",
        },
        "source": {
            "outputFolder": str(project),
            "sourceJson": str(source_json_path),
            "timestamps": str(timestamps_path),
            "identityMap": str(identity_path),
            "sourceUrl": source_url,
            "leadingHeadlessCostume": leading_costume,
            "runtimeFilteredIconCount": len(runtime_icons),
        },
        "validation": {
            "sourceIconCount": len(source_json.get("layout", {}).get("icons") or []),
            "aggregateIdentityRows": len(rows),
            "mappedCanonicalCount": len(segment_by_canonical),
            "visibleComparisonCount": len(visible),
            "diffCount": len(diffs),
            "diffs": diffs,
        },
        "matches": matches,
        "segmentByCanonicalId": segment_by_canonical,
    }

    out_path = Path(args.out) if args.out else project / "character_identity_map.coordinate_smoke.headless_aggregate_order.json"
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(output, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    print(out_path)
    for row in matches:
        print(
            f"{row['segmentKey'] or '-':>4} iconIndex={row['iconIndex']:>2} "
            f"costume={row.get('costumeName')} -> {row.get('canonicalId') or 'extra'} "
            f"compare={row['comparisonWithVisible']}"
        )
    if diffs:
        print("DIFFS:", json.dumps(diffs, ensure_ascii=False))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

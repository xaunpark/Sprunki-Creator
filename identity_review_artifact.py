import html
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


REVIEW_SCHEMA_VERSION = 1
REVIEW_GENERATOR_VERSION = 1

CHARACTER_OPTIONS: List[Tuple[str, str]] = [
    ("extra", "Extra"),
    ("unknown", "Unknown"),
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


def _segment_sort_key(row: Dict[str, Any]) -> Tuple[int, Any]:
    segment_key = str(row.get("segmentKey") or "")
    try:
        prefix, number = segment_key.split("-", 1)
        if prefix == "n":
            return (0, int(number))
    except Exception:
        pass
    return (1, segment_key)


def _identity_value(row: Dict[str, Any]) -> str:
    if row.get("canonicalId"):
        return str(row["canonicalId"])
    status = str(row.get("status") or "").lower()
    if status == "extra":
        return "extra"
    return "unknown"


def _display_for_value(value: str) -> str:
    lookup = dict(CHARACTER_OPTIONS)
    return lookup.get(value, value or "Unknown")


def build_recorded_review_payload(identity: Dict[str, Any], project: Path) -> Dict[str, Any]:
    rows = []
    for index, row in enumerate(sorted(identity.get("segmentIdentities") or [], key=_segment_sort_key), start=1):
        value = _identity_value(row)
        status = row.get("status") or ("matched" if row.get("canonicalId") else "unknown")
        rows.append({
            "order": index,
            "segmentKey": row.get("segmentKey"),
            "autoValue": value,
            "autoLabel": _display_for_value(value),
            "selectedValue": value,
            "selectedLabel": _display_for_value(value),
            "status": status,
            "costumeName": row.get("costumeName"),
            "canonicalId": row.get("canonicalId"),
            "displayName": row.get("displayName"),
            "identityEvidence": row.get("identityEvidence"),
            "identityUsable": row.get("identityUsable", True),
            "identityUnusableReason": row.get("identityUnusableReason"),
        })

    return {
        "schemaVersion": REVIEW_SCHEMA_VERSION,
        "reviewGeneratorVersion": REVIEW_GENERATOR_VERSION,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "projectFolder": str(project),
        "identityStatus": identity.get("status"),
        "needsReviewReason": identity.get("needsReviewReason"),
        "identityGeneratedAt": identity.get("generatedAt"),
        "evidence": identity.get("evidence") or {},
        "validation": identity.get("validation") or {},
        "characters": [{"value": value, "label": label} for value, label in CHARACTER_OPTIONS],
        "rows": rows,
    }


def _classify_costume_value(costume_name: Optional[str]) -> Tuple[str, str]:
    import re

    match = re.match(r"^(\d{1,2})-a(?:\b|-|_)?", costume_name or "", re.I)
    number = int(match.group(1)) if match else None
    if number is not None and 1 <= number <= 20:
        value, label = CHARACTER_OPTIONS[number + 1]
        return value, label
    if number is not None:
        return "extra", "Extra"
    return "unknown", "Unknown"


def build_icon_order_review_payload(
    final_json: Dict[str, Any],
    sidecar: Dict[str, Any],
    json_file_path: Path,
    project_folder: Optional[Path] = None,
) -> Dict[str, Any]:
    rows_by_index = {
        int(row.get("iconIndex")): row
        for row in sidecar.get("iconIdentities") or []
        if row.get("iconIndex") is not None
    }
    rows = []
    layout_icons = final_json.get("layout", {}).get("icons") or []
    for order, icon in enumerate(layout_icons, start=1):
        icon_index = int(icon.get("index", order - 1))
        row = rows_by_index.get(icon_index, {})
        value, label = _classify_costume_value(row.get("costumeName"))
        rows.append({
            "order": order,
            "segmentKey": f"n-{order}",
            "iconIndex": icon_index,
            "autoValue": value,
            "autoLabel": label,
            "selectedValue": value,
            "selectedLabel": label,
            "status": row.get("status") or value,
            "costumeName": row.get("costumeName"),
            "canonicalId": value if value not in {"extra", "unknown"} else None,
            "displayName": label if value not in {"extra", "unknown"} else None,
            "identityEvidence": row.get("source") or "json_generator_sidecar",
            "identityUsable": value != "unknown",
            "identityUnusableReason": None if value != "unknown" else "missing_costume_name",
        })

    project = project_folder or json_file_path.parent
    return {
        "schemaVersion": REVIEW_SCHEMA_VERSION,
        "reviewGeneratorVersion": REVIEW_GENERATOR_VERSION,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "projectFolder": str(project),
        "identityStatus": "source_json_preview",
        "needsReviewReason": None,
        "identityGeneratedAt": sidecar.get("generatedAt"),
        "evidence": {
            "sourceJson": str(json_file_path),
            "sourceJsonHash": sidecar.get("sourceJsonHash"),
            "iconIdentitySource": str(json_file_path.with_suffix(".icon_identity.json")),
            "note": "Preview generated during json_generator before recorded timestamps exist. Segment keys follow layout icon order.",
        },
        "validation": {
            "sourceIconCount": len(layout_icons),
            "timestampSegmentCount": None,
            "reviewMode": "icon_order_preview",
        },
        "characters": [{"value": value, "label": label} for value, label in CHARACTER_OPTIONS],
        "rows": rows,
    }


def _render_html(payload: Dict[str, Any]) -> str:
    payload_json = json.dumps(payload, ensure_ascii=False)
    title = f"Recorded Identity Review - {Path(payload.get('projectFolder') or '').name}"
    escaped_title = html.escape(title)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escaped_title}</title>
  <style>
    body {{ font-family: Segoe UI, Arial, sans-serif; margin: 24px; background: #f6f7f9; color: #1f2933; }}
    h1 {{ font-size: 22px; margin: 0 0 8px; }}
    .meta {{ color: #52616b; margin-bottom: 18px; line-height: 1.5; }}
    .toolbar {{ display: flex; gap: 10px; align-items: center; margin: 14px 0 18px; flex-wrap: wrap; }}
    .notice {{ background: #fff8db; border: 1px solid #f3d37a; padding: 12px; border-radius: 6px; margin: 12px 0 18px; line-height: 1.5; }}
    button {{ border: 1px solid #bac4ce; background: #fff; padding: 8px 12px; border-radius: 6px; cursor: pointer; }}
    button.primary {{ background: #146ef5; color: white; border-color: #146ef5; }}
    table {{ width: 100%; border-collapse: collapse; background: white; border: 1px solid #d9e1e8; }}
    th, td {{ border-bottom: 1px solid #e6ebf0; padding: 8px 10px; text-align: left; font-size: 14px; }}
    th {{ background: #edf2f7; position: sticky; top: 0; z-index: 1; }}
    select:disabled {{ color: #52616b; background: #edf2f7; }}
    .badge {{ display: inline-block; padding: 2px 7px; border-radius: 999px; background: #e6ebf0; font-size: 12px; }}
    .changed {{ background: #fff8db; }}
    .needs {{ background: #ffe8e6; }}
    code {{ background: #edf2f7; padding: 2px 4px; border-radius: 4px; }}
    .cmd {{ white-space: pre-wrap; background: #111827; color: #f9fafb; padding: 12px; border-radius: 6px; margin-top: 12px; }}
  </style>
</head>
<body>
  <h1>{escaped_title}</h1>
  <div class="meta">
    Project: <code id="project"></code><br>
    Identity status: <span id="status"></span> | Reason: <span id="reason"></span><br>
  </div>
  <div class="notice">
    After editing rows, click <b>Save manual overrides (download JSON)</b> below.
    Do <b>not</b> press Ctrl+S and do <b>not</b> save this HTML file.
    Save or move the downloaded file to the project folder as:
    <code id="overridePath"></code>
  </div>
  <div class="toolbar">
    <button class="primary" onclick="downloadOverrides()">Save manual overrides (download JSON)</button>
    <button onclick="unlockAll()">Unlock all rows</button>
    <button onclick="resetAll()">Reset to auto</button>
    <span id="changedCount" class="badge">0 changed</span>
  </div>
  <table>
    <thead>
      <tr>
        <th>Order</th>
        <th>Segment</th>
        <th>Auto</th>
        <th>Manual selection</th>
        <th>Costume</th>
        <th>Status</th>
        <th>Action</th>
      </tr>
    </thead>
    <tbody id="rows"></tbody>
  </table>
  <h2>Run after the JSON file is in the project folder</h2>
  <div class="meta">Only run this after <code>recorded_identity_overrides.json</code> exists in the project folder.</div>
  <div class="cmd" id="command"></div>

<script>
const REVIEW_DATA = {payload_json};
const rows = REVIEW_DATA.rows || [];
const characters = REVIEW_DATA.characters || [];

function optionHtml(selected) {{
  return characters.map(c => `<option value="${{c.value}}" ${{c.value === selected ? 'selected' : ''}}>${{c.label}}</option>`).join('');
}}

function render() {{
  document.getElementById('project').textContent = REVIEW_DATA.projectFolder || '';
  document.getElementById('status').textContent = REVIEW_DATA.identityStatus || 'n/a';
  document.getElementById('reason').textContent = REVIEW_DATA.needsReviewReason || 'n/a';
  document.getElementById('overridePath').textContent = `${{REVIEW_DATA.projectFolder || ''}}\\\\recorded_identity_overrides.json`;
  document.getElementById('command').textContent = `python G:\\\\VS-Project\\\\Sprunki\\\\recorded_identity_resolver.py --project "${{REVIEW_DATA.projectFolder}}" --force`;
  const tbody = document.getElementById('rows');
  tbody.innerHTML = '';
  rows.forEach((row, idx) => {{
    const tr = document.createElement('tr');
    if (row.status === 'unknown' || row.identityUsable === false) tr.classList.add('needs');
    tr.dataset.index = idx;
    tr.innerHTML = `
      <td>${{row.order}}</td>
      <td><code>${{row.segmentKey || ''}}</code></td>
      <td>${{row.autoLabel || 'Unknown'}}</td>
      <td><select id="sel-${{idx}}" disabled onchange="markChanged(${{idx}})">${{optionHtml(row.selectedValue)}}</select></td>
      <td><code>${{row.costumeName || ''}}</code></td>
      <td><span class="badge">${{row.status || ''}}</span></td>
      <td><button onclick="unlockRow(${{idx}})">Override row</button></td>`;
    tbody.appendChild(tr);
  }});
  updateChangedCount();
}}

function unlockRow(idx) {{
  document.getElementById(`sel-${{idx}}`).disabled = false;
}}

function unlockAll() {{
  rows.forEach((_, idx) => document.getElementById(`sel-${{idx}}`).disabled = false);
}}

function resetAll() {{
  rows.forEach((row, idx) => {{
    const select = document.getElementById(`sel-${{idx}}`);
    select.value = row.autoValue || 'unknown';
    select.disabled = true;
    document.querySelector(`tr[data-index="${{idx}}"]`).classList.remove('changed');
  }});
  updateChangedCount();
}}

function markChanged(idx) {{
  const row = rows[idx];
  const select = document.getElementById(`sel-${{idx}}`);
  const changed = select.value !== (row.autoValue || 'unknown');
  document.querySelector(`tr[data-index="${{idx}}"]`).classList.toggle('changed', changed);
  updateChangedCount();
}}

function updateChangedCount() {{
  let count = 0;
  rows.forEach((row, idx) => {{
    const select = document.getElementById(`sel-${{idx}}`);
    if (select && select.value !== (row.autoValue || 'unknown')) count += 1;
  }});
  document.getElementById('changedCount').textContent = `${{count}} changed`;
}}

function downloadOverrides() {{
  const segmentOverrides = [];
  rows.forEach((row, idx) => {{
    const value = document.getElementById(`sel-${{idx}}`).value;
    if (value === (row.autoValue || 'unknown')) return;
    const override = {{
      segmentKey: row.segmentKey,
      reason: 'manual_review'
    }};
    if (value === 'extra') {{
      override.status = 'extra';
      override.canonicalId = null;
    }} else if (value === 'unknown') {{
      override.status = 'unknown';
      override.canonicalId = null;
    }} else {{
      override.status = 'matched';
      override.canonicalId = value;
    }}
    segmentOverrides.push(override);
  }});
  const payload = {{
    schemaVersion: 1,
    rule: 'manual_segment_identity_override',
    generatedAt: new Date().toISOString(),
    projectFolder: REVIEW_DATA.projectFolder,
    segmentOverrides
  }};
  if (segmentOverrides.length === 0) {{
    const ok = confirm('No rows were changed. Download an empty overrides file anyway?');
    if (!ok) return;
  }}
  const blob = new Blob([JSON.stringify(payload, null, 2) + "\\n"], {{type: 'application/json'}});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'recorded_identity_overrides.json';
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}}

render();
</script>
</body>
</html>
"""


def write_recorded_identity_review(project: Path, identity: Dict[str, Any]) -> Dict[str, Path]:
    payload = build_recorded_review_payload(identity, project)
    json_path = project / "recorded_identity_review.json"
    html_path = project / "recorded_identity_review.html"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    html_path.write_text(_render_html(payload), encoding="utf-8")
    return {"json": json_path, "html": html_path}


def write_icon_order_identity_review(
    final_json: Dict[str, Any],
    sidecar: Dict[str, Any],
    json_file_path: Path,
    project_folder: Optional[Path] = None,
) -> Dict[str, Path]:
    payload = build_icon_order_review_payload(final_json, sidecar, json_file_path, project_folder)
    root = json_file_path.with_suffix("")
    json_path = root.with_name(root.name + ".icon_identity_review.json")
    html_path = root.with_name(root.name + ".icon_identity_review.html")
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    html_path.write_text(_render_html(payload), encoding="utf-8")
    return {"json": json_path, "html": html_path}

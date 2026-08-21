"""Read-only OBS/Chrome/session monitor for diagnosing intermittent black recordings.

Run this in a second terminal while the creator workflow is running.  The
monitor never starts/stops recording and never changes OBS settings.  It polls:

* OBS scene, source kind/settings, enabled state and transform
* OBS record status and current output file size
* Scene/source screenshots and simple pixel statistics
* OBS, Chrome and Python process/session information
* Chrome DevTools port 9222 and open targets
* ``quser`` output and recent OBS log lines

Example (from the deployed project directory):

    .\\venv\\Scripts\\python.exe .\\monitor_obs_diagnostics.py \\
        --output-dir D:\\OUTPUT\\obs_diagnostics \\
        --interval 2

Stop with Ctrl+C.  The output directory contains ``samples.jsonl`` (one JSON
record per poll), ``monitor.log`` (human-readable summary), the latest PNGs,
and timestamped PNG snapshots when the state changes, the source is black, or
the snapshot interval elapses.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image

from validate_obs_capture import (
    analyze_image,
    connect_obs,
    decode_obs_image,
    load_config,
    obs_request,
)


DEFAULT_SOURCE = "Sprunki Capture"


def timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def file_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def write_jsonl(path: Path, record: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def run_command(command: list[str], timeout: float = 5.0) -> tuple[int, str, str]:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
        )
        return completed.returncode, completed.stdout.strip(), completed.stderr.strip()
    except Exception as exc:  # pragma: no cover - platform/runtime dependent
        return -1, "", str(exc)


def query_users() -> dict[str, Any]:
    code, output, error = run_command(["quser"])
    return {
        "returnCode": code,
        "output": output,
        "error": error or None,
    }


def query_processes() -> dict[str, Any]:
    """Get process/session/user data without relying on the current shell."""
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if not powershell:
        return {"error": "powershell.exe not found"}

    # Keep the query independent of the shell used to launch this script.
    script = r"""
$names = @('obs64.exe','chrome.exe','python.exe')
Get-CimInstance Win32_Process |
  Where-Object { $names -contains $_.Name } |
  ForEach-Object {
    $owner = $null
    try { $owner = Invoke-CimMethod -InputObject $_ -MethodName GetOwner -ErrorAction Stop } catch {}
    $user = $null
    if ($owner -and $owner.ReturnValue -eq 0) { $user = "$($owner.Domain)\$($owner.User)" }
    [pscustomobject]@{
      name = $_.Name
      processId = $_.ProcessId
      sessionId = $_.SessionId
      user = $user
      commandLine = $_.CommandLine
    }
  } | ConvertTo-Json -Compress
"""
    code, output, error = run_command(
        [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        timeout=8.0,
    )
    if code != 0:
        return {"returnCode": code, "error": error or output}
    if not output:
        return {"returnCode": code, "processes": []}
    try:
        parsed = json.loads(output)
        if isinstance(parsed, dict):
            parsed = [parsed]
        return {"returnCode": code, "processes": parsed}
    except json.JSONDecodeError:
        return {"returnCode": code, "raw": output, "error": error or "PowerShell JSON parse failed"}


def query_chrome_debug() -> dict[str, Any]:
    result: dict[str, Any] = {
        "host": "127.0.0.1",
        "port": 9222,
        "portOpen": False,
        "version": None,
        "targets": [],
        "error": None,
    }
    try:
        with socket.create_connection(("127.0.0.1", 9222), timeout=1.0):
            result["portOpen"] = True
    except OSError as exc:
        result["error"] = f"port closed: {exc}"
        return result

    def get_json(path: str) -> Any:
        request = urllib.request.Request(f"http://127.0.0.1:9222{path}")
        with urllib.request.urlopen(request, timeout=2.0) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))

    try:
        result["version"] = get_json("/json/version")
        targets = get_json("/json/list")
        result["targets"] = [
            {
                "type": item.get("type"),
                "title": item.get("title"),
                "url": item.get("url"),
                "id": item.get("id"),
            }
            for item in targets
            if isinstance(item, dict)
        ]
    except (OSError, ValueError, urllib.error.URLError) as exc:
        result["error"] = f"debug endpoint error: {exc}"
    return result


def recent_obs_log() -> dict[str, Any]:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return {"error": "APPDATA is not set"}
    log_dir = Path(appdata) / "obs-studio" / "logs"
    if not log_dir.exists():
        return {"path": str(log_dir), "error": "OBS log directory not found"}

    files = sorted(log_dir.glob("*.txt"), key=lambda item: item.stat().st_mtime, reverse=True)
    if not files:
        return {"path": str(log_dir), "error": "No OBS log files found"}

    latest = files[0]
    try:
        lines = latest.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return {"path": str(latest), "error": str(exc)}

    interesting_words = (
        "error",
        "failed",
        "capture",
        "render",
        "gpu",
        "black",
        "monitor",
        "window",
        "encoder",
        "dropped",
    )
    matching = [line for line in lines if any(word in line.lower() for word in interesting_words)]
    return {
        "path": str(latest),
        "lastWriteTime": datetime.fromtimestamp(latest.stat().st_mtime).astimezone().isoformat(timespec="seconds"),
        "matchingTail": matching[-30:],
    }


def source_screenshot(ws: Any, source_name: str, path: Path) -> dict[str, Any]:
    response = obs_request(ws, "GetSourceScreenshot", {
        "sourceName": source_name,
        "imageFormat": "png",
        "imageWidth": 1280,
        "imageHeight": 720,
        "imageCompressionQuality": 100,
    })
    image = decode_obs_image(response["imageData"])
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    analysis = analyze_image(image)
    analysis["sha256"] = hashlib.sha256(image.resize((160, 90)).tobytes()).hexdigest()
    analysis["path"] = str(path)
    return analysis


def save_snapshot(path: Path, stamp: str) -> str | None:
    if not path.exists():
        return None
    snapshot = path.with_name(f"{path.stem}_{stamp}{path.suffix}")
    shutil.copy2(path, snapshot)
    return str(snapshot)


def obs_snapshot(
    config: dict[str, Any],
    source_name: str,
    output_dir: Path,
    capture_images: bool,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "obs": {"connected": False},
        "screenshots": {},
    }
    ws = None
    try:
        ws = connect_obs(config)
        ws.settimeout(6.0)
        result["obs"]["connected"] = True

        version = obs_request(ws, "GetVersion")
        current_scene = obs_request(ws, "GetCurrentProgramScene").get("currentProgramSceneName")
        scene_items = obs_request(ws, "GetSceneItemList", {"sceneName": current_scene}).get("sceneItems", [])
        source_item = next((item for item in scene_items if item.get("sourceName") == source_name), None)

        result["obs"].update({
            "version": version,
            "currentScene": current_scene,
            "sceneItems": [
                {
                    "sourceName": item.get("sourceName"),
                    "sceneItemId": item.get("sceneItemId"),
                    "sourceType": item.get("inputKind") or item.get("sourceType"),
                    "sceneItemEnabled": item.get("sceneItemEnabled"),
                }
                for item in scene_items
            ],
            "sourcePresent": bool(source_item),
        })

        if source_item:
            item_id = source_item.get("sceneItemId")
            result["obs"]["source"] = {
                "sceneItemId": item_id,
                "enabled": obs_request(ws, "GetSceneItemEnabled", {
                    "sceneName": current_scene,
                    "sceneItemId": item_id,
                }).get("sceneItemEnabled"),
                "transform": obs_request(ws, "GetSceneItemTransform", {
                    "sceneName": current_scene,
                    "sceneItemId": item_id,
                }).get("sceneItemTransform"),
                "settings": obs_request(ws, "GetInputSettings", {
                    "inputName": source_name,
                }),
            }

        record_status = obs_request(ws, "GetRecordStatus")
        output_path = record_status.get("outputPath")
        output_file = Path(output_path) if output_path else None
        result["obs"]["recording"] = {
            **record_status,
            "outputFileExists": bool(output_file and output_file.exists()),
            "outputFileSizeBytes": output_file.stat().st_size if output_file and output_file.exists() else None,
        }

        if capture_images:
            for label, name in (("scene", current_scene), ("source", source_name)):
                if not name or (label == "source" and not source_item):
                    continue
                latest_path = output_dir / f"latest_{label}.png"
                try:
                    analysis = source_screenshot(ws, name, latest_path)
                    result["screenshots"][label] = analysis
                except Exception as exc:
                    result["screenshots"][label] = {"error": str(exc)}
    except Exception as exc:
        result["obs"]["error"] = str(exc)
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
    return result


def signature(snapshot: dict[str, Any]) -> str:
    obs = snapshot.get("obs", {})
    source = obs.get("source", {})
    transform = source.get("transform", {}) or {}
    return json.dumps({
        "connected": obs.get("connected"),
        "scene": obs.get("currentScene"),
        "sourcePresent": obs.get("sourcePresent"),
        "sourceEnabled": source.get("enabled"),
        "sourceKind": (source.get("settings") or {}).get("inputKind"),
        "crop": {key: transform.get(key) for key in (
            "cropLeft", "cropTop", "cropRight", "cropBottom", "scaleX", "scaleY", "positionX", "positionY"
        )},
        "recording": (obs.get("recording") or {}).get("outputActive"),
    }, sort_keys=True)


def human_summary(snapshot: dict[str, Any]) -> str:
    obs = snapshot.get("obs", {})
    source = obs.get("source", {})
    settings = source.get("settings") or {}
    input_kind = settings.get("inputKind") or "?"
    transform = source.get("transform") or {}
    scene_image = snapshot.get("screenshots", {}).get("scene", {})
    source_image = snapshot.get("screenshots", {}).get("source", {})
    return (
        f"OBS={'OK' if obs.get('connected') else 'FAIL'} "
        f"scene={obs.get('currentScene', '?')} "
        f"source={'YES' if obs.get('sourcePresent') else 'NO'} "
        f"enabled={source.get('enabled', '?')} kind={input_kind} "
        f"crop=({transform.get('cropLeft', '?')},{transform.get('cropTop', '?')},"
        f"{transform.get('cropRight', '?')},{transform.get('cropBottom', '?')}) "
        f"recording={(obs.get('recording') or {}).get('outputActive', '?')} "
        f"sceneBlack={scene_image.get('looksBlack', '?')} "
        f"sourceBlack={source_image.get('looksBlack', '?')} "
        f"chromePort={'OPEN' if snapshot.get('chrome', {}).get('portOpen') else 'CLOSED'}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only OBS/Chrome diagnostic monitor")
    parser.add_argument("--source", default=DEFAULT_SOURCE, help=f"OBS source name (default: {DEFAULT_SOURCE})")
    parser.add_argument("--interval", type=float, default=2.0, help="Seconds between polls (default: 2)")
    parser.add_argument("--snapshot-interval", type=float, default=10.0, help="Seconds between timestamped PNG snapshots (default: 10)")
    parser.add_argument("--output-dir", default=None, help="Diagnostic output directory")
    parser.add_argument("--no-screenshots", action="store_true", help="Do not request OBS screenshots")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.interval <= 0:
        raise SystemExit("--interval must be greater than zero")

    config = load_config()
    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        output_dir = Path("obs_diagnostics") / datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir.mkdir(parents=True, exist_ok=True)

    samples_path = output_dir / "samples.jsonl"
    monitor_log = output_dir / "monitor.log"
    (output_dir / "README.txt").write_text(
        "Read-only diagnostic capture. Stop with Ctrl+C.\n"
        "samples.jsonl contains one JSON record per poll.\n"
        "latest_scene.png/latest_source.png are overwritten on each screenshot poll.\n",
        encoding="utf-8",
    )

    print(f"OBS diagnostic monitor started: {timestamp()}")
    print(f"Source: {args.source}")
    print(f"Output: {output_dir.resolve()}")
    print("Read-only: no StartRecord/StopRecord/Set* requests are sent.")
    print("Press Ctrl+C to stop.\n")

    previous_signature = None
    previous_hashes: dict[str, str] = {}
    last_snapshot = 0.0
    poll_count = 0

    try:
        while True:
            started = time.monotonic()
            poll_count += 1
            snapshot: dict[str, Any] = {
                "timestamp": timestamp(),
                "poll": poll_count,
                "processes": query_processes(),
                "quser": query_users(),
                "chrome": query_chrome_debug(),
                "obsLog": recent_obs_log(),
            }

            # Request the latest pixels on every poll so a short black interval
            # cannot be missed. Timestamped copies are created only for events.
            capture_images = not args.no_screenshots
            obs_data = obs_snapshot(config, args.source, output_dir, capture_images)
            snapshot.update(obs_data)

            current_signature = signature(snapshot)
            source_image = snapshot.get("screenshots", {}).get("source", {})
            scene_image = snapshot.get("screenshots", {}).get("scene", {})
            source_hash = source_image.get("sha256")
            scene_hash = scene_image.get("sha256")
            changed_images = {
                "source": bool(source_hash and source_hash != previous_hashes.get("source")),
                "scene": bool(scene_hash and scene_hash != previous_hashes.get("scene")),
            }
            snapshot["imageChanges"] = changed_images
            state_changed = previous_signature is not None and current_signature != previous_signature
            black_detected = bool(source_image.get("looksBlack") or scene_image.get("looksBlack"))
            periodic_snapshot = last_snapshot == 0.0 or time.monotonic() - last_snapshot >= args.snapshot_interval
            snapshot["stateChanged"] = state_changed

            # Keep only the latest image for normal polls. Preserve a
            # timestamped evidence image when something important happens.
            if capture_images and (periodic_snapshot or state_changed or black_detected):
                stamp = file_stamp()
                for label in ("scene", "source"):
                    image_info = snapshot.get("screenshots", {}).get(label, {})
                    latest_path = output_dir / f"latest_{label}.png"
                    saved_path = save_snapshot(latest_path, stamp)
                    if saved_path:
                        image_info["snapshotPath"] = saved_path

            write_jsonl(samples_path, snapshot)

            line = f"[{snapshot['timestamp']}] {human_summary(snapshot)}"
            if snapshot.get("stateChanged"):
                line += " STATE_CHANGED"
            if source_image.get("looksBlack") or scene_image.get("looksBlack"):
                line += " BLACK_DETECTED"
            if snapshot.get("obs", {}).get("error"):
                line += f" OBS_ERROR={snapshot['obs']['error']}"
            print(line, flush=True)
            with monitor_log.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")

            previous_signature = current_signature
            if source_hash:
                previous_hashes["source"] = source_hash
            if scene_hash:
                previous_hashes["scene"] = scene_hash
            if periodic_snapshot:
                last_snapshot = time.monotonic()

            elapsed = time.monotonic() - started
            time.sleep(max(0.0, args.interval - elapsed))
    except KeyboardInterrupt:
        print(f"\nStopped. Diagnostic files are in: {output_dir.resolve()}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

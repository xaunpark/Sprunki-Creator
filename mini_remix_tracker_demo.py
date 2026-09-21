"""Local Tracker -> Creator canary for the independent mini-remix workflow.

The coordinate constants in this file are the only AI-assisted part of the
demo: they were read from the supplied 1920x1080 screenshot.  Everything
after the fake Tracker hands the queue payload to ``CreatorService`` is the
normal production code path.

Run a payload-only check with::

    python mini_remix_tracker_demo.py

Run the real Creator canary with::

    python mini_remix_tracker_demo.py --run

The real run uses the OBS and Chrome settings already present in config.json.
It does not call a separate AI/browser automation layer after the queue claim.
"""

from __future__ import annotations

import argparse
import copy
import json
import logging
import threading
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlsplit

from manual_prepare_timeline import calculate_prepare_safety_timeline_ms
from manual_profile_v3 import (
    DEFAULT_EXPECTED_VIEWPORT,
    profile_sha256,
    validate_v3_content,
)
from mini_remix_workflow import (
    MiniRemixRequest,
    resolve_center_slot_indices,
)


MOD_URL = "https://www.cocrea.world/@Catt/SprunkeTheDefinitivePhase5"
VIEWPORT_WIDTH = int(DEFAULT_EXPECTED_VIEWPORT["width"])
VIEWPORT_HEIGHT = int(DEFAULT_EXPECTED_VIEWPORT["height"])

# Pixel centers read from the supplied fullscreen screenshot.  The visible
# panel has ten icons per row.  Marker indices are row-major, 0..19.
ICON_PIXEL_CENTERS: Tuple[Tuple[int, int], ...] = (
    (452, 819),
    (567, 819),
    (682, 819),
    (798, 819),
    (913, 819),
    (1028, 819),
    (1143, 819),
    (1258, 819),
    (1373, 819),
    (1488, 819),
    (510, 934),
    (625, 934),
    (740, 934),
    (855, 934),
    (970, 934),
    (1085, 934),
    (1200, 934),
    (1316, 934),
    (1431, 934),
    (1546, 934),
)

# The nine drop targets are left-to-right.  Their y values follow the center
# of each visible Polo target in the supplied screenshot/runtime scan.
SLOT_PIXEL_CENTERS: Tuple[Tuple[int, int], ...] = (
    (251, 532),
    (438, 514),
    (625, 523),
    (812, 515),
    (999, 528),
    (1186, 499),
    (1373, 526),
    (1560, 499),
    (1747, 500),
)

# This is the previous conversation's example combo expressed only as marker
# indices and in left-to-right placement order.  No character name is sent to
# Creator.  Change this list when another combo is needed.
DEFAULT_ICON_INDICES: Tuple[int, ...] = (0, 10, 7)

# Creator's existing normal entry path already performs Run, the game's
# Fullscreen control and F11.  With a queue profile attached, the old blind
# intro path is intentionally bypassed; these are the existing center clicks
# represented as the profile's prepare timeline.
PREPARE_CENTER_CLICK_COUNT = 10
PREPARE_CENTER_CLICK_DELAY_MS = 500
PREPARE_CENTER_PRESS_MS = 50
PREPARE_END_DELAY_MS = 500

DEMO_QUEUE_ID = 990001
DEMO_PROFILE_ID = 990001
DEMO_PROFILE_REVISION = 1
DEMO_PROFILE_REVISION_ID = 990001


def _normalized(value: int, extent: int) -> float:
    return round(float(value) / float(extent), 9)


def _normalized_markers(points: Sequence[Tuple[int, int]]) -> List[Dict[str, Any]]:
    return [
        {
            "index": index,
            "x": _normalized(x, VIEWPORT_WIDTH),
            "y": _normalized(y, VIEWPORT_HEIGHT),
        }
        for index, (x, y) in enumerate(points)
    ]


def build_demo_profile_content(*, created_at: Optional[str] = None) -> Dict[str, Any]:
    """Build the immutable v3 snapshot that a real Tracker would store."""

    actions: List[Dict[str, Any]] = []
    for action_index in range(PREPARE_CENTER_CLICK_COUNT):
        actions.append(
            {
                "type": "click",
                "x": 0.5,
                "y": 0.5,
                "delayMs": 0 if action_index == 0 else PREPARE_CENTER_CLICK_DELAY_MS,
                "pressDurationMs": PREPARE_CENTER_PRESS_MS,
            }
        )

    duration_ms = calculate_prepare_safety_timeline_ms(
        actions,
        PREPARE_END_DELAY_MS,
    )
    content: Dict[str, Any] = {
        "version": 3,
        "canonicalUrl": MOD_URL,
        "urlKey": "Catt/SprunkeTheDefinitivePhase5",
        "coordinateSpace": "viewport_normalized",
        "capture": {
            "createdAt": created_at or datetime.now(timezone.utc).isoformat(),
            "surface": {"type": "browser_viewport"},
            "viewport": {
                "width": VIEWPORT_WIDTH,
                "height": VIEWPORT_HEIGHT,
            },
        },
        "layout": {
            "icons": _normalized_markers(ICON_PIXEL_CENTERS),
            "slots": _normalized_markers(SLOT_PIXEL_CENTERS),
        },
        "prepareActions": actions,
        "prepareTiming": {
            "durationMs": duration_ms,
            "endDelayMs": PREPARE_END_DELAY_MS,
        },
        "settings": {"gameControlType": "unknown"},
        "notes": "Coordinates captured from the supplied 1920x1080 fullscreen reference.",
    }
    validate_v3_content(content)
    return content


def build_demo_job(
    *,
    icon_indices: Sequence[int] = DEFAULT_ICON_INDICES,
    queue_id: int = DEMO_QUEUE_ID,
) -> Dict[str, Any]:
    """Create the exact job object returned by the fake Tracker claim."""

    selected_indices = [int(value) for value in icon_indices]
    if not selected_indices:
        raise ValueError("At least one icon index is required.")
    if len(selected_indices) != len(set(selected_indices)):
        raise ValueError("Demo icon indices must be unique.")
    if any(index < 0 or index >= len(ICON_PIXEL_CENTERS) for index in selected_indices):
        raise ValueError(
            f"Demo icon indices must be in 0..{len(ICON_PIXEL_CENTERS) - 1}."
        )

    content = build_demo_profile_content()
    envelope = {
        "id": DEMO_PROFILE_ID,
        "revision": DEMO_PROFILE_REVISION,
        "revision_id": DEMO_PROFILE_REVISION_ID,
        "sha256": profile_sha256(content),
        "content": content,
    }
    return {
        "id": int(queue_id),
        "mod_title": "Sprunke Phase 5 Definitive - mini-remix canary",
        "mod_game_url": MOD_URL,
        "production_mode": "mini_remix",
        "mini_remix": {"icon_indices": selected_indices},
        "manual_profile_revision_id": DEMO_PROFILE_REVISION_ID,
        "manual_profile": envelope,
    }


class DemoTrackerState:
    """Small in-memory Tracker contract used only by the local canary."""

    def __init__(self, job: Mapping[str, Any]) -> None:
        self.job = copy.deepcopy(dict(job))
        self.lock = threading.RLock()
        self.claimed = False
        self.register_payload: Optional[Dict[str, Any]] = None
        self.heartbeat_payloads: List[Dict[str, Any]] = []
        self.progress_payloads: List[Dict[str, Any]] = []
        self.status_payloads: List[Dict[str, Any]] = []
        self.terminal_event = threading.Event()

    def claim(self, payload: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
        with self.lock:
            capabilities = payload.get("capabilities")
            supports_mini = isinstance(capabilities, Mapping) and bool(
                capabilities.get("mini_remix_v1")
            )
            if self.claimed or not supports_mini:
                return None
            self.claimed = True
            return copy.deepcopy(self.job)

    def record_terminal_if_needed(self, payload: Mapping[str, Any]) -> None:
        status = str(payload.get("status") or "").lower()
        if payload.get("final") is True or status in {
            "completed",
            "failed",
            "cancelled",
            "canceled",
        }:
            self.terminal_event.set()


class _DemoTrackerHandler(BaseHTTPRequestHandler):
    server_version = "MiniRemixDemoTracker/1.0"

    @property
    def state(self) -> DemoTrackerState:
        return self.server.demo_state  # type: ignore[attr-defined]

    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        if not raw:
            return {}
        decoded = json.loads(raw.decode("utf-8"))
        return decoded if isinstance(decoded, dict) else {}

    def _send_json(self, payload: Mapping[str, Any], status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802 - stdlib HTTP handler API
        path = urlsplit(self.path).path
        payload = self._read_json()
        if path == "/api/creators/register":
            with self.state.lock:
                self.state.register_payload = payload
            self._send_json({"status": "success"})
            return
        if path == "/api/creators/heartbeat":
            with self.state.lock:
                self.state.heartbeat_payloads.append(payload)
            self._send_json({"status": "success"})
            return
        if path == "/api/queue/next":
            job = self.state.claim(payload)
            self._send_json({"status": "success", "job": job})
            return
        if path.startswith("/api/queue/progress/"):
            with self.state.lock:
                self.state.progress_payloads.append(payload)
            self._send_json({"status": "success"})
            return
        self._send_json({"status": "not_found"}, HTTPStatus.NOT_FOUND)

    def do_PUT(self) -> None:  # noqa: N802 - stdlib HTTP handler API
        path = urlsplit(self.path).path
        payload = self._read_json()
        if path.startswith("/api/queue/status/"):
            with self.state.lock:
                self.state.status_payloads.append(payload)
                self.state.record_terminal_if_needed(payload)
            self._send_json({"status": "success"})
            return
        self._send_json({"status": "not_found"}, HTTPStatus.NOT_FOUND)

    def do_GET(self) -> None:  # noqa: N802 - stdlib HTTP handler API
        path = urlsplit(self.path).path
        if path.startswith("/api/queue/progress/"):
            self._send_json({"status": "success", "progress": {}})
            return
        self._send_json({"status": "not_found"}, HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: Any) -> None:
        # Keep the demo output focused on Tracker callbacks rather than the
        # standard HTTP access-log noise.
        return


def _parse_indices(raw: str) -> List[int]:
    values = [part.strip() for part in str(raw).split(",") if part.strip()]
    if not values:
        raise argparse.ArgumentTypeError("icon indices cannot be empty")
    try:
        return [int(value) for value in values]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("icon indices must be comma-separated integers") from exc


def _write_payload_artifact(job: Mapping[str, Any]) -> Path:
    path = Path("logs") / "mini_remix_tracker_demo_job.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(job, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def _print_demo_plan(job: Mapping[str, Any]) -> None:
    request = MiniRemixRequest.from_payload(job["mini_remix"])
    slot_indices = resolve_center_slot_indices(
        len(SLOT_PIXEL_CENTERS),
        len(request.icon_indices),
    )
    print("[DemoTracker] job payload prepared")
    print(f"[DemoTracker] URL: {job['mod_game_url']}")
    print(f"[DemoTracker] production_mode: {job['production_mode']}")
    print(f"[DemoTracker] icon_indices (left-to-right): {request.icon_indices}")
    print(f"[Creator will calculate] target_slot_indices: {slot_indices}")
    print(f"[DemoTracker] manual profile icons: {len(ICON_PIXEL_CENTERS)}")
    print(f"[DemoTracker] manual profile slots: {len(SLOT_PIXEL_CENTERS)}")
    print(f"[DemoTracker] prepare clicks: {PREPARE_CENTER_CLICK_COUNT} x center")


def run_creator_canary(job: Mapping[str, Any], timeout_seconds: float) -> int:
    state = DemoTrackerState(job)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _DemoTrackerHandler)
    server.demo_state = state  # type: ignore[attr-defined]
    server_thread = threading.Thread(
        target=server.serve_forever,
        name="mini-remix-demo-tracker",
        daemon=True,
    )
    server_thread.start()

    from creator_api_client import CreatorService

    service = CreatorService(f"http://127.0.0.1:{server.server_port}")
    # Only the local canary opts into the narrowly scoped F11 fallback.  All
    # legacy jobs retain the existing foreground-title safety guard.
    service.processing_manager.json_generator.game_player.allow_unverified_f11_for_demo = True
    print(f"[DemoTracker] listening at http://127.0.0.1:{server.server_port}")
    print("[DemoTracker] starting the real CreatorService...")

    try:
        if not service.start():
            print("[DemoTracker] CreatorService failed to register/start.")
            return 2

        deadline = time.monotonic() + max(float(timeout_seconds), 1.0)
        while time.monotonic() < deadline:
            if state.terminal_event.wait(timeout=1.0):
                break
            if not service.running:
                break
        else:
            print("[DemoTracker] timeout waiting for the terminal Creator callback.")
            return 3
    finally:
        service.stop()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=3.0)

    with state.lock:
        terminal = state.status_payloads[-1] if state.status_payloads else {}
        final_status = str(terminal.get("status") or "unknown")
        final_flag = bool(terminal.get("final"))
        report = terminal.get("diagnostic_report")
        output = terminal.get("video_files")
        progress_count = len(state.progress_payloads)

    print("[DemoTracker] terminal callback received")
    print(f"[DemoTracker] status={final_status} final={final_flag}")
    print(f"[DemoTracker] progress_callbacks={progress_count}")
    if isinstance(report, Mapping):
        summary = report.get("summary") if isinstance(report.get("summary"), Mapping) else {}
        print(f"[DemoTracker] diagnostic effective_mode={report.get('effective_mode') or report.get('effectiveMode')}")
        print(f"[DemoTracker] diagnostic summary={summary}")
    print(f"[DemoTracker] video_files={output}")
    return 0 if final_status == "completed" and final_flag else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run",
        action="store_true",
        help="start the real CreatorService and run the canary job",
    )
    parser.add_argument(
        "--icon-indices",
        type=_parse_indices,
        default=list(DEFAULT_ICON_INDICES),
        help="comma-separated marker indices in left-to-right placement order",
    )
    parser.add_argument(
        "--queue-id",
        type=int,
        default=DEMO_QUEUE_ID,
        help="queue id used by the in-memory Tracker",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=1800.0,
        help="maximum seconds to wait for Creator's terminal callback",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    job = build_demo_job(icon_indices=args.icon_indices, queue_id=args.queue_id)
    artifact_path = _write_payload_artifact(job)
    _print_demo_plan(job)
    print(f"[DemoTracker] payload artifact: {artifact_path.resolve()}")

    if not args.run:
        print("[DemoTracker] dry run only; pass --run to invoke Creator.")
        return 0
    return run_creator_canary(job, args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())

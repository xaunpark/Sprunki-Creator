"""Queue-scoped Manual Profile v3 validation and legacy projection.

Tracker owns the v3 snapshot. Creator validates that snapshot at the queue
boundary and projects it into the absolute-coordinate profile shape consumed
by the existing manual workflow. The projected profile is job-scoped and is
never written to ``manual_profiles/``.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

from manual_prepare_timeline import calculate_prepare_safety_timeline_ms


class ManualProfileV3Error(ValueError):
    """A stable, secret-free rejection reason for a Tracker profile."""


DEFAULT_EXPECTED_VIEWPORT = {"width": 1920, "height": 1080}
MAX_ACTIONS = 256
MAX_SINGLE_DURATION_MS = 10 * 60 * 1000
MAX_TOTAL_DURATION_MS = 30 * 60 * 1000
_MISSING = object()

_PROFILE_TOP_LEVEL_KEYS = {
    "version",
    "canonicalUrl",
    "urlKey",
    "coordinateSpace",
    "capture",
    "prepareActions",
    "prepareTiming",
    "layout",
    "settings",
    "notes",
}
_PROFILE_REQUIRED_KEYS = {
    "version",
    "canonicalUrl",
    "urlKey",
    "coordinateSpace",
    "capture",
    "prepareActions",
    "prepareTiming",
    "layout",
    "settings",
    "notes",
}
_CAPTURE_KEYS = {"viewport", "surface", "createdAt"}
_SURFACE_KEYS = {"type"}
_MARKER_KEYS = {"index", "x", "y"}
_TIMING_KEYS = {"endDelayMs", "durationMs"}
_LAYOUT_KEYS = {"icons", "slots"}
_ACTION_TYPES = {"click", "drag", "double_click_drag", "key"}
_KEY_ALIASES = {
    "arrowup": "up",
    "arrowdown": "down",
    "arrowleft": "left",
    "arrowright": "right",
}
_ALLOWED_SPECIAL_KEYS = {
    "space",
    "enter",
    "arrowup",
    "arrowdown",
    "arrowleft",
    "arrowright",
}
_ALLOWED_ACTION_KEYS = {
    "click": {"type", "x", "y", "delayMs", "pressDurationMs"},
    "drag": {
        "type",
        "startX",
        "startY",
        "endX",
        "endY",
        "delayMs",
        "dragDurationMs",
        "holdBeforeMoveMs",
    },
    "double_click_drag": {
        "type",
        "startX",
        "startY",
        "endX",
        "endY",
        "delayMs",
        "dragDurationMs",
        "holdBeforeMoveMs",
        "clickIntervalMs",
        "firstClickDurationMs",
    },
    "key": {"type", "key", "delayMs"},
}


@dataclass
class ManualProfileJobContext:
    """Immutable-by-convention profile state for one queue attempt.

    ``field_state`` distinguishes a response where Tracker omitted the field
    from an explicit ``null``. Both states intentionally select the no-profile
    path for queue jobs. ``legacy_profile`` is populated only after the live
    Creator viewport has been checked and the v3 content has been projected.
    """

    field_state: str = "omitted"
    envelope: Optional[Dict[str, Any]] = None
    expected_revision_id: Optional[Any] = None
    legacy_profile: Optional[Dict[str, Any]] = None
    audit: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.field_state not in {"omitted", "null", "object"}:
            raise ValueError(f"Unsupported manual profile field state: {self.field_state}")
        if self.envelope is not None:
            self.envelope = copy.deepcopy(dict(self.envelope))
        if self.legacy_profile is not None:
            self.legacy_profile = copy.deepcopy(dict(self.legacy_profile))
        self.audit = copy.deepcopy(dict(self.audit or {}))

    @property
    def has_queue_profile(self) -> bool:
        return self.field_state == "object"

    @property
    def source_token(self) -> Optional[str]:
        if not self.has_queue_profile:
            return None
        profile_id = self.audit.get("id")
        revision = self.audit.get("revision")
        revision_id = self.audit.get("revision_id")
        if profile_id is None or revision is None:
            return "tracker:unknown"
        suffix = f"#{revision_id}" if revision_id is not None else ""
        return f"tracker:{profile_id}@{revision}{suffix}"


def build_manual_profile_context(
    envelope: Any,
    *,
    field_present: bool,
    expected_revision_id: Any = None,
) -> ManualProfileJobContext:
    """Snapshot the queue field without resolving any URL-local profile."""

    if not field_present:
        state = "omitted"
    elif envelope is None:
        state = "null"
    else:
        state = "object"

    audit: Dict[str, Any] = {}
    if isinstance(envelope, Mapping):
        for key in ("id", "revision", "revision_id", "sha256"):
            if key in envelope:
                audit[key] = envelope.get(key)

    return ManualProfileJobContext(
        field_state=state,
        envelope=envelope if isinstance(envelope, Mapping) else None,
        expected_revision_id=expected_revision_id,
        audit=audit,
    )


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


# Keep the Tracker helper's descriptive name available to callers that share
# fixture/verification code with the Tracker repository.  The shorter
# ``sha256`` name remains the canonical internal implementation.
profile_sha256 = sha256


def _require_finite_number(value: Any, code: str) -> float:
    """Return a JSON number, rejecting coercible strings and non-finite values."""

    # Tracker's validator accepts JSON numbers only.  Coercing strings here
    # would allow a hand-crafted queue payload to pass a schema check that the
    # recorder/API would never persist.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ManualProfileV3Error(code)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ManualProfileV3Error(code)
    if not math.isfinite(number):
        raise ManualProfileV3Error(code)
    return number


def _require_non_negative_duration(
    value: Any,
    field_name: str,
    *,
    default: Any = _MISSING,
    maximum: int = MAX_SINGLE_DURATION_MS,
) -> int:
    if value is None:
        # A missing optional field is passed as the explicit default by the
        # caller.  An explicit JSON null is malformed and must not silently
        # become a zero-duration gesture.
        if default is _MISSING:
            raise ManualProfileV3Error(f"manual_profile_action_invalid:{field_name}")
        value = default
    number = _require_finite_number(value, "manual_profile_action_invalid")
    if number < 0 or number > maximum or int(number) != number:
        raise ManualProfileV3Error(f"manual_profile_action_invalid:{field_name}")
    return int(round(number))


def canonicalize_cocrea_url(url: str) -> str:
    if not isinstance(url, str) or not url.strip():
        raise ManualProfileV3Error("manual_profile_url_invalid")
    try:
        parsed = urlsplit(url.strip())
        host = (parsed.hostname or "").lower()
    except ValueError:
        raise ManualProfileV3Error("manual_profile_url_invalid")
    if parsed.scheme.lower() != "https" or host not in {"cocrea.world", "www.cocrea.world"}:
        raise ManualProfileV3Error("manual_profile_url_invalid")

    path = parsed.path.rstrip("/")
    match = re.fullmatch(r"/@([^/]+)/([^/]+)", path)
    if not match:
        raise ManualProfileV3Error("manual_profile_url_invalid")
    author, project = match.groups()
    return urlunsplit(("https", "www.cocrea.world", f"/@{author}/{project}", "", ""))


def _cocrea_url_key(canonical_url: str) -> str:
    parsed = urlsplit(canonical_url)
    path = parsed.path.strip("/")
    return path[1:] if path.startswith("@") else path


def _reject_nonfinite_json(value: Any) -> None:
    """Reject NaN/Infinity anywhere in a decoded JSON object."""

    if isinstance(value, float) and not math.isfinite(value):
        raise ManualProfileV3Error("manual_profile_action_invalid:non_finite")
    if isinstance(value, Mapping):
        for child in value.values():
            _reject_nonfinite_json(child)
    elif isinstance(value, list):
        for child in value:
            _reject_nonfinite_json(child)


def _validate_normalized_point(point: Mapping[str, Any], fields: Tuple[str, str]) -> None:
    for field_name in fields:
        number = _require_finite_number(point.get(field_name), "manual_profile_action_invalid")
        if number < 0 or number > 1:
            raise ManualProfileV3Error("manual_profile_action_invalid:coordinate")


def _validate_indexed_markers(markers: Any, group_name: str) -> None:
    if not isinstance(markers, list) or not markers:
        raise ManualProfileV3Error(f"manual_profile_action_invalid:{group_name}")

    indexes = []
    for marker in markers:
        if not isinstance(marker, Mapping):
            raise ManualProfileV3Error(f"manual_profile_action_invalid:{group_name}")
        if set(marker) - _MARKER_KEYS:
            raise ManualProfileV3Error(f"manual_profile_action_invalid:{group_name}_field")
        index = marker.get("index")
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise ManualProfileV3Error(f"manual_profile_action_invalid:{group_name}_index")
        _validate_normalized_point(marker, ("x", "y"))
        indexes.append(index)
    if indexes != list(range(len(indexes))):
        raise ManualProfileV3Error(f"manual_profile_action_invalid:{group_name}_index")


def _validate_key_name(value: Any) -> None:
    if not isinstance(value, str):
        raise ManualProfileV3Error("manual_profile_action_invalid:key")
    if value != value.strip():
        raise ManualProfileV3Error("manual_profile_action_invalid:key")
    key = value.lower()
    if key in _ALLOWED_SPECIAL_KEYS:
        return
    if len(key) == 1 and (("a" <= key <= "z") or ("0" <= key <= "9")):
        return
    raise ManualProfileV3Error("manual_profile_action_invalid:key")


def _validate_action(action: Any) -> None:
    if not isinstance(action, Mapping):
        raise ManualProfileV3Error("manual_profile_action_invalid")
    action_type_value = action.get("type")
    if not isinstance(action_type_value, str):
        raise ManualProfileV3Error("manual_profile_action_invalid:type")
    action_type = action_type_value.strip().lower()
    if action_type_value != action_type:
        raise ManualProfileV3Error("manual_profile_action_invalid:type")
    if action_type not in _ACTION_TYPES:
        raise ManualProfileV3Error("manual_profile_action_invalid:type")
    if set(action) - _ALLOWED_ACTION_KEYS[action_type]:
        raise ManualProfileV3Error("manual_profile_action_invalid:field")

    if action_type == "click":
        _validate_normalized_point(action, ("x", "y"))
        _require_non_negative_duration(action.get("delayMs", 0), "delayMs")
        if "pressDurationMs" in action:
            _require_non_negative_duration(action.get("pressDurationMs"), "pressDurationMs")
        return

    if action_type in {"drag", "double_click_drag"}:
        _validate_normalized_point(action, ("startX", "startY"))
        _validate_normalized_point(action, ("endX", "endY"))
        _require_non_negative_duration(action.get("delayMs", 0), "delayMs")
        if "dragDurationMs" not in action:
            raise ManualProfileV3Error("manual_profile_action_invalid:dragDurationMs")
        drag_duration_ms = _require_non_negative_duration(
            action.get("dragDurationMs"), "dragDurationMs"
        )
        hold_before_move_ms = _require_non_negative_duration(
            action.get("holdBeforeMoveMs", 0), "holdBeforeMoveMs"
        )
        if hold_before_move_ms > drag_duration_ms:
            raise ManualProfileV3Error("manual_profile_action_invalid:holdBeforeMoveMs")
        if action_type == "double_click_drag":
            if "clickIntervalMs" not in action or "firstClickDurationMs" not in action:
                raise ManualProfileV3Error("manual_profile_action_invalid:double_click_timing")
            click_interval_ms = _require_non_negative_duration(
                action.get("clickIntervalMs"), "clickIntervalMs"
            )
            first_click_duration_ms = _require_non_negative_duration(
                action.get("firstClickDurationMs"), "firstClickDurationMs"
            )
            if first_click_duration_ms > click_interval_ms:
                raise ManualProfileV3Error("manual_profile_action_invalid:firstClickDurationMs")
        return

    _validate_key_name(action.get("key"))
    _require_non_negative_duration(action.get("delayMs", 0), "delayMs")


def validate_v3_content(content: Mapping[str, Any]) -> None:
    """Validate the complete v3 content before any physical input."""

    if not isinstance(content, Mapping):
        raise ManualProfileV3Error("manual_profile_missing_content")
    _reject_nonfinite_json(content)
    if set(content) - _PROFILE_TOP_LEVEL_KEYS:
        raise ManualProfileV3Error("manual_profile_action_invalid:profile_field")
    if _PROFILE_REQUIRED_KEYS - set(content):
        raise ManualProfileV3Error("manual_profile_action_invalid:profile_field")
    if content.get("version") != 3 or content.get("coordinateSpace") != "viewport_normalized":
        raise ManualProfileV3Error("manual_profile_unsupported_coordinate_space")

    canonical_url = canonicalize_cocrea_url(content.get("canonicalUrl"))
    if not isinstance(content.get("urlKey"), str):
        raise ManualProfileV3Error("manual_profile_action_invalid:urlKey")
    if content.get("urlKey") != _cocrea_url_key(canonical_url):
        raise ManualProfileV3Error("manual_profile_url_mismatch")

    capture = content.get("capture")
    if not isinstance(capture, Mapping):
        raise ManualProfileV3Error("manual_profile_viewport_invalid")
    if set(capture) - _CAPTURE_KEYS:
        raise ManualProfileV3Error("manual_profile_action_invalid:capture_field")
    surface = capture.get("surface")
    if not isinstance(surface, Mapping) or surface.get("type") != "browser_viewport":
        raise ManualProfileV3Error("manual_profile_action_invalid:capture_surface")
    if set(surface) - _SURFACE_KEYS:
        raise ManualProfileV3Error("manual_profile_action_invalid:capture_surface_field")
    if not isinstance(capture.get("createdAt"), str):
        raise ManualProfileV3Error("manual_profile_action_invalid:capture_createdAt")
    recorded_viewport = capture.get("viewport")
    if not isinstance(recorded_viewport, Mapping):
        raise ManualProfileV3Error("manual_profile_viewport_invalid")
    for key in ("width", "height"):
        value = recorded_viewport.get(key)
        number = _require_finite_number(value, "manual_profile_viewport_invalid")
        if number <= 0 or int(number) != number:
            raise ManualProfileV3Error("manual_profile_viewport_invalid")

    actions = content.get("prepareActions")
    if not isinstance(actions, list) or len(actions) > MAX_ACTIONS:
        raise ManualProfileV3Error("manual_profile_action_invalid:count")
    for action in actions:
        _validate_action(action)

    timing = content.get("prepareTiming")
    if not isinstance(timing, Mapping):
        raise ManualProfileV3Error("manual_profile_action_invalid:prepareTiming")
    if set(timing) - _TIMING_KEYS:
        raise ManualProfileV3Error("manual_profile_action_invalid:prepareTiming_field")
    if "endDelayMs" not in timing or "durationMs" not in timing:
        raise ManualProfileV3Error("manual_profile_action_invalid:prepareTiming")
    end_delay_ms = _require_non_negative_duration(timing.get("endDelayMs"), "endDelayMs")
    declared_duration_ms = _require_non_negative_duration(
        timing.get("durationMs"), "durationMs", maximum=MAX_TOTAL_DURATION_MS
    )
    actual_timeline_ms = calculate_prepare_safety_timeline_ms(actions, end_delay_ms)
    if actual_timeline_ms > MAX_TOTAL_DURATION_MS:
        raise ManualProfileV3Error("manual_profile_action_invalid:total_duration")

    layout = content.get("layout")
    if not isinstance(layout, Mapping):
        raise ManualProfileV3Error("manual_profile_action_invalid:layout")
    if set(layout) - _LAYOUT_KEYS:
        raise ManualProfileV3Error("manual_profile_action_invalid:layout_field")
    _validate_indexed_markers(layout.get("icons"), "icons")
    _validate_indexed_markers(layout.get("slots"), "slots")

    settings = content.get("settings", {})
    if not isinstance(settings, Mapping):
        raise ManualProfileV3Error("manual_profile_action_invalid:settings")
    if "gameControlType" in settings and not isinstance(settings.get("gameControlType"), str):
        raise ManualProfileV3Error("manual_profile_action_invalid:settings")
    if "notes" in content and not isinstance(content.get("notes"), str):
        raise ManualProfileV3Error("manual_profile_action_invalid:notes")


def _viewport_dimensions(viewport: Mapping[str, Any]) -> Tuple[int, int]:
    if (
        isinstance(viewport, Mapping)
        and "viewport" in viewport
        and not {"width", "height"}.issubset(viewport)
        and isinstance(viewport.get("viewport"), Mapping)
    ):
        viewport = viewport["viewport"]
    try:
        width = viewport["width"]
        height = viewport["height"]
    except (KeyError, TypeError):
        raise ManualProfileV3Error("manual_profile_viewport_invalid")
    width_number = _require_finite_number(width, "manual_profile_viewport_invalid")
    height_number = _require_finite_number(height, "manual_profile_viewport_invalid")
    if int(width_number) != width_number or int(height_number) != height_number:
        raise ManualProfileV3Error("manual_profile_viewport_invalid")
    width, height = int(width_number), int(height_number)
    if width <= 0 or height <= 0:
        raise ManualProfileV3Error("manual_profile_viewport_invalid")
    return width, height


def verify_queue_profile(
    envelope: Mapping[str, Any],
    expected_url: str,
    viewport: Mapping[str, Any],
    expected_revision_id: Any = None,
    expected_environment_viewport: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Verify one immutable Tracker envelope and return a copied v3 content.

    ``expected_revision_id`` is intentionally mandatory in practice: a queue
    object without the claim-row revision binding is stale/ambiguous and must
    fail closed before any replay input is sent.
    """

    if not isinstance(envelope, Mapping) or not isinstance(envelope.get("content"), Mapping):
        object_error = envelope.get("error_code") if isinstance(envelope, Mapping) else None
        if isinstance(object_error, str) and object_error.startswith("manual_profile_"):
            raise ManualProfileV3Error(object_error)
        raise ManualProfileV3Error("manual_profile_missing_content")

    if "revision_id" not in envelope:
        raise ManualProfileV3Error("manual_profile_revision_id_missing")
    for field_name in ("id", "revision", "revision_id"):
        value = envelope.get(field_name)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ManualProfileV3Error(f"manual_profile_{field_name}_invalid")
    if expected_revision_id is None:
        raise ManualProfileV3Error("manual_profile_revision_id_missing")
    if isinstance(expected_revision_id, bool) or not isinstance(expected_revision_id, int) or expected_revision_id <= 0:
        raise ManualProfileV3Error("manual_profile_revision_id_invalid")
    if envelope["revision_id"] != expected_revision_id:
        raise ManualProfileV3Error("manual_profile_revision_id_mismatch")

    envelope_hash = envelope.get("sha256")
    if not isinstance(envelope_hash, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", envelope_hash):
        raise ManualProfileV3Error("manual_profile_hash_invalid")

    content = copy.deepcopy(dict(envelope["content"]))
    validate_v3_content(content)
    try:
        calculated_hash = sha256(content)
    except (TypeError, ValueError, OverflowError):
        raise ManualProfileV3Error("manual_profile_hash_invalid")
    if not hmac.compare_digest(calculated_hash.lower(), envelope_hash.lower()):
        raise ManualProfileV3Error("manual_profile_hash_mismatch")

    if canonicalize_cocrea_url(content.get("canonicalUrl")) != canonicalize_cocrea_url(expected_url):
        raise ManualProfileV3Error("manual_profile_url_mismatch")

    recorded_width, recorded_height = _viewport_dimensions(
        content["capture"]["viewport"]
    )
    live_width, live_height = _viewport_dimensions(viewport)
    if expected_environment_viewport is not None:
        environment_width, environment_height = _viewport_dimensions(expected_environment_viewport)
        if (recorded_width, recorded_height) != (environment_width, environment_height):
            raise ManualProfileV3Error("invalid_capture_environment")
    if (recorded_width, recorded_height) != (live_width, live_height):
        raise ManualProfileV3Error(
            f"manual_profile_viewport_mismatch:{live_width}x{live_height}!={recorded_width}x{recorded_height}"
        )
    return content


def viewport_action_to_pixels(action: Mapping[str, Any], viewport: Mapping[str, Any]) -> Dict[str, Any]:
    """Convert every normalized coordinate in an action/marker to pixels."""

    result = dict(action)
    width, height = _viewport_dimensions(viewport)
    for x_name, y_name in (("x", "y"), ("startX", "startY"), ("endX", "endY")):
        if x_name in result or y_name in result:
            if x_name not in result or y_name not in result:
                raise ManualProfileV3Error("manual_profile_action_invalid:coordinate")
            x = _require_finite_number(result[x_name], "manual_profile_action_invalid:coordinate")
            y = _require_finite_number(result[y_name], "manual_profile_action_invalid:coordinate")
            if not 0 <= x <= 1 or not 0 <= y <= 1:
                raise ManualProfileV3Error("manual_profile_action_invalid:coordinate")
            result[x_name], result[y_name] = round(x * width), round(y * height)
    return result


def _legacy_key_name(value: str) -> str:
    return _KEY_ALIASES.get(value.strip().lower(), value.strip().lower())


def project_v3_content_to_legacy_profile(
    content: Mapping[str, Any],
    viewport: Mapping[str, Any],
    *,
    source_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Project verified v3 content into the existing absolute profile shape."""

    validate_v3_content(content)
    width, height = _viewport_dimensions(viewport)
    layout = content["layout"]

    actions = []
    for raw_action in content.get("prepareActions", []):
        action = viewport_action_to_pixels(raw_action, viewport)
        if action.get("type") == "key":
            action["key"] = _legacy_key_name(action.get("key", ""))
        actions.append(action)

    def map_marker(marker: Mapping[str, Any]) -> Dict[str, Any]:
        return viewport_action_to_pixels(marker, viewport)

    canonical_url = canonicalize_cocrea_url(content["canonicalUrl"])
    return {
        "version": 2,
        "sourceUrl": source_url or canonical_url,
        "canonicalUrl": canonical_url,
        "urlKey": content.get("urlKey") or "",
        "viewport": {"width": width, "height": height},
        # Existing replay treats any non-v3 coordinate space as absolute.
        # This projection is in-memory only and is never presented as the
        # Tracker wire format.
        "coordinateSpace": f"screen_{width}x{height}_with_menu",
        "prepareActions": actions,
        "prepareTiming": copy.deepcopy(dict(content.get("prepareTiming") or {})),
        "layout": {
            "icons": [map_marker(marker) for marker in layout.get("icons", [])],
            "slots": [map_marker(marker) for marker in layout.get("slots", [])],
        },
        "settings": copy.deepcopy(dict(content.get("settings") or {})),
        "notes": content.get("notes", ""),
    }


def materialize_tracker_profile(
    context: ManualProfileJobContext,
    *,
    expected_url: str,
    live_viewport: Mapping[str, Any],
    expected_environment_viewport: Optional[Mapping[str, Any]] = DEFAULT_EXPECTED_VIEWPORT,
) -> ManualProfileJobContext:
    """Validate and project a queue context once the live page is ready."""

    if not context.has_queue_profile:
        return context
    content = verify_queue_profile(
        context.envelope or {},
        expected_url,
        live_viewport,
        context.expected_revision_id,
        expected_environment_viewport,
    )
    profile = project_v3_content_to_legacy_profile(
        content,
        live_viewport,
        source_url=expected_url,
    )
    live_width, live_height = _viewport_dimensions(live_viewport)
    return ManualProfileJobContext(
        field_state="object",
        envelope=context.envelope,
        expected_revision_id=context.expected_revision_id,
        legacy_profile=profile,
        audit={
            "id": context.envelope.get("id"),
            "revision": context.envelope.get("revision"),
            "revision_id": context.envelope.get("revision_id"),
            "sha256": context.envelope.get("sha256"),
            "coordinateSpace": "viewport_normalized",
            "viewport": {"width": live_width, "height": live_height},
        },
    )


def validate_context_runtime_viewport(
    context: ManualProfileJobContext,
    live_viewport: Mapping[str, Any],
) -> None:
    """Ensure a later recovery still runs in the viewport used for projection."""

    if not context.has_queue_profile:
        return
    expected = context.audit.get("viewport") or {}
    expected_width, expected_height = _viewport_dimensions(expected)
    live_width, live_height = _viewport_dimensions(live_viewport)
    if (expected_width, expected_height) != (live_width, live_height):
        raise ManualProfileV3Error(
            f"manual_profile_viewport_mismatch:{live_width}x{live_height}!={expected_width}x{expected_height}"
        )


__all__ = [
    "DEFAULT_EXPECTED_VIEWPORT",
    "ManualProfileJobContext",
    "ManualProfileV3Error",
    "build_manual_profile_context",
    "canonical_json",
    "canonicalize_cocrea_url",
    "materialize_tracker_profile",
    "project_v3_content_to_legacy_profile",
    "profile_sha256",
    "sha256",
    "validate_context_runtime_viewport",
    "validate_v3_content",
    "verify_queue_profile",
    "viewport_action_to_pixels",
]

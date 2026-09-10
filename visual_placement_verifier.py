"""Visual fallback verification for one-slot placement operations.

The semantic placement verifier remains the primary source of truth.  This
module only answers a narrower question after a physical action has already
been attempted: did the pixels in the requested slot change in a stable,
slot-local way?  It deliberately does not inspect controls, infer board
emptiness, or issue input.
"""

from __future__ import annotations

import base64
import io
import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

import numpy as np
from PIL import Image

try:  # scikit-image is already a project dependency, but keep import safe.
    from skimage.metrics import structural_similarity
except Exception:  # pragma: no cover - exercised only in minimal runtimes.
    structural_similarity = None


VISUAL_VERIFIED = "VERIFIED"
VISUAL_INCONCLUSIVE = "INCONCLUSIVE"


@dataclass(frozen=True)
class VisualThresholds:
    """Default visual thresholds.

    Values are intentionally expressed in normalized screenshot pixels rather
    than reference-screen coordinates.  This keeps the classifier independent
    from browser chrome, fullscreen scale, and device pixel ratio.
    """

    pixel_delta: float = 20.0
    changed_ratio: float = 0.15
    mean_abs_diff: float = 10.0
    transition_ssim_max: float = 0.85
    post_stability_ssim_min: float = 0.92
    empty_reference_ssim_min: float = 0.90
    target_vs_control_ratio: float = 1.5
    global_change_ratio_max: float = 0.30
    dynamic_pixel_delta: float = 12.0
    min_stable_pixels: int = 64


@dataclass(frozen=True)
class VisualCapture:
    """One CDP screenshot plus the viewport identity used to crop it."""

    image: np.ndarray
    viewport_width: float
    viewport_height: float
    device_pixel_ratio: float = 1.0
    page_url: str = ""
    geometry_fingerprint: str = ""
    captured_at: float = field(default_factory=time.monotonic)

    @classmethod
    def from_png_base64(
        cls,
        data: str,
        *,
        viewport_width: float,
        viewport_height: float,
        device_pixel_ratio: float = 1.0,
        page_url: str = "",
        geometry_fingerprint: str = "",
    ) -> "VisualCapture":
        raw = base64.b64decode(data)
        with Image.open(io.BytesIO(raw)) as image:
            rgb = image.convert("RGB")
            pixels = np.asarray(rgb, dtype=np.uint8).copy()
        return cls(
            image=pixels,
            viewport_width=float(viewport_width),
            viewport_height=float(viewport_height),
            device_pixel_ratio=max(float(device_pixel_ratio or 1.0), 1e-6),
            page_url=str(page_url or ""),
            geometry_fingerprint=str(geometry_fingerprint or ""),
        )


@dataclass(frozen=True)
class VisualROI:
    """A CSS-pixel capture rectangle anchored to the current page viewport."""

    x: float
    y: float
    width: float
    height: float
    source: str = "live_bounds"
    slot_index: Optional[int] = None
    geometry_fingerprint: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "x": round(float(self.x), 3),
            "y": round(float(self.y), 3),
            "width": round(float(self.width), 3),
            "height": round(float(self.height), 3),
            "source": self.source,
            "slotIndex": self.slot_index,
            "geometryFingerprint": self.geometry_fingerprint,
        }


@dataclass(frozen=True)
class VisualVerificationResult:
    """Classifier result; no result other than VERIFIED authorizes success."""

    status: str
    direction: str
    message: str
    details: Dict[str, Any] = field(default_factory=dict)

    @property
    def verified(self) -> bool:
        return self.status == VISUAL_VERIFIED


def _as_rgb_array(value: Any) -> Optional[np.ndarray]:
    if isinstance(value, VisualCapture):
        value = value.image
    if isinstance(value, Image.Image):
        value = np.asarray(value.convert("RGB"), dtype=np.uint8)
    try:
        array = np.asarray(value)
    except Exception:
        return None
    if array.ndim == 2:
        array = np.repeat(array[:, :, None], 3, axis=2)
    if array.ndim != 3 or array.shape[2] < 3:
        return None
    array = array[:, :, :3]
    if array.dtype != np.uint8:
        array = np.clip(array, 0, 255).astype(np.uint8)
    return array


def _crop_capture(capture: Any, roi: VisualROI) -> Optional[np.ndarray]:
    """Crop a CSS ROI from a full CDP screenshot using viewport scaling."""

    if isinstance(capture, Mapping):
        image_value = capture.get("image")
        if image_value is None:
            image_value = capture.get("pixels")
        image = _as_rgb_array(image_value)
    else:
        image = _as_rgb_array(capture)
    if image is None:
        return None
    if isinstance(capture, VisualCapture):
        viewport_width = capture.viewport_width
        viewport_height = capture.viewport_height
    elif isinstance(capture, Mapping):
        viewport_width = float(capture.get("viewport_width") or capture.get("viewportWidth") or 0)
        viewport_height = float(capture.get("viewport_height") or capture.get("viewportHeight") or 0)
    else:
        viewport_width = viewport_height = 0.0

    if viewport_width <= 0 or viewport_height <= 0:
        # Test fixtures may already be ROI-sized.  Do not silently interpret a
        # full screenshot with missing geometry as a valid target crop.
        if image.shape[1] == int(round(roi.width)) and image.shape[0] == int(round(roi.height)):
            return image
        return None

    scale_x = image.shape[1] / viewport_width
    scale_y = image.shape[0] / viewport_height
    left = int(math.floor(float(roi.x) * scale_x))
    top = int(math.floor(float(roi.y) * scale_y))
    right = int(math.ceil((float(roi.x) + float(roi.width)) * scale_x))
    bottom = int(math.ceil((float(roi.y) + float(roi.height)) * scale_y))
    left = max(0, min(left, image.shape[1] - 1))
    top = max(0, min(top, image.shape[0] - 1))
    right = max(left + 1, min(right, image.shape[1]))
    bottom = max(top + 1, min(bottom, image.shape[0]))
    cropped = image[top:bottom, left:right]
    return cropped.copy() if cropped.size else None


def _resize_rgb(image: np.ndarray, width: int, height: int) -> np.ndarray:
    width = max(int(width), 8)
    height = max(int(height), 8)
    if image.shape[1] == width and image.shape[0] == height:
        return image.copy()
    pil = Image.fromarray(image, mode="RGB")
    return np.asarray(pil.resize((width, height), Image.Resampling.BILINEAR), dtype=np.uint8)


def _gray(image: np.ndarray) -> np.ndarray:
    # Luma is less sensitive to a mod changing a character's hue while still
    # retaining the Polo/Character silhouette transition.
    rgb = image.astype(np.float32)
    return (0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2])


def _ssim(first: np.ndarray, second: np.ndarray) -> float:
    first_gray = _gray(first)
    second_gray = _gray(second)
    if structural_similarity is not None:
        try:
            value = structural_similarity(
                first_gray,
                second_gray,
                data_range=255.0,
            )
            return float(value)
        except Exception:
            pass
    first_centered = first_gray - float(first_gray.mean())
    second_centered = second_gray - float(second_gray.mean())
    denominator = float(np.linalg.norm(first_centered) * np.linalg.norm(second_centered))
    if denominator <= 1e-6:
        return 1.0 if np.allclose(first_gray, second_gray) else 0.0
    correlation = float(np.sum(first_centered * second_centered) / denominator)
    return max(0.0, min(1.0, (correlation + 1.0) / 2.0))


def _median_image(images: Sequence[np.ndarray]) -> np.ndarray:
    stack = np.stack(images, axis=0).astype(np.float32)
    return np.median(stack, axis=0).astype(np.uint8)


def _prepare_frames(
    captures: Iterable[Any],
    roi: VisualROI,
    *,
    width: int = 96,
    height: int = 96,
) -> Tuple[np.ndarray, ...]:
    frames = []
    for capture in captures:
        if isinstance(capture, (np.ndarray, Image.Image)):
            normalized = _as_rgb_array(capture)
            if normalized is None:
                continue
            frames.append(_resize_rgb(normalized, width, height))
            continue
        cropped = _crop_capture(capture, roi)
        if cropped is None:
            continue
        frames.append(_resize_rgb(cropped, width, height))
    return tuple(frames)


def normalize_visual_captures(
    captures: Sequence[Any],
    roi: VisualROI,
    *,
    width: int = 96,
    height: int = 96,
) -> Tuple[np.ndarray, ...]:
    """Return normalized ROI arrays for a transient empty-state reference."""

    return _prepare_frames(captures, roi, width=width, height=height)


def _inner_and_control_masks(
    height: int,
    width: int,
    inner_ratio: float = 0.60,
) -> Tuple[np.ndarray, np.ndarray]:
    safe_ratio = max(0.40, min(0.90, float(inner_ratio)))
    margin_ratio = (1.0 - safe_ratio) / 2.0
    margin_y = max(1, int(round(height * margin_ratio)))
    margin_x = max(1, int(round(width * margin_ratio)))
    target = np.zeros((height, width), dtype=bool)
    target[margin_y:height - margin_y, margin_x:width - margin_x] = True
    control = ~target
    return target, control


def _score_change(
    before: np.ndarray,
    after: np.ndarray,
    stable_mask: np.ndarray,
    thresholds: VisualThresholds,
) -> Dict[str, float]:
    before_gray = _gray(before)
    after_gray = _gray(after)
    diff = np.abs(before_gray - after_gray)
    selected = diff[stable_mask]
    if selected.size == 0:
        return {
            "changedRatio": 0.0,
            "meanAbsDiff": 0.0,
            "score": 0.0,
        }
    changed_ratio = float(np.mean(selected >= thresholds.pixel_delta))
    mean_abs_diff = float(np.mean(selected))
    score = changed_ratio * max(mean_abs_diff, 1.0)
    return {
        "changedRatio": changed_ratio,
        "meanAbsDiff": mean_abs_diff,
        "score": score,
    }


def _inconclusive(
    direction: str,
    message: str,
    details: Optional[Mapping[str, Any]] = None,
) -> VisualVerificationResult:
    return VisualVerificationResult(
        VISUAL_INCONCLUSIVE,
        str(direction),
        str(message),
        dict(details or {}),
    )


def verify_transition(
    before_captures: Sequence[Any],
    after_captures: Sequence[Any],
    roi: VisualROI,
    direction: str,
    *,
    empty_reference_captures: Optional[Sequence[Any]] = None,
    thresholds: Optional[VisualThresholds] = None,
    inner_ratio: float = 0.60,
) -> VisualVerificationResult:
    """Verify one stable Polo/Character transition in a fixed slot ROI.

    ``direction`` must be ``place`` or ``return``.  Return verification is
    intentionally stricter: it requires a stored empty/Polo reference from
    the same board generation and geometry.  A generic pixel change is not
    enough to claim that a character returned to the slot's Polo state.
    """

    normalized_direction = str(direction or "").strip().lower()
    if normalized_direction not in {"place", "return"}:
        return _inconclusive(normalized_direction, "unsupported_direction")
    thresholds = thresholds or VisualThresholds()
    safe_inner_ratio = max(0.40, min(0.90, float(inner_ratio)))
    if len(before_captures) < 2 or len(after_captures) < 2:
        return _inconclusive(normalized_direction, "insufficient_temporal_samples")

    before = _prepare_frames(before_captures, roi)
    after = _prepare_frames(after_captures, roi)
    if len(before) < 2 or len(after) < 2:
        return _inconclusive(normalized_direction, "roi_capture_failed", {"roi": roi.as_dict()})

    before_ref = _median_image(before)
    after_ref = _median_image(after)
    target_mask, control_mask = _inner_and_control_masks(
        before_ref.shape[0], before_ref.shape[1], safe_inner_ratio
    )

    pre_noise = np.abs(_gray(before[0]) - _gray(before[1]))
    dynamic_mask = pre_noise > thresholds.dynamic_pixel_delta
    stable_target_mask = target_mask & ~dynamic_mask
    stable_control_mask = control_mask & ~dynamic_mask
    if int(np.count_nonzero(stable_target_mask)) < thresholds.min_stable_pixels:
        return _inconclusive(
            normalized_direction,
            "target_roi_too_dynamic",
            {"stablePixels": int(np.count_nonzero(stable_target_mask))},
        )

    transition_score = _score_change(
        before_ref,
        after_ref,
        stable_target_mask,
        thresholds,
    )
    control_score = _score_change(
        before_ref,
        after_ref,
        stable_control_mask,
        thresholds,
    )
    after_stability = _ssim(after[0], after[1])
    transition_similarity = _ssim(before_ref, after_ref)
    control_change_ratio = control_score["changedRatio"]
    global_change_ratio = float(np.mean(np.abs(_gray(before_ref) - _gray(after_ref)) >= thresholds.pixel_delta))

    details: Dict[str, Any] = {
        "roi": roi.as_dict(),
        "beforeFrames": len(before),
        "afterFrames": len(after),
        "target": transition_score,
        "control": control_score,
        "postStabilitySsim": round(after_stability, 6),
        "transitionSsim": round(transition_similarity, 6),
        "globalChangeRatio": round(global_change_ratio, 6),
        "thresholds": {
            "roiInnerRatio": safe_inner_ratio,
            "pixelDelta": thresholds.pixel_delta,
            "changedRatio": thresholds.changed_ratio,
            "meanAbsDiff": thresholds.mean_abs_diff,
            "transitionSsimMax": thresholds.transition_ssim_max,
            "postStabilitySsimMin": thresholds.post_stability_ssim_min,
            "emptyReferenceSsimMin": thresholds.empty_reference_ssim_min,
            "targetVsControlRatio": thresholds.target_vs_control_ratio,
            "globalChangeRatioMax": thresholds.global_change_ratio_max,
        },
    }

    if global_change_ratio > thresholds.global_change_ratio_max:
        return _inconclusive(normalized_direction, "global_scene_changed", details)
    if after_stability < thresholds.post_stability_ssim_min:
        return _inconclusive(normalized_direction, "post_action_not_stable", details)
    if transition_similarity > thresholds.transition_ssim_max:
        return _inconclusive(normalized_direction, "target_did_not_change", details)
    if transition_score["changedRatio"] < thresholds.changed_ratio:
        return _inconclusive(normalized_direction, "target_change_ratio_too_small", details)
    if transition_score["meanAbsDiff"] < thresholds.mean_abs_diff:
        return _inconclusive(normalized_direction, "target_change_magnitude_too_small", details)
    target_score = max(transition_score["score"], 1e-6)
    control_score_value = control_score["score"]
    if control_score_value > 0 and target_score < (
        control_score_value * thresholds.target_vs_control_ratio
    ):
        return _inconclusive(normalized_direction, "background_change_dominates", details)
    if control_change_ratio > thresholds.global_change_ratio_max:
        return _inconclusive(normalized_direction, "control_region_changed", details)

    if normalized_direction == "return":
        if not empty_reference_captures or len(empty_reference_captures) < 2:
            return _inconclusive(
                normalized_direction,
                "empty_reference_missing",
                details,
            )
        reference = _prepare_frames(empty_reference_captures, roi)
        if len(reference) < 2:
            return _inconclusive(
                normalized_direction,
                "empty_reference_capture_failed",
                details,
            )
        empty_ref = _median_image(reference)
        before_empty_similarity = _ssim(before_ref, empty_ref)
        after_empty_similarity = _ssim(after_ref, empty_ref)
        details.update(
            {
                "beforeEmptyReferenceSsim": round(before_empty_similarity, 6),
                "afterEmptyReferenceSsim": round(after_empty_similarity, 6),
            }
        )
        if before_empty_similarity >= thresholds.empty_reference_ssim_min:
            return _inconclusive(
                normalized_direction,
                "before_state_already_matches_empty_reference",
                details,
            )
        if after_empty_similarity < thresholds.empty_reference_ssim_min:
            return _inconclusive(
                normalized_direction,
                "after_state_not_empty_reference",
                details,
            )

    details["directionEvidence"] = (
        "polo_to_character" if normalized_direction == "place" else "character_to_polo"
    )
    return VisualVerificationResult(
        VISUAL_VERIFIED,
        normalized_direction,
        "visual_transition_verified",
        details,
    )


def decode_cdp_screenshot(
    response: Mapping[str, Any],
    *,
    viewport_width: float,
    viewport_height: float,
    device_pixel_ratio: float = 1.0,
    page_url: str = "",
    geometry_fingerprint: str = "",
) -> Optional[VisualCapture]:
    """Decode a ``Page.captureScreenshot`` response safely."""

    try:
        data = response.get("result", {}).get("data")
        if not data:
            data = response.get("result", {}).get("result", {}).get("data")
        if not data:
            return None
        return VisualCapture.from_png_base64(
            data,
            viewport_width=viewport_width,
            viewport_height=viewport_height,
            device_pixel_ratio=device_pixel_ratio,
            page_url=page_url,
            geometry_fingerprint=geometry_fingerprint,
        )
    except Exception:
        return None

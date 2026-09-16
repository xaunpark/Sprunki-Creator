import hashlib
import json
from pathlib import Path
import unittest

from manual_profile_v3 import (
    ManualProfileV3Error,
    build_manual_profile_context,
    materialize_tracker_profile,
    project_v3_content_to_legacy_profile,
    verify_queue_profile,
    viewport_action_to_pixels,
)


def _envelope(content, revision_id=9):
    encoded = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {"id": 1, "revision": 1, "revision_id": revision_id, "sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(), "content": content}


class ManualProfileV3ContractTests(unittest.TestCase):
    def setUp(self):
        self.content = {
            "version": 3,
            "coordinateSpace": "viewport_normalized",
            "canonicalUrl": "https://www.cocrea.world/@maker/project",
            "urlKey": "maker/project",
            "capture": {
                "viewport": {"width": 1920, "height": 1080},
                "surface": {"type": "browser_viewport"},
                "createdAt": "2026-01-01T00:00:00+00:00",
            },
            "prepareActions": [
                {"type": "click", "x": 0.5, "y": 0.5, "delayMs": 0}
            ],
            "prepareTiming": {"durationMs": 0, "endDelayMs": 0},
            "layout": {
                "icons": [{"index": 0, "x": 0.1, "y": 0.8}],
                "slots": [{"index": 0, "x": 0.3, "y": 0.8}],
            },
            "settings": {"gameControlType": "unknown"},
            "notes": "",
        }

    def test_accepts_matching_claimed_snapshot(self):
        result = verify_queue_profile(
            _envelope(self.content),
            "https://cocrea.world/@maker/project/?ignored=yes#fragment",
            {"width": 1920, "height": 1080},
            9,
        )
        self.assertEqual(result, self.content)

    def test_rejects_tampered_hash_and_viewport(self):
        envelope = _envelope(self.content)
        envelope["sha256"] = "0" * 64
        with self.assertRaises(ManualProfileV3Error):
            verify_queue_profile(envelope, self.content["canonicalUrl"], {"width": 1920, "height": 1080}, 9)
        with self.assertRaises(ManualProfileV3Error):
            verify_queue_profile(_envelope(self.content), self.content["canonicalUrl"], {"width": 1366, "height": 768}, 9)
        with self.assertRaises(ManualProfileV3Error):
            verify_queue_profile(_envelope(self.content, revision_id=8), self.content["canonicalUrl"], {"width": 1920, "height": 1080}, 9)

    def test_rejects_object_error_or_missing_revision_id(self):
        with self.assertRaises(ManualProfileV3Error) as error:
            verify_queue_profile({"revision_id": 789, "error_code": "manual_profile_revision_missing"}, self.content["canonicalUrl"], {"width": 1920, "height": 1080}, 789)
        self.assertEqual(str(error.exception), "manual_profile_revision_missing")
        envelope = _envelope(self.content)
        envelope.pop("revision_id")
        with self.assertRaises(ManualProfileV3Error) as error:
            verify_queue_profile(envelope, self.content["canonicalUrl"], {"width": 1920, "height": 1080}, 789)
        self.assertEqual(str(error.exception), "manual_profile_revision_id_missing")

    def test_requires_queue_revision_binding(self):
        with self.assertRaises(ManualProfileV3Error) as error:
            verify_queue_profile(
                _envelope(self.content),
                self.content["canonicalUrl"],
                {"width": 1920, "height": 1080},
            )
        self.assertEqual(str(error.exception), "manual_profile_revision_id_missing")

    def test_accepts_tracker_numeric_viewport_values(self):
        content = json.loads(json.dumps(self.content))
        content["capture"]["viewport"] = {"width": 1920.0, "height": 1080.0}
        result = verify_queue_profile(
            _envelope(content),
            content["canonicalUrl"],
            {"width": 1920, "height": 1080},
            9,
            {"viewport": {"width": 1920, "height": 1080}, "browserZoomPolicy": "100_percent"},
        )
        self.assertEqual(result["capture"]["viewport"]["width"], 1920.0)

    def test_rejects_non_integer_or_null_timing(self):
        content = json.loads(json.dumps(self.content))
        content["prepareActions"][0]["pressDurationMs"] = 1.5
        with self.assertRaises(ManualProfileV3Error):
            verify_queue_profile(_envelope(content), content["canonicalUrl"], {"width": 1920, "height": 1080}, 9)
        content = json.loads(json.dumps(self.content))
        content["prepareActions"][0]["delayMs"] = None
        with self.assertRaises(ManualProfileV3Error):
            verify_queue_profile(_envelope(content), content["canonicalUrl"], {"width": 1920, "height": 1080}, 9)

    def test_safety_total_counts_click_press_duration(self):
        content = json.loads(json.dumps(self.content))
        content["prepareActions"] = [
            {"type": "click", "x": 0.5, "y": 0.5, "pressDurationMs": 600_000}
            for _ in range(4)
        ]
        content["prepareTiming"] = {"durationMs": 0, "endDelayMs": 0}
        with self.assertRaises(ManualProfileV3Error) as error:
            verify_queue_profile(
                _envelope(content),
                content["canonicalUrl"],
                {"width": 1920, "height": 1080},
                9,
            )
        self.assertEqual(str(error.exception), "manual_profile_action_invalid:total_duration")

    def test_safety_total_counts_double_first_click_duration(self):
        content = json.loads(json.dumps(self.content))
        content["prepareActions"] = [
            {
                "type": "double_click_drag",
                "startX": 0.1,
                "startY": 0.5,
                "endX": 0.2,
                "endY": 0.5,
                "clickIntervalMs": 600_000,
                "firstClickDurationMs": 600_000,
                "holdBeforeMoveMs": 0,
                "dragDurationMs": 0,
            }
            for _ in range(2)
        ]
        content["prepareTiming"] = {"durationMs": 0, "endDelayMs": 0}
        with self.assertRaises(ManualProfileV3Error) as error:
            verify_queue_profile(
                _envelope(content),
                content["canonicalUrl"],
                {"width": 1920, "height": 1080},
                9,
            )
        self.assertEqual(str(error.exception), "manual_profile_action_invalid:total_duration")

    def test_safety_total_does_not_double_count_hold_before_move(self):
        content = json.loads(json.dumps(self.content))
        content["prepareActions"] = [
            {
                "type": "drag",
                "startX": 0.1,
                "startY": 0.5,
                "endX": 0.2,
                "endY": 0.5,
                "dragDurationMs": 600_000,
                "holdBeforeMoveMs": 600_000,
            }
            for _ in range(3)
        ]
        content["prepareTiming"] = {"durationMs": 0, "endDelayMs": 0}
        self.assertEqual(
            verify_queue_profile(
                _envelope(content),
                content["canonicalUrl"],
                {"width": 1920, "height": 1080},
                9,
            ),
            content,
        )

    def test_maps_viewport_coordinates_without_screen_scaling(self):
        action = viewport_action_to_pixels(
            {"type": "drag", "startX": 0.25, "startY": 0.5, "endX": 1, "endY": 0},
            {"width": 1920, "height": 1080},
        )
        self.assertEqual((action["startX"], action["startY"]), (480, 540))
        self.assertEqual((action["endX"], action["endY"]), (1920, 0))

    def test_preserves_marker_metadata_when_mapping_layout(self):
        marker = viewport_action_to_pixels(
            {"index": 2, "label": "n-3", "x": .5, "y": .25},
            {"width": 1920, "height": 1080},
        )
        self.assertEqual(marker, {"index": 2, "label": "n-3", "x": 960, "y": 270})

    def test_shared_tracker_fixture_hash_and_coordinates(self):
        fixture = json.loads((Path(__file__).parent / "tests" / "fixtures" / "MANUAL_PROFILE_V3_FIXTURE.json").read_text(encoding="utf-8"))
        envelope = fixture["envelope"]
        content = verify_queue_profile(envelope, fixture["canonicalUrlVariants"][0], fixture["expectedCaptureEnvironment"]["viewport"], 789)
        self.assertEqual(viewport_action_to_pixels(content["prepareActions"][0], fixture["expectedCaptureEnvironment"]["viewport"])["x"], fixture["expectedPixelCoordinates"]["click"]["x"])

    def test_projection_creates_absolute_legacy_shape_and_preserves_timing(self):
        fixture = json.loads(
            (Path(__file__).parent / "tests" / "fixtures" / "MANUAL_PROFILE_V3_FIXTURE.json")
            .read_text(encoding="utf-8")
        )
        context = build_manual_profile_context(
            fixture["envelope"],
            field_present=True,
            expected_revision_id=789,
        )
        materialized = materialize_tracker_profile(
            context,
            expected_url=fixture["canonicalUrlVariants"][0],
            live_viewport=fixture["expectedCaptureEnvironment"]["viewport"],
        )
        profile = materialized.legacy_profile
        self.assertEqual(profile["version"], 2)
        self.assertEqual(profile["coordinateSpace"], "screen_1920x1080_with_menu")
        self.assertEqual(profile["prepareActions"][0]["x"], 960)
        self.assertEqual(profile["prepareActions"][0]["pressDurationMs"], 80)
        self.assertEqual(profile["layout"]["icons"][0]["x"], 192)
        self.assertEqual(profile["layout"]["slots"][0]["y"], 864)
        self.assertEqual(profile["prepareTiming"], fixture["envelope"]["content"]["prepareTiming"])
        self.assertEqual(materialized.source_token, "tracker:123@2#789")

    def test_queue_field_states_never_create_a_legacy_lookup_profile(self):
        omitted = build_manual_profile_context(None, field_present=False)
        explicit_null = build_manual_profile_context(None, field_present=True)
        self.assertEqual(omitted.field_state, "omitted")
        self.assertEqual(explicit_null.field_state, "null")
        self.assertFalse(omitted.has_queue_profile)
        self.assertFalse(explicit_null.has_queue_profile)

    def test_projection_does_not_mutate_tracker_content(self):
        fixture = json.loads(
            (Path(__file__).parent / "tests" / "fixtures" / "MANUAL_PROFILE_V3_FIXTURE.json")
            .read_text(encoding="utf-8")
        )
        content = fixture["envelope"]["content"]
        original = json.loads(json.dumps(content))
        projected = project_v3_content_to_legacy_profile(
            content,
            fixture["expectedCaptureEnvironment"]["viewport"],
        )
        self.assertEqual(content, original)
        self.assertNotEqual(projected["layout"]["icons"][0]["x"], content["layout"]["icons"][0]["x"])


if __name__ == "__main__":
    unittest.main()

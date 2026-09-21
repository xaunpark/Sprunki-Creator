import unittest

from manual_profile_v3 import profile_sha256, validate_v3_content
from mini_remix_tracker_demo import (
    ICON_PIXEL_CENTERS,
    SLOT_PIXEL_CENTERS,
    build_demo_job,
    build_demo_profile_content,
)


class MiniRemixTrackerDemoTests(unittest.TestCase):
    def test_profile_uses_screenshot_layout_and_v3_hash(self):
        content = build_demo_profile_content(created_at="2026-01-01T00:00:00+00:00")

        validate_v3_content(content)
        self.assertEqual(len(content["layout"]["icons"]), len(ICON_PIXEL_CENTERS))
        self.assertEqual(len(content["layout"]["slots"]), len(SLOT_PIXEL_CENTERS))
        self.assertEqual(content["capture"]["viewport"], {"width": 1920, "height": 1080})
        self.assertEqual(len(content["prepareActions"]), 10)
        self.assertTrue(all(action["x"] == 0.5 and action["y"] == 0.5 for action in content["prepareActions"]))

    def test_job_is_strict_mini_payload_with_no_names_or_duration(self):
        job = build_demo_job(icon_indices=[0, 10, 7], queue_id=991001)

        self.assertEqual(job["production_mode"], "mini_remix")
        self.assertEqual(job["mini_remix"], {"icon_indices": [0, 10, 7]})
        self.assertNotIn("duration", job["mini_remix"])
        self.assertNotIn("character_names", job["mini_remix"])
        profile = job["manual_profile"]
        self.assertEqual(profile["revision_id"], job["manual_profile_revision_id"])
        self.assertEqual(profile["sha256"], profile_sha256(profile["content"]))


if __name__ == "__main__":
    unittest.main()

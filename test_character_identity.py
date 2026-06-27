import unittest

from character_identity import build_character_identity_map


def icon(segment_index, costume_name, target="Icons"):
    return {
        "segmentKey": f"n-{segment_index}",
        "iconTargetName": target,
        "runtimeIndex": segment_index,
        "costumeName": costume_name,
        "visible": True,
    }


class CharacterIdentityMapTests(unittest.TestCase):
    def test_clean_case_maps_n1_to_oren(self):
        state = {"ok": True, "icons": [icon(i, f"{i:02d}-a") for i in range(1, 21)]}
        identity = build_character_identity_map(state)

        self.assertEqual(identity["status"], "verified")
        self.assertEqual(identity["segmentByCanonicalId"]["oren"], "n-1")
        self.assertEqual(identity["segmentByCanonicalId"]["black"], "n-20")
        self.assertEqual(identity["validation"]["excludedSegmentCount"], 0)

    def test_shifted_case_excludes_bonus_before_standard_icons(self):
        icons = [icon(1, "24-a", target="Icons2")]
        icons.extend(icon(i + 2, f"{i + 1:02d}-a", target="Icons2") for i in range(20))
        state = {"ok": True, "icons": icons}
        identity = build_character_identity_map(state)

        self.assertEqual(identity["status"], "verified")
        self.assertEqual(identity["segmentByCanonicalId"]["oren"], "n-2")
        self.assertIn("n-1", identity["excludedSegments"])
        self.assertEqual(identity["excludedSegments"]["n-1"]["iconNumber"], 24)

    def test_duplicate_case_chooses_group_with_complete_standard_set(self):
        icons = [icon(1, "01-a", target="Icons2")]
        icons.extend(icon(i + 2, f"{i + 1:02d}-a", target="Icons") for i in range(20))
        state = {"ok": True, "icons": icons}
        identity = build_character_identity_map(state)

        self.assertEqual(identity["status"], "verified")
        self.assertEqual(identity["source"]["bestIconGroup"], "Icons")
        self.assertEqual(identity["segmentByCanonicalId"]["oren"], "n-2")
        self.assertIn("n-1", identity["excludedSegments"])

    def test_generic_sprite_names_do_not_affect_costume_mapping(self):
        state = {
            "ok": True,
            "icons": [
                icon(i, f"{i:02d}-a", target=f"Beat {i}" if i <= 10 else f"Voice {i - 10}")
                for i in range(1, 21)
            ],
        }
        identity = build_character_identity_map(state)

        self.assertEqual(identity["status"], "verified")
        self.assertEqual(identity["source"]["bestIconGroup"], "__visible_icon_costumes__")
        self.assertEqual(identity["segmentByCanonicalId"]["raddy"], "n-2")
        self.assertEqual(identity["standardCharacters"][0]["displayName"], "Oren")


if __name__ == "__main__":
    unittest.main()

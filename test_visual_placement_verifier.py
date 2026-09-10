import unittest
from unittest.mock import Mock

import numpy as np

from placement_verifier import OperationContext, OperationResult, OperationStatus
from sprunki_game_player import SprunkiGamePlayer
from visual_placement_verifier import (
    VISUAL_INCONCLUSIVE,
    VISUAL_VERIFIED,
    VisualROI,
    VisualThresholds,
    verify_transition,
)


def _polo_frame(*, background=(90, 90, 90), polo=(35, 35, 35)):
    frame = np.empty((64, 64, 3), dtype=np.uint8)
    frame[:] = background
    frame[17:47, 17:47] = polo
    return frame


def _character_frame(color=(220, 40, 40), *, background=(90, 90, 90)):
    frame = _polo_frame(background=background)
    frame[17:47, 17:47] = color
    return frame


class VisualPlacementVerifierTests(unittest.TestCase):
    def setUp(self):
        self.roi = VisualROI(0, 0, 64, 64, slot_index=3)
        self.polo = _polo_frame()
        self.character = _character_frame()

    def test_polo_to_character_requires_stable_slot_local_change(self):
        result = verify_transition(
            [self.polo, self.polo],
            [self.character, self.character],
            self.roi,
            "place",
        )

        self.assertEqual(result.status, VISUAL_VERIFIED)
        self.assertEqual(result.details["directionEvidence"], "polo_to_character")
        self.assertLessEqual(result.details["transitionSsim"], 0.85)
        self.assertLess(result.details["globalChangeRatio"], 0.30)

    def test_character_to_polo_requires_same_slot_empty_reference(self):
        result = verify_transition(
            [self.character, self.character],
            [self.polo, self.polo],
            self.roi,
            "return",
            empty_reference_captures=[self.polo, self.polo],
        )

        self.assertEqual(result.status, VISUAL_VERIFIED)
        self.assertEqual(result.details["directionEvidence"], "character_to_polo")
        self.assertGreaterEqual(result.details["afterEmptyReferenceSsim"], 0.90)

    def test_return_without_slot_reference_is_inconclusive(self):
        result = verify_transition(
            [self.character, self.character],
            [self.polo, self.polo],
            self.roi,
            "return",
        )

        self.assertEqual(result.status, VISUAL_INCONCLUSIVE)
        self.assertEqual(result.message, "empty_reference_missing")

    def test_background_animation_is_not_target_success(self):
        before_a = _polo_frame(background=(90, 90, 90))
        before_b = _polo_frame(background=(125, 125, 125))
        after_a = _polo_frame(background=(90, 90, 90))
        after_b = _polo_frame(background=(125, 125, 125))

        result = verify_transition(
            [before_a, before_b],
            [after_a, after_b],
            self.roi,
            "place",
        )

        self.assertEqual(result.status, VISUAL_INCONCLUSIVE)
        self.assertIn(
            result.message,
            {"target_did_not_change", "target_change_ratio_too_small", "target_roi_too_dynamic"},
        )

    def test_global_scene_change_is_inconclusive(self):
        after_a = np.full((64, 64, 3), 230, dtype=np.uint8)
        after_b = after_a.copy()

        result = verify_transition(
            [self.polo, self.polo],
            [after_a, after_b],
            self.roi,
            "place",
        )

        self.assertEqual(result.status, VISUAL_INCONCLUSIVE)
        self.assertEqual(result.message, "global_scene_changed")

    def test_post_action_animation_is_inconclusive(self):
        result = verify_transition(
            [self.polo, self.polo],
            [self.character, self.polo],
            self.roi,
            "place",
        )

        self.assertEqual(result.status, VISUAL_INCONCLUSIVE)
        self.assertEqual(result.message, "post_action_not_stable")

    def test_full_screenshot_mapping_is_cropped_using_viewport_geometry(self):
        before = np.full((128, 128, 3), 90, dtype=np.uint8)
        before[49:79, 49:79] = 35
        after = before.copy()
        after[49:79, 49:79] = (220, 40, 40)
        roi = VisualROI(32, 32, 64, 64, slot_index=2)

        result = verify_transition(
            [
                {"image": before, "viewport_width": 128, "viewport_height": 128},
                {"image": before, "viewport_width": 128, "viewport_height": 128},
            ],
            [
                {"image": after, "viewport_width": 128, "viewport_height": 128},
                {"image": after, "viewport_width": 128, "viewport_height": 128},
            ],
            roi,
            "place",
        )

        self.assertEqual(result.status, VISUAL_VERIFIED)
        self.assertEqual(result.details["roi"]["slotIndex"], 2)

    def test_aggregate_primary_success_does_not_create_visual_reference(self):
        player = SprunkiGamePlayer.__new__(SprunkiGamePlayer)
        player._visual_empty_references = {}
        player._board_generation = 0
        arm = {
            "direction": "place",
            "targetSlot": 3,
            "roi": self.roi,
            "geometryFingerprint": "g1",
            "beforeFrames": (self.polo, self.polo),
        }
        result = OperationResult(
            OperationStatus.VERIFIED,
            direction="place",
            details={
                "targetSpecific": False,
                "aggregateFallback": True,
                "softVerified": True,
            },
        )

        returned = player._visual_complete_fallback(
            arm,
            result,
            action_attempted=True,
        )

        self.assertIs(returned, result)
        self.assertEqual(player._visual_empty_references, {})

    def test_primary_return_success_does_not_overwrite_empty_reference(self):
        player = SprunkiGamePlayer.__new__(SprunkiGamePlayer)
        player._visual_empty_references = {
            (0, 3): {
                "frames": (self.polo, self.polo),
                "geometryFingerprint": "g1",
            }
        }
        player._board_generation = 0
        arm = {
            "direction": "return",
            "targetSlot": 3,
            "roi": self.roi,
            "geometryFingerprint": "g1",
            "beforeFrames": (self.character, self.character),
        }
        result = OperationResult(
            OperationStatus.VERIFIED,
            direction="return",
            details={"targetSpecific": True},
        )

        player._visual_complete_fallback(arm, result, action_attempted=True)

        self.assertTrue(np.array_equal(player._visual_empty_references[(0, 3)]["frames"][0], self.polo))

    def test_unresolved_physical_action_can_be_promoted_by_image_without_cache_data(self):
        player = SprunkiGamePlayer.__new__(SprunkiGamePlayer)
        player._visual_empty_references = {}
        player._board_generation = 0
        player.visual_thresholds = VisualThresholds()
        player._visual_capture_after_frames = lambda arm: (self.character, self.character)
        arm = {
            "direction": "place",
            "targetSlot": 3,
            "roi": self.roi,
            "geometryFingerprint": "g1",
            "beforeFrames": (self.polo, self.polo),
        }
        result = OperationResult(
            OperationStatus.FAILED,
            direction="place",
            message="expected_transition_not_observed",
        )

        returned = player._visual_complete_fallback(
            arm,
            result,
            action_attempted=True,
        )

        self.assertEqual(returned.status, OperationStatus.VERIFIED)
        self.assertTrue(returned.details["visualFallback"])
        self.assertEqual(returned.details["verificationMethod"], "image_fallback")
        self.assertTrue(returned.target_verified)
        self.assertEqual(len(player._visual_empty_references), 1)

    def test_image_fallback_is_not_called_when_action_was_not_sent(self):
        player = SprunkiGamePlayer.__new__(SprunkiGamePlayer)
        arm = {
            "direction": "place",
            "targetSlot": 3,
            "roi": self.roi,
            "geometryFingerprint": "g1",
            "beforeFrames": (self.polo, self.polo),
        }
        result = OperationResult(
            OperationStatus.UNKNOWN,
            direction="place",
            message="expected_transition_not_observed",
        )

        returned = player._visual_complete_fallback(
            arm,
            result,
            action_attempted=False,
        )

        self.assertIs(returned, result)
        self.assertNotIn("visualFallback", returned.details)

    def test_carpet_primary_aggregate_transition_is_detected_without_count_progress(self):
        player = SprunkiGamePlayer.__new__(SprunkiGamePlayer)
        before = {
            "characters_count": 5,
            "putted_characters": 4,
            "polos_array": None,
        }
        after = {
            "characters_count": 5,
            "putted_characters": 3,
            "polos_array": None,
        }

        evaluation = player._carpet_aggregate_transition_evaluation(before, after)

        self.assertEqual(evaluation.status, OperationStatus.VERIFIED)
        self.assertTrue(evaluation.aggregate_fallback)
        self.assertFalse(evaluation.target_specific)

    def test_return_completion_event_is_emitted_once_for_visual_success(self):
        player = SprunkiGamePlayer.__new__(SprunkiGamePlayer)
        player.obs_controller = Mock()
        context = OperationContext.begin("return", 3, operation_id="return-visual-1")
        result = OperationResult(
            OperationStatus.ALREADY_EMPTY,
            operation_id=context.operation_id,
            direction="return",
            message="return_verified_by_image_fallback",
            details={
                "targetSpecific": True,
                "visualFallback": True,
                "verificationMethod": "image_fallback",
            },
        )

        player._log_return_completed_successfully(context, result)
        player._log_return_completed_successfully(context, result)

        self.assertEqual(player.obs_controller.log_event_timestamp.call_count, 1)
if __name__ == "__main__":
    unittest.main()

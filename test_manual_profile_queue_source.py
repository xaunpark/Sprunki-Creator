import json
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from manual_profile_v3 import (
    ManualProfileV3Error,
    build_manual_profile_context,
    materialize_tracker_profile,
)
from sprunki_game_player import SprunkiGamePlayer


class QueueManualProfileSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture_path = Path(__file__).parent / "tests" / "fixtures" / "MANUAL_PROFILE_V3_FIXTURE.json"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        cls.fixture = fixture
        raw_context = build_manual_profile_context(
            fixture["envelope"],
            field_present=True,
            expected_revision_id=789,
        )
        cls.context = materialize_tracker_profile(
            raw_context,
            expected_url=fixture["expectedCanonicalUrl"],
            live_viewport=fixture["expectedCaptureEnvironment"]["viewport"],
        )

    def _player(self):
        player = SprunkiGamePlayer(stop_event=threading.Event())
        player.config = {"currentUrl": self.fixture["expectedCanonicalUrl"], "settings": {}}
        return player

    def test_explicit_null_uses_no_profile_without_local_lookup(self):
        player = self._player()
        player.manual_profile_context = build_manual_profile_context(None, field_present=True)
        with patch("sprunki_game_player.find_profile", side_effect=AssertionError("local lookup")):
            self.assertEqual(player._resolve_manual_profile(), (None, None))

    def test_omitted_uses_no_profile_without_local_lookup(self):
        player = self._player()
        player.manual_profile_context = build_manual_profile_context(None, field_present=False)
        with patch("sprunki_game_player.find_profile", side_effect=AssertionError("local lookup")):
            self.assertEqual(player._resolve_manual_profile(), (None, None))

    def test_tracker_context_precedes_local_lookup(self):
        player = self._player()
        player.manual_profile_context = self.context
        with patch.object(
            player,
            "_resolve_queue_manual_profile",
            return_value=(self.context.legacy_profile, self.context.source_token),
        ) as resolve_queue, patch("sprunki_game_player.find_profile", side_effect=AssertionError("local lookup")):
            profile, source = player._resolve_manual_profile()
        resolve_queue.assert_called_once_with(self.context)
        self.assertEqual(profile, self.context.legacy_profile)
        self.assertEqual(source, "tracker:123@2#789")

    def test_invalid_tracker_object_never_falls_back_to_local_lookup(self):
        player = self._player()
        player.manual_profile_context = build_manual_profile_context(
            {"revision_id": 789, "error_code": "manual_profile_revision_missing"},
            field_present=True,
            expected_revision_id=789,
        )
        with patch.object(
            player,
            "_resolve_queue_manual_profile",
            side_effect=ManualProfileV3Error("manual_profile_revision_missing"),
        ), patch("sprunki_game_player.find_profile", side_effect=AssertionError("local lookup")):
            with self.assertRaises(ManualProfileV3Error):
                player._resolve_queue_manual_profile(player.manual_profile_context)
            profile, source = player._resolve_manual_profile()
        self.assertIsNone(profile)
        self.assertIsNone(source)
        self.assertEqual(player._manual_profile_resolution_error, "manual_profile_revision_missing")

    def test_cdp_unavailable_has_stable_profile_failure(self):
        player = self._player()
        player.manual_profile_context = self.context
        with patch.object(player, "_get_websocket_url", return_value=None):
            with self.assertRaises(ManualProfileV3Error) as error:
                player._resolve_queue_manual_profile(self.context)
        self.assertEqual(str(error.exception), "manual_profile_cdp_inconclusive")


if __name__ == "__main__":
    unittest.main()

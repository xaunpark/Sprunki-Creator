import json
import tempfile
import unittest
from pathlib import Path

from recorded_identity_resolver import resolve


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def timestamp_events(icon_count, omit_start_listen_icon_index=False):
    events = []
    for icon_index in range(icon_count):
        events.append({"event": "successful_drag_started", "timestamp": icon_index * 2, "metadata": {"icon_index": icon_index}})
        metadata = {"segmentKey": f"n-{icon_index + 1}"}
        if not omit_start_listen_icon_index:
            metadata["icon_index"] = icon_index
        events.append({"event": "start_listen", "timestamp": icon_index * 2 + 0.1, "metadata": metadata})
        events.append({"event": "end_listen", "timestamp": icon_index * 2 + 1.1, "metadata": {}})
    return {"events": events}


class RecordedIdentityResolverTests(unittest.TestCase):
    def test_extra_before_standard_maps_by_icon_index(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            source = project / "source.json"
            timestamps = project / "showcase.timestamps.json"
            sidecar = project / "source.icon_identity.json"
            write_json(source, {"layout": {"icons": [{"index": i, "x": i, "y": 10} for i in range(3)]}})
            write_json(timestamps, timestamp_events(3))
            write_json(sidecar, {"iconIdentities": [
                {"iconIndex": 0, "costumeName": "32-a"},
                {"iconIndex": 1, "costumeName": "01-a"},
                {"iconIndex": 2, "costumeName": "02-a"},
            ]})

            payload = resolve(project, str(source), str(timestamps), str(sidecar))

            self.assertEqual(payload["status"], "needs_review")
            self.assertEqual(payload["segmentIdentities"][0]["status"], "extra")
            self.assertIsNone(payload["segmentIdentities"][0]["canonicalId"])
            self.assertEqual(payload["segmentIdentities"][1]["canonicalId"], "oren")
            self.assertEqual(payload["segmentIdentities"][2]["canonicalId"], "raddy")

    def test_start_listen_can_fallback_to_previous_drag_icon_index(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            source = project / "source.json"
            timestamps = project / "showcase.timestamps.json"
            sidecar = project / "source.icon_identity.json"
            write_json(source, {"layout": {"icons": [{"index": 0, "x": 1, "y": 1}]}})
            write_json(timestamps, timestamp_events(1, omit_start_listen_icon_index=True))
            write_json(sidecar, {"iconIdentities": [{"iconIndex": 0, "costumeName": "01-a"}]})

            payload = resolve(project, str(source), str(timestamps), str(sidecar))

            self.assertEqual(payload["segmentIdentities"][0]["segmentKey"], "n-1")
            self.assertEqual(payload["segmentIdentities"][0]["canonicalId"], "oren")

    def test_missing_source_json_is_unavailable(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            timestamps = project / "showcase.timestamps.json"
            write_json(timestamps, timestamp_events(1))

            payload = resolve(project, None, str(timestamps), None)

            self.assertEqual(payload["status"], "unavailable")
            self.assertIn("missing_source_json", payload["validation"]["errors"])

    def test_legacy_search_mode_without_runtime_coordinates_needs_review(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            source = project / "source.json"
            timestamps = project / "showcase.timestamps.json"
            sidecar = project / "source.icon_identity.json"
            write_json(source, {"layout": {"icons": [{"index": i, "x": i, "y": 10} for i in range(20)]}})
            events = []
            for icon_index in range(20):
                metadata = {"icon_index": icon_index, "method": "cache_hit"}
                if icon_index == 0:
                    metadata["method"] = "search_mode"
                events.append({"event": "successful_drag_started", "timestamp": icon_index * 2, "metadata": metadata})
                events.append({
                    "event": "start_listen",
                    "timestamp": icon_index * 2 + 0.1,
                    "metadata": {"icon_index": icon_index, "segmentKey": f"n-{icon_index + 1}"},
                })
            write_json(timestamps, {"events": events})
            write_json(sidecar, {"iconIdentities": [
                {"iconIndex": i, "costumeName": f"{i + 1:02d}-a"}
                for i in range(20)
            ]})

            payload = resolve(project, str(source), str(timestamps), str(sidecar))

            self.assertEqual(payload["status"], "needs_review")
            self.assertIn(
                "search_mode_without_runtime_coordinates:n-1",
                payload["validation"]["warnings"],
            )

    def test_search_mode_with_runtime_coordinates_can_verify(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            source = project / "source.json"
            timestamps = project / "showcase.timestamps.json"
            sidecar = project / "source.icon_identity.json"
            write_json(source, {"layout": {"icons": [{"index": i, "x": i, "y": 10} for i in range(20)]}})
            events = []
            for icon_index in range(20):
                metadata = {
                    "icon_index": icon_index,
                    "method": "search_mode" if icon_index == 0 else "cache_hit",
                    "pickupIconOffsetIdx": 0,
                    "pickupX": 100 + icon_index,
                    "pickupY": 900,
                    "slotOffsetIdx": 0,
                    "dropX": 400,
                    "dropY": 400,
                }
                events.append({"event": "successful_drag_started", "timestamp": icon_index * 2, "metadata": metadata})
                events.append({
                    "event": "start_listen",
                    "timestamp": icon_index * 2 + 0.1,
                    "metadata": {"icon_index": icon_index, "segmentKey": f"n-{icon_index + 1}"},
                })
            write_json(timestamps, {"events": events})
            write_json(sidecar, {"iconIdentities": [
                {"iconIndex": i, "costumeName": f"{i + 1:02d}-a"}
                for i in range(20)
            ]})

            payload = resolve(project, str(source), str(timestamps), str(sidecar))

            self.assertEqual(payload["status"], "verified")
            self.assertEqual(payload["segmentIdentities"][0]["pickupX"], 100)


if __name__ == "__main__":
    unittest.main()

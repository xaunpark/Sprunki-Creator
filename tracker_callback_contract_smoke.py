"""Offline smoke test for the Sprunki Tracker diagnostic callback contract.

This deliberately uses a fake HTTP session.  It exercises the real
TrackerAPIClient payload construction without claiming or mutating a real
Tracker queue.
"""

from __future__ import annotations

import json as jsonlib
import uuid

from creator_api_client import TrackerAPIClient
from tracker_reporting import SCHEMA_VERSION


class FakeResponse:
    def __init__(self, status_code: int, body: str):
        self.status_code = status_code
        self.text = body


class FakeTrackerSession:
    def __init__(self, queue_id: int):
        self.queue_id = queue_id
        self.calls = []

    def put(self, url, json, timeout):
        callback = "terminal" if json.get("final") else "status"
        body = jsonlib.dumps(
            {"accepted": True, "callback": callback, "queue_id": self.queue_id}
        )
        self.calls.append({
            "method": "PUT",
            "url": url,
            "payload": json,
            "response_body": body,
        })
        return FakeResponse(200, body)

    def post(self, url, json, timeout):
        body = jsonlib.dumps(
            {"accepted": True, "callback": "progress", "queue_id": self.queue_id}
        )
        self.calls.append({
            "method": "POST",
            "url": url,
            "payload": json,
            "response_body": body,
        })
        return FakeResponse(200, body)


def main() -> None:
    queue_id = 900001
    report_id = str(uuid.uuid4())
    attempt_nonce = str(uuid.uuid4())

    client = TrackerAPIClient.__new__(TrackerAPIClient)
    client.tracker_url = "http://fake-tracker.test"
    client.timeout = 3
    client.service_name = "creator-contract-smoke"
    client.session = FakeTrackerSession(queue_id)
    client.bind_job_report(queue_id, report_id, attempt_nonce)

    assert client.update_job_status(
        queue_id,
        "processing",
        details={"stage": "starting"},
        report_id=report_id,
        attempt_nonce=attempt_nonce,
        sequence=0,
        stage="video_processing",
        final=False,
    )
    assert client.update_job_progress(
        queue_id,
        "video_processing",
        "starting",
        50,
        "active",
    )

    diagnostic_report = {
        "schema_version": SCHEMA_VERSION,
        "report_id": report_id,
        "attempt_nonce": attempt_nonce,
        "status": "failed",
        "job": {"queue_id": queue_id, "mod_id": 196711, "mode": "master"},
        "mod": {},
        "summary": {
            "status": "failed",
            "failure_code": "placement_not_verified",
            "blocking_stage": "placement",
        },
        "timing": {},
        "phases": {
            "statuses": {
                "json_generation": "PASS",
                "runtime_entry": "PASS",
                "placement": "FAIL",
                "cleanup": "PASS",
            },
            "placement": {
                "status": "FAIL",
                "reason": "placement_not_verified",
            },
        },
        "quality_gate": {
            "passed": False,
            "status": "FAIL",
            "checks": [
                {
                    "name": "placement",
                    "passed": False,
                    "status": "FAIL",
                    "required": True,
                    "evidence": {"reason": "placement_not_verified"},
                }
            ],
        },
        "outputs": {},
        "logs": {},
    }
    assert client.update_job_status(
        queue_id,
        "failed",
        report_id=report_id,
        attempt_nonce=attempt_nonce,
        diagnostic_report=diagnostic_report,
        final=True,
    )

    calls = client.session.calls
    payloads = [call["payload"] for call in calls]
    assert [payload["sequence"] for payload in payloads] == [0, 1, 2]
    assert {payload["report_id"] for payload in payloads} == {report_id}
    assert {payload["attempt_nonce"] for payload in payloads} == {attempt_nonce}
    assert payloads[-1]["final"] is True
    assert payloads[-1]["diagnostic_report"]["attempt_nonce"] == attempt_nonce
    assert payloads[-1]["diagnostic_report"]["report_id"] == report_id
    assert all(call["method"] in {"PUT", "POST"} for call in calls)

    print(jsonlib.dumps({
        "mode": "offline_fake_tracker_contract_test",
        "queue_id": queue_id,
        "sequence_sent": [payload["sequence"] for payload in payloads],
        "payload_keys": [sorted(payload.keys()) for payload in payloads],
        "terminal_diagnostic_keys": sorted(payloads[-1]["diagnostic_report"].keys()),
        "report_id": report_id,
        "report_id_consistent": len({payload["report_id"] for payload in payloads}) == 1,
        "attempt_nonce": attempt_nonce,
        "attempt_nonce_consistent": len({payload["attempt_nonce"] for payload in payloads}) == 1,
        "response_bodies": [
            {"url": call["url"], "http_status": 200, "body": call["response_body"]}
            for call in calls
        ],
        "terminal_callback_http_200": True,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

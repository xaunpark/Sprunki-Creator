"""Replay a compact Creator terminal callback from the Tracker outbox.

Example:
    python replay_tracker_terminal.py --queue-id 3118 --tracker-url http://localhost:5000
"""

from __future__ import annotations

import argparse
import sys

from creator_api_client import TrackerAPIClient


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Replay a compact terminal callback saved by Sprunki Creator."
    )
    parser.add_argument("--queue-id", type=int, default=3118)
    parser.add_argument("--tracker-url", default="http://localhost:5000")
    args = parser.parse_args()

    client = TrackerAPIClient(args.tracker_url)
    accepted = client.resend_terminal_callback(args.queue_id)
    if accepted:
        print(f"[Tracker] Terminal callback replay accepted for queue {args.queue_id}")
        return 0
    print(f"[Tracker] Terminal callback replay failed for queue {args.queue_id}")
    return 1


if __name__ == "__main__":
    sys.exit(main())

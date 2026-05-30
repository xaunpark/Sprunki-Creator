"""
Shared game-entry helper for runtime inspection scripts.

The inspection scripts should observe the actual in-game layout, not the
pre-game/intro screen. SprunkiGamePlayer.navigate_and_prepare_game() opens the
mod, clicks Run/Fullscreen, and enters browser fullscreen. The normal pipeline
then performs an intro/action-button skip step separately. This helper keeps
the test scripts aligned with that pipeline.
"""

from __future__ import annotations

import time
from typing import Any


def complete_game_entry_sequence(player: Any, post_skip_wait: float = 1.5) -> None:
    """
    Run the same post-navigation entry actions used by the production flow.

    This scans/clicks action buttons such as Skip/Play/Start/Intro when present,
    presses space for mods that require a key press, and performs the configured
    blind center clicks via CDP as a fallback.
    """
    print("\nPOST-NAVIGATION GAME ENTRY")
    print("Running intro/action skip sequence before runtime inspection...")

    try:
        if hasattr(player, "verify_cdp_ready"):
            if not player.verify_cdp_ready(max_attempts=3):
                print("Warning: CDP readiness check failed; trying intro skip anyway.")
    except Exception as exc:
        print(f"Warning: CDP readiness check raised: {exc}")

    try:
        if not player.intro_skip_via_cdp():
            print("Warning: intro_skip_via_cdp returned False; continuing with current screen.")
    except Exception as exc:
        print(f"Warning: intro/action skip raised: {exc}")

    if post_skip_wait > 0:
        print(f"Waiting {post_skip_wait:.1f}s after intro/action skip...")
        time.sleep(post_skip_wait)

# -*- coding: utf-8 -*-
"""Minimal stub for VPS-friendly checks."""
from typing import Tuple

class PageLoadVerifier:
    def __init__(self, debug_port: int = 9222) -> None:
        self.debug_port = debug_port

    def verify_page_fully_loaded(self, target_url: str, max_retries: int = 3) -> Tuple[bool, str]:
        return True, 'Stub verifier: assumed ready'

    def wait_for_page_ready_with_refresh(self, target_url: str) -> Tuple[bool, str]:
        return True, 'Stub verifier: page ready (no refresh required)'

# structured_config.py
# [PHASE 2] NEW FILE - Defines the single, immutable source of truth for configuration.

from dataclasses import dataclass, field
from typing import Dict, List, Any

# Để code gọn gàng, chúng ta chia config thành các dataclass nhỏ hơn.
@dataclass(frozen=True)
class ChromeConfig:
    """Chrome-specific settings."""
    executable_paths: List[str]
    debugging_port: int
    user_data_dir: str

@dataclass(frozen=True)
class GameTimingConfig:
    """Game timing settings."""
    drag_delays: Dict[str, Dict[str, float]]
    page_load_wait: int
    game_load_wait: int
    fullscreen_wait: int
    intro_skip_clicks: int

@dataclass(frozen=True)
class VerificationConfig:
    """Verification settings."""
    page_verification_enabled: bool
    game_verification_enabled: bool
    page_verification_timeout_sec: float

@dataclass(frozen=True)
class GameConfig:
    """
    Main immutable configuration object. This is the single source of truth.
    It is created once at application startup.
    """
    chrome: ChromeConfig
    timing: GameTimingConfig
    verification: VerificationConfig
    current_url: str
    layout: Dict[str, Any]
    game_settings: Dict[str, Any]
    remix_library: List[Dict[str, Any]]
    
    @classmethod
    def from_config_manager(cls) -> 'GameConfig':
        """Factory method to create the config object from config.json."""
        print("📋 Loading structured configuration from single source...")
        from config_manager import get_config
        raw_config = get_config()

        chrome_raw = raw_config.get('chrome', {})
        game_raw = raw_config.get('game', {})
        verification_raw = raw_config.get('page_verification', {})

        return cls(
            chrome=ChromeConfig(
                executable_paths=chrome_raw.get('executable_paths', []),
                debugging_port=chrome_raw.get('debugging_port', 9222),
                user_data_dir=chrome_raw.get('user_data_dir', 'chrome_permanent_profile')
            ),
            timing=GameTimingConfig(
                # DRAG_DELAYS được hardcode ở đây vì nó là hằng số nghiệp vụ, không phải config người dùng
                drag_delays={
                    'first_icon': {'approach': 0.8, 'focus': 0.6, 'hold': 0.8, 'drag': 1.5, 'drop': 0.8, 'post_drop': 0.5},
                    'normal_icon': {'approach': 0.4, 'focus': 0.3, 'hold': 0.6, 'drag': 1.0, 'drop': 0.6, 'post_drop': 0.4}
                },
                page_load_wait=game_raw.get('page_load_wait', 20),
                game_load_wait=game_raw.get('game_load_wait', 10),
                fullscreen_wait=game_raw.get('fullscreen_wait', 8),
                intro_skip_clicks=game_raw.get('intro_skip_clicks', 10)
            ),
            verification=VerificationConfig(
                page_verification_enabled=verification_raw.get('enabled', True),
                game_verification_enabled=raw_config.get('game_load_verification', {}).get('enabled', True),
                page_verification_timeout_sec=verification_raw.get('timeout_ms', 120000) / 1000
            ),
            current_url=raw_config.get('currentUrl', ''),
            layout=raw_config.get('layout', {}),
            game_settings=raw_config.get('gameSettings', {}),
            remix_library=raw_config.get('remixLibrary', [])
        )
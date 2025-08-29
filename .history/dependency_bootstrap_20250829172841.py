# dependency_bootstrap.py
# [PHASE 4] UPDATE - Use clean constructors and create the new CleanSprunkiGamePlayer.

from structured_config import GameConfig
from event_system import EventBus
from browser_manager import BrowserManager
from verification_system import VerificationSystem
from game_operations import GameOperations
from workflow_manager import WorkflowManager
from carpet_bombing import CarpetBombing
# [PHASE 4] Import the new clean player
from sprunki_game_player import CleanSprunkiGamePlayer

class DependencyBootstrap:
    """Manages the creation and wiring of application dependencies."""
    
    def __init__(self):
        print("🚀 [Bootstrap] Initializing core components...")
        self.config = GameConfig.from_config_manager()
        self.event_bus = EventBus()
        print("✅ [Bootstrap] Core components (Config, EventBus) are ready.")

    def create_clean_player(self) -> CleanSprunkiGamePlayer:
        """
        [PHASE 4] Creates a fully wired, clean game player instance.
        """
        print("\n--- [Bootstrap] Starting CLEAN Dependency Wiring Process ---")
        
        # 1. Create managers using their new, clean constructors
        print("   -> Creating BrowserManager...")
        browser = BrowserManager(config=self.config, event_bus=self.event_bus)
        
        print("   -> Creating VerificationSystem...")
        verifier = VerificationSystem(browser_manager=browser, config=self.config, event_bus=self.event_bus)
        
        print("   -> Creating GameOperations...")
        game_ops = GameOperations(
            browser_manager=browser,
            verification_system=verifier,
            config=self.config,
            event_bus=self.event_bus
        )
        
        print("   -> Creating WorkflowManager...")
        workflow = WorkflowManager(
            game_operations=game_ops,
            config=self.config,
            event_bus=self.event_bus
        )
        
        # CarpetBombing và các manager khác có thể được thêm vào đây theo cách tương tự
        
        # 2. Create the new CleanSprunkiGamePlayer, injecting all dependencies
        print("   -> Creating CleanSprunkiGamePlayer...")
        player = CleanSprunkiGamePlayer(
            browser=browser,
            verifier=verifier,
            game_ops=game_ops,
            workflow=workflow,
            config=self.config,
            event_bus=self.event_bus
        )

        print("--- [Bootstrap] CLEAN Dependency Wiring Complete ---")
        return player
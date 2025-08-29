# dependency_bootstrap.py
# [PHASE 3] NEW FILE - The Dependency Injection Container.
# This class is responsible for creating and wiring all major components.

# Import các lớp cụ thể và config
from structured_config import GameConfig
from event_system import EventBus
from browser_manager import BrowserManager
from verification_system import VerificationSystem
from game_operations import GameOperations
from workflow_manager import WorkflowManager
from carpet_bombing import CarpetBombing
from sprunki_game_player import SprunkiGamePlayer

class DependencyBootstrap:
    """Manages the creation and wiring of application dependencies."""
    
    def __init__(self):
        # 1. Load config and create event bus ONCE.
        print("🚀 [Bootstrap] Initializing core components...")
        self.config = GameConfig.from_config_manager()
        self.event_bus = EventBus()
        print("✅ [Bootstrap] Core components (Config, EventBus) are ready.")

    def create_and_wire_components(self) -> SprunkiGamePlayer:
        """
        Creates all manager instances in the correct order and injects their dependencies.
        Returns a fully configured SprunkiGamePlayer instance ready to be used.
        """
        print("\n--- [Bootstrap] Starting Dependency Wiring Process ---")
        
        # 2. Create managers in dependency order (from lowest to highest level)
        print("   -> Creating BrowserManager...")
        browser = BrowserManager(player_instance=None, event_bus=self.event_bus, config=self.config)
        
        print("   -> Creating VerificationSystem...")
        verifier = VerificationSystem(browser_manager=browser, event_bus=self.event_bus, config=self.config)
        
        print("   -> Creating GameOperations...")
        game_ops = GameOperations(player_instance=None, event_bus=self.event_bus, config=self.config)
        # Manually inject dependencies that were previously accessed via self.player
        game_ops.browser_manager = browser
        game_ops.verification_system = verifier
        
        print("   -> Creating CarpetBombing...")
        carpet_bomb = CarpetBombing(player_instance=None, event_bus=self.event_bus, config=self.config)
        # Manually inject dependencies
        carpet_bomb.game_operations = game_ops
        carpet_bomb.verification_system = verifier
        carpet_bomb.browser_manager = browser
        
        print("   -> Creating WorkflowManager...")
        workflow = WorkflowManager(player_instance=None, event_bus=self.event_bus, config=self.config)
        # Manually inject dependencies
        workflow.game_operations = game_ops
        workflow.verification_system = verifier
        
        print("   -> Creating main SprunkiGamePlayer...")
        # 3. Create the main player object.
        # It still uses the old __init__ for now, but we will replace its managers.
        player = SprunkiGamePlayer(event_bus=self.event_bus)

        # 4. CRITICAL STEP: Replace the player's internally-created managers
        # with our cleanly instantiated and wired ones.
        print("   -> Injecting clean dependencies into SprunkiGamePlayer...")
        player.structured_config = self.config
        player.browser_manager = browser
        player.verification_system = verifier
        player.game_operations = game_ops
        player.workflow_manager = workflow
        player.carpet_bombing = carpet_bomb
        
        # Also, manually set the 'player' reference on the managers for backward compatibility
        # This allows old code inside the managers to continue working during the transition.
        # This will be removed in Phase 5.
        browser.player = player
        verifier.player = player # Verifier không có `player`, nhưng thêm để an toàn
        game_ops.player = player
        workflow.player = player
        carpet_bomb.player = player

        print("--- [Bootstrap] Dependency Wiring Complete ---")
        return player
# obs_event_handler.py
# [PHASE 1] NEW FILE - Decouples OBS logic from game workflows.

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sprunki_obs_controller import SprunkiController
    from event_system import EventBus

class OBSEventHandler:
    """
    Handles all game events that need to trigger an action in OBS.
    This class acts as the bridge between the game logic and the OBS controller,
    allowing for loose coupling.
    """
    def __init__(self, obs_controller: 'SprunkiController', event_bus: 'EventBus'):
        self.obs_controller = obs_controller
        self.event_bus = event_bus
        self._subscribe_to_events()
        print("🎥 OBSEventHandler initialized and subscribed to game events.")

    def _subscribe_to_events(self):
        """Subscribes to all relevant events from the event bus."""
        self.event_bus.subscribe('workflow_started', self._on_workflow_started)
        self.event_bus.subscribe('workflow_completed', self._on_workflow_completed)
        self.event_bus.subscribe('drag_completed', self._on_drag_completed)
        self.event_bus.subscribe('browser_ready', self._on_browser_ready)
        self.event_bus.subscribe('horror_transition_started', self._on_horror_transition_started)
        self.event_bus.subscribe('horror_transition_completed', self._on_horror_transition_completed)
        self.event_bus.subscribe('remix_playback_started', self._on_remix_playback_started)
        self.event_bus.subscribe('remix_playback_completed', self._on_remix_playback_completed)


    def _on_workflow_started(self, event):
        """Handles the start of a major workflow (e.g., showcasing an icon)."""
        data = event.get('data', {})
        workflow_type = data.get('type', 'unknown')
        icon_index = data.get('icon_index', 'N/A')
        print(f"🎥 OBS: Received 'workflow_started' event for '{workflow_type}'")
        self.obs_controller.log_event_timestamp(
            event_name=f"start_{workflow_type}",
            label=f"Started {workflow_type} for icon {icon_index}"
        )

    def _on_workflow_completed(self, event):
        """Handles the completion of a major workflow."""
        data = event.get('data', {})
        workflow_type = data.get('type', 'unknown')
        success = data.get('success', False)
        icon_index = data.get('icon_index', 'N/A')
        print(f"🎥 OBS: Received 'workflow_completed' event for '{workflow_type}'")
        self.obs_controller.log_event_timestamp(
            event_name=f"end_{workflow_type}",
            label=f"Completed {workflow_type} for icon {icon_index} - {'Success' if success else 'Failed'}"
        )

    def _on_drag_completed(self, event):
        """Handles the completion of a drag-and-drop operation."""
        data = event.get('data', {})
        icon_index = data.get('icon_index', 'N/A')
        target_slot_index = data.get('target_slot_index', 'N/A')
        print(f"🎥 OBS: Received 'drag_completed' event for icon {icon_index}.")
        self.obs_controller.log_event_timestamp(
            event_name=f"drag_icon_{icon_index}_to_slot_{target_slot_index}",
            label=f"Dragged icon {icon_index} to slot {target_slot_index}"
        )

    def _on_browser_ready(self, event):
        """Handles the browser becoming ready."""
        data = event.get('data', {})
        url = data.get('url', 'unknown')
        print(f"🎥 OBS: Received 'browser_ready' event.")
        self.obs_controller.log_event_timestamp(
            event_name="browser_ready",
            label=f"Browser ready for {url}"
        )
        
    def _on_horror_transition_started(self, event):
        self.obs_controller.log_event_timestamp(
            event_name="start_horror_transition",
            label="Dragging horror icon to trigger transition"
        )

    def _on_horror_transition_completed(self, event):
        self.obs_controller.log_event_timestamp(
            event_name="end_horror_transition",
            label="Horror transition visual effect completed"
        )
        
    def _on_remix_playback_started(self, event):
        data = event.get('data', {})
        remix_name = data.get('remix_name', 'unknown')
        self.obs_controller.log_event_timestamp(
            event_name="start_remix_playback",
            label=f"Start playback for {remix_name}",
            metadata=data  # Pass all remix data as metadata
        )

    def _on_remix_playback_completed(self, event):
        data = event.get('data', {})
        remix_name = data.get('remix_name', 'unknown')
        self.obs_controller.log_event_timestamp(
            event_name="end_remix_playback",
            label=f"End playback for {remix_name}"
        )
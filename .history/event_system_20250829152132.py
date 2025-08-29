# event_system.py
# [PHASE 1] NEW FILE - The core of our decoupled communication system.

from typing import Dict, List, Callable, Any
import time

class EventBus:
    """
    A simple event bus system for decoupled communication between components.
    Components can publish events without knowing who is listening, and listeners
    can react to events without knowing who published them.
    """
    def __init__(self):
        self._listeners: Dict[str, List[Callable]] = {}
        print("📡 EventBus initialized.")

    def subscribe(self, event_type: str, callback: Callable):
        """Subscribe a callback function to a specific event type."""
        if event_type not in self._listeners:
            self._listeners[event_type] = []
        self._listeners[event_type].append(callback)
        # Simple logging to track subscriptions
        print(f"   -> Subscriber '{callback.__qualname__}' registered for event '{event_type}'")

    def publish(self, event_type: str, data: Any = None, source: str = "Unknown"):
        """Publish an event to all subscribed listeners."""
        # Simple logging to track published events
        print(f"📤 Event Published: [{source}] -> '{event_type}'")
        if event_type in self._listeners:
            for callback in self._listeners[event_type]:
                try:
                    # Pass a structured dictionary including timestamp and source
                    event_data = {
                        'data': data,
                        'timestamp': time.time(),
                        'source': source
                    }
                    callback(event_data)
                except Exception as e:
                    print(f"❌ Error in event handler for '{event_type}': {e}")
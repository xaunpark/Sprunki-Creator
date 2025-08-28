"""
Resource management for Sprunki Game Player
Handles thread-safe progress updates, resource cleanup, and memory management
"""

import threading
import gc
import weakref


class ThreadSafeProgressUpdater:
    def __init__(self, root, action_var):
        self.root = root
        self.action_var = action_var
        self.update_queue = []
        self.queue_lock = threading.Lock()
        
    def safe_update_progress(self, message):
        """Thread-safe progress update"""
        try:
            with self.queue_lock:
                self.update_queue.append(message)
            
            # Schedule GUI update on main thread
            self.root.after(0, self._process_queue)
            
        except Exception as e:
            print(f"Progress update error: {e}")
    
    def _process_queue(self):
        """Process updates on main thread"""
        try:
            with self.queue_lock:
                if self.update_queue:
                    message = self.update_queue.pop(0)
                    self.action_var.set(message)
                    self.root.update_idletasks()
        except Exception as e:
            print(f"Queue process error: {e}")


class ResourceManager:
    def __init__(self):
        self.cleanup_registry = weakref.WeakSet()
        self.active_threads = []
        self.websockets = []
        
    def register_cleanup(self, obj):
        """Register object for cleanup"""
        self.cleanup_registry.add(obj)
        
    def force_cleanup(self):
        """Force cleanup all registered resources"""
        try:
            # 1. Close WebSocket connections
            for ws in self.websockets[:]:
                try:
                    ws.close()
                    self.websockets.remove(ws)
                except:
                    pass
            
            # 2. Wait for threads to finish
            for thread in self.active_threads[:]:
                if thread.is_alive():
                    thread.join(timeout=2.0)
                self.active_threads.remove(thread)
            
            # 3. Force garbage collection
            gc.collect()
            
            print(f"Resource cleanup completed")
            
        except Exception as e:
            print(f"Resource cleanup error: {e}")
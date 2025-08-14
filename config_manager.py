import json
import os
from pathlib import Path
from typing import Dict, Any, Optional

class ConfigManager:
    """Centralized configuration manager for all components"""
    
    def __init__(self, config_file: str = "config.json"):
        self.config_file = config_file
        self.config: Dict[str, Any] = {}
        self.load_config()
    
    def load_config(self) -> bool:
        """Load configuration from JSON file"""
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                self.config = json.load(f)
            print(f"✅ Configuration loaded from: {self.config_file}")
            return True
        except Exception as e:
            print(f"❌ Error loading config: {e}")
            return False
    
    def get(self, key_path: str, default: Any = None) -> Any:
        """Get configuration value using dot notation (e.g., 'obs.host')"""
        try:
            keys = key_path.split('.')
            value = self.config
            
            for key in keys:
                if isinstance(value, dict) and key in value:
                    value = value[key]
                else:
                    return default
            return value
        except:
            return default

# Global config instance
_config_manager: Optional[ConfigManager] = None

def get_config() -> ConfigManager:
    """Get global config manager instance"""
    global _config_manager
    if _config_manager is None:
        _config_manager = ConfigManager()
    return _config_manager

def initialize_config(config_file: str = "config.json") -> ConfigManager:
    """Initialize global config manager"""
    global _config_manager
    _config_manager = ConfigManager(config_file)
    return _config_manager
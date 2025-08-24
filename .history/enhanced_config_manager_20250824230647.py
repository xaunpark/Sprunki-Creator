# enhanced_config_manager.py - Advanced configuration management with hot-reload
import json
import os
import time
import threading
from datetime import datetime
from typing import Dict, Any, Optional, List, Callable, Union
from dataclasses import dataclass, asdict
from pathlib import Path
import logging
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import yaml
from enum import Enum
import hashlib

class ConfigSource(Enum):
    FILE = "file"
    ENVIRONMENT = "environment" 
    DATABASE = "database"
    REMOTE = "remote"
    DEFAULT = "default"

class ConfigLevel(Enum):
    SYSTEM = "system"
    APPLICATION = "application"
    USER = "user"
    RUNTIME = "runtime"

@dataclass
class ConfigSchema:
    """Define configuration schema with validation rules"""
    key: str
    data_type: type
    required: bool = False
    default: Any = None
    min_value: Optional[Union[int, float]] = None
    max_value: Optional[Union[int, float]] = None
    allowed_values: Optional[List[Any]] = None
    description: str = ""
    category: str = "general"
    sensitive: bool = False  # Hide in logs
    restart_required: bool = False  # Require restart to take effect
    validation_func: Optional[Callable[[Any], bool]] = None

@dataclass
class ConfigChange:
    """Track configuration changes for auditing"""
    timestamp: datetime
    key: str
    old_value: Any
    new_value: Any
    source: ConfigSource
    level: ConfigLevel
    user: str = "system"
    reason: str = ""

class ConfigValidator:
    """Validate configuration values according to schema"""
    
    @staticmethod
    def validate_value(value: Any, schema: ConfigSchema) -> tuple[bool, str]:
        """Validate a single configuration value"""
        
        # Type validation
        if not isinstance(value, schema.data_type):
            if schema.data_type == bool and isinstance(value, str):
                # Special case for string boolean values
                if value.lower() in ['true', 'false', '1', '0', 'yes', 'no']:
                    value = value.lower() in ['true', '1', 'yes']
                else:
                    return False, f"Invalid boolean value: {value}"
            elif schema.data_type in [int, float]:
                try:
                    value = schema.data_type(value)
                except (ValueError, TypeError):
                    return False, f"Cannot convert {value} to {schema.data_type.__name__}"
            else:
                return False, f"Expected {schema.data_type.__name__}, got {type(value).__name__}"
        
        # Range validation
        if schema.min_value is not None and hasattr(value, '__lt__'):
            if value < schema.min_value:
                return False, f"Value {value} is below minimum {schema.min_value}"
        
        if schema.max_value is not None and hasattr(value, '__gt__'):
            if value > schema.max_value:
                return False, f"Value {value} is above maximum {schema.max_value}"
        
        # Allowed values validation
        if schema.allowed_values is not None and value not in schema.allowed_values:
            return False, f"Value {value} not in allowed values: {schema.allowed_values}"
        
        # Custom validation function
        if schema.validation_func is not None:
            try:
                if not schema.validation_func(value):
                    return False, f"Custom validation failed for value: {value}"
            except Exception as e:
                return False, f"Validation function error: {str(e)}"
        
        return True, ""

class ConfigFileHandler(FileSystemEventHandler):
    """Handle configuration file changes for hot-reload"""
    
    def __init__(self, config_manager):
        self.config_manager = config_manager
        self.last_modified = {}
    
    def on_modified(self, event):
        if event.is_directory:
            return
        
        file_path = event.src_path
        if not file_path.endswith(('.json', '.yaml', '.yml')):
            return
        
        # Debounce multiple rapid file changes
        current_time = time.time()
        if file_path in self.last_modified:
            if current_time - self.last_modified[file_path] < 1.0:  # 1 second debounce
                return
        
        self.last_modified[file_path] = current_time
        
        # Reload configuration
        try:
            self.config_manager.reload_config_file(file_path)
        except Exception as e:
            self.config_manager.logger.error(f"Failed to reload config file {file_path}: {e}")

class EnhancedConfigManager:
    """
    Advanced configuration management with:
    - Multi-source configuration (files, env vars, database)
    - Hot-reload capabilities
    - Schema validation
    - Change auditing
    - Environment-specific overrides
    - Secure handling of sensitive values
    """
    
    def __init__(self, config_paths: List[str] = None, 
                 watch_files: bool = True,
                 db_manager = None):
        
        self.config_paths = config_paths or ['config.json', 'config.yaml']
        self.watch_files = watch_files
        self.db = db_manager
        
        # Multi-level configuration storage
        self.configs = {
            ConfigLevel.SYSTEM: {},
            ConfigLevel.APPLICATION: {},
            ConfigLevel.USER: {},
            ConfigLevel.RUNTIME: {}
        }
        
        # Configuration schema
        self.schema: Dict[str, ConfigSchema] = {}
        self.change_history: List[ConfigChange] = []
        self.change_callbacks: Dict[str, List[Callable]] = {}
        
        # File watching
        self.file_observer = None
        self.file_handler = None
        
        # Caching
        self.merged_config_cache = None
        self.cache_hash = None
        
        # Thread safety
        self.config_lock = threading.RLock()
        
        # Setup
        self.setup_logging()
        self.initialize_schema()
        self.load_all_configs()
        
        if self.watch_files:
            self.start_file_watching()
    
    def setup_logging(self):
        """Setup configuration manager logging"""
        self.logger = logging.getLogger('EnhancedConfigManager')
        self.logger.setLevel(logging.INFO)
        
        handler = logging.FileHandler('config_manager.log')
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)
    
    def initialize_schema(self):
        """Initialize comprehensive configuration schema"""
        
        # Define all configuration schemas
        schemas = [
            # Database configuration
            ConfigSchema(
                key="database.path",
                data_type=str,
                required=True,
                default="sprunki_tracker.db",
                description="SQLite database file path",
                category="database"
            ),
            ConfigSchema(
                key="database.connection_pool_size",
                data_type=int,
                default=10,
                min_value=1,
                max_value=100,
                description="Database connection pool size",
                category="database"
            ),
            ConfigSchema(
                key="database.query_timeout",
                data_type=int,
                default=30,
                min_value=1,
                max_value=300,
                description="Database query timeout in seconds",
                category="database"
            ),
            
            # API configuration
            ConfigSchema(
                key="api.host",
                data_type=str,
                default="0.0.0.0",
                description="API server host address",
                category="api"
            ),
            ConfigSchema(
                key="api.port",
                data_type=int,
                default=5000,
                min_value=1024,
                max_value=65535,
                description="API server port",
                category="api",
                restart_required=True
            ),
            ConfigSchema(
                key="api.cors_enabled",
                data_type=bool,
                default=True,
                description="Enable CORS for API",
                category="api"
            ),
            ConfigSchema(
                key="api.rate_limit_enabled",
                data_type=bool,
                default=True,
                description="Enable API rate limiting",
                category="api"
            ),
            ConfigSchema(
                key="api.rate_limit_requests_per_minute",
                data_type=int,
                default=60,
                min_value=1,
                max_value=10000,
                description="API rate limit requests per minute",
                category="api"
            ),
            
            # WebSocket configuration
            ConfigSchema(
                key="websocket.enabled",
                data_type=bool,
                default=True,
                description="Enable WebSocket support",
                category="websocket",
                restart_required=True
            ),
            ConfigSchema(
                key="websocket.heartbeat_interval",
                data_type=int,
                default=30,
                min_value=5,
                max_value=300,
                description="WebSocket heartbeat interval in seconds",
                category="websocket"
            ),
            
            # OBS configuration
            ConfigSchema(
                key="obs.host",
                data_type=str,
                default="192.168.1.17",
                description="OBS WebSocket host",
                category="obs"
            ),
            ConfigSchema(
                key="obs.port",
                data_type=int,
                default=4455,
                min_value=1024,
                max_value=65535,
                description="OBS WebSocket port",
                category="obs"
            ),
            ConfigSchema(
                key="obs.password",
                data_type=str,
                default="",
                description="OBS WebSocket password",
                category="obs",
                sensitive=True
            ),
            ConfigSchema(
                key="obs.connection_timeout",
                data_type=int,
                default=10,
                min_value=1,
                max_value=60,
                description="OBS connection timeout in seconds",
                category="obs"
            ),
            ConfigSchema(
                key="obs.max_reconnect_attempts",
                data_type=int,
                default=5,
                min_value=1,
                max_value=20,
                description="Maximum OBS reconnection attempts",
                category="obs"
            ),
            
            # Processing configuration
            ConfigSchema(
                key="processing.max_concurrent_jobs",
                data_type=int,
                default=3,
                min_value=1,
                max_value=20,
                description="Maximum concurrent video processing jobs",
                category="processing"
            ),
            ConfigSchema(
                key="processing.default_timeout",
                data_type=int,
                default=1800,
                min_value=300,
                max_value=7200,
                description="Default job timeout in seconds",
                category="processing"
            ),
            ConfigSchema(
                key="processing.max_retries",
                data_type=int,
                default=3,
                min_value=0,
                max_value=10,
                description="Maximum retry attempts for failed jobs",
                category="processing"
            ),
            ConfigSchema(
                key="processing.pause_between_items",
                data_type=int,
                default=5,
                min_value=0,
                max_value=60,
                description="Pause between processing items in seconds",
                category="processing"
            ),
            ConfigSchema(
                key="processing.auto_skip_on_error",
                data_type=bool,
                default=True,
                description="Automatically skip failed items",
                category="processing"
            ),
            
            # Output configuration
            ConfigSchema(
                key="output.base_directory",
                data_type=str,
                required=True,
                default="/tmp/sprunki_output",
                description="Base directory for output files",
                category="output"
            ),
            ConfigSchema(
                key="output.create_project_folders",
                data_type=bool,
                default=True,
                description="Create separate folders for each project",
                category="output"
            ),
            ConfigSchema(
                key="output.cleanup_old_files",
                data_type=bool,
                default=True,
                description="Clean up old output files automatically",
                category="output"
            ),
            ConfigSchema(
                key="output.cleanup_after_days",
                data_type=int,
                default=30,
                min_value=1,
                max_value=365,
                description="Days to keep output files before cleanup",
                category="output"
            ),
            
            # Quality validation
            ConfigSchema(
                key="quality.validation_enabled",
                data_type=bool,
                default=True,
                description="Enable output quality validation",
                category="quality"
            ),
            ConfigSchema(
                key="quality.minimum_score",
                data_type=float,
                default=7.0,
                min_value=0.0,
                max_value=10.0,
                description="Minimum quality score for acceptance",
                category="quality"
            ),
            ConfigSchema(
                key="quality.minimum_file_size_mb",
                data_type=int,
                default=1,
                min_value=1,
                max_value=1000,
                description="Minimum output file size in MB",
                category="quality"
            ),
            ConfigSchema(
                key="quality.required_duration_seconds",
                data_type=int,
                default=10,
                min_value=1,
                max_value=600,
                description="Minimum video duration in seconds",
                category="quality"
            ),
            
            # Automation timings
            ConfigSchema(
                key="automation.recording_duration.icon_showcase",
                data_type=int,
                default=14,
                min_value=5,
                max_value=60,
                description="Recording duration for individual icons",
                category="automation"
            ),
            ConfigSchema(
                key="automation.recording_duration.remix_playback",
                data_type=int,
                default=20,
                min_value=10,
                max_value=120,
                description="Recording duration for remix playback",
                category="automation"
            ),
            ConfigSchema(
                key="automation.wait_times.page_load",
                data_type=int,
                default=10,
                min_value=3,
                max_value=60,
                description="Wait time for page loading",
                category="automation"
            ),
            ConfigSchema(
                key="automation.wait_times.obs_setup",
                data_type=int,
                default=5,
                min_value=1,
                max_value=30,
                description="Wait time for OBS setup",
                category="automation"
            ),
            
            # Hotkeys
            ConfigSchema(
                key="hotkeys.emergency_stop",
                data_type=str,
                default="esc",
                allowed_values=["esc", "f1", "f2", "f3", "f4", "f12"],
                description="Hotkey for emergency stop",
                category="hotkeys"
            ),
            ConfigSchema(
                key="hotkeys.global_enabled",
                data_type=bool,
                default=True,
                description="Enable global hotkeys",
                category="hotkeys"
            ),
            
            # Notifications
            ConfigSchema(
                key="notifications.system_notifications",
                data_type=bool,
                default=True,
                description="Enable system notifications",
                category="notifications"
            ),
            ConfigSchema(
                key="notifications.email_enabled",
                data_type=bool,
                default=False,
                description="Enable email notifications",
                category="notifications"
            ),
            ConfigSchema(
                key="notifications.email_smtp_host",
                data_type=str,
                default="",
                description="SMTP host for email notifications",
                category="notifications"
            ),
            ConfigSchema(
                key="notifications.email_smtp_port",
                data_type=int,
                default=587,
                min_value=1,
                max_value=65535,
                description="SMTP port for email notifications",
                category="notifications"
            ),
            ConfigSchema(
                key="notifications.email_username",
                data_type=str,
                default="",
                description="SMTP username",
                category="notifications",
                sensitive=True
            ),
            ConfigSchema(
                key="notifications.email_password",
                data_type=str,
                default="",
                description="SMTP password",
                category="notifications",
                sensitive=True
            ),
            
            # Logging
            ConfigSchema(
                key="logging.level",
                data_type=str,
                default="INFO",
                allowed_values=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
                description="Logging level",
                category="logging"
            ),
            ConfigSchema(
                key="logging.file_rotation",
                data_type=bool,
                default=True,
                description="Enable log file rotation",
                category="logging"
            ),
            ConfigSchema(
                key="logging.max_file_size_mb",
                data_type=int,
                default=10,
                min_value=1,
                max_value=100,
                description="Maximum log file size in MB",
                category="logging"
            ),
            ConfigSchema(
                key="logging.backup_count",
                data_type=int,
                default=5,
                min_value=1,
                max_value=20,
                description="Number of log backup files to keep",
                category="logging"
            ),
            
            # Application metadata
            ConfigSchema(
                key="application.version",
                data_type=str,
                default="2.0.0",
                description="Application version",
                category="application"
            ),
            ConfigSchema(
                key="application.instance_id",
                data_type=str,
                default="1",
                description="Unique instance identifier",
                category="application"
            ),
            ConfigSchema(
                key="application.environment",
                data_type=str,
                default="development",
                allowed_values=["development", "staging", "production"],
                description="Application environment",
                category="application"
            )
        ]
        
        # Register all schemas
        for schema in schemas:
            self.register_schema(schema)
        
        self.logger.info(f"✅ Registered {len(schemas)} configuration schemas")
    
    def register_schema(self, schema: ConfigSchema):
        """Register a configuration schema"""
        self.schema[schema.key] = schema
    
    def load_all_configs(self):
        """Load configurations from all sources"""
        
        with self.config_lock:
            # Load from files
            for config_path in self.config_paths:
                if os.path.exists(config_path):
                    try:
                        self.load_config_file(config_path, ConfigLevel.APPLICATION)
                        self.logger.info(f"✅ Loaded config from {config_path}")
                    except Exception as e:
                        self.logger.error(f"❌ Failed to load config from {config_path}: {e}")
            
            # Load from environment variables
            self.load_from_environment()
            
            # Load from database if available
            if self.db:
                try:
                    self.load_from_database()
                except Exception as e:
                    self.logger.error(f"Failed to load config from database: {e}")
            
            # Set default values for missing required configs
            self.set_defaults()
            
            # Validate all configurations
            self.validate_all()
            
            # Clear cache to force rebuild
            self.clear_cache()
    
    def load_config_file(self, file_path: str, level: ConfigLevel = ConfigLevel.APPLICATION):
        """Load configuration from a file"""
        
        file_path = Path(file_path)
        
        if not file_path.exists():
            self.logger.warning(f"Config file not found: {file_path}")
            return
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                if file_path.suffix.lower() in ['.yaml', '.yml']:
                    config_data = yaml.safe_load(f)
                else:  # Assume JSON
                    config_data = json.load(f)
            
            # Flatten nested configuration
            flattened = self.flatten_dict(config_data)
            
            # Store in appropriate level
            with self.config_lock:
                for key, value in flattened.items():
                    old_value = self.configs[level].get(key)
                    if old_value != value:
                        self.configs[level][key] = value
                        self.record_change(key, old_value, value, ConfigSource.FILE, level)
            
            self.clear_cache()
            self.logger.info(f"✅ Loaded {len(flattened)} configs from {file_path}")
            
        except Exception as e:
            self.logger.error(f"❌ Error loading config file {file_path}: {e}")
            raise
    
    def load_from_environment(self):
        """Load configuration from environment variables"""
        
        env_configs = {}
        prefix = "SPRUNKI_"
        
        for key, value in os.environ.items():
            if key.startswith(prefix):
                # Convert SPRUNKI_API_PORT to api.port
                config_key = key[len(prefix):].lower().replace('_', '.')
                
                # Try to convert to appropriate type
                if config_key in self.schema:
                    schema = self.schema[config_key]
                    try:
                        if schema.data_type == bool:
                            value = value.lower() in ['true', '1', 'yes', 'on']
                        elif schema.data_type == int:
                            value = int(value)
                        elif schema.data_type == float:
                            value = float(value)
                        # else keep as string
                    except ValueError:
                        self.logger.warning(f"Invalid environment value for {config_key}: {value}")
                        continue
                
                env_configs[config_key] = value
        
        # Apply environment configs
        with self.config_lock:
            for key, value in env_configs.items():
                old_value = self.configs[ConfigLevel.SYSTEM].get(key)
                if old_value != value:
                    self.configs[ConfigLevel.SYSTEM][key] = value
                    self.record_change(key, old_value, value, ConfigSource.ENVIRONMENT, ConfigLevel.SYSTEM)
        
        if env_configs:
            self.clear_cache()
            self.logger.info(f"✅ Loaded {len(env_configs)} configs from environment")
    
    def load_from_database(self):
        """Load configuration from database"""
        if not self.db:
            return
        
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT config_key, config_value, config_type 
                FROM system_config 
                WHERE category != 'sensitive' OR config_key IN (
                    SELECT config_key FROM system_config WHERE updated_by = 'admin'
                )
            ''')
            
            db_configs = {}
            for row in cursor.fetchall():
                key = row['config_key']
                value = row['config_value']
                config_type = row['config_type']
                
                # Convert value based on type
                try:
                    if config_type == 'integer':
                        value = int(value)
                    elif config_type == 'float':
                        value = float(value)
                    elif config_type == 'boolean':
                        value = value.lower() in ['true', '1', 'yes']
                    elif config_type == 'json':
                        value = json.loads(value)
                    # else keep as string
                except (ValueError, json.JSONDecodeError):
                    self.logger.warning(f"Invalid database value for {key}: {value}")
                    continue
                
                db_configs[key] = value
            
            # Apply database configs at system level
            with self.config_lock:
                for key, value in db_configs.items():
                    old_value = self.configs[ConfigLevel.SYSTEM].get(key)
                    if old_value != value:
                        self.configs[ConfigLevel.SYSTEM][key] = value
                        self.record_change(key, old_value, value, ConfigSource.DATABASE, ConfigLevel.SYSTEM)
            
            if db_configs:
                self.clear_cache()
                self.logger.info(f"✅ Loaded {len(db_configs)} configs from database")
        
        except Exception as e:
            self.logger.error(f"❌ Error loading config from database: {e}")
    
    def set_defaults(self):
        """Set default values for missing required configurations"""
        
        with self.config_lock:
            defaults_set = 0
            
            for key, schema in self.schema.items():
                if schema.default is not None:
                    # Check if value exists in any level
                    if not self.has_config(key):
                        self.configs[ConfigLevel.APPLICATION][key] = schema.default
                        self.record_change(key, None, schema.default, ConfigSource.DEFAULT, ConfigLevel.APPLICATION)
                        defaults_set += 1
            
            if defaults_set > 0:
                self.clear_cache()
                self.logger.info(f"✅ Set {defaults_set} default configuration values")
    
    def validate_all(self):
        """Validate all configurations against schemas"""
        
        validation_errors = []
        
        for key, schema in self.schema.items():
            current_value = self.get(key)
            
            if current_value is None:
                if schema.required:
                    validation_errors.append(f"Required configuration missing: {key}")
                continue
            
            is_valid, error_msg = ConfigValidator.validate_value(current_value, schema)
            if not is_valid:
                validation_errors.append(f"Validation failed for {key}: {error_msg}")
        
        if validation_errors:
            error_summary = "\n".join(validation_errors)
            self.logger.error(f"❌ Configuration validation errors:\n{error_summary}")
            raise ValueError(f"Configuration validation failed:\n{error_summary}")
        
        self.logger.info("✅ All configurations validated successfully")
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value with priority order: runtime > user > application > system"""
        
        with self.config_lock:
            # Check cache first
            if self.merged_config_cache is not None:
                return self.merged_config_cache.get(key, default)
            
            # Build merged config cache
            self.rebuild_cache()
            return self.merged_config_cache.get(key, default)
    
    def set(self, key: str, value: Any, level: ConfigLevel = ConfigLevel.RUNTIME,
           user: str = "system", reason: str = "") -> bool:
        """Set configuration value with validation"""
        
        # Validate against schema
        if key in self.schema:
            schema = self.schema[key]
            is_valid, error_msg = ConfigValidator.validate_value(value, schema)
            if not is_valid:
                self.logger.error(f"❌ Validation failed for {key}: {error_msg}")
                return False
        
        with self.config_lock:
            old_value = self.configs[level].get(key)
            
            if old_value != value:
                self.configs[level][key] = value
                self.record_change(key, old_value, value, ConfigSource.RUNTIME, level, user, reason)
                self.clear_cache()
                
                # Trigger change callbacks
                if key in self.change_callbacks:
                    for callback in self.change_callbacks[key]:
                        try:
                            callback(key, old_value, value)
                        except Exception as e:
                            self.logger.error(f"Error in change callback for {key}: {e}")
                
                self.logger.info(f"✅ Config updated: {key} = {self.safe_value_str(key, value)}")
                return True
            
            return True  # Value unchanged but operation successful
    
    def delete(self, key: str, level: ConfigLevel = ConfigLevel.RUNTIME):
        """Delete configuration value from specific level"""
        
        with self.config_lock:
            if key in self.configs[level]:
                old_value = self.configs[level][key]
                del self.configs[level][key]
                self.record_change(key, old_value, None, ConfigSource.RUNTIME, level)
                self.clear_cache()
                self.logger.info(f"✅ Config deleted: {key} from {level.value}")
                return True
            
            return False
    
    def has_config(self, key: str) -> bool:
        """Check if configuration exists in any level"""
        return any(key in config for config in self.configs.values())
    
    def get_all_in_category(self, category: str) -> Dict[str, Any]:
        """Get all configurations in a specific category"""
        result = {}
        
        for key, schema in self.schema.items():
            if schema.category == category:
                value = self.get(key)
                if value is not None:
                    result[key] = value
        
        return result
    
    def get_schema(self, key: str) -> Optional[ConfigSchema]:
        """Get schema for a configuration key"""
        return self.schema.get(key)
    
    def get_all_schemas(self) -> Dict[str, ConfigSchema]:
        """Get all configuration schemas"""
        return self.schema.copy()
    
    def register_change_callback(self, key: str, callback: Callable[[str, Any, Any], None]):
        """Register callback for configuration changes"""
        if key not in self.change_callbacks:
            self.change_callbacks[key] = []
        
        self.change_callbacks[key].append(callback)
    
    def unregister_change_callback(self, key: str, callback: Callable):
        """Unregister change callback"""
        if key in self.change_callbacks:
            try:
                self.change_callbacks[key].remove(callback)
                if not self.change_callbacks[key]:
                    del self.change_callbacks[key]
            except ValueError:
                pass
    
    def save_to_file(self, file_path: str, category: str = None, 
                    exclude_sensitive: bool = True) -> bool:
        """Save current configuration to file"""
        
        try:
            config_data = {}
            
            for key, schema in self.schema.items():
                if category and schema.category != category:
                    continue
                
                if exclude_sensitive and schema.sensitive:
                    continue
                
                value = self.get(key)
                if value is not None:
                    # Convert flat key to nested structure
                    self.set_nested_value(config_data, key, value)
            
            # Save to file
            file_path = Path(file_path)
            file_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(file_path, 'w', encoding='utf-8') as f:
                if file_path.suffix.lower() in ['.yaml', '.yml']:
                    yaml.dump(config_data, f, default_flow_style=False, indent=2)
                else:  # JSON
                    json.dump(config_data, f, indent=2, default=str)
            
            self.logger.info(f"✅ Configuration saved to {file_path}")
            return True
            
        except Exception as e:
            self.logger.error(f"❌ Failed to save configuration: {e}")
            return False
    
    def save_to_database(self, category: str = None) -> bool:
        """Save configuration to database"""
        if not self.db:
            return False
        
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            
            saved_count = 0
            for key, schema in self.schema.items():
                if category and schema.category != category:
                    continue
                
                value = self.get(key)
                if value is None:
                    continue
                
                # Determine config type
                if isinstance(value, bool):
                    config_type = 'boolean'
                    config_value = str(value).lower()
                elif isinstance(value, int):
                    config_type = 'integer'
                    config_value = str(value)
                elif isinstance(value, float):
                    config_type = 'float'
                    config_value = str(value)
                elif isinstance(value, (dict, list)):
                    config_type = 'json'
                    config_value = json.dumps(value)
                else:
                    config_type = 'string'
                    config_value = str(value)
                
                # Insert or update
                cursor.execute('''
                    INSERT OR REPLACE INTO system_config 
                    (config_key, config_value, config_type, description, category, is_sensitive, updated_by)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    key, config_value, config_type, schema.description,
                    schema.category, schema.sensitive, 'config_manager'
                ))
                
                saved_count += 1
            
            conn.commit()
            self.logger.info(f"✅ Saved {saved_count} configurations to database")
            return True
            
        except Exception as e:
            self.logger.error(f"❌ Failed to save configuration to database: {e}")
            return False
    
    def reload_config_file(self, file_path: str):
        """Reload a specific configuration file (for hot-reload)"""
        self.logger.info(f"🔄 Hot-reloading configuration from {file_path}")
        
        try:
            self.load_config_file(file_path, ConfigLevel.APPLICATION)
            self.validate_all()
            
            # Notify about hot-reload
            self.logger.info(f"✅ Hot-reload completed for {file_path}")
            
        except Exception as e:
            self.logger.error(f"❌ Hot-reload failed for {file_path}: {e}")
            raise
    
    def start_file_watching(self):
        """Start watching configuration files for changes"""
        if not self.watch_files:
            return
        
        try:
            self.file_handler = ConfigFileHandler(self)
            self.file_observer = Observer()
            
            # Watch all config file directories
            watched_dirs = set()
            for config_path in self.config_paths:
                config_dir = Path(config_path).parent.absolute()
                if config_dir not in watched_dirs:
                    self.file_observer.schedule(
                        self.file_handler,
                        str(config_dir),
                        recursive=False
                    )
                    watched_dirs.add(config_dir)
            
            self.file_observer.start()
            self.logger.info(f"👁️ Started watching {len(watched_dirs)} directories for config changes")
            
        except Exception as e:
            self.logger.error(f"❌ Failed to start file watching: {e}")
    
    def stop_file_watching(self):
        """Stop watching configuration files"""
        if self.file_observer:
            self.file_observer.stop()
            self.file_observer.join(timeout=5)
            self.file_observer = None
            self.logger.info("👁️ Stopped file watching")
    
    # === UTILITY METHODS ===
    
    def flatten_dict(self, d: Dict, parent_key: str = '', separator: str = '.') -> Dict:
        """Flatten nested dictionary"""
        items = []
        for key, value in d.items():
            new_key = f"{parent_key}{separator}{key}" if parent_key else key
            if isinstance(value, dict):
                items.extend(self.flatten_dict(value, new_key, separator).items())
            else:
                items.append((new_key, value))
        return dict(items)
    
    def set_nested_value(self, d: Dict, key: str, value: Any, separator: str = '.'):
        """Set value in nested dictionary using dotted key"""
        keys = key.split(separator)
        for k in keys[:-1]:
            d = d.setdefault(k, {})
        d[keys[-1]] = value
    
    def rebuild_cache(self):
        """Rebuild merged configuration cache"""
        merged = {}
        
        # Merge in priority order: system -> application -> user -> runtime
        for level in [ConfigLevel.SYSTEM, ConfigLevel.APPLICATION, ConfigLevel.USER, ConfigLevel.RUNTIME]:
            merged.update(self.configs[level])
        
        self.merged_config_cache = merged
        
        # Calculate cache hash for change detection
        cache_str = json.dumps(merged, sort_keys=True, default=str)
        self.cache_hash = hashlib.md5(cache_str.encode()).hexdigest()
    
    def clear_cache(self):
        """Clear configuration cache"""
        self.merged_config_cache = None
        self.cache_hash = None
    
    def record_change(self, key: str, old_value: Any, new_value: Any,
                     source: ConfigSource, level: ConfigLevel,
                     user: str = "system", reason: str = ""):
        """Record configuration change for auditing"""
        
        change = ConfigChange(
            timestamp=datetime.now(),
            key=key,
            old_value=old_value,
            new_value=new_value,
            source=source,
            level=level,
            user=user,
            reason=reason
        )
        
        self.change_history.append(change)
        
        # Keep only last 1000 changes
        if len(self.change_history) > 1000:
            self.change_history = self.change_history[-1000:]
    
    def safe_value_str(self, key: str, value: Any) -> str:
        """Get safe string representation of value (hide sensitive values)"""
        if key in self.schema and self.schema[key].sensitive:
            return "***HIDDEN***"
        return str(value)
    
    def get_change_history(self, key: str = None, limit: int = 100) -> List[ConfigChange]:
        """Get configuration change history"""
        if key:
            filtered = [change for change in self.change_history if change.key == key]
            return filtered[-limit:]
        else:
            return self.change_history[-limit:]
    
    def get_configuration_summary(self) -> Dict[str, Any]:
        """Get comprehensive configuration summary"""
        categories = {}
        
        for key, schema in self.schema.items():
            category = schema.category
            if category not in categories:
                categories[category] = {
                    'configs': [],
                    'total_count': 0,
                    'required_count': 0,
                    'sensitive_count': 0
                }
            
            value = self.get(key)
            categories[category]['configs'].append({
                'key': key,
                'value': self.safe_value_str(key, value),
                'required': schema.required,
                'sensitive': schema.sensitive,
                'description': schema.description,
                'has_value': value is not None
            })
            
            categories[category]['total_count'] += 1
            if schema.required:
                categories[category]['required_count'] += 1
            if schema.sensitive:
                categories[category]['sensitive_count'] += 1
        
        return {
            'total_schemas': len(self.schema),
            'categories': categories,
            'cache_hash': self.cache_hash,
            'recent_changes': len(self.change_history),
            'file_watching_active': self.file_observer is not None and self.file_observer.is_alive()
        }

# Global configuration manager instance
_config_manager = None

def initialize_config(config_paths: List[str] = None, db_manager = None) -> EnhancedConfigManager:
    """Initialize global configuration manager"""
    global _config_manager
    
    if _config_manager is None:
        _config_manager = EnhancedConfigManager(config_paths, db_manager=db_manager)
    
    return _config_manager

def get_config_manager() -> EnhancedConfigManager:
    """Get global configuration manager"""
    global _config_manager
    if _config_manager is None:
        raise RuntimeError("Configuration manager not initialized. Call initialize_config() first.")
    return _config_manager

def get_config(key: str = None, default: Any = None) -> Any:
    """Get configuration value from global manager"""
    manager = get_config_manager()
    if key is None:
        return manager.merged_config_cache or {}
    return manager.get(key, default)

def set_config(key: str, value: Any, **kwargs) -> bool:
    """Set configuration value in global manager"""
    manager = get_config_manager()
    return manager.set(key, value, **kwargs)

# Example usage and testing
if __name__ == "__main__":
    # Initialize configuration manager
    config_manager = initialize_config(['config.json', 'config.yaml'])
    
    # Test configuration access
    print("🔧 Testing configuration management...")
    
    # Get some configurations
    api_port = get_config('api.port', 5000)
    obs_host = get_config('obs.host', 'localhost')
    max_jobs = get_config('processing.max_concurrent_jobs', 3)
    
    print(f"API Port: {api_port}")
    print(f"OBS Host: {obs_host}")
    print(f"Max Concurrent Jobs: {max_jobs}")
    
    # Set runtime configuration
    set_config('processing.max_concurrent_jobs', 5, reason="Increased for testing")
    
    # Get configuration summary
    summary = config_manager.get_configuration_summary()
    print(f"\n📊 Configuration Summary:")
    print(f"Total schemas: {summary['total_schemas']}")
    print(f"Categories: {len(summary['categories'])}")
    print(f"Recent changes: {summary['recent_changes']}")
    
    # Test validation
    try:
        set_config('api.port', 999)  # Should fail validation (below min_value)
    except Exception as e:
        print(f"✅ Validation working: {e}")
    
    # Test change history
    recent_changes = config_manager.get_change_history(limit=5)
    print(f"\n📝 Recent changes:")
    for change in recent_changes:
        print(f"  {change.timestamp}: {change.key} = {config_manager.safe_value_str(change.key, change.new_value)}")
    
    # Save configuration
    config_manager.save_to_file('output_config.json', exclude_sensitive=True)
    print("✅ Configuration saved to output_config.json")
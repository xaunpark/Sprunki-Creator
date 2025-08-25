"""
Smart Retry Manager - PHASE 2 (API-Based Version)
Handles intelligent retry logic using API calls instead of direct database access
"""

import time
import threading
import requests
from typing import Dict, Any, Callable, Optional
from datetime import datetime, timedelta
import random
import hashlib
import re

class SmartRetryManager:
    """Manages intelligent retry logic using API calls to Tracker"""
    
    def __init__(self, api_client=None):
        self.api_client = api_client
        self.active_retries = {}  # queue_id -> retry_info
        self.recovery_actions = {}  # action_type -> recovery_function
        self.max_concurrent_retries = 3
        
        # Built-in error patterns (no database dependency)
        self.error_patterns = self._get_builtin_error_patterns()
        
        # Register default recovery actions
        self._register_default_recovery_actions()
    
    def _get_builtin_error_patterns(self) -> dict:
        """Built-in error patterns to avoid database dependency"""
        return {
            'browser_timeout': {
                'category': 'transient', 'severity': 'medium', 'retry_strategy': 'backoff',
                'max_retries': 3, 'backoff_multiplier': 2.0
            },
            'webdriver_crash': {
                'category': 'transient', 'severity': 'high', 'retry_strategy': 'immediate',
                'max_retries': 2, 'backoff_multiplier': 1.5
            },
            'element_not_found': {
                'category': 'transient', 'severity': 'low', 'retry_strategy': 'immediate',
                'max_retries': 3, 'backoff_multiplier': 1.0
            },
            'obs_connection_failed': {
                'category': 'network', 'severity': 'high', 'retry_strategy': 'backoff',
                'max_retries': 5, 'backoff_multiplier': 2.0
            },
            'obs_recording_failed': {
                'category': 'config', 'severity': 'high', 'retry_strategy': 'manual',
                'max_retries': 1, 'backoff_multiplier': 1.0
            },
            'obs_file_locked': {
                'category': 'transient', 'severity': 'medium', 'retry_strategy': 'backoff',
                'max_retries': 3, 'backoff_multiplier': 1.5
            },
            'game_load_timeout': {
                'category': 'network', 'severity': 'medium', 'retry_strategy': 'backoff',
                'max_retries': 4, 'backoff_multiplier': 2.0
            },
            'json_generation_failed': {
                'category': 'config', 'severity': 'high', 'retry_strategy': 'immediate',
                'max_retries': 2, 'backoff_multiplier': 1.0
            },
            'network_unreachable': {
                'category': 'network', 'severity': 'medium', 'retry_strategy': 'backoff',
                'max_retries': 5, 'backoff_multiplier': 3.0
            },
            'disk_space_low': {
                'category': 'config', 'severity': 'critical', 'retry_strategy': 'manual',
                'max_retries': 0, 'backoff_multiplier': 1.0
            }
        }
    
    def _register_default_recovery_actions(self):
        """Register default recovery actions"""
        self.recovery_actions = {
            'browser_restart': self._recovery_browser_restart,
            'obs_reconnect': self._recovery_obs_reconnect,
            'file_cleanup': self._recovery_file_cleanup,
            'memory_cleanup': self._recovery_memory_cleanup,
            'network_wait': self._recovery_network_wait,
            'config_reset': self._recovery_config_reset
        }
    
    def classify_error_local(self, error_message: str, context: dict = None) -> dict:
        """Classify error locally without database dependency"""
        try:
            # Create error signature
            error_signature = self._create_error_signature(error_message, context)
            
            # Look for known pattern
            if error_signature in self.error_patterns:
                pattern = self.error_patterns[error_signature]
                return {
                    'error_signature': error_signature,
                    'category': pattern['category'],
                    'severity': pattern['severity'],
                    'retry_strategy': pattern['retry_strategy'],
                    'max_retries': pattern['max_retries'],
                    'backoff_multiplier': pattern['backoff_multiplier'],
                    'known_pattern': True
                }
            else:
                # Fallback heuristic classification
                return self._heuristic_error_classification(error_message, error_signature, context)
                
        except Exception as e:
            print(f"❌ Error classifying error locally: {e}")
            return {
                'error_signature': 'unknown_error',
                'category': 'unknown',
                'severity': 'medium',
                'retry_strategy': 'backoff',
                'max_retries': 2,
                'backoff_multiplier': 2.0,
                'known_pattern': False
            }
    
    def should_retry(self, queue_id: int, error_message: str, context: dict = None) -> dict:
        """Determine if job should be retried using local logic"""
        try:
            # Get current retry count from context or estimate
            current_attempt = context.get('attempt', 1) if context else 1
            
            # Classify error locally
            error_classification = self.classify_error_local(error_message, context)
            
            # Check if we should retry
            max_retries = error_classification.get('max_retries', 2)
            if current_attempt > max_retries:
                return {
                    'should_retry': False,
                    'reason': f'Max retries ({max_retries}) exceeded',
                    'classification': error_classification,
                    'retry_attempt': current_attempt
                }
            
            # Check retry strategy
            retry_strategy = error_classification.get('retry_strategy', 'immediate')
            if retry_strategy == 'manual':
                return {
                    'should_retry': False,
                    'reason': 'Manual intervention required',
                    'classification': error_classification,
                    'retry_attempt': current_attempt,
                    'requires_manual': True
                }
            
            if retry_strategy == 'skip':
                return {
                    'should_retry': False,
                    'reason': 'Error type should be skipped',
                    'classification': error_classification,
                    'retry_attempt': current_attempt,
                    'should_skip': True
                }
            
            # Calculate retry delay
            retry_delay = self._calculate_retry_delay(retry_strategy, current_attempt, error_classification)
            
            # Select recovery action
            recovery_action = self._select_recovery_action(error_classification, context)
            
            return {
                'should_retry': True,
                'retry_attempt': current_attempt,
                'retry_delay': retry_delay,
                'retry_strategy': retry_strategy,
                'recovery_action': recovery_action,
                'classification': error_classification,
                'reason': f'Retry attempt {current_attempt}/{max_retries} with {retry_strategy} strategy'
            }
            
        except Exception as e:
            print(f"❌ Error in should_retry: {e}")
            return {
                'should_retry': False,
                'reason': f'Error in retry decision: {str(e)}',
                'retry_attempt': 1
            }
    
    def execute_retry(self, queue_id: int, retry_decision: dict, retry_function: Callable) -> bool:
        """Execute retry with proper delays and recovery actions"""
        try:
            retry_attempt = retry_decision['retry_attempt']
            retry_delay = retry_decision.get('retry_delay', 0)
            recovery_action = retry_decision.get('recovery_action')
            classification = retry_decision.get('classification', {})
            
            # Report retry start to Tracker via API
            if self.api_client:
                self.api_client.update_job_progress(
                    queue_id, 'retry', f'attempt_{retry_attempt}', 0.0, 'active',
                    metadata={
                        'retry_strategy': retry_decision.get('retry_strategy'),
                        'retry_delay': retry_delay,
                        'recovery_action': recovery_action,
                        'error_signature': classification.get('error_signature')
                    }
                )
            
            print(f"🔄 Starting retry {retry_attempt} for job {queue_id}")
            print(f"   Strategy: {retry_decision.get('retry_strategy')}")
            print(f"   Delay: {retry_delay}s")
            print(f"   Recovery: {recovery_action}")
            
            # Execute recovery action if needed
            recovery_success = True
            if recovery_action:
                print(f"🛠️ Executing recovery action: {recovery_action}")
                recovery_success = self._execute_recovery_action(queue_id, recovery_action)
                
                if not recovery_success:
                    print(f"❌ Recovery action failed: {recovery_action}")
                    return False
            
            # Wait for retry delay with progress updates
            if retry_delay > 0:
                print(f"⏳ Waiting {retry_delay}s before retry...")
                
                remaining = retry_delay
                while remaining > 0:
                    wait_chunk = min(remaining, 10)  # Update every 10s max
                    time.sleep(wait_chunk)
                    remaining -= wait_chunk
                    
                    if self.api_client and remaining > 0:
                        progress = ((retry_delay - remaining) / retry_delay) * 100
                        self.api_client.update_job_progress(
                            queue_id, 'retry', f'waiting_{retry_attempt}', progress, 'active',
                            metadata={'remaining_wait_seconds': remaining}
                        )
            
            # Mark retry as ready to execute
            if self.api_client:
                self.api_client.update_job_progress(
                    queue_id, 'retry', f'executing_{retry_attempt}', 50.0, 'active'
                )
            
            # Execute the actual retry
            print(f"▶️ Executing retry function for job {queue_id}")
            retry_start_time = time.time()
            
            retry_success = retry_function()
            
            retry_duration = time.time() - retry_start_time
            
            # Update retry result
            if retry_success:
                print(f"✅ Retry {retry_attempt} successful for job {queue_id} (took {retry_duration:.1f}s)")
                
                if self.api_client:
                    self.api_client.update_job_progress(
                        queue_id, 'retry', f'completed_{retry_attempt}', 100.0, 'completed',
                        metadata={'retry_duration_seconds': retry_duration}
                    )
            else:
                print(f"❌ Retry {retry_attempt} failed for job {queue_id} (took {retry_duration:.1f}s)")
                
                if self.api_client:
                    self.api_client.update_job_progress(
                        queue_id, 'retry', f'failed_{retry_attempt}', 0.0, 'failed',
                        metadata={'retry_duration_seconds': retry_duration}
                    )
            
            return retry_success
            
        except Exception as e:
            print(f"❌ Error executing retry: {e}")
            if self.api_client:
                self.api_client.update_job_progress(
                    queue_id, 'retry', 'exception', 0.0, 'failed',
                    error_details=f'Retry execution error: {str(e)}'
                )
            return False
    
    def _create_error_signature(self, error_message: str, context: dict = None) -> str:
        """Create error signature for pattern matching"""
        error_lower = error_message.lower()
        
        # Pattern-based signatures (same as database version)
        if 'timeout' in error_lower and 'browser' in error_lower:
            return 'browser_timeout'
        elif 'webdriver' in error_lower and ('crash' in error_lower or 'died' in error_lower):
            return 'webdriver_crash'
        elif 'element' in error_lower and 'not found' in error_lower:
            return 'element_not_found'
        elif 'obs' in error_lower and 'connection' in error_lower:
            return 'obs_connection_failed'
        elif 'obs' in error_lower and 'recording' in error_lower:
            return 'obs_recording_failed'
        elif 'file' in error_lower and ('locked' in error_lower or 'busy' in error_lower):
            return 'obs_file_locked'
        elif 'game' in error_lower and 'timeout' in error_lower:
            return 'game_load_timeout'
        elif 'json' in error_lower and 'generation' in error_lower:
            return 'json_generation_failed'
        elif 'network' in error_lower and ('unreachable' in error_lower or 'connection refused' in error_lower):
            return 'network_unreachable'
        elif 'disk' in error_lower and 'space' in error_lower:
            return 'disk_space_low'
        else:
            # Hash-based signature for unknown patterns
            clean_msg = re.sub(r'[0-9]+', 'N', error_message)
            clean_msg = re.sub(r'[^\w\s]', '', clean_msg)
            return f"hash_{hashlib.md5(clean_msg.encode()).hexdigest()[:8]}"
    
    def _heuristic_error_classification(self, error_message: str, error_signature: str, context: dict = None) -> dict:
        """Heuristic classification for unknown errors"""
        error_lower = error_message.lower()
        
        # Determine category
        if any(word in error_lower for word in ['network', 'connection', 'timeout', 'unreachable']):
            category = 'network'
            retry_strategy = 'backoff'
            max_retries = 4
        elif any(word in error_lower for word in ['config', 'setting', 'invalid', 'missing']):
            category = 'config'
            retry_strategy = 'manual'
            max_retries = 1
        elif any(word in error_lower for word in ['disk', 'memory', 'space', 'resource']):
            category = 'config'
            retry_strategy = 'manual'
            max_retries = 0
        else:
            category = 'transient'
            retry_strategy = 'immediate'
            max_retries = 2
        
        # Determine severity
        critical_keywords = ['critical', 'fatal', 'crash', 'died', 'killed']
        high_keywords = ['error', 'failed', 'exception', 'timeout']
        
        if any(word in error_lower for word in critical_keywords):
            severity = 'critical'
        elif any(word in error_lower for word in high_keywords):
            severity = 'high'
        elif 'warning' in error_lower:
            severity = 'low'
        else:
            severity = 'medium'
        
        return {
            'error_signature': error_signature,
            'category': category,
            'severity': severity,
            'retry_strategy': retry_strategy,
            'max_retries': max_retries,
            'backoff_multiplier': 2.0,
            'known_pattern': False
        }
    
    def _calculate_retry_delay(self, strategy: str, attempt: int, classification: dict) -> int:
        """Calculate retry delay based on strategy"""
        base_delay = 5  # seconds
        
        if strategy == 'immediate':
            return 0
        
        elif strategy == 'backoff':
            multiplier = classification.get('backoff_multiplier', 2.0)
            delay = base_delay * (multiplier ** (attempt - 1))
            
            # Add jitter to prevent thundering herd
            jitter = random.uniform(0.8, 1.2)
            delay = int(delay * jitter)
            
            # Cap at 5 minutes
            return min(delay, 300)
        
        else:  # Default fixed delay
            return base_delay
    
    def _select_recovery_action(self, classification: dict, context: dict = None) -> Optional[str]:
        """Select appropriate recovery action based on error type"""
        category = classification.get('category', '')
        error_signature = classification.get('error_signature', '')
        
        # Browser-related errors
        if 'browser' in error_signature or 'webdriver' in error_signature:
            return 'browser_restart'
        
        # OBS-related errors
        elif 'obs' in error_signature:
            return 'obs_reconnect'
        
        # File-related errors
        elif 'file' in error_signature or 'locked' in error_signature:
            return 'file_cleanup'
        
        # Network issues
        elif category == 'network':
            return 'network_wait'
        
        # Configuration issues
        elif category == 'config':
            return 'config_reset'
        
        return None
    
    def _execute_recovery_action(self, queue_id: int, action_type: str) -> bool:
        """Execute recovery action"""
        if action_type not in self.recovery_actions:
            print(f"❌ Unknown recovery action: {action_type}")
            return False
        
        try:
            recovery_function = self.recovery_actions[action_type]
            success = recovery_function(queue_id)
            return success
            
        except Exception as e:
            print(f"❌ Recovery action {action_type} exception: {e}")
            return False
    
    # Recovery Action Functions (Simplified versions)
    def _recovery_browser_restart(self, queue_id: int) -> bool:
        """Recovery: Restart browser/webdriver"""
        try:
            print("🌐 Recovery: Simulating browser restart...")
            time.sleep(3)  # Simulate browser restart
            print("✅ Browser restart completed")
            return True
        except:
            return False
    
    def _recovery_obs_reconnect(self, queue_id: int) -> bool:
        """Recovery: Reconnect to OBS"""
        try:
            print("🎥 Recovery: Simulating OBS reconnect...")
            time.sleep(2)  # Simulate OBS reconnect
            print("✅ OBS reconnection completed")
            return True
        except:
            return False
    
    def _recovery_file_cleanup(self, queue_id: int) -> bool:
        """Recovery: Clean up locked files"""
        try:
            print("🗂️ Recovery: Simulating file cleanup...")
            time.sleep(1)  # Simulate file cleanup
            print("✅ File cleanup completed")
            return True
        except:
            return False
    
    def _recovery_memory_cleanup(self, queue_id: int) -> bool:
        """Recovery: Memory cleanup"""
        try:
            print("🧠 Recovery: Cleaning up memory...")
            import gc
            gc.collect()
            print("✅ Memory cleanup completed")
            return True
        except:
            return False
    
    def _recovery_network_wait(self, queue_id: int) -> bool:
        """Recovery: Wait for network stability"""
        try:
            print("🌐 Recovery: Waiting for network stability...")
            time.sleep(10)  # Wait for network
            print("✅ Network wait completed")
            return True
        except:
            return False
    
    def _recovery_config_reset(self, queue_id: int) -> bool:
        """Recovery: Reset configuration"""
        try:
            print("⚙️ Recovery: Simulating configuration reset...")
            time.sleep(2)  # Simulate config reset
            print("✅ Configuration reset completed")
            return True
        except:
            return False
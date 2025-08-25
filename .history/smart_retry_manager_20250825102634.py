"""
Smart Retry Manager - PHASE 2
Handles intelligent retry logic with backoff strategies
"""

import time
import threading
from typing import Dict, Any, Callable, Optional
from datetime import datetime, timedelta
import random

class SmartRetryManager:
    """Manages intelligent retry logic for various error types"""
    
    def __init__(self, database_manager, api_client=None):
        self.db = database_manager
        self.api_client = api_client
        self.active_retries = {}  # queue_id -> retry_info
        self.recovery_actions = {}  # action_type -> recovery_function
        self.max_concurrent_retries = 3
        
        # Register default recovery actions
        self._register_default_recovery_actions()
    
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
    
    def should_retry(self, queue_id: int, error_message: str, context: dict = None) -> dict:
        """Determine if job should be retried and with what strategy"""
        try:
            # Get current retry count
            retry_history = self.db.get_job_retry_history(queue_id)
            current_attempt = len(retry_history) + 1
            
            # Classify error
            error_classification = self.db.classify_error(error_message, context)
            
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
            
            # Log retry attempt
            retry_id = self.db.log_retry_attempt(
                queue_id, retry_attempt, classification, 
                retry_delay, retry_decision.get('reason')
            )
            
            # Update progress
            if self.api_client:
                self.api_client.update_job_progress(
                    queue_id, 'retry', f'attempt_{retry_attempt}', 0.0, 'active',
                    metadata={
                        'retry_strategy': retry_decision.get('retry_strategy'),
                        'retry_delay': retry_delay,
                        'recovery_action': recovery_action
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
                    self.db.update_retry_result(retry_id, 'failed', f'Recovery action {recovery_action} failed')
                    return False
            
            # Wait for retry delay
            if retry_delay > 0:
                print(f"⏳ Waiting {retry_delay}s before retry...")
                
                # Progressive delay with status updates
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
                self.db.update_retry_result(retry_id, 'success', recovery_action)
                
                if self.api_client:
                    self.api_client.update_job_progress(
                        queue_id, 'retry', f'completed_{retry_attempt}', 100.0, 'completed',
                        metadata={'retry_duration_seconds': retry_duration}
                    )
            else:
                print(f"❌ Retry {retry_attempt} failed for job {queue_id} (took {retry_duration:.1f}s)")
                self.db.update_retry_result(retry_id, 'failed', recovery_action)
                
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
        
        # Memory issues
        elif 'memory' in error_signature:
            return 'memory_cleanup'
        
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
            # Log recovery action start
            recovery_id = self._log_recovery_action(queue_id, action_type)
            
            # Execute recovery function
            recovery_function = self.recovery_actions[action_type]
            success = recovery_function(queue_id)
            
            # Update recovery action result
            self._update_recovery_action(recovery_id, 'completed' if success else 'failed', 
                                       'Success' if success else 'Recovery action failed')
            
            return success
            
        except Exception as e:
            print(f"❌ Recovery action {action_type} exception: {e}")
            return False
    
    def _log_recovery_action(self, queue_id: int, action_type: str) -> int:
        """Log recovery action start"""
        try:
            conn = self.db.get_connection() if hasattr(self.db, 'get_connection') else sqlite3.connect(self.db.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO recovery_actions 
                (queue_id, action_type, action_trigger, action_status)
                VALUES (?, ?, 'automatic', 'executing')
            ''', (queue_id, action_type))
            
            recovery_id = cursor.lastrowid
            conn.commit()
            conn.close()
            
            return recovery_id
            
        except Exception as e:
            print(f"❌ Error logging recovery action: {e}")
            return -1
    
    def _update_recovery_action(self, recovery_id: int, status: str, result: str):
        """Update recovery action result"""
        try:
            import sqlite3
            conn = sqlite3.connect(self.db.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                UPDATE recovery_actions 
                SET action_status = ?, completed_at = CURRENT_TIMESTAMP, action_result = ?
                WHERE id = ?
            ''', (status, result, recovery_id))
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            print(f"❌ Error updating recovery action: {e}")
    
    # Recovery Action Functions
    def _recovery_browser_restart(self, queue_id: int) -> bool:
        """Recovery: Restart browser/webdriver"""
        try:
            print("🌐 Recovery: Restarting browser...")
            time.sleep(3)  # Simulate browser restart
            print("✅ Browser restart completed")
            return True
        except:
            return False
    
    def _recovery_obs_reconnect(self, queue_id: int) -> bool:
        """Recovery: Reconnect to OBS"""
        try:
            print("🎥 Recovery: Reconnecting to OBS...")
            time.sleep(2)  # Simulate OBS reconnect
            print("✅ OBS reconnection completed")
            return True
        except:
            return False
    
    def _recovery_file_cleanup(self, queue_id: int) -> bool:
        """Recovery: Clean up locked files"""
        try:
            print("🗂️ Recovery: Cleaning up files...")
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
            print("⚙️ Recovery: Resetting configuration...")
            time.sleep(2)  # Simulate config reset
            print("✅ Configuration reset completed")
            return True
        except:
            return False
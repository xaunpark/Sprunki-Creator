"""
Creator API Client - Bridge between Creator and Tracker
Replaces tkinter GUI with API-based job polling
"""

import json
import threading
import time
import requests
from threading import Thread
import os
from typing import Dict, Optional, Any
from datetime import datetime
from config_manager import get_config
from performance_analyzer import performance_analyzer

# --- FIX: Import necessary components ---
from sprunki_game_player import SprunkiGamePlayer
from sprunki_obs_controller import SprunkiController
# --- FIX: Import ProcessingConfig to create the configuration object ---
from sprunki_ultimate_master import UnifiedProcessingManager, ProcessingConfig

from collections.abc import Callable
from smart_retry_manager import SmartRetryManager
from file_validator import FileQualityValidator

class TrackerAPIClient:
    def __init__(self, tracker_url="http://localhost:5000"):
        self.tracker_url = tracker_url
        self.config = get_config()
        self.service_name = f"creator-{self.config.get('application', {}).get('instance_id', '1')}"
        self.session = requests.Session()
        self.timeout = 30
        
        self.capabilities = {
            'master': True,
            'individual': True, 
            'both': True,
            'max_concurrent': 1,
            'version': self.config.get('application', {}).get('version', '1.0')
        }
        
        self.host_info = {
            'ip': '127.0.0.1',
            'port': 8001,
            'version': self.capabilities['version'],
            'config_loaded': True
        }
        
        print(f"🔗 Tracker API Client initialized")
        print(f"   Service: {self.service_name}")
        print(f"   Tracker: {self.tracker_url}")
    
    def register_service(self) -> bool:
        """Register this Creator instance with Tracker"""
        try:
            response = self.session.post(
                f"{self.tracker_url}/api/creators/register", 
                json={
                    'service_name': self.service_name,
                    'host_info': self.host_info,
                    'capabilities': self.capabilities
                },
                timeout=self.timeout
            )
            
            if response.status_code == 200:
                print(f"✅ Registered with Tracker: {self.service_name}")
                return True
            else:
                print(f"❌ Registration failed: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            print(f"❌ Registration error: {e}")
            return False
    
    def send_heartbeat(self, status="online", current_job_id=None, performance_stats=None) -> bool:
        """Send heartbeat to Tracker"""
        try:
            payload = {
                'service_name': self.service_name,
                'status': status,
                'current_job_id': current_job_id,
                'performance_stats': performance_stats
            }
            
            response = self.session.post(
                f"{self.tracker_url}/api/creators/heartbeat", 
                json=payload,
                timeout=self.timeout
            )
            return response.status_code == 200
            
        except Exception as e:
            print(f"⚠️ Heartbeat error: {e}")
            return False
    
    def get_next_job(self) -> Optional[Dict]:
        """Get next job from Tracker queue"""
        try:
            response = self.session.post(
                f"{self.tracker_url}/api/queue/next", 
                json={
                    'service_name': self.service_name,
                    'capabilities': self.capabilities
                },
                timeout=self.timeout
            )
            
            if response.status_code == 200:
                data = response.json()
                if data.get('status') == 'success' and data.get('job'):
                    return data['job']
                else:
                    return None
            else:
                print(f"⚠️ Get job failed: {response.status_code}")
                return None
                
        except Exception as e:
            print(f"❌ Get job error: {e}")
            return None
    
    def update_job_status(self, queue_id: int, status: str, details: Optional[Dict] = None, 
                         video_files: Optional[Dict] = None, error_log: Optional[str] = None) -> bool:
        """Update job status in Tracker"""
        try:
            payload = {
                'status': status,
                'details': details,
                'video_files': video_files,
                'error_log': error_log
            }
            
            response = self.session.put(
                f"{self.tracker_url}/api/queue/status/{queue_id}", 
                json=payload,
                timeout=self.timeout
            )
            
            if response.status_code == 200:
                print(f"📊 Updated job {queue_id} status: {status}")
                return True
            else:
                print(f"⚠️ Status update failed: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            print(f"❌ Status update error: {e}")
            return False

    def update_job_progress(self, queue_id: int, stage: str, substep: Optional[str] = None,
                        progress_percent: float = 0.0, status: str = 'active',
                        metadata: Optional[Dict] = None, error_details: Optional[str] = None) -> bool:
        """Report detailed job progress to Tracker - PHASE 1"""
        try:
            payload = {
                'stage': stage,
                'substep': substep,
                'progress_percent': progress_percent,
                'status': status,
                'metadata': metadata,
                'error_details': error_details
            }
            
            response = self.session.post(
                f"{self.tracker_url}/api/queue/progress/{queue_id}",
                json=payload,
                timeout=self.timeout
            )
            
            if response.status_code == 200:
                # Optional: Only log important milestones to reduce noise
                if status in ['completed', 'failed'] or progress_percent in [0.0, 25.0, 50.0, 75.0, 100.0]:
                    print(f"📊 Progress {queue_id}: {stage}/{substep} -> {progress_percent}% ({status})")
                return True
            else:
                print(f"⚠️ Progress update failed: {response.status_code}")
                return False
            
        except Exception as e:
            print(f"⚠️ Progress update error: {e}")
            return False

    def get_job_progress(self, queue_id: int) -> dict:
        """Get detailed progress for job - PHASE 1"""
        try:
            response = self.session.get(
                f"{self.tracker_url}/api/queue/progress/{queue_id}",
                timeout=self.timeout
            )
            
            if response.status_code == 200:
                data = response.json()
                return data.get('progress', {})
            else:
                print(f"⚠️ Get progress failed: {response.status_code}")
                return {}
            
        except Exception as e:
            print(f"❌ Get progress error: {e}")
            return {}

class CreatorService:
    """
    Creator Service - API-driven replacement for tkinter GUI
    Polls Tracker for jobs and processes them using existing UnifiedProcessingManager
    """
    
    def __init__(self, tracker_url="http://localhost:5000"):
        self.api_client = TrackerAPIClient(tracker_url)

        self.config_manager = get_config()
        self.config = self.config_manager.config

        self.running = False
        self.processing_thread = None
        self.heartbeat_thread = None
        self.current_job = None
        
        self.game_player = None
        self.obs_controller = None
        # self.config = None #Dòng này bây giờ không cần nữa và có thể bị ghi đè. Tốt nhất nên xóa.
        
        from sprunki_ultimate_master import UnifiedProcessingManager
        self.processing_manager = UnifiedProcessingManager(master_controller=self)

        # 🆕 PHASE 2: Initialize Smart Retry Manager (API-based, no database import)
        self.retry_manager = SmartRetryManager(self.api_client)

        # 🆕 PHASE 3: Initialize File Quality Validator
        self.file_validator = FileQualityValidator()

        # 🆕 PHASE 4: Initialize Performance Monitoring
        self.performance_analyzer = performance_analyzer

        # --- FIX: Initialize the processing config for the manager ---
        self._initialize_processing_config()
        print("🤖 Creator Service initialized with API-based Smart Retry Manager")

    # --- FIX: New method to create and set the ProcessingConfig ---
    def _initialize_processing_config(self):
        """Create and set the ProcessingConfig for the manager from the main config file."""
        try:
            print("⚙️ Initializing processing configuration for service mode...")
            # Use the config loaded by the api_client's config_manager
            main_config = self.api_client.config
            
            processing_config_data = main_config.get('processing', {})
            output_config = main_config.get('output', {})
            
            # Create the specific ProcessingConfig object
            processing_config = ProcessingConfig(
                output_base_dir=output_config.get('base_directory', r"C:\Users\dinht\Videos\SPRUNKI"),
                max_retries=processing_config_data.get('max_retries', 2),
                auto_skip_on_error=processing_config_data.get('auto_skip_on_error', True),
                pause_between_items=processing_config_data.get('pause_between_items', 5)
            )
            
            # Assign it to the manager
            self.processing_manager.config = processing_config
            print(f"✅ Processing config set. Output directory: {processing_config.output_base_dir}")
        except Exception as e:
            print(f"❌ CRITICAL: Failed to initialize processing config: {e}")
            self.processing_manager.config = None

    def _initialize_components(self, config_data: Dict[str, Any]) -> bool:
        """Initialize game player and OBS controller for a specific job."""
        try:
            print("⚙️ Initializing components for new job...")
            
            # ADDED: Cleanup previous OBS controller if exists
            if hasattr(self, 'obs_controller') and self.obs_controller:
                print("🧹 Cleaning up previous OBS controller...")
                self.obs_controller = None
                time.sleep(1)  # Brief pause for cleanup
            
            self.game_player = SprunkiGamePlayer()
            self.game_player.config = config_data
            
            current_url = config_data.get('currentUrl', '').lower()
            if 'cocrea.world' not in current_url:
                print(f"❌ Unsupported Domain: {current_url}")
                return False

            game_settings = config_data.get('gameSettings', {})
            has_horror = game_settings.get('hasHorror', False)
            
            self.game_player.remix_library = self.game_player.generate_random_remixes(avoid_horror_icon=has_horror)
            self.game_player.current_remix_index = 0
            
            # Enhanced OBS controller initialization for service mode
            print("🎥 Initializing OBS controller for service mode...")
            self.obs_controller = SprunkiController()
            
            if not self.obs_controller.load_config_from_dict(config_data):
                print("❌ OBS Error: Could not load recording area config!")
                return False
            
            self.config = config_data
            print("✅ Components initialized successfully.")
            return True
            
        except Exception as e:
            print(f"❌ Error initializing components: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def start(self):
        """Start Creator service"""
        print("🚀 Starting Creator Service...")
        
        if not self.api_client.register_service():
            print("❌ Failed to register with Tracker")
            return False
        
        self.running = True

        # 🆕 PHASE 4: Start performance monitoring
        self.performance_analyzer.start_monitoring()
        
        self.processing_thread = threading.Thread(target=self._processing_loop, daemon=True)
        self.processing_thread.start()
        
        self.heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self.heartbeat_thread.start()
        
        print("✅ Creator Service started successfully with performance monitoring")
        return True
    
    def stop(self):
        """Stop Creator service with performance cleanup - PHASE 4 Enhanced"""
        print("🛑 Stopping Creator Service...")
        self.running = False
        
        # 🆕 PHASE 4: Stop performance monitoring
        self.performance_analyzer.stop_monitoring()
        
        if self.processing_thread:
            self.processing_thread.join(timeout=5)
        if self.heartbeat_thread:
            self.heartbeat_thread.join(timeout=5)
        
        print("✅ Creator Service stopped")
    
    def _processing_loop(self):
        """Main processing loop - polls for jobs"""
        print("🔄 Processing loop started")
        
        while self.running:
            try:
                job = self.api_client.get_next_job()
                
                if job:
                    self.current_job = job
                    print(f"📥 Received job: {job.get('mod_title', 'Unknown Title')} ({job.get('production_mode', 'N/A')})")
                    
                    success = self._process_job(job)
                    
                    if success:
                        print(f"✅ Job completed: {job['id']}")
                    else:
                        print(f"❌ Job failed: {job['id']}")
                    
                    self.current_job = None
                else:
                    # --- FIX: Add a short pause to the job polling loop ---
                    # In service mode, we pause here between jobs, not in the manager's loop.
                    if self.processing_manager.config:
                        pause_duration = self.processing_manager.config.pause_between_items
                        print(f"No jobs available. Pausing for {pause_duration}s...")
                        time.sleep(pause_duration)
                    else:
                        time.sleep(30) # Fallback pause
                
            except Exception as e:
                print(f"❌ Processing loop error: {e}")
                time.sleep(10)
    
    def _heartbeat_loop(self):
        """Heartbeat loop - reports status to Tracker"""
        while self.running:
            try:
                status = "busy" if self.current_job else "online"
                current_job_id = self.current_job['id'] if self.current_job else None
                
                self.api_client.send_heartbeat(status=status, current_job_id=current_job_id)
                time.sleep(60)
                
            except Exception as e:
                print(f"⚠️ Heartbeat error: {e}")
                time.sleep(30)
    
    def _process_job(self, job: Dict) -> bool:
        """Process job with performance tracking - PHASE 4 Enhanced"""
        queue_id = job['id']
        max_job_retries = 3
        
        # 🆕 PHASE 4: Record job start
        self.performance_analyzer.record_job_start(queue_id, {
            'mod_title': job.get('mod_title', 'Unknown'),
            'production_mode': job.get('production_mode', 'unknown'),
            'mod_game_url': job.get('mod_game_url', '')
        })
        
        for job_attempt in range(1, max_job_retries + 1):
            try:
                print(f"📋 Job {queue_id} - Attempt {job_attempt}/{max_job_retries}")
                
                processing_item = self._convert_job_to_processing_item(job)
                
                self.api_client.update_job_status(queue_id, 'processing', {
                    'stage': 'starting',
                    'started_by': self.api_client.service_name,
                    'attempt': job_attempt
                })
                
                success = self._process_with_smart_retry(processing_item, queue_id)
                
                if success:
                    print(f"✅ Job {queue_id} completed successfully on attempt {job_attempt}")
                    
                    # 🆕 PHASE 4: Record successful completion
                    self.performance_analyzer.record_job_completion(queue_id, True, {
                        'attempt': job_attempt,
                        'output_files_count': len(processing_item.output_files)
                    })
                    
                    return True
                else:
                    if job_attempt < max_job_retries:
                        print(f"❌ Job {queue_id} failed on attempt {job_attempt}, will retry")
                        
                        # 🆕 PHASE 4: Record retry
                        self.performance_analyzer.record_job_retry(queue_id, {
                            'attempt': job_attempt,
                            'reason': 'job_failed'
                        })
                        
                        job_retry_delay = min(30 * job_attempt, 120)
                        print(f"⏳ Waiting {job_retry_delay}s before job retry...")
                        time.sleep(job_retry_delay)
                        
                        self.api_client.update_job_progress(
                            queue_id, 'retry', f'job_retry_{job_attempt}', 0.0, 'active',
                            metadata={'job_attempt': job_attempt + 1}
                        )
                        # 🆕 PHASE 4: Track performance
                        self.performance_analyzer.record_job_progress(queue_id, 'retry', 0.0)
                    else:
                        print(f"❌ Job {queue_id} failed after {max_job_retries} attempts")
                        
                        # 🆕 PHASE 4: Record failed completion
                        self.performance_analyzer.record_job_completion(queue_id, False, {
                            'attempt': job_attempt,
                            'failure_reason': 'max_retries_exceeded'
                        })
                        
                        self.api_client.update_job_status(queue_id, 'failed', 
                            error_log=f"Job failed after {max_job_retries} attempts")
                        return False
                        
            except Exception as e:
                print(f"❌ Critical error in job {queue_id}, attempt {job_attempt}: {e}")
                if job_attempt == max_job_retries:
                    # 🆕 PHASE 4: Record exception completion
                    self.performance_analyzer.record_job_completion(queue_id, False, {
                        'attempt': job_attempt,
                        'failure_reason': 'critical_exception',
                        'exception': str(e)
                    })
                    
                    self.api_client.update_job_status(queue_id, 'failed', 
                        error_log=f"Critical job error: {str(e)}")
                    return False
        
        return False

    def _process_with_smart_retry(self, item, queue_id: int) -> bool:
        """Process with smart retry and ensure manifest generation."""
        try:
            # Step 1: JSON Generation
            if item.source_url and not item.json_data:
                json_success = self._execute_with_retry(
                    queue_id, 'json_generation',
                    lambda: self.processing_manager._generate_json_for_item(item),
                    context={'url': item.source_url}
                )
                if not json_success:
                    item.end_time = datetime.now() # Đặt thời gian kết thúc
                    self.processing_manager._generate_manifest_for_item(item) # Cố gắng tạo manifest lỗi
                    return False
            
            # Step 2: Video Processing
            video_success = self._execute_with_retry(
                queue_id, 'video_processing',
                lambda: self.processing_manager._process_video_for_item(item),
                context={'mode': item.mode, 'project_slug': item.project_slug}
            )
            
            # Step 3: Luôn tạo manifest sau khi xử lý xong (dù thành công hay thất bại)
            item.end_time = datetime.now()
            self.processing_manager._generate_manifest_for_item(item)

            return video_success
            
        except Exception as e:
            print(f"❌ Smart retry processing error: {e}")
            item.status = "failed"
            item.error_message = f"Critical error in retry manager: {e}"
            item.end_time = datetime.now()
            self.processing_manager._generate_manifest_for_item(item)
            return False

    def _execute_with_retry(self, queue_id: int, operation_name: str, operation_function: Callable, 
                        context: Optional[dict] = None, max_step_retries: int = 3) -> bool:
        """Execute operation with smart retry logic - PHASE 2 (API-based)"""
        
        for step_attempt in range(1, max_step_retries + 1):
            try:
                print(f"🔧 {operation_name} - Step attempt {step_attempt}/{max_step_retries}")
                
                # Execute the operation
                result = operation_function()
                
                if result:
                    print(f"✅ {operation_name} succeeded on attempt {step_attempt}")
                    return True
                else:
                    error_msg = f"{operation_name} returned False"
                    print(f"❌ {operation_name} failed on attempt {step_attempt}: {error_msg}")
                    
                    # Prepare context with attempt number
                    retry_context = {**(context or {}), 'operation': operation_name, 'attempt': step_attempt}
                    
                    # Use smart retry logic
                    retry_decision = self.retry_manager.should_retry(queue_id, error_msg, retry_context)
                    
                    if not retry_decision['should_retry']:
                        print(f"🚫 Not retrying {operation_name}: {retry_decision['reason']}")
                        
                        # Handle special cases
                        if retry_decision.get('requires_manual'):
                            self.api_client.update_job_status(queue_id, 'failed',
                                error_log=f"{operation_name} requires manual intervention: {error_msg}")
                        elif retry_decision.get('should_skip'):
                            self.api_client.update_job_status(queue_id, 'failed',
                                error_log=f"{operation_name} should be skipped: {error_msg}")
                        
                        return False
                    
                    # Execute retry if this isn't the last attempt
                    if step_attempt < max_step_retries:
                        retry_success = self.retry_manager.execute_retry(
                            queue_id, retry_decision, operation_function
                        )
                        
                        if retry_success:
                            print(f"✅ {operation_name} retry succeeded")
                            return True
                        else:
                            print(f"❌ {operation_name} retry failed, will try again")
                            continue
                    else:
                        print(f"❌ {operation_name} exhausted all {max_step_retries} attempts")
                        return False
                        
            except Exception as e:
                error_msg = f"{operation_name} exception: {str(e)}"
                print(f"❌ {operation_name} exception on attempt {step_attempt}: {e}")
                
                # Prepare context with attempt number and exception flag
                retry_context = {
                    **(context or {}), 
                    'operation': operation_name, 
                    'attempt': step_attempt, 
                    'exception': True
                }
                
                # Use smart retry for exceptions too
                retry_decision = self.retry_manager.should_retry(queue_id, error_msg, retry_context)
                
                if not retry_decision['should_retry'] or step_attempt == max_step_retries:
                    print(f"🚫 Not retrying {operation_name} exception: {retry_decision.get('reason', 'Max attempts reached')}")
                    
                    # Final failure
                    self.api_client.update_job_progress(
                        queue_id, operation_name, 'exception', 0.0, 'failed',
                        error_details=error_msg
                    )
                    # 🆕 PHASE 4: Track performance
                    self.performance_analyzer.record_job_progress(queue_id, 'retry', 0.0)                    
                    return False
                
                # Execute retry for exception
                if step_attempt < max_step_retries:
                    print(f"🔄 Retrying {operation_name} after exception...")
                    retry_success = self.retry_manager.execute_retry(
                        queue_id, retry_decision, operation_function
                    )
                    
                    if retry_success:
                        return True
        
        return False
    
    def _convert_job_to_processing_item(self, job: Dict):
        """Convert Tracker job to ProcessingItem"""
        from sprunki_ultimate_master import ProcessingItem
        
        item = ProcessingItem(
            source_url=job.get('mod_game_url'),
            mode=job['production_mode'],
            project_slug=self._create_slug_from_title(job.get('mod_title', 'untitled-job'))
        )
        return item
    
    def _create_slug_from_title(self, title: str) -> str:
        """Create project slug from mod title"""
        import re
        slug = re.sub(r'[^a-zA-Z0-9\-]', '-', title.lower())
        slug = re.sub(r'-+', '-', slug).strip('-')
        return slug or "video-job"
    
    def _generate_json_and_process(self, item, queue_id: int) -> bool:
        """Generate JSON then process video"""
        try:
            self.api_client.update_job_status(queue_id, 'processing', {
                'stage': 'json_generation'
            })
            
            success = self.processing_manager._generate_json_for_item(item)
            
            if not success:
                self.api_client.update_job_status(queue_id, 'failed', 
                    error_log="JSON generation failed")
                return False
            
            return self._process_video_for_item(item, queue_id)
            
        except Exception as e:
            print(f"❌ JSON generation error: {e}")
            return False
    
    def _process_video_for_item(self, item, queue_id: int) -> bool:
        """Process video with detailed progress reporting - PHASE 1"""
        try:
            # 🆕 START: Initialize progress tracking
            self.api_client.update_job_progress(
                queue_id, 'initialization', 'starting', 0.0, 'active',
                metadata={'item_mode': item.mode, 'project_slug': item.project_slug}
            )
            # 🆕 PHASE 4: Track performance
            self.performance_analyzer.record_job_progress(queue_id, 'retry', 0.0)            
            print(f"🎥 Processing video for item: {item.project_slug}")
            print(f"📊 Mode: {item.mode}")
            print(f"🔗 URL: {item.source_url}")
            print(f"📋 JSON data available: {item.json_data is not None}")
            
            # 🆕 PROGRESS: Component initialization
            self.api_client.update_job_progress(
                queue_id, 'initialization', 'components', 10.0, 'active'
            )
            # 🆕 PHASE 4: Track performance
            self.performance_analyzer.record_job_progress(queue_id, 'retry', 0.0) 

            if not self._initialize_components(item.json_data):
                self.api_client.update_job_progress(
                    queue_id, 'initialization', 'components', 0.0, 'failed',
                    error_details="Failed to initialize components"
                )

                # 🆕 PHASE 4: Track performance
                self.performance_analyzer.record_job_progress(queue_id, 'retry', 0.0)

                return False
            
            # 🆕 PROGRESS: OBS connection test
            self.api_client.update_job_progress(
                queue_id, 'initialization', 'obs_test', 25.0, 'active'
            )

            # 🆕 PHASE 4: Track performance
            self.performance_analyzer.record_job_progress(queue_id, 'retry', 0.0)

            if hasattr(self, 'obs_controller') and self.obs_controller:
                print("🔍 Testing OBS connection...")
                test_ws = self.obs_controller.connect_obs_fast()
                if test_ws:
                    print("✅ OBS connection test successful")
                    self.api_client.update_job_progress(
                        queue_id, 'initialization', 'obs_test', 40.0, 'completed'
                    )

                    # 🆕 PHASE 4: Track performance
                    self.performance_analyzer.record_job_progress(queue_id, 'retry', 0.0)

                    test_ws.close()
                else:
                    print("❌ OBS connection test failed")
                    self.api_client.update_job_progress(
                        queue_id, 'initialization', 'obs_test', 0.0, 'failed',
                        error_details="OBS connection test failed before video processing"
                    )

                    # 🆕 PHASE 4: Track performance
                    self.performance_analyzer.record_job_progress(queue_id, 'retry', 0.0)

                    return False
            
            # 🆕 PROGRESS: Start main processing
            self.api_client.update_job_progress(
                queue_id, 'video_processing', 'starting', 50.0, 'active'
            )

            # 🆕 PHASE 4: Track performance
            self.performance_analyzer.record_job_progress(queue_id, 'retry', 0.0)

            # Enhanced processing with progress callbacks
            success = self._process_with_progress_callbacks(item, queue_id)
                        
            if success:
                # 🆕 PHASE 3: Validate output files before marking as completed
                print(f"🔍 Validating output files for quality control...")
                
                self.api_client.update_job_progress(
                    queue_id, 'validation', 'files', 95.0, 'active',
                    metadata={'output_files_count': len(item.output_files)}
                )

                # 🆕 PHASE 4: Track performance
                self.performance_analyzer.record_job_progress(queue_id, 'retry', 0.0)

                # Organize and validate files
                video_files = self._organize_output_files(item.output_files)
                validation_summary = video_files.get('validation_summary', {})
                
                # Check if validation meets minimum requirements
                validation_success = self._check_validation_requirements(validation_summary, item.mode)
                
                if validation_success:
                    print(f"✅ File validation passed: {validation_summary['success_rate']}% success rate")
                    
                    self.api_client.update_job_progress(
                        queue_id, 'completion', 'finalizing', 100.0, 'completed',
                        metadata={
                            'validation_summary': validation_summary,
                            'total_valid_files': validation_summary.get('valid_files', 0)
                        }
                    )

                    # 🆕 PHASE 4: Track performance
                    self.performance_analyzer.record_job_progress(queue_id, 'retry', 0.0)

                    # Update traditional status with validation metadata
                    self.api_client.update_job_status(queue_id, 'completed', 
                        details={
                            'completion_time': datetime.now().isoformat(),
                            'file_validation': validation_summary
                        },
                        video_files=video_files)
                    
                    return True
                else:
                    print(f"❌ File validation failed: {validation_summary['success_rate']}% success rate")
                    
                    self.api_client.update_job_progress(
                        queue_id, 'validation', 'failed', 0.0, 'failed',
                        error_details=f"Validation failed: {validation_summary['invalid_files']} invalid files"
                    )

                    # 🆕 PHASE 4: Track performance
                    self.performance_analyzer.record_job_progress(queue_id, 'retry', 0.0)

                    self.api_client.update_job_status(queue_id, 'failed',
                        error_log=f"File validation failed: {validation_summary['success_rate']}% success rate")
                    return False
                
        except Exception as e:
            print(f"❌ Video processing error: {e}")
            import traceback
            traceback.print_exc()
            
            # 🆕 EXCEPTION: Critical error
            self.api_client.update_job_progress(
                queue_id, 'error', 'exception', 0.0, 'failed',
                error_details=f"Critical exception: {str(e)}"
            )

            # 🆕 PHASE 4: Track performance
            self.performance_analyzer.record_job_progress(queue_id, 'retry', 0.0)

            self.api_client.update_job_status(queue_id, 'failed',
                error_log=f"Video processing error: {str(e)}")
            return False
        return False        

    def _process_with_progress_callbacks(self, item, queue_id: int) -> bool:
        """Enhanced processing with progress callbacks - PHASE 1"""
        try:
            mode = item.mode
            
            if mode == "master":
                self.api_client.update_job_progress(
                    queue_id, 'video_processing', 'master_mode', 60.0, 'active'
                )

                # 🆕 PHASE 4: Track performance
                self.performance_analyzer.record_job_progress(queue_id, 'retry', 0.0)

                success = self.processing_manager._process_master_mode(item)
                
            elif mode == "individual":
                self.api_client.update_job_progress(
                    queue_id, 'video_processing', 'individual_mode', 60.0, 'active'
                )

                # 🆕 PHASE 4: Track performance
                self.performance_analyzer.record_job_progress(queue_id, 'retry', 0.0)

                success = self.processing_manager._process_individual_mode(item)
                
            elif mode == "both":
                # Master phase
                self.api_client.update_job_progress(
                    queue_id, 'video_processing', 'both_master_phase', 60.0, 'active'
                )

                # 🆕 PHASE 4: Track performance
                self.performance_analyzer.record_job_progress(queue_id, 'retry', 0.0)

                # Note: Individual phase progress will be updated inside _process_both_mode_enhanced
                self.api_client.update_job_progress(
                    queue_id, 'video_processing', 'both_individual_phase', 80.0, 'active'
                )

                # 🆕 PHASE 4: Track performance
                self.performance_analyzer.record_job_progress(queue_id, 'retry', 0.0)

                success = self.processing_manager._process_both_mode_enhanced(item)
            else:
                # Unknown mode
                self.api_client.update_job_progress(
                    queue_id, 'video_processing', 'unknown_mode', 0.0, 'failed',
                    error_details=f"Unknown processing mode: {mode}"
                )

                # 🆕 PHASE 4: Track performance
                self.performance_analyzer.record_job_progress(queue_id, 'retry', 0.0)

                return False
            
            if success:
                self.api_client.update_job_progress(
                    queue_id, 'video_processing', 'completed', 95.0, 'completed'
                )

                # 🆕 PHASE 4: Track performance
                self.performance_analyzer.record_job_progress(queue_id, 'retry', 0.0)

            else:
                self.api_client.update_job_progress(
                    queue_id, 'video_processing', 'failed', 0.0, 'failed',
                    error_details=f"Mode {mode} processing failed"
                )

                # 🆕 PHASE 4: Track performance
                self.performance_analyzer.record_job_progress(queue_id, 'retry', 0.0)

            return success
            
        except Exception as e:
            print(f"❌ Process with callbacks error: {e}")
            self.api_client.update_job_progress(
                queue_id, 'video_processing', 'exception', 0.0, 'failed',
                error_details=f"Processing callback exception: {str(e)}"
            )

            # 🆕 PHASE 4: Track performance
            self.performance_analyzer.record_job_progress(queue_id, 'retry', 0.0)

            return False

    def _organize_output_files(self, output_files: list) -> dict:
        """Organize and validate output files - PHASE 3 Enhanced"""
        print(f"📁 Organizing and validating {len(output_files)} output files...")
        
        video_files = {
            'master': [],
            'individual': [],
            'validation_summary': {}
        }
        
        if not output_files:
            print("⚠️ No output files to organize")
            return video_files
        
        # Separate files by type
        master_files = []
        individual_files = []
        
        for file_path in output_files:
            filename = os.path.basename(file_path).lower()
            
            if any(keyword in filename for keyword in ['showcase', 'master']):
                master_files.append(file_path)
            elif any(keyword in filename for keyword in ['individual', 'n-', 'h-', 'remix']):
                individual_files.append(file_path)
            else:
                # Unknown type, treat as individual
                individual_files.append(file_path)
        
        # Validate master files
        if master_files:
            print(f"🎬 Validating {len(master_files)} master file(s)...")
            master_validation = self.file_validator.validate_batch(master_files, 'video')
            video_files['master'] = self._process_validation_results(master_validation)
            
            print(f"   Master validation: {master_validation['summary']['valid_files']}/{master_validation['summary']['total_files']} valid")
        
        # Validate individual files
        if individual_files:
            print(f"🎭 Validating {len(individual_files)} individual file(s)...")
            individual_validation = self.file_validator.validate_batch(individual_files, 'video')
            video_files['individual'] = self._process_validation_results(individual_validation)
            
            print(f"   Individual validation: {individual_validation['summary']['valid_files']}/{individual_validation['summary']['total_files']} valid")
        
        # Create combined validation summary
        total_files = len(master_files) + len(individual_files)
        total_valid = len(video_files['master']) + len(video_files['individual'])
        
        video_files['validation_summary'] = {
            'total_files': total_files,
            'valid_files': total_valid,
            'invalid_files': total_files - total_valid,
            'success_rate': round((total_valid / max(total_files, 1)) * 100, 1),
            'master_files': len(video_files['master']),
            'individual_files': len(video_files['individual'])
        }
        
        print(f"✅ File organization completed: {total_valid}/{total_files} files validated successfully")
        
        return video_files

    def _process_validation_results(self, validation_results: dict) -> list:
        """Process validation results and return only valid files with metadata"""
        valid_files = []
        
        for result in validation_results.get('individual_results', []):
            if result.get('is_valid', False):
                # Add validation metadata to file info
                file_info = {
                    'path': result['file_path'],
                    'size_mb': result.get('size_mb', 0),
                    'quality_score': result.get('quality_score', 0),
                    'quality_grade': result.get('quality_grade', 'unknown'),
                    'validation_time': result.get('validation_time', 0)
                }
                
                # Add technical details if available
                tech = result.get('technical_analysis', {})
                if tech.get('available'):
                    file_info.update({
                        'duration_seconds': tech.get('duration_seconds', 0),
                        'resolution': tech.get('video_resolution', 'unknown'),
                        'bitrate_kbps': tech.get('bitrate_kbps', 0),
                        'codec': tech.get('video_codec', 'unknown')
                    })
                
                valid_files.append(file_info)
            else:
                # Log invalid file details
                filename = os.path.basename(result.get('file_path', 'unknown'))
                errors = result.get('validation_errors', [])
                print(f"❌ Invalid file: {filename}")
                for error in errors[:3]:  # Show first 3 errors
                    print(f"   - {error}")
        
        return valid_files

    def _check_validation_requirements(self, validation_summary: dict, mode: str) -> bool:
        """Check if validation meets minimum requirements for the mode"""
        try:
            success_rate = validation_summary.get('success_rate', 0)
            valid_files = validation_summary.get('valid_files', 0)
            
            # Different requirements based on mode
            if mode == "master":
                # Master mode: At least 1 valid file with 100% success rate
                return valid_files >= 1 and success_rate >= 100.0
            
            elif mode == "individual":
                # Individual mode: At least 70% success rate and minimum 3 files
                return valid_files >= 3 and success_rate >= 70.0
            
            elif mode == "both":
                # Both mode: At least 80% success rate and minimum 4 files
                master_files = validation_summary.get('master_files', 0)
                individual_files = validation_summary.get('individual_files', 0)
                
                return (valid_files >= 4 and 
                    success_rate >= 80.0 and 
                    master_files >= 1 and 
                    individual_files >= 3)
            
            else:
                # Unknown mode: Basic validation
                return valid_files >= 1 and success_rate >= 50.0
                
        except Exception as e:
            print(f"❌ Error checking validation requirements: {e}")
            return False

def main():
    """Main entry point for Creator Service"""
    print("🤖 Sprunki Creator Service")
    print("🔗 API-driven video production service")
    print("="*50)
    
    service = CreatorService()
    
    try:
        if service.start():
            print("✅ Service running. Press Ctrl+C to stop.")
            
            while service.running:
                time.sleep(1)
        else:
            print("❌ Failed to start service")
            
    except KeyboardInterrupt:
        print("\n🛑 Shutdown requested")
    finally:
        service.stop()

if __name__ == "__main__":
    main()

"""
Creator API Client - Bridge between Creator and Tracker
Replaces tkinter GUI with API-based job polling
"""

import json
import time
import requests
import threading
from typing import Dict, Optional, Any
from datetime import datetime
from config_manager import get_config

# --- FIX: Import necessary components ---
from sprunki_game_player import SprunkiGamePlayer
from sprunki_obs_controller import SprunkiController
# --- FIX: Import ProcessingConfig to create the configuration object ---
from sprunki_ultimate_master import UnifiedProcessingManager, ProcessingConfig


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

    def update_job_progress(self, queue_id: int, stage: str, substep: str = None,
                        progress_percent: float = 0.0, status: str = 'active',
                        metadata: dict = None, error_details: str = None) -> bool:
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
        self.running = False
        self.processing_thread = None
        self.heartbeat_thread = None
        self.current_job = None
        
        self.game_player = None
        self.obs_controller = None
        self.config = None
        
        from sprunki_ultimate_master import UnifiedProcessingManager
        self.processing_manager = UnifiedProcessingManager(master_controller=self)
        
        # --- FIX: Initialize the processing config for the manager ---
        self._initialize_processing_config()
        
        print("🤖 Creator Service initialized")

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
        
        self.processing_thread = threading.Thread(target=self._processing_loop, daemon=True)
        self.processing_thread.start()
        
        self.heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self.heartbeat_thread.start()
        
        print("✅ Creator Service started successfully")
        return True
    
    def stop(self):
        """Stop Creator service"""
        print("🛑 Stopping Creator Service...")
        self.running = False
        
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
        """Process job using existing UnifiedProcessingManager logic"""
        try:
            queue_id = job['id']
            
            processing_item = self._convert_job_to_processing_item(job)
            
            self.api_client.update_job_status(queue_id, 'processing', {
                'stage': 'starting',
                'started_by': self.api_client.service_name
            })
            
            if processing_item.source_url and not processing_item.json_data:
                success = self._generate_json_and_process(processing_item, queue_id)
            else:
                success = self._process_video_for_item(processing_item, queue_id)
            
            return success
            
        except Exception as e:
            print(f"❌ Job processing error: {e}")
            self.api_client.update_job_status(job.get('id', -1), 'failed', 
                error_log=f"Processing error: {str(e)}")
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
            
            print(f"🎥 Processing video for item: {item.project_slug}")
            print(f"📊 Mode: {item.mode}")
            print(f"🔗 URL: {item.source_url}")
            print(f"📋 JSON data available: {item.json_data is not None}")
            
            # 🆕 PROGRESS: Component initialization
            self.api_client.update_job_progress(
                queue_id, 'initialization', 'components', 10.0, 'active'
            )
            
            if not self.processing_manager._initialize_components(item.json_data):
                self.api_client.update_job_progress(
                    queue_id, 'initialization', 'components', 0.0, 'failed',
                    error_details="Failed to initialize components"
                )
                return False
            
            # 🆕 PROGRESS: OBS connection test
            self.api_client.update_job_progress(
                queue_id, 'initialization', 'obs_test', 25.0, 'active'
            )
            
            if hasattr(self, 'obs_controller') and self.obs_controller:
                print("🔍 Testing OBS connection...")
                test_ws = self.obs_controller.connect_obs_fast()
                if test_ws:
                    print("✅ OBS connection test successful")
                    self.api_client.update_job_progress(
                        queue_id, 'initialization', 'obs_test', 40.0, 'completed'
                    )
                    test_ws.close()
                else:
                    print("❌ OBS connection test failed")
                    self.api_client.update_job_progress(
                        queue_id, 'initialization', 'obs_test', 0.0, 'failed',
                        error_details="OBS connection test failed before video processing"
                    )
                    return False
            
            # 🆕 PROGRESS: Start main processing
            self.api_client.update_job_progress(
                queue_id, 'video_processing', 'starting', 50.0, 'active'
            )
            
            # Enhanced processing with progress callbacks
            success = self._process_with_progress_callbacks(item, queue_id)
            
            if success:
                # 🆕 FINAL: Processing completed
                self.api_client.update_job_progress(
                    queue_id, 'completion', 'finalizing', 100.0, 'completed',
                    metadata={'output_files_count': len(item.output_files)}
                )
                
                # Update traditional status as well
                video_files = self._organize_output_files(item.output_files)
                self.api_client.update_job_status(queue_id, 'completed', 
                    details={'completion_time': datetime.now().isoformat()},
                    video_files=video_files)
                
                return True
            else:
                # 🆕 FAILED: Processing failed
                self.api_client.update_job_progress(
                    queue_id, 'video_processing', 'failed', 0.0, 'failed',
                    error_details="Video processing failed - check logs for details"
                )
                
                self.api_client.update_job_status(queue_id, 'failed',
                    error_log="Video processing failed")
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
            
            self.api_client.update_job_status(queue_id, 'failed',
                error_log=f"Video processing error: {str(e)}")
            return False

    def _process_with_progress_callbacks(self, item, queue_id: int) -> bool:
        """Enhanced processing with progress callbacks - PHASE 1"""
        try:
            mode = item.mode
            
            if mode == "master":
                self.api_client.update_job_progress(
                    queue_id, 'video_processing', 'master_mode', 60.0, 'active'
                )
                success = self.processing_manager._process_master_mode(item)
                
            elif mode == "individual":
                self.api_client.update_job_progress(
                    queue_id, 'video_processing', 'individual_mode', 60.0, 'active'
                )
                success = self.processing_manager._process_individual_mode(item)
                
            elif mode == "both":
                # Master phase
                self.api_client.update_job_progress(
                    queue_id, 'video_processing', 'both_master_phase', 60.0, 'active'
                )
                
                # Note: Individual phase progress will be updated inside _process_both_mode_enhanced
                self.api_client.update_job_progress(
                    queue_id, 'video_processing', 'both_individual_phase', 80.0, 'active'
                )
                
                success = self.processing_manager._process_both_mode_enhanced(item)
            else:
                # Unknown mode
                self.api_client.update_job_progress(
                    queue_id, 'video_processing', 'unknown_mode', 0.0, 'failed',
                    error_details=f"Unknown processing mode: {mode}"
                )
                return False
            
            if success:
                self.api_client.update_job_progress(
                    queue_id, 'video_processing', 'completed', 95.0, 'completed'
                )
            else:
                self.api_client.update_job_progress(
                    queue_id, 'video_processing', 'failed', 0.0, 'failed',
                    error_details=f"Mode {mode} processing failed"
                )
                
            return success
            
        except Exception as e:
            print(f"❌ Process with callbacks error: {e}")
            self.api_client.update_job_progress(
                queue_id, 'video_processing', 'exception', 0.0, 'failed',
                error_details=f"Processing callback exception: {str(e)}"
            )
            return False

    def _organize_output_files(self, output_files: list) -> dict:
        """Organize output files by type - PHASE 1 Helper"""
        video_files = {}
        
        for file_path in output_files:
            if 'showcase' in file_path or 'master' in file_path:
                video_files['master'] = file_path
            elif any(x in file_path for x in ['individual', 'n-', 'h-', 'remix']):
                if 'individual' not in video_files:
                    video_files['individual'] = []
                video_files['individual'].append(file_path)
        
        return video_files

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

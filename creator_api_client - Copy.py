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

class TrackerAPIClient:
    def __init__(self, tracker_url="http://localhost:5000"):
        self.tracker_url = tracker_url
        self.config = get_config()
        self.service_name = f"creator-{self.config.get('application', {}).get('instance_id', '1')}"
        self.session = requests.Session()
        self.session.timeout = 30
        
        # Service capabilities from config
        self.capabilities = {
            'master': True,
            'individual': True, 
            'both': True,
            'max_concurrent': 1,
            'version': self.config.get('application', {}).get('version', '1.0')
        }
        
        # Host info
        self.host_info = {
            'ip': '127.0.0.1',
            'port': 8001,  # Could be from config
            'version': self.capabilities['version'],
            'config_loaded': True
        }
        
        print(f"🔗 Tracker API Client initialized")
        print(f"   Service: {self.service_name}")
        print(f"   Tracker: {self.tracker_url}")
    
    def register_service(self) -> bool:
        """Register this Creator instance with Tracker"""
        try:
            response = self.session.post(f"{self.tracker_url}/api/creators/register", json={
                'service_name': self.service_name,
                'host_info': self.host_info,
                'capabilities': self.capabilities
            })
            
            if response.status_code == 200:
                print(f"✅ Registered with Tracker: {self.service_name}")
                return True
            else:
                print(f"❌ Registration failed: {response.status_code}")
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
            
            response = self.session.post(f"{self.tracker_url}/api/creators/heartbeat", json=payload)
            return response.status_code == 200
            
        except Exception as e:
            print(f"⚠️ Heartbeat error: {e}")
            return False
    
    def get_next_job(self) -> Optional[Dict]:
        """Get next job from Tracker queue"""
        try:
            response = self.session.post(f"{self.tracker_url}/api/queue/next", json={
                'service_name': self.service_name,
                'capabilities': self.capabilities
            })
            
            if response.status_code == 200:
                data = response.json()
                if data.get('status') == 'success' and data.get('job'):
                    return data['job']
                else:
                    # No jobs available
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
            
            response = self.session.put(f"{self.tracker_url}/api/queue/status/{queue_id}", json=payload)
            
            if response.status_code == 200:
                print(f"📊 Updated job {queue_id} status: {status}")
                return True
            else:
                print(f"⚠️ Status update failed: {response.status_code}")
                return False
                
        except Exception as e:
            print(f"❌ Status update error: {e}")
            return False

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
        
        # Initialize UnifiedProcessingManager (existing code)
        from sprunki_ultimate_master import UnifiedProcessingManager
        self.processing_manager = UnifiedProcessingManager(master_controller=None)
        
        print("🤖 Creator Service initialized")
    
    def start(self):
        """Start Creator service"""
        print("🚀 Starting Creator Service...")
        
        # Register with Tracker
        if not self.api_client.register_service():
            print("❌ Failed to register with Tracker")
            return False
        
        self.running = True
        
        # Start processing thread
        self.processing_thread = threading.Thread(target=self._processing_loop, daemon=True)
        self.processing_thread.start()
        
        # Start heartbeat thread  
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
                # Get next job from Tracker
                job = self.api_client.get_next_job()
                
                if job:
                    self.current_job = job
                    print(f"📥 Received job: {job['title']} ({job['production_mode']})")
                    
                    # Process job using existing logic
                    success = self._process_job(job)
                    
                    if success:
                        print(f"✅ Job completed: {job['id']}")
                    else:
                        print(f"❌ Job failed: {job['id']}")
                    
                    self.current_job = None
                else:
                    # No jobs available, wait
                    time.sleep(30)
                
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
                time.sleep(60)  # Send heartbeat every minute
                
            except Exception as e:
                print(f"⚠️ Heartbeat error: {e}")
                time.sleep(30)
    
    def _process_job(self, job: Dict) -> bool:
        """Process job using existing UnifiedProcessingManager logic"""
        try:
            queue_id = job['id']
            
            # Convert Tracker job to ProcessingItem format
            processing_item = self._convert_job_to_processing_item(job)
            
            # Update status to processing
            self.api_client.update_job_status(queue_id, 'processing', {
                'stage': 'starting',
                'started_by': self.api_client.service_name
            })
            
            # Use existing processing logic
            if processing_item.json_data:
                # Already has JSON, skip to video processing
                success = self._process_video_for_item(processing_item, queue_id)
            else:
                # Need JSON generation first
                success = self._generate_json_and_process(processing_item, queue_id)
            
            return success
            
        except Exception as e:
            print(f"❌ Job processing error: {e}")
            
            # Report failure
            self.api_client.update_job_status(queue_id, 'failed', 
                error_log=f"Processing error: {str(e)}")
            return False
    
    def _convert_job_to_processing_item(self, job: Dict):
        """Convert Tracker job to ProcessingItem"""
        from sprunki_ultimate_master import ProcessingItem
        
        # Create ProcessingItem from job data
        item = ProcessingItem(
            source_url=job.get('game_url'),
            mode=job['production_mode'],
            project_slug=self._create_slug_from_title(job['title'])
        )
        
        # If mod has existing JSON data, we could load it here
        # For now, we'll regenerate JSON for each job
        
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
            # Stage 1: JSON Generation
            self.api_client.update_job_status(queue_id, 'processing', {
                'stage': 'json_generation'
            })
            
            # Use existing JSON generator
            success = self.processing_manager._generate_json_for_item(item)
            
            if not success:
                self.api_client.update_job_status(queue_id, 'failed', 
                    error_log="JSON generation failed")
                return False
            
            # Stage 2: Video Processing
            return self._process_video_for_item(item, queue_id)
            
        except Exception as e:
            print(f"❌ JSON generation error: {e}")
            return False
    
    def _process_video_for_item(self, item, queue_id: int) -> bool:
        """Process video using existing logic"""
        try:
            # Update status
            self.api_client.update_job_status(queue_id, 'processing', {
                'stage': 'video_processing'
            })
            
            # Use existing video processing logic
            success = self.processing_manager._process_video_for_item(item)
            
            if success:
                # Collect output files
                video_files = {}
                for file_path in item.output_files:
                    # Determine file type from filename
                    if 'showcase' in file_path or 'master' in file_path:
                        video_files['master'] = file_path
                    elif any(x in file_path for x in ['individual', 'n-', 'h-']):
                        if 'individual' not in video_files:
                            video_files['individual'] = []
                        video_files['individual'].append(file_path)
                
                # Report completion
                self.api_client.update_job_status(queue_id, 'completed', 
                    details={'completion_time': datetime.now().isoformat()},
                    video_files=video_files)
                return True
            else:
                self.api_client.update_job_status(queue_id, 'failed',
                    error_log="Video processing failed")
                return False
                
        except Exception as e:
            print(f"❌ Video processing error: {e}")
            self.api_client.update_job_status(queue_id, 'failed',
                error_log=f"Video processing error: {str(e)}")
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
            
            # Keep main thread alive
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
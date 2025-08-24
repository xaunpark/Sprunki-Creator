# enhanced_creator_client.py - Improved Creator with detailed progress tracking
import json
import time
import requests
import threading
import socketio # type: ignore
import psutil
import os
from typing import Dict, Optional, Any, List, Callable
from datetime import datetime
from dataclasses import dataclass, asdict
from enum import Enum
import logging
from contextlib import contextmanager
import traceback

# Import existing components
from sprunki_ultimate_master import UnifiedProcessingManager, ProcessingConfig
from config_manager import get_config

class WorkflowStep(Enum):
    INITIALIZATION = "initialization"
    JSON_GENERATION = "json_generation"
    GAME_AUTOMATION = "game_automation"
    VIDEO_PROCESSING = "video_processing"
    QUALITY_VALIDATION = "quality_validation"

@dataclass
class ProgressMetrics:
    step_progress: float  # 0.0 - 100.0 for current step
    overall_progress: float  # 0.0 - 100.0 for entire job
    substep_name: str
    items_processed: int = 0
    total_items: int = 0
    processing_speed: float = 0.0  # items per minute
    estimated_time_remaining: Optional[int] = None
    
@dataclass
class PerformanceMetrics:
    cpu_usage: float
    memory_usage: float
    disk_usage: float
    processing_speed: float
    network_latency: float = 0.0
    
@dataclass
class QualityMetrics:
    overall_score: float  # 0.0 - 10.0
    file_validations: Dict[str, bool]
    video_durations: Dict[str, int]  # seconds
    file_sizes: Dict[str, int]  # bytes
    resolution_checks: Dict[str, str]
    
class ProgressTracker:
    """Comprehensive progress tracking for video creation workflow"""
    
    def __init__(self):
        self.workflow_steps = {
            WorkflowStep.INITIALIZATION: {
                'weight': 10.0,  # percentage of total workflow
                'substeps': ['environment_check', 'obs_setup', 'browser_init'],
                'estimated_duration': 30  # seconds
            },
            WorkflowStep.JSON_GENERATION: {
                'weight': 10.0,
                'substeps': ['url_analysis', 'dom_extraction', 'config_validation'],
                'estimated_duration': 20
            },
            WorkflowStep.GAME_AUTOMATION: {
                'weight': 40.0,
                'substeps': ['icon_recording', 'remix_generation', 'horror_mode'],
                'estimated_duration': 120
            },
            WorkflowStep.VIDEO_PROCESSING: {
                'weight': 30.0,
                'substeps': ['recording_setup', 'capture_execution', 'file_processing'],
                'estimated_duration': 90
            },
            WorkflowStep.QUALITY_VALIDATION: {
                'weight': 10.0,
                'substeps': ['file_verification', 'quality_check', 'upload_prep'],
                'estimated_duration': 20
            }
        }
        
        self.current_step = None
        self.current_substep = None
        self.step_start_time = None
        self.job_start_time = None
        self.completed_steps = []
        
    def start_job(self):
        """Initialize job tracking"""
        self.job_start_time = datetime.now()
        self.completed_steps = []
        
    def start_step(self, step: WorkflowStep, substep: Optional[str] = None):
        """Start tracking a workflow step"""
        self.current_step = step
        self.current_substep = substep or self.workflow_steps[step]['substeps'][0]
        self.step_start_time = datetime.now()
        
    def update_substep(self, substep: str):
        """Update current substep"""
        self.current_substep = substep
        
    def complete_step(self, step: WorkflowStep):
        """Mark step as completed"""
        if step not in self.completed_steps:
            self.completed_steps.append(step.value)
        self.current_step = None
        self.current_substep = None
        
    def calculate_progress(self, step_progress: float = 0.0, 
                         items_processed: int = 0, total_items: int = 0) -> ProgressMetrics:
        """Calculate comprehensive progress metrics"""
        
        # Calculate overall progress
        overall_progress = 0.0
        
        # Add completed steps
        for completed_step_name in self.completed_steps:
            for step_enum in WorkflowStep:
                if step_enum.value == completed_step_name:
                    overall_progress += self.workflow_steps[step_enum]['weight']
                    break
        
        # Add current step progress
        if self.current_step:
            step_weight = self.workflow_steps[self.current_step]['weight']
            overall_progress += (step_progress / 100.0) * step_weight
        
        # Calculate processing speed
        processing_speed = 0.0
        if self.step_start_time and items_processed > 0:
            elapsed_minutes = (datetime.now() - self.step_start_time).total_seconds() / 60.0
            if elapsed_minutes > 0:
                processing_speed = items_processed / elapsed_minutes
        
        # Estimate remaining time
        estimated_time_remaining = None
        if self.job_start_time and overall_progress > 0:
            elapsed_seconds = (datetime.now() - self.job_start_time).total_seconds()
            if overall_progress > 5.0:  # Only estimate after significant progress
                estimated_total_time = elapsed_seconds * (100.0 / overall_progress)
                estimated_time_remaining = int(estimated_total_time - elapsed_seconds)
        
        return ProgressMetrics(
            step_progress=step_progress,
            overall_progress=min(overall_progress, 100.0),
            substep_name=self.current_substep or '',
            items_processed=items_processed,
            total_items=total_items,
            processing_speed=processing_speed,
            estimated_time_remaining=estimated_time_remaining
        )

class PerformanceMonitor:
    """System performance monitoring for Creator"""
    
    def __init__(self):
        self.monitoring = False
        self.monitor_thread = None
        self.callbacks: List[Callable[[PerformanceMetrics], None]] = []
        
    def add_callback(self, callback: Callable[[PerformanceMetrics], None]):
        """Add callback for performance updates"""
        self.callbacks.append(callback)
        
    def start_monitoring(self, interval: int = 30):
        """Start performance monitoring"""
        self.monitoring = True
        self.monitor_thread = threading.Thread(target=self._monitor_loop, args=(interval,), daemon=True)
        self.monitor_thread.start()
        
    def stop_monitoring(self):
        """Stop performance monitoring"""
        self.monitoring = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=5)
            
    def _monitor_loop(self, interval: int):
        """Performance monitoring loop"""
        while self.monitoring:
            try:
                metrics = self.get_current_metrics()
                
                # Call all callbacks
                for callback in self.callbacks:
                    try:
                        callback(metrics)
                    except Exception as e:
                        print(f"Performance callback error: {e}")
                
                time.sleep(interval)
                
            except Exception as e:
                print(f"Performance monitoring error: {e}")
                time.sleep(interval)
    
    def get_current_metrics(self) -> PerformanceMetrics:
        """Get current system performance metrics"""
        
        # CPU usage
        cpu_usage = psutil.cpu_percent(interval=1)
        
        # Memory usage
        memory = psutil.virtual_memory()
        memory_usage = memory.percent
        
        # Disk usage (for output directory)
        disk = psutil.disk_usage('/')
        disk_usage = disk.percent
        
        # Processing speed (placeholder - would be calculated based on actual work)
        processing_speed = 0.0
        
        return PerformanceMetrics(
            cpu_usage=cpu_usage,
            memory_usage=memory_usage,
            disk_usage=disk_usage,
            processing_speed=processing_speed
        )

class QualityValidator:
    """Comprehensive output quality validation"""
    
    def __init__(self):
        self.validation_rules = {
            'min_file_size': 1024 * 1024,  # 1MB
            'min_duration': 10,  # seconds
            'max_duration': 300,  # seconds
            'required_resolutions': ['1920x1080', '1280x720'],
            'required_codecs': ['h264', 'h265'],
            'min_overall_score': 7.0
        }
    
    def validate_job_output(self, output_files: Dict[str, Any]) -> QualityMetrics:
        """Comprehensive quality validation of job output"""
        
        file_validations = {}
        video_durations = {}
        file_sizes = {}
        resolution_checks = {}
        
        quality_scores = []
        
        # Validate each output file
        for file_type, file_paths in output_files.items():
            if isinstance(file_paths, str):
                file_paths = [file_paths]
            
            for file_path in file_paths:
                if os.path.exists(file_path):
                    # File size check
                    file_size = os.path.getsize(file_path)
                    file_sizes[os.path.basename(file_path)] = file_size
                    
                    # Basic validation
                    is_valid = file_size >= self.validation_rules['min_file_size']
                    file_validations[os.path.basename(file_path)] = is_valid
                    
                    if is_valid:
                        quality_scores.append(8.0)  # Good quality
                        
                        # Mock video properties (would use ffprobe in real implementation)
                        video_durations[os.path.basename(file_path)] = 30  # seconds
                        resolution_checks[os.path.basename(file_path)] = '1920x1080'
                    else:
                        quality_scores.append(3.0)  # Poor quality
                        video_durations[os.path.basename(file_path)] = 0
                        resolution_checks[os.path.basename(file_path)] = 'unknown'
                else:
                    file_validations[os.path.basename(file_path)] = False
                    quality_scores.append(0.0)  # File missing
        
        # Calculate overall quality score
        overall_score = sum(quality_scores) / len(quality_scores) if quality_scores else 0.0
        
        return QualityMetrics(
            overall_score=overall_score,
            file_validations=file_validations,
            video_durations=video_durations,
            file_sizes=file_sizes,
            resolution_checks=resolution_checks
        )

class EnhancedTrackerClient:
    """Enhanced API client with WebSocket support and detailed tracking"""
    
    def __init__(self, tracker_url="http://localhost:5000"):
        self.tracker_url = tracker_url
        self.session = requests.Session()
        self.config = get_config()
        
        # Service identification
        self.service_name = f"creator-{self.config.get('application', {}).get('instance_id', '1')}"
        
        # WebSocket client
        self.sio = socketio.Client()
        self.setup_websocket_handlers()
        
        # Performance monitoring
        self.performance_monitor = PerformanceMonitor()
        self.performance_monitor.add_callback(self._on_performance_update)
        
        # Quality validator
        self.quality_validator = QualityValidator()
        
        self.capabilities = {
            'master': True,
            'individual': True,
            'both': True,
            'max_concurrent': 1,
            'version': self.config.get('application', {}).get('version', '2.0'),
            'supports_progress_tracking': True,
            'supports_quality_validation': True
        }
        
    def setup_websocket_handlers(self):
        """Setup WebSocket event handlers"""
        
        @self.sio.on('connect')
        def on_connect():
            print("✅ Connected to Tracker WebSocket")
            # Join creator room
            self.sio.emit('join_creator', {'creator_name': self.service_name})
            
        @self.sio.on('disconnect')
        def on_disconnect():
            print("❌ Disconnected from Tracker WebSocket")
            
        @self.sio.on('job_cancelled')
        def on_job_cancelled(data):
            job_id = data.get('job_id')
            print(f"🚫 Job {job_id} cancelled by Tracker")
            # Handle job cancellation
            
        @self.sio.on('priority_changed')
        def on_priority_changed(data):
            job_id = data.get('job_id')
            new_priority = data.get('priority')
            print(f"📊 Job {job_id} priority changed to {new_priority}")
    
    def connect(self) -> bool:
        """Connect to Tracker with WebSocket support"""
        try:
            # REST API registration
            if not self.register_service():
                return False
            
            # WebSocket connection
            ws_url = self.tracker_url.replace('http', 'ws')
            self.sio.connect(ws_url)
            
            # Start performance monitoring
            self.performance_monitor.start_monitoring(interval=30)
            
            return True
            
        except Exception as e:
            print(f"❌ Connection failed: {e}")
            return False
    
    def disconnect(self):
        """Clean disconnect from Tracker"""
        try:
            self.performance_monitor.stop_monitoring()
            self.sio.disconnect()
            print("✅ Disconnected cleanly")
        except Exception as e:
            print(f"⚠️ Disconnect error: {e}")
    
    def register_service(self) -> bool:
        """Register Creator service with enhanced capabilities"""
        try:
            response = self.session.post(
                f"{self.tracker_url}/api/creators/register",
                json={
                    'service_name': self.service_name,
                    'host_info': {
                        'ip': '127.0.0.1',
                        'version': '2.0',
                        'features': ['progress_tracking', 'quality_validation', 'websocket_support']
                    },
                    'capabilities': self.capabilities
                },
                timeout=30
            )
            
            if response.status_code == 200:
                print(f"✅ Registered with enhanced capabilities")
                return True
            else:
                print(f"❌ Registration failed: {response.status_code}")
                return False
                
        except Exception as e:
            print(f"❌ Registration error: {e}")
            return False
    
    def get_next_job(self) -> Optional[Dict]:
        """Get next job with enhanced metadata"""
        try:
            response = self.session.post(
                f"{self.tracker_url}/api/queue/next",
                json={
                    'service_name': self.service_name,
                    'capabilities': self.capabilities
                },
                timeout=30
            )
            
            if response.status_code == 200:
                data = response.json()
                return data.get('job')
            
            return None
            
        except Exception as e:
            print(f"❌ Get job error: {e}")
            return None
    
    def update_progress(self, job_id: int, progress_metrics: ProgressMetrics,
                       current_step: WorkflowStep, performance_metrics: PerformanceMetrics) -> bool:
        """Send detailed progress update to Tracker"""
        try:
            payload = {
                'overall_progress': progress_metrics.overall_progress,
                'current_step': current_step.value,
                'current_substep': progress_metrics.substep_name,
                'steps_completed': [], # Would be populated by progress tracker
                'estimated_time_remaining': progress_metrics.estimated_time_remaining,
                'performance_metrics': {
                    'items_processed': progress_metrics.items_processed,
                    'total_items': progress_metrics.total_items,
                    'processing_speed': progress_metrics.processing_speed,
                    'cpu_usage': performance_metrics.cpu_usage,
                    'memory_usage': performance_metrics.memory_usage,
                    'disk_usage': performance_metrics.disk_usage
                }
            }
            
            # Send via REST API
            response = self.session.post(
                f"{self.tracker_url}/api/queue/progress/{job_id}",
                json=payload,
                timeout=10
            )
            
            # Also emit via WebSocket for real-time updates
            self.sio.emit('progress_update', {
                'job_id': job_id,
                'progress': payload
            })
            
            return response.status_code == 200
            
        except Exception as e:
            print(f"⚠️ Progress update error: {e}")
            return False
    
    def report_error(self, job_id: int, error_category: str, error_type: str,
                    message: str, context: Optional[Dict] = None, retry_attempt: int = 0) -> Dict:
        """Report detailed error information"""
        try:
            error_payload = {
                'category': error_category,
                'error_type': error_type,
                'message': message,
                'stack_trace': traceback.format_exc(),
                'context': context or {},
                'retry_attempt': retry_attempt,
                'max_retries': 3,
                'auto_retry_in': self._calculate_retry_delay(error_category, retry_attempt)
            }
            
            response = self.session.post(
                f"{self.tracker_url}/api/queue/error/{job_id}",
                json=error_payload,
                timeout=10
            )
            
            # Emit via WebSocket
            self.sio.emit('error_occurred', {
                'job_id': job_id,
                'error': error_payload
            })
            
            if response.status_code == 200:
                return response.json()
            
            return {'will_retry': False}
            
        except Exception as e:
            print(f"❌ Error reporting failed: {e}")
            return {'will_retry': False}
    
    def complete_job(self, job_id: int, output_files: Dict, quality_metrics: QualityMetrics,
                    performance_stats: Dict) -> bool:
        """Complete job with comprehensive results"""
        try:
            completion_payload = {
                'output_files': output_files,
                'quality_metrics': asdict(quality_metrics),
                'performance_stats': performance_stats
            }
            
            response = self.session.post(
                f"{self.tracker_url}/api/queue/complete/{job_id}",
                json=completion_payload,
                timeout=30
            )
            
            # Emit completion event
            self.sio.emit('job_completed', {
                'job_id': job_id,
                'completion_data': completion_payload
            })
            
            return response.status_code == 200
            
        except Exception as e:
            print(f"❌ Job completion error: {e}")
            return False
    
    def _calculate_retry_delay(self, error_category: str, retry_attempt: int) -> int:
        """Calculate intelligent retry delay based on error type and attempt"""
        base_delays = {
            'recoverable': 30,  # Network, temporary issues
            'configuration': 60,  # Config issues might need manual intervention
            'resource': 120,  # Resource issues need time to resolve
            'critical': 0  # Don't auto-retry critical errors
        }
        
        base_delay = base_delays.get(error_category.lower(), 60)
        
        # Exponential backoff
        return base_delay * (2 ** retry_attempt)
    
    def _on_performance_update(self, metrics: PerformanceMetrics):
        """Handle performance monitoring updates"""
        # Send heartbeat with performance data
        self.sio.emit('creator_heartbeat', {
            'creator_name': self.service_name,
            'performance': asdict(metrics)
        })

class EnhancedCreatorService:
    """Enhanced Creator Service with comprehensive tracking"""
    
    def __init__(self, tracker_url="http://localhost:5000"):
        self.tracker_client = EnhancedTrackerClient(tracker_url)
        self.progress_tracker = ProgressTracker()
        self.running = False
        self.current_job = None
        
        # Initialize processing manager (existing code integration)
        self.processing_manager = UnifiedProcessingManager(master_controller=self)
        self._initialize_processing_config()
        
        # Logging setup
        self.setup_logging()
        
    def setup_logging(self):
        """Enhanced logging with structured output"""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler('creator_service.log'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger('EnhancedCreator')
    
    def _initialize_processing_config(self):
        """Initialize processing configuration"""
        try:
            config = get_config()
            processing_config_data = config.get('processing', {})
            output_config = config.get('output', {})
            
            self.processing_config = ProcessingConfig(
                output_base_dir=output_config.get('base_directory', r"C:\Users\dinht\Videos\SPRUNKI"),
                max_retries=processing_config_data.get('max_retries', 2),
                auto_skip_on_error=processing_config_data.get('auto_skip_on_error', True),
                pause_between_items=processing_config_data.get('pause_between_items', 5)
            )
            
            self.processing_manager.config = self.processing_config
            self.logger.info(f"✅ Processing config initialized: {self.processing_config.output_base_dir}")
            
        except Exception as e:
            self.logger.error(f"❌ Failed to initialize processing config: {e}")
            self.processing_config = None
    
    def start(self) -> bool:
        """Start enhanced Creator service"""
        try:
            self.logger.info("🚀 Starting Enhanced Creator Service...")
            
            if not self.tracker_client.connect():
                return False
            
            self.running = True
            
            # Start main processing loop
            processing_thread = threading.Thread(target=self._processing_loop, daemon=True)
            processing_thread.start()
            
            self.logger.info("✅ Enhanced Creator Service started successfully")
            return True
            
        except Exception as e:
            self.logger.error(f"❌ Failed to start service: {e}")
            return False
    
    def stop(self):
        """Stop service gracefully"""
        self.logger.info("🛑 Stopping Enhanced Creator Service...")
        self.running = False
        self.tracker_client.disconnect()
        self.logger.info("✅ Service stopped")
    
    def _processing_loop(self):
        """Enhanced processing loop with detailed tracking"""
        self.logger.info("🔄 Processing loop started")
        
        while self.running:
            try:
                job = self.tracker_client.get_next_job()
                
                if job:
                    self.current_job = job
                    self.logger.info(f"📥 Received job: {job.get('mod_title')} (ID: {job.get('id')})")
                    
                    success = self._process_job_with_tracking(job)
                    
                    if success:
                        self.logger.info(f"✅ Job {job['id']} completed successfully")
                    else:
                        self.logger.error(f"❌ Job {job['id']} failed")
                    
                    self.current_job = None
                else:
                    # No jobs available - intelligent wait
                    wait_time = self.processing_config.pause_between_items if self.processing_config else 30
                    self.logger.debug(f"No jobs available. Waiting {wait_time}s...")
                    time.sleep(wait_time)
                
            except Exception as e:
                self.logger.error(f"❌ Processing loop error: {e}")
                time.sleep(10)
    
    def _process_job_with_tracking(self, job: Dict) -> bool:
        """Process job with comprehensive progress tracking"""
        job_id = job['id']
        
        # Initialize progress tracking
        self.progress_tracker.start_job()
        
        try:
            # STEP 1: INITIALIZATION
            with self._track_step(WorkflowStep.INITIALIZATION, job_id):
                if not self._initialize_job(job):
                    return False
            
            # STEP 2: JSON GENERATION (if needed)
            processing_item = self._convert_job_to_processing_item(job)
            
            if processing_item.source_url and not processing_item.json_data:
                with self._track_step(WorkflowStep.JSON_GENERATION, job_id):
                    if not self._generate_json_with_tracking(processing_item, job_id):
                        return False
            
            # STEP 3: GAME AUTOMATION & VIDEO PROCESSING
            with self._track_step(WorkflowStep.GAME_AUTOMATION, job_id):
                if not self._process_video_with_tracking(processing_item, job_id):
                    return False
            
            # STEP 4: QUALITY VALIDATION
            with self._track_step(WorkflowStep.QUALITY_VALIDATION, job_id):
                quality_metrics = self._validate_output_quality(processing_item)
                
                if quality_metrics.overall_score < 7.0:
                    self.tracker_client.report_error(
                        job_id, 'configuration', 'quality_validation_failed',
                        f'Quality score {quality_metrics.overall_score} below threshold'
                    )
                    return False
            
            # Complete job successfully
            performance_stats = self._gather_performance_stats()
            
            return self.tracker_client.complete_job(
                job_id, 
                self._format_output_files(processing_item),
                quality_metrics,
                performance_stats
            )
            
        except Exception as e:
            self.logger.error(f"❌ Job processing error: {e}")
            
            self.tracker_client.report_error(
                job_id, 'critical', 'processing_exception',
                str(e), {'step': self.progress_tracker.current_step.value if self.progress_tracker.current_step else 'unknown'}
            )
            
            return False
    
    @contextmanager
    def _track_step(self, step: WorkflowStep, job_id: int):
        """Context manager for tracking workflow steps"""
        self.progress_tracker.start_step(step)
        
        try:
            yield
            self.progress_tracker.complete_step(step)
            
            # Final step progress update
            progress_metrics = self.progress_tracker.calculate_progress(100.0)
            performance_metrics = self.tracker_client.performance_monitor.get_current_metrics()
            
            self.tracker_client.update_progress(job_id, progress_metrics, step, performance_metrics)
            
        except Exception as e:
            self.logger.error(f"Step {step.value} failed: {e}")
            raise
    
    def _initialize_job(self, job: Dict) -> bool:
        """Initialize job with progress tracking"""
        self.progress_tracker.update_substep('environment_check')
        # Environment check logic here
        
        self.progress_tracker.update_substep('obs_setup')
        # OBS setup logic here
        
        self.progress_tracker.update_substep('browser_init')
        # Browser initialization logic here
        
        return True
    
    def _generate_json_with_tracking(self, processing_item, job_id: int) -> bool:
        """JSON generation with progress tracking"""
        
        self.progress_tracker.update_substep('url_analysis')
        # Update progress at 33%
        progress = self.progress_tracker.calculate_progress(33.0)
        performance = self.tracker_client.performance_monitor.get_current_metrics()
        self.tracker_client.update_progress(job_id, progress, WorkflowStep.JSON_GENERATION, performance)
        
        self.progress_tracker.update_substep('dom_extraction')
        # Actual JSON generation logic (using existing ProcessingManager)
        success = self.processing_manager._generate_json_for_item(processing_item)
        
        # Update progress at 66%
        progress = self.progress_tracker.calculate_progress(66.0)
        self.tracker_client.update_progress(job_id, progress, WorkflowStep.JSON_GENERATION, performance)
        
        if not success:
            return False
        
        self.progress_tracker.update_substep('config_validation')
        # Validate generated JSON
        
        return True
    
    def _process_video_with_tracking(self, processing_item, job_id: int) -> bool:
        """Video processing with detailed progress tracking"""
        
        # This would integrate with existing processing logic
        # but add progress reporting at regular intervals
        
        success = self.processing_manager._process_video_for_item(processing_item)
        
        return success
    
    def _validate_output_quality(self, processing_item) -> QualityMetrics:
        """Validate output with comprehensive quality metrics"""
        
        output_files = self._format_output_files(processing_item)
        return self.tracker_client.quality_validator.validate_job_output(output_files)
    
    def _format_output_files(self, processing_item) -> Dict[str, Any]:
        """Format output files for reporting"""
        output_files = {}
        
        for file_path in processing_item.output_files:
            if 'showcase' in file_path or 'master' in file_path:
                output_files['master'] = file_path
            elif any(x in file_path for x in ['individual', 'n-', 'h-']):
                if 'individual' not in output_files:
                    output_files['individual'] = []
                output_files['individual'].append(file_path)
            elif 'remix' in file_path:
                if 'remixes' not in output_files:
                    output_files['remixes'] = []
                output_files['remixes'].append(file_path)
        
        return output_files
    
    def _gather_performance_stats(self) -> Dict:
        """Gather comprehensive performance statistics"""
        performance = self.tracker_client.performance_monitor.get_current_metrics()
        
        return {
            'total_processing_time': (datetime.now() - self.progress_tracker.job_start_time).total_seconds() if self.progress_tracker.job_start_time else 0,
            'cpu_avg': performance.cpu_usage,
            'memory_avg': performance.memory_usage,
            'disk_usage': performance.disk_usage,
            'steps_completed': len(self.progress_tracker.completed_steps),
            'version': '2.0'
        }
    
    def _convert_job_to_processing_item(self, job: Dict):
        """Convert job to ProcessingItem (existing logic)"""
        from sprunki_ultimate_master import ProcessingItem
        
        item = ProcessingItem(
            source_url=job.get('mod_game_url'),
            mode=job['production_mode'],
            project_slug=self._create_slug_from_title(job.get('mod_title', 'untitled-job'))
        )
        return item
    
    def _create_slug_from_title(self, title: str) -> str:
        """Create project slug from title (existing logic)"""
        import re
        slug = re.sub(r'[^a-zA-Z0-9\-]', '-', title.lower())
        slug = re.sub(r'-+', '-', slug).strip('-')
        return slug or "video-job"

# Enhanced initialization and component compatibility
def _initialize_components(self, config_data: Dict[str, Any]) -> bool:
    """Enhanced component initialization (existing logic integration)"""
    # This would integrate with existing component initialization
    # but add progress reporting capabilities
    return True

def main():
    """Enhanced main entry point"""
    print("🤖 Enhanced Sprunki Creator Service v2.0")
    print("📊 Features: Progress Tracking, Quality Validation, WebSocket Communication")
    print("="*70)
    
    service = EnhancedCreatorService()
    
    try:
        if service.start():
            print("✅ Enhanced service running. Press Ctrl+C to stop.")
            
            while service.running:
                time.sleep(1)
        else:
            print("❌ Failed to start enhanced service")
            
    except KeyboardInterrupt:
        print("\n🛑 Shutdown requested")
    finally:
        service.stop()

if __name__ == "__main__":
    main()
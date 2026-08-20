"""
Creator API Client - Bridge between Creator and Tracker
Replaces tkinter GUI with API-based job polling
"""

import json
import threading
import time
import uuid
import requests
from threading import Thread
import os
import sys
from typing import Dict, Optional, Any, Tuple
from datetime import datetime
from config_manager import get_config
from performance_analyzer import performance_analyzer
import logging

# --- FIX: Import necessary components ---
from sprunki_game_player import SprunkiGamePlayer
from sprunki_obs_controller import SprunkiController
# --- FIX: Import ProcessingConfig to create the configuration object ---
from sprunki_ultimate_master import ProcessingConfig

from collections.abc import Callable
from smart_retry_manager import SmartRetryManager
from file_validator import FileQualityValidator
from manual_profile_manager import suggested_profile_path_for_url, url_key_from_url
from workflow_quality import (
    NON_RETRYABLE_FAILURE_CODES,
    validate_playable_layout,
)
from tracker_reporting import (
    SCHEMA_VERSION as TRACKER_REPORT_SCHEMA_VERSION,
    build_log_snapshot,
    build_tracker_diagnostic_report,
)

# --- CONFIG: Thư mục chứa file JSON thủ công ---
MANUAL_JSON_DIR = "manual_jsons"
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))


class _TeeStream:
    """Write to the original stream and a job log file without owning either."""

    def __init__(self, original_stream, log_file):
        self.original_stream = original_stream
        self.log_file = log_file
        self.encoding = getattr(original_stream, "encoding", "utf-8")

    def write(self, data):
        if not data:
            return 0

        try:
            self.original_stream.write(data)
        except Exception:
            pass

        try:
            self.log_file.write(data)
        except Exception:
            pass

        return len(data)

    def flush(self):
        for stream in (self.original_stream, self.log_file):
            try:
                stream.flush()
            except Exception:
                pass

    def isatty(self):
        return bool(getattr(self.original_stream, "isatty", lambda: False)())

    def fileno(self):
        return self.original_stream.fileno()


class JobLogCapture:
    """Best-effort per-job log capture for Tracker tailing."""

    def __init__(self, queue_id: int, log_directory: str, enabled: bool = True):
        self.queue_id = queue_id
        self.log_directory = log_directory
        self.enabled = enabled
        self.log_path = os.path.join(log_directory, f"job-{queue_id}.log")
        self.log_file = None
        self.file_handler = None
        self.original_stdout = None
        self.original_stderr = None
        self.started = False

    def start(self) -> bool:
        if not self.enabled:
            return False

        try:
            os.makedirs(self.log_directory, exist_ok=True)
            self.log_file = open(self.log_path, "a", encoding="utf-8", buffering=1)
            self.log_file.write(
                f"\n===== Job {self.queue_id} log started at {datetime.now().isoformat(timespec='seconds')} =====\n"
            )

            formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
            self.file_handler = logging.FileHandler(self.log_path, mode="a", encoding="utf-8")
            self.file_handler.setLevel(logging.DEBUG)
            self.file_handler.setFormatter(formatter)
            logging.getLogger().addHandler(self.file_handler)

            self.original_stdout = sys.stdout
            self.original_stderr = sys.stderr
            sys.stdout = _TeeStream(self.original_stdout, self.log_file)
            sys.stderr = _TeeStream(self.original_stderr, self.log_file)
            self.started = True
            return True
        except Exception as exc:
            self._safe_restore_streams()
            self._safe_close_handler()
            self._safe_close_file()
            try:
                print(f"⚠️ Job log capture disabled for job {self.queue_id}: {exc}")
            except Exception:
                pass
            return False

    def stop(self):
        if not self.started:
            return

        try:
            if self.log_file:
                self.log_file.write(
                    f"\n===== Job {self.queue_id} log ended at {datetime.now().isoformat(timespec='seconds')} =====\n"
                )
        except Exception:
            pass
        finally:
            self._safe_restore_streams()
            self._safe_close_handler()
            self._safe_close_file()
            self.started = False

    def snapshot(self) -> dict:
        """Return a bounded excerpt and a reference to the complete job log."""
        try:
            if self.log_file:
                self.log_file.flush()
        except Exception:
            pass
        return build_log_snapshot(self.log_path)

    def _safe_restore_streams(self):
        if self.original_stdout is not None:
            sys.stdout = self.original_stdout
        if self.original_stderr is not None:
            sys.stderr = self.original_stderr

    def _safe_close_handler(self):
        if self.file_handler:
            try:
                logging.getLogger().removeHandler(self.file_handler)
                self.file_handler.close()
            except Exception:
                pass
            self.file_handler = None

    def _safe_close_file(self):
        if self.log_file:
            try:
                self.log_file.flush()
                self.log_file.close()
            except Exception:
                pass
            self.log_file = None


class TrackerAPIClient:
    def __init__(self, tracker_url="http://localhost:5000"):
        self.tracker_url = tracker_url
        self.config = get_config()
        self.service_name = f"creator-{self.config.get('application', {}).get('instance_id', '1')}"
        self.session = requests.Session()
        self.timeout = 30
        self._active_report_ids = {}
        self._progress_sequences = {}
        
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

    def bind_job_report(self, queue_id: int, report_id: str) -> None:
        """Associate progress events with the job's final diagnostic report."""
        if not hasattr(self, "_active_report_ids"):
            self._active_report_ids = {}
        if not hasattr(self, "_progress_sequences"):
            self._progress_sequences = {}
        self._active_report_ids[queue_id] = report_id
        self._progress_sequences[queue_id] = 0

    def unbind_job_report(self, queue_id: int) -> None:
        if hasattr(self, "_active_report_ids"):
            self._active_report_ids.pop(queue_id, None)
        if hasattr(self, "_progress_sequences"):
            self._progress_sequences.pop(queue_id, None)

    def _next_progress_sequence(self, queue_id: int) -> int:
        if not hasattr(self, "_progress_sequences"):
            self._progress_sequences = {}
        self._progress_sequences[queue_id] = self._progress_sequences.get(queue_id, 0) + 1
        return self._progress_sequences[queue_id]
    
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
                         video_files: Optional[Dict] = None, error_log: Optional[str] = None,
                         workflow_report: Optional[Dict] = None,
                         diagnostic_report: Optional[Dict] = None,
                         manifest_ref: Optional[Dict] = None,
                         log_ref: Optional[Dict] = None,
                         report_id: Optional[str] = None,
                         final: bool = False,
                         sequence: Optional[int] = None) -> bool:
        """Update job status in Tracker with backward-compatible diagnostics.

        ``final`` is intentionally explicit.  Intermediate status calls may
        still be accepted by older Trackers, but only the final call carries
        the authoritative quality/evidence report.
        """
        try:
            active_report_id = getattr(self, "_active_report_ids", {}).get(queue_id)
            payload = {
                'status': status,
                'details': details,
                'video_files': video_files,
                'error_log': error_log,
                'workflow_report': workflow_report
            }
            payload.update({
                'schema_version': TRACKER_REPORT_SCHEMA_VERSION,
                'diagnostic_report': diagnostic_report,
                'manifest_ref': manifest_ref,
                'log_ref': log_ref,
                'report_id': report_id or active_report_id,
                'final': bool(final),
                'sequence': sequence,
            })
            
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
                        metadata: Optional[Dict] = None, error_details: Optional[str] = None,
                        report_id: Optional[str] = None, sequence: Optional[int] = None,
                        final: bool = False) -> bool:
        """Report detailed job progress to Tracker - PHASE 1"""
        try:
            active_report_id = getattr(self, "_active_report_ids", {}).get(queue_id)
            effective_report_id = report_id or active_report_id
            effective_sequence = sequence
            if effective_sequence is None and effective_report_id:
                effective_sequence = self._next_progress_sequence(queue_id)
            payload = {
                'stage': stage,
                'substep': substep,
                'progress_percent': progress_percent,
                'status': status,
                'metadata': metadata,
                'error_details': error_details,
                'schema_version': TRACKER_REPORT_SCHEMA_VERSION,
                'report_id': effective_report_id,
                'sequence': effective_sequence,
                'final': bool(final),
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

    def update_mod_production_status(self, mod_id: int, manifest_data: Dict) -> bool:
        """Gửi thông tin từ manifest để cập nhật production_status của mod."""
        try:
            summary = manifest_data.get("processingSummary", {})
            payload = {
                'status': summary.get('overallStatus'),
                'mode': summary.get('mode'),
                'output_path': manifest_data.get("projectInfo", {}).get("outputDirectory"),
                'produced_at': summary.get('endTime'),
                'production_details': summary.get('statusDetails'),
                'quality_gates': manifest_data.get('qualityGates'),
                'coverage': summary.get('coverage'),
                'manifest_snapshot': manifest_data,
            }
            
            response = self.session.put(
                f"{self.tracker_url}/api/mods/{mod_id}/production-status",
                json=payload,
                timeout=self.timeout
            )
            return response.status_code == 200
        except Exception as e:
            print(f"❌ Error updating mod production status: {e}")
            return False

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
        self.service_stop_event = threading.Event()        
        self.current_job = None
        self._watchdog_failures = {}
        self._watchdog_failures_lock = threading.Lock()
        
        self.game_player = None
        self.obs_controller = None
        
        from sprunki_ultimate_master import UnifiedProcessingManager
        self.processing_manager = UnifiedProcessingManager(master_controller=self)

        self.logger = logging.getLogger(self.__class__.__name__)
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

        # 🆕 PHASE 2: Initialize Smart Retry Manager (API-based, no database import)
        self.retry_manager = SmartRetryManager(self.api_client)

        # 🆕 PHASE 3: Initialize File Quality Validator
        self.file_validator = FileQualityValidator()

        # 🆕 PHASE 4: Initialize Performance Monitoring
        self.performance_analyzer = performance_analyzer

        # --- FIX: Initialize the processing config for the manager ---
        self._initialize_processing_config()
        self._stop_event = threading.Event() # [NEW] Global stop event
        
        # Ensure manual json directory exists
        os.makedirs(MANUAL_JSON_DIR, exist_ok=True)
        
        print("🤖 Creator Service initialized with API-based Smart Retry Manager")
        print(f"📂 Manual JSON Override directory: {os.path.abspath(MANUAL_JSON_DIR)}")

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

    def _create_job_log_capture(self, queue_id: int) -> JobLogCapture:
        enabled = self.config_manager.get('logging.enable_job_log_capture', True)
        log_directory = self.config_manager.get('logging.job_log_directory', 'logs/video_jobs')
        if not os.path.isabs(log_directory):
            log_directory = os.path.join(PROJECT_DIR, log_directory)
        return JobLogCapture(queue_id=queue_id, log_directory=log_directory, enabled=enabled)

    def _snapshot_job_log(self, job_log_capture: Any) -> Dict[str, Any]:
        """Capture log evidence while remaining compatible with test/dummy captures."""
        snapshotter = getattr(job_log_capture, "snapshot", None)
        if callable(snapshotter):
            try:
                return snapshotter()
            except Exception as exc:
                self.logger.warning("Could not snapshot job log: %s", exc)
        return build_log_snapshot(getattr(job_log_capture, "log_path", None))

    @property
    def root(self):
        """
        Dummy 'root' property to ensure compatibility with UnifiedProcessingManager,
        which was designed with a Tkinter root in mind.
        """
        class DummyRoot:
            def after(self, ms, func, *args):
                # In service mode, we don't use Tkinter's event loop.
                # This no-op function prevents crashes if manager's queue logic is ever called.
                pass
        return DummyRoot()

    def _initialize_components(self, config_data: Dict[str, Any], job_stop_event: threading.Event) -> bool:
        """
        [REVISED] Initialize game player and OBS controller for a specific job,
        passing the job-specific stop event.
        """
        try:
            print("⚙️ Initializing components for new job...")

            layout_quality = validate_playable_layout(config_data)
            if not layout_quality.passed:
                print(f"🛑 Component initialization rejected: {layout_quality.failure_code}")
                return False
            
            if hasattr(self, 'obs_controller') and self.obs_controller:
                print("🧹 Cleaning up previous OBS controller...")
                self.obs_controller.comprehensive_obs_cleanup()
                self.obs_controller = None
                time.sleep(1)
            
            # Khởi tạo GamePlayer VÀ TRUYỀN CỜ DỪNG CỦA JOB VÀO
            self.game_player = SprunkiGamePlayer(stop_event=job_stop_event)
            self.game_player.config = config_data
            
            current_url = config_data.get('currentUrl', '').lower()
            if 'cocrea.world' not in current_url:
                print(f"❌ Unsupported Domain: {current_url}")
                return False

            layout_data = config_data.get('layout', {})
            total_slots = len(layout_data.get('slots', []))
            
            if total_slots > 0:
                print(f"✅ Detected {total_slots} slots. Generating dynamic remix patterns...")
                self.game_player.generate_dynamic_patterns(total_slots)
            else:
                print("⚠️ Warning: No slots detected in JSON. Remixes may not be generated correctly.")
            
            game_settings = config_data.get('gameSettings', {})
            has_horror = game_settings.get('hasHorror', False)
            
            self.game_player.remix_library = self.game_player.generate_random_remixes(avoid_horror_icon=has_horror)
            self.game_player.current_remix_index = 0
            
            print("🎥 Initializing OBS controller for service mode...")
            self.obs_controller = SprunkiController()
            self.game_player.obs_controller = self.obs_controller

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
        self.service_stop_event.clear()

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
        self.service_stop_event.set()

        # 🆕 PHASE 4: Stop performance monitoring
        self.performance_analyzer.stop_monitoring()

        # Thêm logic dọn dẹp toàn diện khi dừng service
        try:
            print("🧹 Performing comprehensive cleanup on service stop...")
            if hasattr(self, 'obs_controller') and self.obs_controller:
                print("   -> Cleaning up OBS controller...")
                self.obs_controller.comprehensive_obs_cleanup()
            if hasattr(self, 'game_player') and self.game_player:
                print("   -> Cleaning up game player (closing browser)...")
                self.game_player.close_game_window()
        except Exception as e:
            print(f"⚠️ Error during final cleanup: {e}")
        
        if self.processing_thread:
            self.processing_thread.join(timeout=5)
        if self.heartbeat_thread:
            self.heartbeat_thread.join(timeout=5)
        
        print("✅ Creator Service stopped")

    def _processing_loop(self):
        """Main processing loop - polls for jobs"""
        print("🔄 Processing loop started")
        
        # SỬA ĐỔI: Vòng lặp while chỉ phụ thuộc vào cờ dừng của service
        while not self.service_stop_event.is_set():
            try:
                # Không cần kiểm tra cờ stop ở đây nữa.
                
                job = self.api_client.get_next_job()
                
                if job:
                    self.current_job = job
                    
                    print(f"📥 Received job: {job.get('mod_title', 'Unknown Title')} ({job.get('production_mode', 'N/A')})")
                    
                    # _process_job sẽ tự quản lý cờ stop của riêng nó
                    success = self._process_job(job)
                    
                    if success:
                        print(f"✅ Job completed: {job['id']}")
                    else:
                        print(f"❌ Job failed: {job['id']}")
                    
                    self.current_job = None
                else:
                    if self.processing_manager.config:
                        pause_duration = self.processing_manager.config.pause_between_items
                        print(f"No jobs available. Pausing for {pause_duration}s...")
                        # Sử dụng wait() của Event để có thể ngắt ngay lập tức
                        # thay vì sleep cứng
                        self.service_stop_event.wait(timeout=pause_duration)
                    else:
                        self.service_stop_event.wait(timeout=30)
                
            except Exception as e:
                print(f"❌ Processing loop error: {e}")
                self.service_stop_event.wait(timeout=10) # Chờ 10s nếu có lỗi
        
        # Log khi vòng lặp kết thúc
        print("🔄 Processing loop has finished.")
    
    def _heartbeat_loop(self):
        """Heartbeat loop - reports status to Tracker"""
        # SỬA ĐỔI: Chỉ phụ thuộc vào cờ dừng của service
        while not self.service_stop_event.is_set():
            try:
                status = "busy" if self.current_job else "online"
                current_job_id = self.current_job['id'] if self.current_job else None
                
                performance_stats = self.performance_analyzer.get_performance_summary()
                
                self.api_client.send_heartbeat(
                    status=status, 
                    current_job_id=current_job_id,
                    performance_stats=performance_stats
                )

                # Sử dụng wait() để có thể dừng ngay lập tức
                self.service_stop_event.wait(timeout=60)
                
            except Exception as e:
                print(f"⚠️ Heartbeat error: {e}")
                # Chờ một khoảng ngắn hơn nếu có lỗi
                if not self.service_stop_event.is_set():
                    self.service_stop_event.wait(timeout=30)
        
        # Log khi vòng lặp kết thúc
        print("❤️ Heartbeat loop has finished.")

    def _job_watchdog(self, queue_id, job_start_time, job_stop_event: threading.Event):
        """
        [REVISED] Watchdog thread to monitor job execution time.
        Uses a job-specific stop event.
        """
        job_timeout_minutes = self.config_manager.get('processing.job_timeout_minutes', 60)
        job_timeout_seconds = job_timeout_minutes * 60
        
        # wait() sẽ trả về False nếu timeout, True nếu event được set.
        # Điều này cho phép chúng ta thoát sớm nếu job hoàn thành trước khi timeout.
        job_finished_before_timeout = job_stop_event.wait(timeout=job_timeout_seconds)

        # Nếu vòng lặp kết thúc vì timeout (wait() trả về False) VÀ service chưa bị dừng
        if not job_finished_before_timeout and not self.service_stop_event.is_set():
            error_msg = f"Job timed out after {job_timeout_minutes} minutes."
            self.logger.error(f"🚨 [WATCHDOG] Job {queue_id} timed out!")
            
            # Kích hoạt cờ dừng của JOB này
            job_stop_event.set()

            # Do not send a terminal status from the watchdog thread.  The
            # finalizer owns the only terminal report and will attach the
            # workflow/manifest/log evidence collected by the job.
            failure = {
                "failure_code": "runtime_verification_unknown",
                "message": f"WATCHDOG: {error_msg}",
                "evidence": {
                    "watchdog": True,
                    "timeout_minutes": job_timeout_minutes,
                },
                "status": "UNKNOWN",
                "retryable": True,
            }
            with self._watchdog_failures_lock:
                self._watchdog_failures[queue_id] = failure
            self.api_client.update_job_progress(
                queue_id,
                "watchdog",
                "timeout",
                0.0,
                "failed",
                metadata={**failure, "final": False},
                error_details=failure["message"],
                final=False,
            )
        elif job_finished_before_timeout:
             self.logger.info(f"🐶 Watchdog for job {queue_id}: Job finished normally. Exiting.")
        elif self.service_stop_event.is_set():
             self.logger.info(f"🐶 Watchdog for job {queue_id}: Service is stopping. Exiting.")

    def _take_watchdog_failure(self, queue_id: int) -> Optional[Dict[str, Any]]:
        """Consume a watchdog failure so it is included in the final report."""
        failures = getattr(self, "_watchdog_failures", None)
        if not isinstance(failures, dict):
            return None
        lock = getattr(self, "_watchdog_failures_lock", None)
        if lock is not None:
            with lock:
                return failures.pop(queue_id, None)
        return failures.pop(queue_id, None)

    def _process_job(self, job: Dict) -> bool:
        """
        [REVISED & WATCHDOG ENABLED] Orchestrates job processing with a job-specific stop event.
        """
        queue_id = job['id']
        job_log_capture = self._create_job_log_capture(queue_id)
        job_log_capture.start()
        item = None
        job_successful = False
        final_success = False
        start_time = time.monotonic()
        terminal_error = None
        report_id = f"{getattr(self.api_client, 'service_name', 'creator')}:{queue_id}:{uuid.uuid4().hex}"
        bind_report = getattr(self.api_client, "bind_job_report", None)
        if callable(bind_report):
            bind_report(queue_id, report_id)
        
        # 1. TẠO CỜ DỪNG RIÊNG CHO JOB NÀY
        job_stop_event = threading.Event()
        
        # 2. Bắt đầu Watchdog và truyền cờ stop của job vào
        watchdog_thread = threading.Thread(
            target=self._job_watchdog,
            args=(queue_id, start_time, job_stop_event),
            daemon=True
        )
        watchdog_thread.start()
        self.logger.info(f"🐶 Watchdog started for job {queue_id}.")
        
        try:
            # 3. Truyền cờ stop của job xuống các tầng dưới
            self.processing_manager.stop_event = job_stop_event
            
            item = self._convert_job_to_processing_item(job)
            if getattr(item, "start_time", None) is None:
                item.start_time = datetime.now()
            
            # --- HIỂN THỊ SLUG ĐỂ USER DỄ COPY ---
            print(f"🔑 Project Slug: {item.project_slug}")
            # ---------------------------------------

            self.api_client.update_job_status(queue_id, 'processing', {'stage': 'starting', 'started_by': self.api_client.service_name})
            
            self.performance_analyzer.record_job_start(queue_id, job)

            # 4. Thực thi workflow. Nó sẽ tự dừng nếu job_stop_event được set (bởi watchdog hoặc lỗi)
            job_successful = self._process_with_smart_retry(item, queue_id)

        except Exception as e:
            error_msg = f"Critical job processing error: {str(e)}"
            terminal_error = error_msg
            self.logger.error(f"❌ Unhandled exception in job {queue_id}: {error_msg}", exc_info=True)
            if item:
                self.processing_manager._record_quality_failure(
                    item,
                    getattr(item, "failure_code", None) or "runtime_verification_unknown",
                    error_msg,
                    evidence={"error": error_msg, "queueId": queue_id},
                    status="UNKNOWN",
                    retryable=True,
                )
            job_successful = False
            self.api_client.update_job_progress(queue_id, 'critical_failure', str(e), 0.0, 'failed')
        
        finally:
            # 5. BÁO HIỆU CHO WATCHDOG DỪNG LẠI vì job đã xong (dù thành công hay thất bại)
            job_stop_event.set()

            watchdog_failure = self._take_watchdog_failure(queue_id)
            if watchdog_failure:
                terminal_error = watchdog_failure.get("message") or terminal_error
                if item:
                    self.processing_manager._record_quality_failure(
                        item,
                        watchdog_failure.get("failure_code", "runtime_verification_unknown"),
                        watchdog_failure.get("message", "Watchdog stopped the workflow."),
                        evidence=watchdog_failure.get("evidence", {}),
                        status=watchdog_failure.get("status", "UNKNOWN"),
                        retryable=watchdog_failure.get("retryable", True),
                    )

            manifest_data = None
            video_files_payload = {}
            workflow_report_obj = getattr(item, "workflow_report", None) if item else None

            if item:
                if workflow_report_obj is None:
                    self.processing_manager._record_quality_failure(
                        item,
                        "runtime_verification_unknown",
                        "Workflow ended without a quality report.",
                        evidence={"queueId": queue_id},
                        status="UNKNOWN",
                        retryable=True,
                    )
                    workflow_report_obj = getattr(item, "workflow_report", None)

                # Align the manifest status with the same final authority as
                # the Tracker status.  A false workflow return must not leave
                # an item looking successful merely because an older phase
                # had a passing gate.
                if not job_successful and item.status != "failed":
                    self.processing_manager._record_quality_failure(
                        item,
                        getattr(item, "failure_code", None) or "workflow_stopped",
                        getattr(item, "error_message", None) or "Workflow returned failure before finalization.",
                        evidence={"jobSuccessful": False},
                        retryable=False,
                    )
                    workflow_report_obj = getattr(item, "workflow_report", None)

                item.end_time = datetime.now()
                try:
                    manifest_data = self.processing_manager._generate_manifest_for_item(item)
                except Exception as manifest_error:
                    terminal_error = terminal_error or f"Manifest generation failed: {manifest_error}"
                    self.logger.error("❌ Manifest generation failed for %s: %s", queue_id, manifest_error, exc_info=True)
                    self.processing_manager._record_quality_failure(
                        item,
                        "artifact_missing",
                        terminal_error,
                        evidence={"manifest_generation_error": str(manifest_error)},
                        retryable=False,
                    )
                    workflow_report_obj = getattr(item, "workflow_report", None)
                if manifest_data is None:
                    terminal_error = terminal_error or "Manifest generation returned no data."
                    self.processing_manager._record_quality_failure(
                        item,
                        "artifact_missing",
                        terminal_error,
                        evidence={"manifest_generation": "no_data"},
                        retryable=False,
                    )
                    workflow_report_obj = getattr(item, "workflow_report", None)

                try:
                    video_files_payload = self._organize_output_files(item.output_files)
                except Exception as output_error:
                    terminal_error = terminal_error or f"Output organization failed: {output_error}"
                    self.logger.error("❌ Output organization failed for %s: %s", queue_id, output_error, exc_info=True)
                    self.processing_manager._record_quality_failure(
                        item,
                        "artifact_missing",
                        terminal_error,
                        evidence={"output_organization_error": str(output_error)},
                        retryable=False,
                    )

            # Dọn dẹp các component của job hiện tại trước khi chụp log cuối.
            try:
                if hasattr(self, 'obs_controller') and self.obs_controller:
                    self.obs_controller.comprehensive_obs_cleanup()
                if hasattr(self, 'game_player') and self.game_player:
                    self.game_player.close_game_window()
            except Exception as cleanup_error:
                self.logger.error(f"Error during post-job cleanup for {queue_id}: {cleanup_error}")
            finally:
                job_log_capture.stop()

            # The final status is calculated only after all evidence-producing
            # steps above have had a chance to record their own failures.
            next_sequence = getattr(self.api_client, "_next_progress_sequence", None)
            final_sequence = next_sequence(queue_id) if callable(next_sequence) else 1
            if item:
                workflow_report_obj = getattr(item, "workflow_report", None)
                quality_passed = (
                    workflow_report_obj is not None
                    and getattr(getattr(workflow_report_obj, "quality_gate", None), "passed", False) is True
                )
                final_status = "completed" if job_successful and quality_passed and item.status != "failed" else "failed"
                final_success = final_status == "completed"
                self.logger.info(f"Finalizing job {queue_id} with status: {final_status.upper()}")

                diagnostic_report = build_tracker_diagnostic_report(
                    job,
                    item,
                    workflow_report_obj,
                    manifest_data=manifest_data,
                    log_snapshot=self._snapshot_job_log(job_log_capture),
                    output_files_payload=video_files_payload,
                    report_id=report_id,
                    max_attempts=getattr(getattr(self.processing_manager, "config", None), "max_retries", None),
                    terminal_status=final_status,
                    terminal_error=terminal_error,
                )
                self.api_client.update_job_status(
                    queue_id,
                    status=final_status,
                    details={
                        "stage": diagnostic_report["summary"].get("blocking_stage"),
                        "failure_code": diagnostic_report["summary"].get("failure_code"),
                        "retryable": diagnostic_report["summary"].get("retryable"),
                        "report_id": report_id,
                    },
                    video_files=video_files_payload,
                    error_log=(
                        item.error_message
                        or diagnostic_report["summary"].get("root_cause")
                        or None
                    ),
                    workflow_report=diagnostic_report.get("workflow_report"),
                    diagnostic_report=diagnostic_report,
                    manifest_ref=diagnostic_report["outputs"].get("manifest_ref"),
                    log_ref=diagnostic_report["logs"],
                    report_id=report_id,
                    final=True,
                    sequence=final_sequence,
                )

                if manifest_data:
                    mod_id = job.get('mod_id')
                    if mod_id:
                        self.logger.info(f"Syncing production status for mod {mod_id}...")
                        self.api_client.update_mod_production_status(mod_id, manifest_data)
            else:
                final_status = "failed"
                final_success = False
                diagnostic_report = build_tracker_diagnostic_report(
                    job,
                    None,
                    None,
                    log_snapshot=self._snapshot_job_log(job_log_capture),
                    report_id=report_id,
                    terminal_status=final_status,
                    terminal_error=terminal_error or "Job ended before a ProcessingItem was created.",
                )
                self.api_client.update_job_status(
                    queue_id,
                    status=final_status,
                    details={
                        "stage": diagnostic_report["summary"].get("blocking_stage"),
                        "failure_code": diagnostic_report["summary"].get("failure_code"),
                        "retryable": diagnostic_report["summary"].get("retryable"),
                        "report_id": report_id,
                    },
                    error_log=diagnostic_report["summary"].get("root_cause"),
                    workflow_report=diagnostic_report.get("workflow_report"),
                    diagnostic_report=diagnostic_report,
                    manifest_ref=diagnostic_report["outputs"].get("manifest_ref"),
                    log_ref=diagnostic_report["logs"],
                    report_id=report_id,
                    final=True,
                    sequence=final_sequence,
                )

            unbind_report = getattr(self.api_client, "unbind_job_report", None)
            if callable(unbind_report):
                unbind_report(queue_id)

            duration = time.monotonic() - start_time
            self.performance_analyzer.record_job_completion(queue_id, final_success, {
                "report_id": report_id,
                "duration_seconds": duration,
            })
        # The orchestration result must agree with the status sent to the
        # tracker.  A workflow return value alone is not sufficient: the
        # shared quality gate is the final authority.
        return final_success

    def _check_for_manual_json_override(self, slug: str, job_id: int) -> Tuple[Optional[Dict], Optional[str]]:
        """
        Check if a manual JSON file exists for this job in the manual_jsons directory.
        Checks by Job ID first, then by Slug.
        """
        self._manual_json_failure = None
        try:
            files_to_check = []
            if job_id:
                files_to_check.append(f"{job_id}.json")
            if slug:
                files_to_check.append(f"{slug}.json")
            
            for filename in files_to_check:
                filepath = os.path.join(MANUAL_JSON_DIR, filename)
                if os.path.exists(filepath):
                    self.logger.info(f"🔍 Checking manual JSON: {filepath} ... FOUND!")
                    try:
                        with open(filepath, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                        layout_quality = validate_playable_layout(data)
                        if layout_quality.passed:
                            self._manual_json_failure = None
                            return data, filepath
                        else:
                            self._manual_json_failure = {
                                "path": filepath,
                                "failure_code": layout_quality.failure_code or "manual_json_invalid",
                                "message": layout_quality.message,
                                "evidence": layout_quality.evidence,
                            }
                            self.logger.warning(
                                f"⚠️ Manual JSON {filename} rejected: {layout_quality.failure_code}"
                            )
                    except Exception as e:
                        self.logger.error(f"❌ Error reading manual JSON {filename}: {e}")
                        self._manual_json_failure = {
                            "path": filepath,
                            "failure_code": "manual_json_invalid",
                            "message": f"Manual JSON could not be read: {e}",
                            "evidence": {"error": str(e)},
                        }
                        return None, None
            
            return None, None
        except Exception as e:
            self.logger.error(f"❌ Error in override check: {e}")
            self._manual_json_failure = {
                "failure_code": "manual_json_invalid",
                "message": str(e),
            }
            return None, None

    def _build_manual_profile_failure_details(self, url: str, error_message: Optional[str] = None) -> Dict:
        url = (url or "").strip()
        details = {
            "manualProfileRequired": True,
            "sourceUrl": url,
            "urlKey": url_key_from_url(url),
            "suggestedManualProfilePath": suggested_profile_path_for_url(url),
        }
        if error_message:
            details["reason"] = error_message
        return details

    def _process_with_smart_retry(self, item, queue_id: int) -> bool:
        """Process with smart retry for individual steps - PHASE 2"""
        try:
            # --- STEP 0: CHECK MANUAL OVERRIDE ---
            manual_data, manual_path = self._check_for_manual_json_override(item.project_slug, queue_id)

            if getattr(self, "_manual_json_failure", None):
                failure = self._manual_json_failure
                item.json_generation_status = "failure"
                self.processing_manager._record_quality_failure(
                    item,
                    failure.get("failure_code", "manual_json_invalid"),
                    failure.get("message", "Manual JSON failed static playability validation."),
                    evidence={
                        "manualJson": failure,
                        "path": failure.get("path"),
                    },
                )
                self.api_client.update_job_progress(
                    queue_id,
                    "json_generation",
                    "manual_override",
                    0.0,
                    "failed",
                    metadata=failure,
                    error_details=item.error_message,
                )
                return False
            
            if manual_data:
                print(f"🟢 MANUAL OVERRIDE DETECTED! Using local JSON file: {manual_path}")
                item.json_data = manual_data
                item.json_path = manual_path
                item.json_generation_status = "success"
                # Đánh dấu là JSON đã có sẵn để bỏ qua bước sinh tự động
                self.api_client.update_job_progress(queue_id, 'json_generation', 'manual_override', 100.0, 'completed',
                    metadata={'override_file': manual_path})
            
            # --- Step 1: JSON Generation (ONLY if not overridden) ---
            if item.source_url and not item.json_data:
                success = self._execute_with_retry(
                    queue_id, 'json_generation',
                    lambda: self.processing_manager._generate_json_for_item(item),
                    context={'url': item.source_url, 'item': item}
                )
                
                if not success:
                    return False
            
            # --- Step 2: Video Processing ---
            success = self._execute_with_retry(
                queue_id, 'video_processing',
                lambda: self.processing_manager._process_video_for_item(item),
                context={'mode': item.mode, 'project_slug': item.project_slug, 'item': item}
            )
            
            return success
            
        except Exception as e:
            print(f"❌ Smart retry processing error: {e}")
            self.processing_manager._record_quality_failure(
                item,
                getattr(item, "failure_code", None) or "runtime_verification_unknown",
                f"Smart retry processing error: {e}",
                evidence={"error": str(e)},
                status="UNKNOWN",
                retryable=True,
            )
            return False

    def _execute_with_retry(self, queue_id: int, operation_name: str, operation_function: Callable, 
                        context: Optional[dict] = None, max_step_retries: int = 3) -> bool:
        """Execute operation with smart retry logic - PHASE 2 (API-based)"""

        def record_retry_evidence(
            attempt: int,
            *,
            error: str,
            exception: bool = False,
            status: Optional[str] = None,
            retryable: Optional[bool] = None,
            started_at: Optional[datetime] = None,
        ) -> None:
            failed_item = (context or {}).get('item')
            report = getattr(failed_item, 'workflow_report', None)
            if report is None:
                return
            ended_at = datetime.now()
            duration_ms = (
                max(0, int((ended_at - started_at).total_seconds() * 1000))
                if isinstance(started_at, datetime)
                else None
            )
            attempts = report.evidence.setdefault('retryAttempts', [])
            attempts.append({
                'operation': operation_name,
                'attempt': attempt,
                'error': error,
                'exception': exception,
                'failureCode': getattr(failed_item, 'failure_code', None),
                'failure_code': getattr(failed_item, 'failure_code', None),
                'status': status or ('FAIL' if exception else 'UNKNOWN'),
                'retryable': retryable,
                'started_at': started_at.isoformat() if isinstance(started_at, datetime) else None,
                'ended_at': ended_at.isoformat(),
                'duration_ms': duration_ms,
            })
        
        for step_attempt in range(1, max_step_retries + 1):
            attempt_started_at = datetime.now()
            try:
                print(f"🔧 {operation_name} - Step attempt {step_attempt}/{max_step_retries}")
                
                # Execute the operation
                result = operation_function()
                
                if result:
                    record_retry_evidence(
                        step_attempt,
                        error=f"{operation_name} succeeded",
                        status="PASS",
                        retryable=False,
                        started_at=attempt_started_at,
                    )
                    print(f"✅ {operation_name} succeeded on attempt {step_attempt}")
                    return True
                else:
                    failed_item = (context or {}).get('item')
                    failure_code = getattr(failed_item, 'failure_code', None)
                    failed_gate = getattr(getattr(failed_item, 'workflow_report', None), 'quality_gate', None)
                    error_msg = (
                        f"{operation_name} failed with {failure_code}"
                        if failure_code
                        else f"{operation_name} returned False"
                    )
                    print(f"❌ {operation_name} failed on attempt {step_attempt}: {error_msg}")
                    record_retry_evidence(
                        step_attempt,
                        error=error_msg,
                        status="FAIL",
                        retryable=(failure_code not in NON_RETRYABLE_FAILURE_CODES),
                        started_at=attempt_started_at,
                    )

                    if failure_code in NON_RETRYABLE_FAILURE_CODES or (
                        failed_gate is not None and not getattr(failed_gate, "retryable", True)
                    ):
                        self.logger.error(
                            "🚫 Deterministic quality failure is not retryable: %s",
                            failure_code,
                        )
                        self.api_client.update_job_progress(
                            queue_id,
                            operation_name,
                            'quality_gate_failed',
                            0.0,
                            'failed',
                            error_details=error_msg,
                            metadata={'failure_code': failure_code},
                        )
                        return False
                    
                    # Prepare context with attempt number
                    retry_context = {**(context or {}), 'operation': operation_name, 'attempt': step_attempt}
                    
                    # Use smart retry logic
                    retry_decision = self.retry_manager.should_retry(queue_id, error_msg, retry_context)
                    
                    if not retry_decision['should_retry']:
                        print(f"🚫 Not retrying {operation_name}: {retry_decision['reason']}")

                        failed_item = (context or {}).get('item')
                        
                        # Handle special cases
                        if retry_decision.get('requires_manual'):
                            if failed_item is not None:
                                self.processing_manager._record_quality_failure(
                                    failed_item,
                                    "manual_json_invalid"
                                    if operation_name == "json_generation"
                                    else (getattr(failed_item, "failure_code", None) or "workflow_stopped"),
                                    f"{operation_name} requires manual intervention: {error_msg}",
                                    evidence={"retryDecision": retry_decision},
                                    retryable=False,
                                )
                            details = None
                            if operation_name == 'json_generation':
                                details = self._build_manual_profile_failure_details((context or {}).get('url', ''), error_msg)
                            self.api_client.update_job_progress(
                                queue_id,
                                operation_name,
                                "manual_intervention",
                                0.0,
                                "failed",
                                metadata={
                                    "final": False,
                                    "details": details,
                                    "failure_code": getattr(failed_item, "failure_code", None)
                                    if failed_item is not None else "manual_json_invalid",
                                },
                                error_details=f"{operation_name} requires manual intervention: {error_msg}",
                            )
                        elif retry_decision.get('should_skip'):
                            if failed_item is not None:
                                self.processing_manager._record_quality_failure(
                                    failed_item,
                                    "skipped_by_policy",
                                    f"{operation_name} was skipped by retry policy: {error_msg}",
                                    evidence={"retryDecision": retry_decision},
                                    retryable=False,
                                )
                            details = None
                            if operation_name == 'json_generation':
                                details = self._build_manual_profile_failure_details((context or {}).get('url', ''), error_msg)
                            self.api_client.update_job_progress(
                                queue_id,
                                operation_name,
                                "skipped_by_policy",
                                0.0,
                                "failed",
                                metadata={
                                    "final": False,
                                    "details": details,
                                    "failure_code": "skipped_by_policy",
                                },
                                error_details=f"{operation_name} should be skipped: {error_msg}",
                            )
                        
                        return False
                    
                    # Execute retry if this isn't the last attempt
                    if step_attempt < max_step_retries:
                        retry_success = self.retry_manager.execute_retry(
                            queue_id, retry_decision, operation_function
                        )
                        
                        if retry_success:
                            record_retry_evidence(
                                retry_decision.get("retry_attempt", step_attempt + 1),
                                error=f"{operation_name} retry succeeded",
                                status="PASS",
                                retryable=False,
                            )
                            print(f"✅ {operation_name} retry succeeded")
                            return True
                        else:
                            record_retry_evidence(
                                retry_decision.get("retry_attempt", step_attempt + 1),
                                error=f"{operation_name} retry failed",
                                status="FAIL",
                                retryable=True,
                            )
                            print(f"❌ {operation_name} retry failed, will try again")
                            continue
                    else:
                        print(f"❌ {operation_name} exhausted all {max_step_retries} attempts")
                        return False
                        
            except Exception as e:
                failed_item = (context or {}).get('item')
                failure_code = getattr(failed_item, 'failure_code', None)
                failed_gate = getattr(getattr(failed_item, 'workflow_report', None), 'quality_gate', None)
                error_msg = f"{operation_name} exception: {str(e)}"
                print(f"❌ {operation_name} exception on attempt {step_attempt}: {e}")
                record_retry_evidence(
                    step_attempt,
                    error=error_msg,
                    exception=True,
                    status="FAIL",
                    retryable=(failure_code not in NON_RETRYABLE_FAILURE_CODES),
                    started_at=attempt_started_at,
                )

                if failure_code in NON_RETRYABLE_FAILURE_CODES or (
                    failed_gate is not None and not getattr(failed_gate, "retryable", True)
                ):
                    self.logger.error(
                        "🚫 Deterministic quality exception is not retryable: %s",
                        failure_code,
                    )
                    return False
                
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
                        metadata=self._build_manual_profile_failure_details((context or {}).get('url', ''), error_msg) if operation_name == 'json_generation' else None,
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
                        record_retry_evidence(
                            retry_decision.get("retry_attempt", step_attempt + 1),
                            error=f"{operation_name} retry succeeded after exception",
                            exception=True,
                            status="PASS",
                            retryable=False,
                        )
                        return True
                    record_retry_evidence(
                        retry_decision.get("retry_attempt", step_attempt + 1),
                        error=f"{operation_name} retry failed after exception",
                        exception=True,
                        status="FAIL",
                        retryable=True,
                    )
        
        return False
    
    def _convert_job_to_processing_item(self, job: Dict):
        """[FIXED] Convert Tracker job to ProcessingItem using URL for slug."""
        from sprunki_ultimate_master import ProcessingItem
        
        game_url = job.get('mod_game_url')
        
        # [FIX] Đảm bảo game_url luôn là một chuỗi để tránh lỗi type hint.
        # Nếu không có URL, một slug dựa trên thời gian sẽ được tạo ra bên trong _create_slug_from_url.
        url_for_slug = game_url or ""

        item = ProcessingItem(
            source_url=game_url,
            mode=job['production_mode'],
            # SỬA ĐỔI QUAN TRỌNG: Gọi hàm mới và truyền URL vào
            project_slug=self._create_slug_from_url(url_for_slug)
        )
        return item
    
    def _create_slug_from_url(self, url: str) -> str:
        """
        [NEW & FIXED] Creates a unique URL slug by combining author and project name.
        Example: /@author/project-name -> author-project-name
        This ensures consistency with the GUI mode.
        """
        try:
            from urllib.parse import urlparse
            import re
            
            if not url:
                return f"video-job-{int(time.time())}"

            parsed_url = urlparse(url)
            path_parts = [part for part in parsed_url.path.strip('/').split('/') if part]
            
            slug_base = ""
            if len(path_parts) >= 2 and path_parts[0].startswith('@'):
                author = path_parts[0][1:]
                project_name = path_parts[1]
                slug_base = f"{author}_{project_name}"
            elif len(path_parts) > 0:
                slug_base = "_".join(path_parts)
            else:
                domain = parsed_url.netloc.lower().replace('www.', '')
                slug_base = domain.split('.')[0]
            
            slug = re.sub(r'[^a-zA-Z0-9\-_]', '-', slug_base)
            slug = re.sub(r'[-_]+', '-', slug)
            slug = slug.strip('-')
            slug = slug[:70]
            
            return slug or f"video-job-{int(time.time())}"
            
        except Exception:
            return f"video-job-{int(time.time())}"
    
    def _generate_json_and_process(self, item, queue_id: int) -> bool:
        """Generate JSON then process video"""
        try:
            self.api_client.update_job_status(queue_id, 'processing', {
                'stage': 'json_generation'
            })
            
            success = self.processing_manager._generate_json_for_item(item)
            
            if not success:
                self.api_client.update_job_progress(
                    queue_id,
                    "json_generation",
                    "failed",
                    0.0,
                    "failed",
                    metadata={
                        "final": False,
                        "details": self._build_manual_profile_failure_details(
                            item.source_url or "", "JSON generation failed"
                        ),
                    },
                    error_details="JSON generation failed",
                )
                return False
            
            return self._process_video_for_item(item, queue_id)
            
        except Exception as e:
            print(f"❌ JSON generation error: {e}")
            return False
    
    def _process_video_for_item(self, item, queue_id: int) -> bool:
            """
            [REVISED & FIXED] Process video with detailed progress reporting and correct
            stop_event propagation.
            """
            try:
                if item.mode == "skip":
                    self.processing_manager._record_quality_failure(
                        item,
                        "skipped_by_policy",
                        "Item was skipped by policy and did not run gameplay verification.",
                    )
                    self.api_client.update_job_progress(
                        queue_id,
                        "video_processing",
                        "skipped_by_policy",
                        0.0,
                        "failed",
                        error_details=item.error_message,
                    )
                    return False

                # === START: PRE-FLIGHT CHECK ===
                if not item.json_data:
                    error_msg = "Cannot process video: item.json_data is missing."
                    self.logger.error(error_msg)
                    self.processing_manager._record_quality_failure(
                        item,
                        "manual_json_invalid",
                        error_msg,
                    )
                    self.api_client.update_job_progress(
                        queue_id,
                        "preflight",
                        "missing_json",
                        0.0,
                        "failed",
                        metadata={"final": False, "failure_code": "manual_json_invalid"},
                        error_details=error_msg,
                    )
                    return False
                # === END: PRE-FLIGHT CHECK ===

                layout_quality = validate_playable_layout(item.json_data)
                if not layout_quality.passed:
                    self.processing_manager._record_quality_failure(
                        item,
                        layout_quality.failure_code or "manual_json_invalid",
                        layout_quality.message,
                        evidence=layout_quality.evidence,
                    )
                    return False

                # --- Initialize progress tracking ---
                self.api_client.update_job_progress(
                    queue_id, 'initialization', 'starting', 0.0, 'active',
                    metadata={'item_mode': item.mode, 'project_slug': item.project_slug}
                )
                self.performance_analyzer.record_job_progress(queue_id, 'initialization', 0.0)
                
                self.logger.info(f"🎥 Processing video for item: {item.project_slug} (Mode: {item.mode})")

                # --- Component initialization (FIXED CALL) ---
                self.api_client.update_job_progress(
                    queue_id, 'initialization', 'components', 10.0, 'active'
                )
                
                # === BẮT ĐẦU SỬA ĐỔI ===
                # Lấy cờ stop của job hiện tại từ processing_manager
                job_stop_event = self.processing_manager.stop_event 

                # Truyền cờ stop vào hàm _initialize_components
                if not self._initialize_components(item.json_data, job_stop_event):
                # === KẾT THÚC SỬA ĐỔI ===
                    error_details = "Failed to initialize components (GamePlayer/OBS)."
                    self.api_client.update_job_progress(
                        queue_id, 'initialization', 'components', 0.0, 'failed',
                        error_details=error_details
                    )
                    self.processing_manager._record_quality_failure(
                        item,
                        getattr(item, "failure_code", None) or "runtime_verification_unknown",
                        error_details,
                        status="UNKNOWN",
                        retryable=True,
                    )
                    return False
                
                # --- OBS connection test ---
                self.api_client.update_job_progress(
                    queue_id, 'initialization', 'obs_test', 25.0, 'active'
                )

                if hasattr(self, 'obs_controller') and self.obs_controller:
                    self.logger.info("🔍 Testing OBS connection...")
                    test_ws = self.obs_controller.connect_obs_fast()
                    if test_ws:
                        self.logger.info("✅ OBS connection test successful")
                        self.api_client.update_job_progress(queue_id, 'initialization', 'obs_test', 40.0, 'completed')
                        test_ws.close()
                    else:
                        error_details = "OBS connection test failed before video processing."
                        self.logger.error(f"❌ {error_details}")
                        self.api_client.update_job_progress(
                            queue_id, 'initialization', 'obs_test', 0.0, 'failed',
                            error_details=error_details
                        )
                        self.processing_manager._record_quality_failure(
                            item,
                            "runtime_verification_unknown",
                            error_details,
                            status="UNKNOWN",
                            retryable=True,
                        )
                        return False
                
                # --- Start main processing ---
                self.api_client.update_job_progress(
                    queue_id, 'video_processing', 'starting', 50.0, 'active'
                )

                success = self._process_with_progress_callbacks(item, queue_id)
                
                # --- Early exit on core workflow failure ---
                if not success:
                    self.logger.error(f"❌ Video processing workflow failed for item {item.project_slug} (mode: {item.mode}). Skipping file validation.")
                    self.processing_manager._record_quality_failure(
                        item,
                        getattr(item, "failure_code", None) or "placement_not_verified",
                        getattr(item, "error_message", None) or "Core workflow failed.",
                    )
                    return False

                project_folder = getattr(self.obs_controller, 'current_project_folder', None)
                if item.mode != "skip" and project_folder:
                    identity_ok = self.processing_manager._run_recorded_identity_resolver(item, project_folder)
                    if not identity_ok:
                        identity_code = (
                            "recorded_identity_needs_review"
                            if item.recorded_identity_status in {"needs_review", "unavailable"}
                            else "recorded_identity_missing"
                        )
                        self.processing_manager._record_quality_failure(
                            item,
                            identity_code,
                            item.recorded_identity_reason or "Recorded identity was not verified.",
                            evidence={"identityStatus": item.recorded_identity_status},
                        )
                        return False

                if not self.processing_manager._finalize_item_quality(item):
                    self.logger.error(
                        "❌ Shared quality gate failed for %s: %s",
                        item.project_slug,
                        item.failure_code,
                    )
                    return False
                
                # --- File validation ---
                self.logger.info(f"✅ Video processing workflow completed for {item.project_slug}. Proceeding to file validation.")
                
                print(f"🔍 Validating output files for quality control...")
                self.api_client.update_job_progress(
                    queue_id, 'validation', 'files', 95.0, 'active',
                    metadata={'output_files_count': len(item.output_files)}
                )

                video_files = self._organize_output_files(item.output_files)
                validation_summary = video_files.get('validation_summary', {})
                
                validation_success = self._check_validation_requirements(validation_summary, item)
                
                if validation_success:
                    item.status = "completed"
                    item.end_time = datetime.now()
                    self.logger.info(f"✅ File validation passed: {validation_summary.get('success_rate', 0)}% success rate")
                    self.api_client.update_job_progress(
                        queue_id, 'completion', 'finalizing', 100.0, 'completed',
                        metadata={
                            'validation_summary': validation_summary,
                            'total_valid_files': validation_summary.get('valid_files', 0)
                        }
                    )
                    return True
                else:
                    failure_reason = f"Validation failed: {validation_summary.get('invalid_files', 0)} invalid files out of {validation_summary.get('total_files', 0)}."
                    self.logger.error(f"❌ File validation failed: {validation_summary.get('success_rate', 0)}% success rate. Reason: {failure_reason}")
                    
                    self.api_client.update_job_progress(
                        queue_id, 'validation', 'failed', 95.0, 'failed',
                        error_details=failure_reason
                    )
                    self.processing_manager._record_quality_failure(
                        item,
                        "artifact_missing",
                        failure_reason,
                        evidence={"validationSummary": validation_summary},
                    )
                    return False
                        
            except Exception as e:
                error_msg = f"Critical exception in _process_video_for_item: {str(e)}"
                self.logger.error(error_msg, exc_info=True)
                
                self.api_client.update_job_progress(
                    queue_id, 'error', 'exception', 0.0, 'failed',
                    error_details=error_msg
                )
                self.processing_manager._record_quality_failure(
                    item,
                    getattr(item, "failure_code", None) or "runtime_verification_unknown",
                    error_msg,
                    evidence={"error": error_msg},
                    status="UNKNOWN",
                    retryable=True,
                )
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
        """
        [FIXED] Organize and validate output files - PHASE 3 Enhanced.
        Ensures consistent structure and handles single-file cases correctly.
        """
        self.logger.info(f"📁 Organizing and validating {len(output_files)} output files...")
        
        # --- START OF FIX ---
        # Luôn khởi tạo cấu trúc đầy đủ, ngay cả khi không có file nào.
        final_payload = {
            'master': [],
            'individual': [],
            'validation_summary': {
                'total_files': 0, 'valid_files': 0, 'invalid_files': 0,
                'success_rate': 0.0, 'master_files': 0, 'individual_files': 0
            }
        }
        # --- END OF FIX ---
        
        if not output_files:
            self.logger.warning("⚠️ No output files to organize or validate.")
            return final_payload # Trả về cấu trúc rỗng nhưng hợp lệ
        
        master_files = []
        individual_files = []
        
        for file_path in output_files:
            filename = os.path.basename(file_path).lower()
            
            # Cải thiện logic phân loại để bắt cả "showcase"
            if 'showcase' in filename or 'master' in filename:
                master_files.append(file_path)
            elif any(keyword in filename for keyword in ['individual', 'n-', 'h-', 'remix']):
                individual_files.append(file_path)
            else:
                # Nếu không xác định được, giả sử là file individual
                self.logger.warning(f"Unknown file type for '{filename}', classifying as individual.")
                individual_files.append(file_path)
        
        # Validate master files
        if master_files:
            self.logger.info(f"🎬 Validating {len(master_files)} master file(s)...")
            master_validation = self.file_validator.validate_batch(master_files, 'video')
            final_payload['master'] = self._process_validation_results(master_validation)
            self.logger.info(f"   Master validation: {master_validation['summary']['valid_files']}/{master_validation['summary']['total_files']} valid")
        
        # Validate individual files
        if individual_files:
            self.logger.info(f"🎭 Validating {len(individual_files)} individual file(s)...")
            individual_validation = self.file_validator.validate_batch(individual_files, 'video')
            final_payload['individual'] = self._process_validation_results(individual_validation)
            self.logger.info(f"   Individual validation: {individual_validation['summary']['valid_files']}/{individual_validation['summary']['total_files']} valid")
        
        # Create combined validation summary
        total_files = len(master_files) + len(individual_files)
        # Lấy số file hợp lệ từ payload đã xử lý
        total_valid = len(final_payload['master']) + len(final_payload['individual'])
        
        final_payload['validation_summary'] = {
            'total_files': total_files,
            'valid_files': total_valid,
            'invalid_files': total_files - total_valid,
            'success_rate': round((total_valid / max(total_files, 1)) * 100, 1),
            'master_files': len(final_payload['master']),
            'individual_files': len(final_payload['individual'])
        }
        
        self.logger.info(f"✅ File organization completed: {total_valid}/{total_files} files validated successfully")
        
        return final_payload

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

    def _check_validation_requirements(self, validation_summary: dict, item) -> bool:
        """
        [REWRITTEN] Check if validation meets DYNAMIC requirements based on the input JSON.
        """
        try:
            mode = item.mode
            json_data = item.json_data
            if not json_data:
                self.logger.error("Validation check failed: Input JSON data is missing.")
                return False

            workflow_report = getattr(item, "workflow_report", None)
            quality_gate = getattr(workflow_report, "quality_gate", None)
            if (
                workflow_report is None
                or quality_gate is None
                or getattr(quality_gate, "passed", False) is not True
            ):
                self.logger.error(
                    "Validation rejected because shared quality gate did not pass: %s",
                    getattr(item, "failure_code", None),
                )
                return False

            # --- Lấy số lượng file THỰC TẾ từ validation_summary ---
            master_files_actual = validation_summary.get('master_files', 0)
            # Giả định file individual được đặt tên là n-xx.mp4 hoặc h-xx.mp4
            # Logic này cần sự nhất quán từ sprunki_ultimate_master.py -> _process_individual_mode
            individual_files_actual = validation_summary.get('individual_files', 0)
            
            # --- Áp dụng quy tắc cho từng chế độ ---
            if mode == "master":
                # Master mode: Phải có đúng 1 video master và nó phải hợp lệ.
                return master_files_actual >= 1

            elif mode == "individual":
                # Gameplay thresholds are already evaluated by the shared
                # WorkflowReport. File validation remains an auxiliary
                # artifact check and must not recalculate coverage from raw
                # JSON that may contain policy-skipped horror content.
                self.logger.info("Individual Mode Validation: shared quality gate passed")
                return individual_files_actual >= 1

            elif mode == "both":
                # Both mode: both phase gates passed before this check; require
                # at least one valid artifact from each output family.
                master_requirement_met = (master_files_actual >= 1)

                self.logger.info(f"Both Mode Validation:")
                self.logger.info(f"  - Master: {master_files_actual} found. Met threshold? {master_requirement_met}")
                self.logger.info(f"  - Individual artifacts: {individual_files_actual} found")
                
                return master_requirement_met and individual_files_actual >= 1
            
            else:
                return False # Chế độ không xác định
                
        except Exception as e:
            self.logger.error(f"❌ CRITICAL: Error checking DYNAMIC validation requirements: {e}")
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

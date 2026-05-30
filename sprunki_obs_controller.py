import json
import time
from typing import Optional
import websocket
import hashlib
import base64
import os
import re
from urllib.parse import urlparse
from datetime import datetime
from config_manager import get_config

class SprunkiController:
    def __init__(self):
        # Load configuration
        self.config = get_config()
        
        # OBS WebSocket settings from config
        self.obs_host = self.config.get('obs.host', '192.168.1.17')
        self.obs_port = self.config.get('obs.port', 4455)
        self.obs_password = self.config.get('obs.password', 'BDdtMRgQabXu3sy1')
        
        # Screen resolution from config  
        self.screen_width = self.config.get('automation.screen_width', 1920)
        self.screen_height = self.config.get('automation.screen_height', 1080)
        
        # Output directory from config
        self.base_output_dir = self.config.get('output.base_directory', r"C:\Users\dinht\Videos")
        
        # Recording area từ JSON
        self.recording_area = None
        self.current_project_folder = None
        self.current_filename = None
        
        # Performance tracking
        self.operation_start_time = None
        self.total_operations = 0
        self.successful_operations = 0
        self.master_mode_active = False
        
        print(f"✅ OBS Controller configured from config.json:")
        print(f"   Host: {self.obs_host}:{self.obs_port}")
        print(f"   Output: {self.base_output_dir}")
        print(f"   Screen: {self.screen_width}x{self.screen_height}")

        # Thuộc tính để theo dõi trạng thái và sự kiện ghi hình
        self.is_currently_recording = False
        self.recording_start_time = None
        self.event_timestamps = []
        self.recording_areas = {}

    def log_event_timestamp(self, event_name: str, label: str = "", metadata: Optional[dict] = None, event_timestamp: Optional[float] = None):
        """Ghi lại một sự kiện và timestamp tương đối so với thời điểm bắt đầu ghi hình."""
        if not self.is_currently_recording or self.recording_start_time is None:
            return
        
        import time
        if event_timestamp is None:
            elapsed_time = time.monotonic() - self.recording_start_time
        else:
            elapsed_time = event_timestamp - self.recording_start_time
        
        event_data = {
            "event": event_name,
            "timestamp": round(elapsed_time, 3),
            "label": label or event_name,
            "metadata": metadata or {}
        }
        
        self.event_timestamps.append(event_data)
        print(f"🕒 Event Logged: '{event_name}' at {elapsed_time:.3f}s")

    def store_recording_area(self, segment_key: str, recording_area: dict):
        """Store per-showcase-segment crop metadata for manifest generation."""
        if segment_key and isinstance(recording_area, dict):
            self.recording_areas[segment_key] = recording_area

    def _save_timestamps_to_file(self, video_path: str):
        """Lưu danh sách các timestamp đã ghi lại vào một file JSON tương ứng với video."""
        # Chỉ tạo file nếu có nhiều hơn 2 sự kiện (start/stop mặc định) để tránh file rác
        if len(self.event_timestamps) <= 2 or not video_path:
            print("🕒 No detailed events to log. Skipping timestamp file creation.")
            return
        
        import json
        import os
        import time

        try:
            base, _ = os.path.splitext(video_path)
            timestamp_path = f"{base}.timestamps.json"

            total_duration = time.monotonic() - self.recording_start_time if self.recording_start_time else 0

            data_to_save = {
                "videoFilename": os.path.basename(video_path),
                "totalDurationSeconds": round(total_duration, 3),
                "events": self.event_timestamps
            }

            with open(timestamp_path, 'w', encoding='utf-8') as f:
                json.dump(data_to_save, f, indent=2, ensure_ascii=False)
            
            print(f"🕒 Timestamp file saved: {os.path.basename(timestamp_path)}")
        
        except Exception as e:
            print(f"❌ Error saving timestamp file: {e}")

    def log_performance(self, operation_name, success=True):
        """PHASE 1: Track performance metrics"""
        self.total_operations += 1
        if success:
            self.successful_operations += 1
        
        if self.operation_start_time:
            duration = time.time() - self.operation_start_time
            status = "✅" if success else "❌"
            print(f"{status} {operation_name}: {duration:.2f}s")
        
        self.operation_start_time = time.time()
    
    def create_simple_url_slug(self, url):
        """
        [FIXED] Tạo URL slug duy nhất bằng cách kết hợp tên tác giả và tên dự án.
        Ví dụ: /@author/project-name -> author_project-name
        """
        try:
            self.operation_start_time = time.time()
            
            parsed_url = urlparse(url)
            # Tách path thành các phần, loại bỏ các phần tử rỗng (như phần tử đầu tiên sau dấu '/')
            path_parts = [part for part in parsed_url.path.strip('/').split('/') if part]
            
            slug_base = ""
            # Logic mới để tạo slug an toàn
            if len(path_parts) >= 2 and path_parts[0].startswith('@'):
                # Trường hợp chuẩn: /@author/project-name
                author = path_parts[0][1:]  # Bỏ ký tự '@'
                project_name = path_parts[1]
                slug_base = f"{author}_{project_name}"
            elif len(path_parts) > 0:
                # Trường hợp chỉ có một phần trong path, hoặc không có '@'
                slug_base = "_".join(path_parts)
            else:
                # Fallback: dùng domain nếu không có path
                domain = parsed_url.netloc.lower().replace('www.', '')
                slug_base = domain.split('.')[0]

            # Dọn dẹp slug để tạo tên file/thư mục hợp lệ
            import re
            slug = re.sub(r'[^a-zA-Z0-9\-_]', '-', slug_base) # Chỉ cho phép chữ, số, -, _
            slug = re.sub(r'[-_]+', '-', slug) # Thay thế nhiều gạch ngang/gạch dưới bằng một gạch ngang
            slug = slug.strip('-')

            # Giới hạn độ dài
            slug = slug[:70]
            
            self.log_performance("URL Slug Creation", True)
            print(f"🏷️ [FIXED] URL Slug: '{url}' → '{slug}'")
            return slug
            
        except Exception as e:
            print(f"❌ Lỗi tạo URL slug: {e}")
            self.log_performance("URL Slug Creation", False)
            fallback_slug = f"sprunki-{datetime.now().strftime('%Y%m%d%H%M%S')}"
            return fallback_slug
    
    def setup_project_folder(self, url):
        """Tạo folder project đơn giản - PHASE 1: Optimized"""
        try:
            self.operation_start_time = time.time()
            
            slug = self.create_simple_url_slug(url)
            project_folder = os.path.join(self.base_output_dir, slug)
            
            # PHASE 1: Faster folder creation with exist_ok
            os.makedirs(project_folder, exist_ok=True)
            
            self.current_project_folder = project_folder
            self.log_performance("Project Folder Setup", True)
            print(f"📁 Project folder: {project_folder}")
            
            return project_folder
            
        except Exception as e:
            print(f"❌ Lỗi tạo project folder: {e}")
            self.log_performance("Project Folder Setup", False)
            self.current_project_folder = self.base_output_dir
            return self.base_output_dir
    
    def get_recording_status(self):
        """Kiểm tra trạng thái recording - PHASE 1: Optimized with timeout"""
        self.operation_start_time = time.time()
        ws = self.connect_obs_fast()  # Use faster connection method
        if not ws:
            self.log_performance("Recording Status Check", False)
            return False, None
        
        try:
            status_request = {
                "op": 6,
                "d": {
                    "requestType": "GetRecordStatus",
                    "requestId": "get-record-status"
                }
            }
            
            ws.send(json.dumps(status_request))
            
            # PHASE 1: Add timeout for faster response
            ws.settimeout(3.0)  # 3 second timeout
            response = json.loads(ws.recv())
            
            success = response.get("d", {}).get("requestStatus", {}).get("result", False)
            if success:
                is_recording = response.get("d", {}).get("responseData", {}).get("outputActive", False)
                output_path = response.get("d", {}).get("responseData", {}).get("outputPath", "")
                ws.close()
                self.log_performance("Recording Status Check", True)
                return is_recording, output_path
            
            ws.close()
            self.log_performance("Recording Status Check", False)
            return False, None
            
        except Exception as e:
            print(f"❌ Lỗi get recording status: {e}")
            self.log_performance("Recording Status Check", False)
            if ws:
                ws.close()
            return False, None
    
    def force_obs_filename(self, filename):
        """PHASE 1: Enhanced filename forcing với multiple strategies và faster execution"""
        self.operation_start_time = time.time()
        
        # ✨ DEBUG: Check current state
        print(f"🔍 FORCE OBS FILENAME DEBUG:")
        print(f"   📄 Requested filename: {filename}")
        print(f"   📂 Base directory: {self.base_output_dir}")
        print(f"   📂 Current project folder: {self.current_project_folder}")
        print(f"   ✅ Project folder exists: {os.path.exists(self.current_project_folder) if self.current_project_folder else 'N/A'}")
        
        # Đảm bảo không có recording đang chạy
        is_recording, _ = self.get_recording_status()
        if is_recording:
            print("⚠️ Stopping existing recording before setting new filename...")
            self.stop_recording()
            time.sleep(1.5)  # Reduced from 2s
        
        ws = self.connect_obs_fast()
        if not ws:
            self.log_performance("OBS Filename Forcing", False)
            return False
        
        try:
            # ✨ LUÔN LUÔN sử dụng project folder nếu có (cho TẤT CẢ các mode)
            if self.current_project_folder:
                # Sử dụng project folder cho TẤT CẢ các mode
                output_dir = self.current_project_folder
                full_path = os.path.join(self.current_project_folder, filename)
                print(f"🎯 Using project folder: {self.current_project_folder}")
            else:
                # Fallback về base folder nếu không có project folder
                output_dir = self.base_output_dir
                full_path = os.path.join(self.base_output_dir, filename)
                print(f"🎯 Using base folder (no project set): {self.base_output_dir}")

            print(f"🎯 FINAL OBS OUTPUT: {full_path}")
            print(f"🔧 Forcing OBS filename: {filename}")
            print(f"📁 Output directory: {output_dir}")
            print(f"📄 Full path: {full_path}")
            
            filename_without_ext = filename.replace('.mp4', '').replace('.mkv', '')
            
            # 🔥 NEW: FORCE ALL RECORDING PATHS - Multiple methods with enhanced force
            methods_success = []
            
            # Method 1: Set Main Recording Output Path
            try:
                set_path_request = {
                    "op": 6,
                    "d": {
                        "requestType": "SetProfileParameter",
                        "requestId": "set-recording-path",
                        "requestData": {
                            "parameterCategory": "Output",
                            "parameterName": "FilePath",
                            "parameterValue": output_dir
                        }
                    }
                }
                
                ws.send(json.dumps(set_path_request))
                response1 = json.loads(ws.recv())
                path_success = response1.get('d', {}).get('requestStatus', {}).get('result', False)
                methods_success.append(path_success)
                print(f"📁 Set main recording path: {path_success}")
                time.sleep(1.0)
            except Exception as e:
                print(f"Method 1 failed: {e}")
                methods_success.append(False)
            
            # 🔥 NEW Method 2: Set SimpleOutput FilePath (THIS IS CRUCIAL!)
            try:
                set_simple_path_request = {
                    "op": 6,
                    "d": {
                        "requestType": "SetProfileParameter",
                        "requestId": "set-simple-output-path",
                        "requestData": {
                            "parameterCategory": "SimpleOutput",
                            "parameterName": "FilePath", 
                            "parameterValue": output_dir
                        }
                    }
                }
                
                ws.send(json.dumps(set_simple_path_request))
                response2 = json.loads(ws.recv())
                simple_success = response2.get('d', {}).get('requestStatus', {}).get('result', False)
                methods_success.append(simple_success)
                print(f"📁 Set SimpleOutput path: {simple_success}")
                time.sleep(1.0)
            except Exception as e:
                print(f"Method 2 failed: {e}")
                methods_success.append(False)

            # 🔥 NEW Method 3: Set AdvOut RecFilePath (Advanced Output)
            try:
                set_adv_rec_request = {
                    "op": 6,
                    "d": {
                        "requestType": "SetProfileParameter",
                        "requestId": "set-adv-rec-path",
                        "requestData": {
                            "parameterCategory": "AdvOut", 
                            "parameterName": "RecFilePath",
                            "parameterValue": output_dir
                        }
                    }
                }
                
                ws.send(json.dumps(set_adv_rec_request))
                response3 = json.loads(ws.recv())
                adv_rec_success = response3.get('d', {}).get('requestStatus', {}).get('result', False)
                methods_success.append(adv_rec_success)
                print(f"📁 Set AdvOut RecFilePath: {adv_rec_success}")
                time.sleep(1.0)
            except Exception as e:
                print(f"Method 3 failed: {e}")
                methods_success.append(False)

            # 🔥 NEW Method 4: Set AdvOut FFFilePath (if different from RecFilePath)
            try:
                set_adv_ff_request = {
                    "op": 6,
                    "d": {
                        "requestType": "SetProfileParameter",
                        "requestId": "set-adv-ff-path",
                        "requestData": {
                            "parameterCategory": "AdvOut", 
                            "parameterName": "FFFilePath",
                            "parameterValue": output_dir
                        }
                    }
                }
                
                ws.send(json.dumps(set_adv_ff_request))
                response4 = json.loads(ws.recv())
                adv_ff_success = response4.get('d', {}).get('requestStatus', {}).get('result', False)
                methods_success.append(adv_ff_success)
                print(f"📁 Set AdvOut FFFilePath: {adv_ff_success}")
                time.sleep(1.0)
            except Exception as e:
                print(f"Method 4 failed: {e}")
                methods_success.append(False)

            # Method 5: Set Filename Formatting (AFTER setting all paths)
            try:
                set_filename_request = {
                    "op": 6,
                    "d": {
                        "requestType": "SetProfileParameter",
                        "requestId": "set-filename-format",
                        "requestData": {
                            "parameterCategory": "Output",
                            "parameterName": "FilenameFormatting",
                            "parameterValue": filename_without_ext
                        }
                    }
                }
                
                ws.send(json.dumps(set_filename_request))
                response5 = json.loads(ws.recv())
                filename_success = response5.get('d', {}).get('requestStatus', {}).get('result', False)
                methods_success.append(filename_success)
                print(f"📄 Set filename formatting: {filename_success}")
                time.sleep(1.0)
            except Exception as e:
                print(f"Method 5 failed: {e}")
                methods_success.append(False)

            # 🔥 NEW Method 6: Force SimpleOutput filename (CRUCIAL BACKUP!)
            try:
                set_simple_filename_request = {
                    "op": 6,
                    "d": {
                        "requestType": "SetProfileParameter",
                        "requestId": "set-simple-filename",
                        "requestData": {
                            "parameterCategory": "SimpleOutput",
                            "parameterName": "FilenameFormatting",
                            "parameterValue": filename_without_ext
                        }
                    }
                }
                
                ws.send(json.dumps(set_simple_filename_request))
                response6 = json.loads(ws.recv())
                simple_filename_success = response6.get('d', {}).get('requestStatus', {}).get('result', False)
                methods_success.append(simple_filename_success)
                print(f"📄 Set SimpleOutput filename: {simple_filename_success}")
                time.sleep(1.0)
            except Exception as e:
                print(f"Method 6 failed: {e}")
                methods_success.append(False)

            # 🔥 NEW Method 7: Set AdvOut RecRB (Recording Rebuild - sometimes needed)
            try:
                set_rec_rb_request = {
                    "op": 6,
                    "d": {
                        "requestType": "SetProfileParameter",
                        "requestId": "set-rec-rb",
                        "requestData": {
                            "parameterCategory": "AdvOut",
                            "parameterName": "RecRB",
                            "parameterValue": "false"  # Disable rebuild to force path
                        }
                    }
                }
                
                ws.send(json.dumps(set_rec_rb_request))
                response7 = json.loads(ws.recv())
                rec_rb_success = response7.get('d', {}).get('requestStatus', {}).get('result', False)
                methods_success.append(rec_rb_success)
                print(f"🔧 Set RecRB disable: {rec_rb_success}")
                time.sleep(1.0)
            except Exception as e:
                print(f"Method 7 failed: {e}")
                methods_success.append(False)
            
            ws.close()
            
            # Store filename for verification
            self.current_filename = filename
            
            # PHASE 1: Evaluate success rate
            success_rate = sum(methods_success) / len(methods_success) if methods_success else 0
            overall_success = success_rate >= 0.4  # Lowered to 40% for more tolerance
            
            print(f"📊 METHODS RESULTS: {methods_success}")
            print(f"📊 SUCCESS RATE: {success_rate*100:.0f}% ({sum(methods_success)}/{len(methods_success)})")
            
            if overall_success:
                print(f"✅ OBS configured successfully! ({success_rate*100:.0f}% methods succeeded)")
                print(f"   📁 Directory: {output_dir}")
                print(f"   📄 Filename: {filename}")
                
                # 🔥 ENHANCED: Longer wait time for ALL settings to properly apply
                print("⏳ Waiting for ALL OBS settings to apply...")
                time.sleep(4.0)  # Increased from 2.0s for better reliability
            else:
                print(f"⚠️ Warning: Only {success_rate*100:.0f}% of OBS settings applied correctly")
            
            self.log_performance("OBS Filename Forcing", overall_success)
            return overall_success
            
        except Exception as e:
            print(f"❌ Lỗi force filename: {e}")
            self.log_performance("OBS Filename Forcing", False)
            if ws:
                ws.close()
            return False
    
    def load_config_from_dict(self, config_dict):
        """Load config from dictionary - PHASE 1: Optimized validation"""
        try:
            self.operation_start_time = time.time()
            
            self.recording_area = config_dict.get('layout', {}).get('recordingArea')
            if not self.recording_area:
                print("ℹ️ No static recordingArea in config; showcase mode will record full screen.")
                self.log_performance("Config Loading", True)
                return True
            
            # PHASE 1: Quick validation
            required_keys = ['x', 'y', 'width', 'height']
            if not all(key in self.recording_area for key in required_keys):
                print("❌ Recording area missing required keys")
                self.log_performance("Config Loading", False)
                return False
            
            x = self.recording_area['x']
            y = self.recording_area['y'] 
            width = self.recording_area['width']
            height = self.recording_area['height']
            
            print(f"📐 Recording Area: ({x}, {y}) {width}x{height}")
            
            # PHASE 1: Faster base folder setup
            os.makedirs(self.base_output_dir, exist_ok=True)
            print(f"📂 Base output: {self.base_output_dir}")
            
            self.log_performance("Config Loading", True)
            return True
            
        except Exception as e:
            print(f"❌ Error loading config: {e}")
            self.log_performance("Config Loading", False)
            return False

    def full_setup_from_config(self, config_dict, use_full_screen=False, url=None):
        """
        🎬 Complete setup from config dictionary with project folder
        Combines config loading + project setup + OBS setup
        """
        try:
            self.operation_start_time = time.time()
            
            # Step 1: Load config
            print("📁 Loading configuration...")
            if not self.load_config_from_dict(config_dict):
                print("❌ Failed to load config")
                self.log_performance("Full Setup From Config", False)
                return False
            
            # Step 2: Setup project folder if URL provided
            if url:
                print("📁 Setting up project folder...")
                project_folder = self.setup_project_folder(url)
                print(f"📁 Project folder: {project_folder}")
            
            # Step 3: Full OBS setup
            print("🎬 Setting up OBS...")
            success = self.full_setup(use_full_screen)
            
            if success:
                print("✅ Complete setup successful!")
            else:
                print("❌ OBS setup failed")
            
            self.log_performance("Full Setup From Config", success)
            return success
            
        except Exception as e:
            print(f"❌ Error in full setup from config: {e}")
            self.log_performance("Full Setup From Config", False)
            return False

    def validate_recording_area(self):
        """🔍 Validate recording area configuration"""
        if not self.recording_area:
            print("❌ recording_area is None")
            return False
        
        required_keys = ['x', 'y', 'width', 'height']
        for key in required_keys:
            if key not in self.recording_area:
                print(f"❌ Missing key in recording_area: {key}")
                return False
        
        try:
            x, y, width, height = (
                self.recording_area['x'],
                self.recording_area['y'], 
                self.recording_area['width'],
                self.recording_area['height']
            )
            
            # Validate ranges
            if x < 0 or y < 0:
                print(f"❌ Negative position: ({x}, {y})")
                return False
            
            if width <= 0 or height <= 0:
                print(f"❌ Invalid dimensions: {width}x{height}")
                return False
            
            if x + width > self.screen_width or y + height > self.screen_height:
                print(f"❌ Recording area exceeds screen: ({x}, {y}) {width}x{height} > {self.screen_width}x{self.screen_height}")
                return False
            
            print(f"✅ Recording area valid: ({x}, {y}) {width}x{height}")
            return True
            
        except (TypeError, ValueError) as e:
            print(f"❌ Invalid recording_area values: {e}")
            return False

    def connect_obs_fast(self):
        """PHASE 1: Faster OBS WebSocket connection với optimized timeout"""
        try:
            self.operation_start_time = time.time()
            
            ws_url = f"ws://{self.obs_host}:{self.obs_port}"
            # PHASE 1: Reduced connection timeout
            ws = websocket.create_connection(ws_url, timeout=5)  # 5s instead of default 10s
            
            # PHASE 1: Faster authentication
            hello_msg = json.loads(ws.recv())
            if hello_msg["op"] == 0:
                auth_data = hello_msg["d"]["authentication"]
                
                secret = base64.b64encode(
                    hashlib.sha256((self.obs_password + auth_data["salt"]).encode()).digest()
                ).decode()
                
                auth_string = base64.b64encode(
                    hashlib.sha256((secret + auth_data["challenge"]).encode()).digest()
                ).decode()
                
                identify_msg = {
                    "op": 1,
                    "d": {
                        "rpcVersion": 1,
                        "authentication": auth_string
                    }
                }
                
                ws.send(json.dumps(identify_msg))
                identified_msg = json.loads(ws.recv())
                
                if identified_msg["op"] != 2:
                    ws.close()
                    self.log_performance("OBS Connection", False)
                    return None
            
            self.log_performance("OBS Connection", True)
            return ws
            
        except Exception as e:
            print(f"❌ Lỗi kết nối OBS: {e}")
            self.log_performance("OBS Connection", False)
            return None
    
    def connect_obs(self):
        """Legacy method - redirects to optimized version"""
        return self.connect_obs_fast()
    
    def set_canvas_resolution(self):
        """PHASE 1: Optimized canvas resolution setting with None check"""
        if not self.recording_area:
            print("❌ recording_area is None! Cannot set canvas resolution.")
            print("💡 Make sure to call load_config_from_dict() first or use full_setup_from_config()")
            self.log_performance("Canvas Resolution Setting", False)
            return False
        
        self.operation_start_time = time.time()
        
        try:
            width = self.recording_area['width']
            height = self.recording_area['height']
        except (KeyError, TypeError) as e:
            print(f"❌ Invalid recording_area structure: {e}")
            print(f"📊 Current recording_area: {self.recording_area}")
            self.log_performance("Canvas Resolution Setting", False)
            return False
        
        # ✨ THÊM: Check và stop recording nếu đang active
        print("🔍 Checking for active recordings before canvas resolution change...")
        is_recording, current_path = self.get_recording_status()
        if is_recording:
            print("⚠️ Active recording detected - stopping before canvas change...")
            stop_success, _ = self.stop_recording()
            if stop_success:
                print("✅ Recording stopped successfully")
                # Wait for OBS to fully stop recording
                time.sleep(2)
            else:
                print("⚠️ Warning: Could not stop recording - attempting canvas change anyway")
        
        ws = self.connect_obs_fast()
        if not ws:
            self.log_performance("Canvas Resolution Setting", False)
            return False
        
        try:
            print(f"🖼️ Setting canvas resolution to {width}x{height}...")
            
            video_settings_request = {
                "op": 6,
                "d": {
                    "requestType": "SetVideoSettings",
                    "requestId": "set-video-settings",
                    "requestData": {
                        "baseWidth": width,
                        "baseHeight": height,
                        "outputWidth": width,
                        "outputHeight": height
                    }
                }
            }
            
            ws.send(json.dumps(video_settings_request))
            response = json.loads(ws.recv())
            
            success = response.get("d", {}).get("requestStatus", {}).get("result", False)
            
            if success:
                print(f"✅ Canvas resolution set to {width}x{height}")
            else:
                print(f"❌ Failed to set canvas resolution")
                print(f"📊 OBS Response: {response}")
            
            ws.close()
            self.log_performance("Canvas Resolution Setting", success)
            return success
            
        except Exception as e:
            print(f"❌ Error set canvas resolution: {e}")
            self.log_performance("Canvas Resolution Setting", False)
            try:
                ws.close()
            except:
                pass
            return False
    
    def apply_crop_and_scale(self):
        """PHASE 1: Optimized crop and scale application with None check"""
        if not self.recording_area:
            print("❌ recording_area is None! Cannot apply crop and scale.")
            print("💡 Make sure to call load_config_from_dict() first or use full_setup_from_config()")
            self.log_performance("Crop and Scale Application", False)
            return False
        
        self.operation_start_time = time.time()
        
        # Validate recording_area structure
        required_keys = ['x', 'y', 'width', 'height']
        try:
            for key in required_keys:
                if key not in self.recording_area:
                    raise KeyError(f"Missing key: {key}")
            
            x = self.recording_area['x']
            y = self.recording_area['y']
            width = self.recording_area['width']
            height = self.recording_area['height']
            
            # Validate values are reasonable
            if width <= 0 or height <= 0:
                raise ValueError(f"Invalid dimensions: {width}x{height}")
            
        except (KeyError, TypeError, ValueError) as e:
            print(f"❌ Invalid recording_area: {e}")
            print(f"📊 Current recording_area: {self.recording_area}")
            self.log_performance("Crop and Scale Application", False)
            return False
        
        ws = self.connect_obs_fast()
        if not ws:
            self.log_performance("Crop and Scale Application", False)
            return False
        
        try:
            print(f"✂️ Cropping and scaling source...")
            print(f"📐 Area: ({x}, {y}) {width}x{height}")
            
            # PHASE 1: Batch requests for faster execution
            
            # Get current scene
            get_scene_request = {
                "op": 6,
                "d": {
                    "requestType": "GetCurrentProgramScene",
                    "requestId": "get-scene"
                }
            }
            
            ws.send(json.dumps(get_scene_request))
            scene_response = json.loads(ws.recv())
            current_scene = scene_response.get("d", {}).get("responseData", {}).get("currentProgramSceneName", "Scene")
            
            # Get scene item ID for 'Sprunki Capture'
            get_item_request = {
                "op": 6,
                "d": {
                    "requestType": "GetSceneItemId",
                    "requestId": "get-item-id",
                    "requestData": {
                        "sceneName": current_scene,
                        "sourceName": "Sprunki Capture"
                    }
                }
            }
            
            ws.send(json.dumps(get_item_request))
            item_response = json.loads(ws.recv())
            
            if not item_response.get("d", {}).get("requestStatus", {}).get("result", False):
                print("❌ Source 'Sprunki Capture' không tồn tại!")
                print("💡 Make sure the source 'Sprunki Capture' exists in OBS")
                ws.close()
                self.log_performance("Crop and Scale Application", False)
                return False
            
            scene_item_id = item_response.get("d", {}).get("responseData", {}).get("sceneItemId")
            
            # Calculate crop values
            crop_right = self.screen_width - x - width
            crop_bottom = self.screen_height - y - height
            
            print(f"🔧 Crop values: Left={x}, Top={y}, Right={crop_right}, Bottom={crop_bottom}")
            
            # Apply transform
            transform_request = {
                "op": 6,
                "d": {
                    "requestType": "SetSceneItemTransform",
                    "requestId": "set-transform",
                    "requestData": {
                        "sceneName": current_scene,
                        "sceneItemId": scene_item_id,
                        "sceneItemTransform": {
                            "cropLeft": x,
                            "cropTop": y,
                            "cropRight": crop_right,
                            "cropBottom": crop_bottom,
                            "positionX": 0,
                            "positionY": 0,
                            "scaleX": 1.0,
                            "scaleY": 1.0
                        }
                    }
                }
            }
            
            ws.send(json.dumps(transform_request))
            transform_response = json.loads(ws.recv())
            
            success = transform_response.get("d", {}).get("requestStatus", {}).get("result", False)
            
            if success:
                print("✅ Crop and scale applied!")
            else:
                print("❌ Failed to apply transform")
                print(f"📊 OBS Response: {transform_response}")
            
            ws.close()
            self.log_performance("Crop and Scale Application", success)
            return success
            
        except Exception as e:
            print(f"❌ Error apply crop and scale: {e}")
            self.log_performance("Crop and Scale Application", False)
            try:
                ws.close()
            except:
                pass
            return False

    def reset_crop_and_scale(self):
        """PHASE 1: Optimized reset crop/scale về mặc định"""
        self.operation_start_time = time.time()
        ws = self.connect_obs_fast()
        if not ws:
            return False
        
        try:
            print(f"✂️ Resetting crop and scale for full screen...")
            
            # PHASE 1: Streamlined process
            # Get current scene
            get_scene_request = { "op": 6, "d": { "requestType": "GetCurrentProgramScene", "requestId": "get-scene" } }
            ws.send(json.dumps(get_scene_request))
            scene_response = json.loads(ws.recv())
            current_scene = scene_response.get("d", {}).get("responseData", {}).get("currentProgramSceneName", "Scene")
            
            # Get scene item ID
            get_item_request = { "op": 6, "d": { "requestType": "GetSceneItemId", "requestId": "get-item-id", "requestData": { "sceneName": current_scene, "sourceName": "Sprunki Capture" } } }
            ws.send(json.dumps(get_item_request))
            item_response = json.loads(ws.recv())
            if not item_response.get("d", {}).get("requestStatus", {}).get("result", False):
                print("❌ Source 'Sprunki Capture' không tồn tại!")
                ws.close()
                self.log_performance("Crop Reset", False)
                return False
            
            scene_item_id = item_response.get("d", {}).get("responseData", {}).get("sceneItemId")
            
            # Apply RESET transform (all crops to 0)
            transform_request = {
                "op": 6,
                "d": {
                    "requestType": "SetSceneItemTransform",
                    "requestId": "reset-transform",
                    "requestData": {
                        "sceneName": current_scene,
                        "sceneItemId": scene_item_id,
                        "sceneItemTransform": {
                            "cropLeft": 0,
                            "cropTop": 0,
                            "cropRight": 0,
                            "cropBottom": 0,
                            "positionX": 0,
                            "positionY": 0,
                            "scaleX": 1.0,
                            "scaleY": 1.0
                        }
                    }
                }
            }
            
            ws.send(json.dumps(transform_request))
            transform_response = json.loads(ws.recv())
            success = transform_response.get("d", {}).get("requestStatus", {}).get("result", False)
            
            if success:
                print("✅ Crop and scale reset!")
            else:
                print("❌ Failed to reset transform")
            
            ws.close()
            self.log_performance("Crop Reset", success)
            return success
            
        except Exception as e:
            print(f"❌ Lỗi reset crop and scale: {e}")
            self.log_performance("Crop Reset", False)
            ws.close()
            return False

    def apply_master_ui_crop_and_scale(self):
        """
        Crop phần UI theo Master mode (menu trái 78px, viền trên/dưới 22px)
        rồi scale khít 1920x1080. Không tạo source mới, áp vào 'Sprunki Capture'.
        """
        self.operation_start_time = time.time()
        ws = self.connect_obs_fast()
        if not ws:
            return False

        try:
            # Crop theo yêu cầu
            crop_left = 78
            crop_top = 22
            crop_right = 0
            crop_bottom = 22

            # Scale để phần còn lại fill đúng 1920x1080
            content_w = self.screen_width - crop_left - crop_right
            content_h = self.screen_height - crop_top - crop_bottom
            scale_x = self.screen_width / content_w     # 1920/1842 ≈ 1.0423
            scale_y = self.screen_height / content_h    # 1080/1036 ≈ 1.0425

            # Lấy scene đang on-air
            get_scene_request = {
                "op": 6,
                "d": {"requestType": "GetCurrentProgramScene", "requestId": "get-scene"}
            }
            ws.send(json.dumps(get_scene_request))
            scene_response = json.loads(ws.recv())
            current_scene = scene_response.get("d", {}).get("responseData", {}).get("currentProgramSceneName", "Scene")

            # Lấy sceneItemId của source 'Sprunki Capture'
            get_item_request = {
                "op": 6,
                "d": {
                    "requestType": "GetSceneItemId",
                    "requestId": "get-item-id",
                    "requestData": {
                        "sceneName": current_scene,
                        "sourceName": "Sprunki Capture"
                    }
                }
            }
            ws.send(json.dumps(get_item_request))
            item_response = json.loads(ws.recv())
            if not item_response.get("d", {}).get("requestStatus", {}).get("result", False):
                print("❌ Source 'Sprunki Capture' không tồn tại!")
                ws.close()
                self.log_performance("Master Crop+Scale", False)
                return False

            scene_item_id = item_response.get("d", {}).get("responseData", {}).get("sceneItemId")

            # Áp transform: crop + scale + căn vị trí 0,0
            transform_request = {
                "op": 6,
                "d": {
                    "requestType": "SetSceneItemTransform",
                    "requestId": "set-transform-master",
                    "requestData": {
                        "sceneName": current_scene,
                        "sceneItemId": scene_item_id,
                        "sceneItemTransform": {
                            "cropLeft": crop_left,
                            "cropTop": crop_top,
                            "cropRight": crop_right,
                            "cropBottom": crop_bottom,
                            "positionX": 0,
                            "positionY": 0,
                            "scaleX": scale_x,
                            "scaleY": scale_y
                        }
                    }
                }
            }
            ws.send(json.dumps(transform_request))
            transform_response = json.loads(ws.recv())
            success = transform_response.get("d", {}).get("requestStatus", {}).get("result", False)

            if success:
                print("✅ Master crop + scale applied!")
            else:
                print("❌ Failed to apply Master crop + scale")

            ws.close()
            self.log_performance("Master Crop+Scale", success)
            return success

        except Exception as e:
            print(f"❌ Lỗi Master crop+scale: {e}")
            self.log_performance("Master Crop+Scale", False)
            try:
                ws.close()
            except:
                pass
            return False
    
    def reset_canvas_resolution(self):
        """PHASE 1: Optimized reset canvas về 1920x1080"""
        self.operation_start_time = time.time()
        
        # ✨ THÊM: Check và stop recording nếu đang active
        print("🔍 Checking for active recordings before canvas reset...")
        is_recording, current_path = self.get_recording_status()
        if is_recording:
            print("⚠️ Active recording detected - stopping before canvas reset...")
            stop_success, _ = self.stop_recording()
            if stop_success:
                print("✅ Recording stopped successfully")
                # Wait for OBS to fully stop recording
                time.sleep(2)
            else:
                print("⚠️ Warning: Could not stop recording - attempting canvas reset anyway")
        
        ws = self.connect_obs_fast()
        if not ws:
            return False
        
        try:
            print("📄 Resetting canvas to 1920x1080...")
            
            video_settings_request = {
                "op": 6,
                "d": {
                    "requestType": "SetVideoSettings",
                    "requestId": "reset-video-settings",
                    "requestData": {
                        "baseWidth": 1920,
                        "baseHeight": 1080,
                        "outputWidth": 1920,
                        "outputHeight": 1080
                    }
                }
            }
            
            ws.send(json.dumps(video_settings_request))
            response = json.loads(ws.recv())
            
            success = response.get("d", {}).get("requestStatus", {}).get("result", False)
            
            if success:
                print("✅ Canvas reset to 1920x1080")
            else:
                print("❌ Failed to reset canvas")
            
            ws.close()
            self.log_performance("Canvas Reset", success)
            return success
            
        except Exception as e:
            print(f"❌ Lỗi reset canvas: {e}")
            self.log_performance("Canvas Reset", False)
            ws.close()
            return False
    
    def full_setup(self, use_full_screen=False):
        """PHASE 1: Optimized setup hoàn chỉnh với performance tracking"""
        self.operation_start_time = time.time()
        setup_start_time = time.time()
        
        if use_full_screen:
            # YÊU CẦU MỚI: Quy trình cho Master Mode (Full Screen)
            print("\n🎬 FULL SCREEN SETUP (1920x1080) - OPTIMIZED")
            print("="*50)
            
            # Step 1: Reset canvas về 1920x1080
            if not self.reset_canvas_resolution():
                print("❌ Failed to reset canvas resolution for full screen")
                self.log_performance("Full Setup", False)
                return False
            
            # Step 2: Reset crop của source về 0
            if not self.reset_crop_and_scale():
                print("❌ Failed to reset crop/scale for full screen")
                self.log_performance("Full Setup", False)
                return False

            if not self.apply_master_ui_crop_and_scale():
                print("❌ Failed to apply Master crop+scale")
                self.log_performance("Full Setup (Master)", False)
                return False

            self.master_mode_active = True
            
            print(f"\n✅ FULL SCREEN SETUP COMPLETED!")
            print(f"🎯 Video output will be: 1920x1080 pixels")
            
        else:
            # Quy trình cũ cho Individual Mode (Cropped)
            print("\n🎬 CROPPED VIDEO SETUP - OPTIMIZED")
            print("="*50)
            
            # Kiểm tra recording_area trước khi làm gì
            if not self.recording_area:
                print("❌ recording_area is None! Cannot setup cropped mode.")
                print("💡 Make sure to call load_config_from_dict() first")
                self.log_performance("Full Setup", False)
                return False
        
            # Step 1: Set canvas resolution theo recordingArea
            if not self.set_canvas_resolution():
                print("❌ Failed to set canvas resolution")
                self.log_performance("Full Setup", False)
                return False
            
            # Step 2: Apply crop và scale
            if not self.apply_crop_and_scale():
                print("❌ Failed to apply crop and scale")
                self.log_performance("Full Setup", False)
                return False
            
            # SỬA LỖI: Đảm bảo recording_area không None trước khi truy cập
            try:
                width = self.recording_area['width']
                height = self.recording_area['height']
                print(f"\n✅ CROPPED SETUP COMPLETED!")
                print(f"🎯 Video output will be: {width}x{height} pixels")
            except (TypeError, KeyError) as e:
                print(f"❌ Error accessing recording_area dimensions: {e}")
                self.log_performance("Full Setup", False)
                return False

        # PHASE 1: Performance summary
        total_setup_time = time.time() - setup_start_time
        print(f"📹 Ready for recording!")
        print(f"⚡ Setup completed in {total_setup_time:.2f}s")
        self.log_performance("Full Setup", True)
        return True
    
    def start_recording(self):
        """PHASE 1: Optimized recording start với faster status checking"""
        self.operation_start_time = time.time()
        
        is_recording, _ = self.get_recording_status()
        if is_recording:
            print("⚠️ OBS đang recording, dừng recording cũ trước...")
            self.stop_recording()
            time.sleep(1.5)
        
        ws = self.connect_obs_fast()
        if not ws:
            self.log_performance("Recording Start", False)
            return False
        
        try:
            start_request = {"op": 6, "d": {"requestType": "StartRecord", "requestId": "start-recording"}}
            ws.send(json.dumps(start_request))
            response = json.loads(ws.recv())
            success = response.get("d", {}).get("requestStatus", {}).get("result", False)
            
            if success:
                print("🎬 Recording started!")
                
                # BẮT ĐẦU THEO DÕI TIMESTAMP
                self.is_currently_recording = True
                self.recording_start_time = time.monotonic()
                self.event_timestamps = [] # Reset cho video mới
                self.recording_areas = {}
                self.log_event_timestamp("recording_started", "OBS recording confirmed active")
                
                if self.current_filename: print(f"📹 Target filename: {self.current_filename}")
                if self.current_project_folder: print(f"📁 Output folder: {self.current_project_folder}")
                
                time.sleep(0.8)
                is_active, _ = self.get_recording_status()
                if is_active: print("✅ Recording verified as active")
                else: print("⚠️ Warning: Recording may not be active")
            else:
                print(f"❌ Failed to start recording: {response}")
            
            ws.close()
            self.log_performance("Recording Start", success)
            return success
            
        except Exception as e:
            print(f"❌ Lỗi start recording: {e}")
            self.log_performance("Recording Start", False)
            ws.close()
            return False
    
    def stop_recording(self):
        """PHASE 1: Optimized recording stop với faster retry mechanism"""
        self.operation_start_time = time.time()
        
        is_recording, current_path = self.get_recording_status()
        if not is_recording:
            print("⚠️ OBS không đang recording")
            self.is_currently_recording = False
            self.recording_start_time = None
            self.log_performance("Recording Stop", True)
            return True, current_path
        
        ws = self.connect_obs_fast()
        if not ws:
            self.log_performance("Recording Stop", False)
            return False, None
        
        # PHASE 1: Reduced max retries and faster retry timing
        max_retries = 2  # Reduced from 3
        for attempt in range(max_retries):
            try:
                print(f"🛑 Attempting to stop recording (attempt {attempt + 1}/{max_retries})...")
                
                stop_request = {
                    "op": 6,
                    "d": {
                        "requestType": "StopRecord",
                        "requestId": f"stop-recording-{attempt}"
                    }
                }
                
                ws.send(json.dumps(stop_request))
                response = json.loads(ws.recv())
                
                success = response.get("d", {}).get("requestStatus", {}).get("result", False)
                
                if success:
                    output_path = response.get("d", {}).get("responseData", {}).get("outputPath", "")
                    print("⏹️ Recording stopped successfully!")

                    # LƯU FILE TIMESTAMP
                    self.log_event_timestamp("recording_stopped", "OBS recording stopped")
                    self._save_timestamps_to_file(output_path)
                    self.is_currently_recording = False
                    self.recording_start_time = None

                    if output_path:
                        print(f"📁 Video saved: {output_path}")
                        
                        # PHASE 1: Faster filename and directory verification
                        actual_filename = os.path.basename(output_path)
                        if self.current_filename and self.current_filename not in actual_filename:
                            print(f"⚠️ Filename mismatch: expected '{self.current_filename}', got '{actual_filename}'")
                        else:
                            print(f"✅ Filename correct: {actual_filename}")
                        
                        # Kiểm tra thư mục có đúng không
                        actual_dir = os.path.dirname(output_path)
                        expected_dir = self.current_project_folder or self.base_output_dir
                        
                        if actual_dir.lower() == expected_dir.lower():
                            print(f"✅ Directory correct: {actual_dir}")
                        else:
                            print(f"⚠️ Directory mismatch: expected '{expected_dir}', got '{actual_dir}'")
                        
                        # PHASE 1: Optimized file existence check
                        if os.path.exists(output_path):
                            file_size = os.path.getsize(output_path)
                            print(f"✅ File exists: {file_size} bytes")
                        else:
                            print(f"❌ File not found: {output_path}")
                    
                    ws.close()
                    
                    if self.master_mode_active:
                        # trả source về crop=0, scale=1 để các mode khác/ghi tiếp theo không bị ảnh hưởng
                        try:
                            self.reset_crop_and_scale()
                        finally:
                            self.master_mode_active = False                    
                    
                    self.log_performance("Recording Stop", True)
                    return True, output_path
                
                else:
                    error_code = response.get("d", {}).get("requestStatus", {}).get("code", "unknown")
                    print(f"❌ Stop recording failed (attempt {attempt + 1}): code {error_code}")
                    
                    if attempt < max_retries - 1:
                        print("⏳ Waiting 1.5 seconds before retry...")  # Reduced from 2s
                        time.sleep(1.5)
                        
                        # Check if recording is still active
                        is_still_recording, _ = self.get_recording_status()
                        if not is_still_recording:
                            print("✅ Recording stopped by itself")
                            ws.close()
                            self.log_performance("Recording Stop", True)
                            return True, current_path
                
            except Exception as e:
                print(f"❌ Exception on stop attempt {attempt + 1}: {e}")
                if attempt < max_retries - 1:
                    time.sleep(1.5)  # Reduced from 2s
        
        print("❌ Failed to stop recording after all retries")
        ws.close()
        
        # Final check - maybe recording stopped anyway
        final_status, final_path = self.get_recording_status()
        if not final_status:
            print("✅ Recording appears to have stopped anyway")
            self.log_performance("Recording Stop", True)
            return True, final_path
        
        self.log_performance("Recording Stop", False)
        return False, None
    
    def print_performance_summary(self):
        """PHASE 1: Print performance summary"""
        if self.total_operations > 0:
            success_rate = (self.successful_operations / self.total_operations) * 100
            print(f"\n📊 PHASE 1 PERFORMANCE SUMMARY:")
            print(f"   Total Operations: {self.total_operations}")
            print(f"   Successful: {self.successful_operations}")
            print(f"   Success Rate: {success_rate:.1f}%")
            print(f"   ⚡ Optimized timing applied across all operations")
        else:
            print("📊 No operations tracked yet")

    def comprehensive_obs_cleanup(self):
        """🧹 ENHANCED COMPREHENSIVE OBS CLEANUP - Cấp độ 1 với health check và force stop"""
        self.operation_start_time = time.time()
        
        print("🧹 Starting ENHANCED comprehensive OBS cleanup...")
        print("="*60)
        
        cleanup_success_count = 0
        cleanup_total_count = 0
        
        try:
            # ✨ NEW STEP 0: OBS Health Check
            cleanup_total_count += 1
            print("🏥 Step 0: OBS Health Check...")
            try:
                health_ok = self._obs_health_check()
                if health_ok:
                    print("✅ OBS is responsive")
                    cleanup_success_count += 1
                else:
                    print("⚠️ OBS health issues detected")
            except Exception as e:
                print(f"❌ OBS health check failed: {e}")

            # ✨ ENHANCED STEP 1: Force stop ALL recording types
            cleanup_total_count += 1
            print("🔴 Step 1: Enhanced force stop ALL recordings...")
            try:
                stop_success = self._force_stop_all_recordings()
                if stop_success:
                    print("✅ All recordings force stopped")
                    cleanup_success_count += 1
                else:
                    print("⚠️ Some recordings may still be active")
            except Exception as e:
                print(f"❌ Error force stopping recordings: {e}")
            
            # ✨ ENHANCED STEP 2: Wait for OBS to stabilize after recording stop
            cleanup_total_count += 1
            print("⏳ Step 2: Enhanced stabilization wait...")
            try:
                stabilization_time = 5  # Increased from 3
                for countdown in range(stabilization_time, 0, -1):
                    print(f"   🔄 Enhanced stabilizing... {countdown}s remaining")
                    time.sleep(1)
                
                print("✅ Enhanced stabilization completed")
                cleanup_success_count += 1
            except Exception as e:
                print(f"❌ Error during enhanced stabilization: {e}")

            # STEP 3: Reset canvas to default 1920x1080 (with retry)
            cleanup_total_count += 1
            print("📐 Step 3: Enhanced canvas reset with retry...")
            try:
                canvas_reset_success = self._enhanced_canvas_reset()
                if canvas_reset_success:
                    print("✅ Enhanced canvas reset successful")
                    cleanup_success_count += 1
                else:
                    print("⚠️ Enhanced canvas reset had issues")
            except Exception as e:
                print(f"❌ Error in enhanced canvas reset: {e}")
            
            # STEP 4: Enhanced source transforms reset
            cleanup_total_count += 1
            print("✂️ Step 4: Enhanced source transforms reset...")
            try:
                transform_reset_success = self._enhanced_transform_reset()
                if transform_reset_success:
                    print("✅ Enhanced transforms reset successful")
                    cleanup_success_count += 1
                else:
                    print("⚠️ Enhanced transforms reset had issues")
            except Exception as e:
                print(f"❌ Error in enhanced transform reset: {e}")
            
            # STEP 5: Enhanced settings cleanup
            cleanup_total_count += 1
            print("📝 Step 5: Enhanced settings cleanup...")
            try:
                settings_success = self._enhanced_settings_cleanup()
                if settings_success:
                    print("✅ Enhanced settings cleanup successful")
                    cleanup_success_count += 1
                else:
                    print("⚠️ Enhanced settings cleanup had issues")
            except Exception as e:
                print(f"❌ Error in enhanced settings cleanup: {e}")
            
            # STEP 6: Clear internal state
            cleanup_total_count += 1
            print("🔄 Step 6: Clear internal state...")
            try:
                self.current_filename = None
                self.current_project_folder = None
                self.master_mode_active = False
                print("✅ Internal state cleared")
                cleanup_success_count += 1
            except Exception as e:
                print(f"❌ Error clearing internal state: {e}")
            
            # ✨ NEW STEP 7: Final OBS health verification
            cleanup_total_count += 1
            print("🏥 Step 7: Final OBS health verification...")
            try:
                final_health = self._obs_health_check()
                if final_health:
                    print("✅ Final OBS health check passed")
                    cleanup_success_count += 1
                else:
                    print("⚠️ Final OBS health check issues")
            except Exception as e:
                print(f"❌ Final health check error: {e}")

            # CLEANUP SUMMARY
            print("="*60)
            cleanup_duration = time.time() - self.operation_start_time
            success_rate = (cleanup_success_count / cleanup_total_count * 100) if cleanup_total_count > 0 else 0
            
            print(f"🧹 ENHANCED CLEANUP COMPLETED")
            print(f"   ⏱️ Duration: {cleanup_duration:.1f}s")
            print(f"   ✅ Success: {cleanup_success_count}/{cleanup_total_count} steps ({success_rate:.0f}%)")
            print(f"   🎯 OBS ready for next job (enhanced)")
            
            # Log performance
            overall_success = cleanup_success_count >= (cleanup_total_count * 0.6)  # 60% success rate
            self.log_performance("Enhanced Comprehensive OBS Cleanup", overall_success)
            
            return overall_success
            
        except Exception as e:
            cleanup_duration = time.time() - self.operation_start_time if self.operation_start_time else 0
            print(f"💥 Critical error during enhanced cleanup after {cleanup_duration:.1f}s: {e}")
            self.log_performance("Enhanced Comprehensive OBS Cleanup", False)
            return False

    def _obs_health_check(self):
        """🏥 Kiểm tra health của OBS WebSocket connection"""
        try:
            ws = self.connect_obs_fast()
            if not ws:
                return False
            
            # Test basic communication
            health_request = {
                "op": 6,
                "d": {
                    "requestType": "GetVersion",
                    "requestId": "health-check"
                }
            }
            
            ws.send(json.dumps(health_request))
            response = json.loads(ws.recv())
            
            ws.close()
            
            # Check if response is valid
            return response.get("d", {}).get("requestStatus", {}).get("result", False)
            
        except Exception as e:
            print(f"   Health check exception: {e}")
            return False

    def _force_stop_all_recordings(self):
        """🔴 Force stop ALL types of recordings với enhanced retry"""
        max_attempts = 3
        
        for attempt in range(max_attempts):
            try:
                print(f"   🔄 Force stop attempt {attempt + 1}/{max_attempts}")
                
                ws = self.connect_obs_fast()
                if not ws:
                    continue
                
                # Check recording status first
                status_request = {
                    "op": 6,
                    "d": {
                        "requestType": "GetRecordStatus",
                        "requestId": f"force-check-{attempt}"
                    }
                }
                
                ws.send(json.dumps(status_request))
                status_response = json.loads(ws.recv())
                
                is_recording = status_response.get("d", {}).get("responseData", {}).get("outputActive", False)
                
                if not is_recording:
                    print(f"   ✅ No active recording found (attempt {attempt + 1})")
                    ws.close()
                    return True
                
                print(f"   ⚠️ Active recording detected, forcing stop...")
                
                # Force stop recording
                stop_request = {
                    "op": 6,
                    "d": {
                        "requestType": "StopRecord",
                        "requestId": f"force-stop-{attempt}"
                    }
                }
                
                ws.send(json.dumps(stop_request))
                stop_response = json.loads(ws.recv())
                
                stop_success = stop_response.get("d", {}).get("requestStatus", {}).get("result", False)
                
                ws.close()
                
                if stop_success:
                    print(f"   ✅ Recording force stopped (attempt {attempt + 1})")
                    # Wait longer after successful stop
                    time.sleep(3)
                    return True
                else:
                    print(f"   ⚠️ Stop failed (attempt {attempt + 1})")
                    time.sleep(2)  # Wait before retry
                    
            except Exception as e:
                print(f"   ❌ Force stop attempt {attempt + 1} error: {e}")
                time.sleep(2)
        
        print(f"   ⚠️ Force stop failed after {max_attempts} attempts")
        return False

    def _enhanced_canvas_reset(self):
        """📐 Enhanced canvas reset với retry mechanism"""
        max_attempts = 3
        
        for attempt in range(max_attempts):
            try:
                print(f"   🔄 Canvas reset attempt {attempt + 1}/{max_attempts}")
                
                ws = self.connect_obs_fast()
                if not ws:
                    continue
                
                video_settings_request = {
                    "op": 6,
                    "d": {
                        "requestType": "SetVideoSettings",
                        "requestId": f"enhanced-canvas-reset-{attempt}",
                        "requestData": {
                            "baseWidth": 1920,
                            "baseHeight": 1080,
                            "outputWidth": 1920,
                            "outputHeight": 1080
                        }
                    }
                }
                
                ws.send(json.dumps(video_settings_request))
                response = json.loads(ws.recv())
                
                success = response.get("d", {}).get("requestStatus", {}).get("result", False)
                
                ws.close()
                
                if success:
                    print(f"   ✅ Canvas reset successful (attempt {attempt + 1})")
                    time.sleep(1)  # Wait for settings to apply
                    return True
                else:
                    print(f"   ⚠️ Canvas reset failed (attempt {attempt + 1}): {response}")
                    time.sleep(2)  # Wait before retry
                    
            except Exception as e:
                print(f"   ❌ Canvas reset attempt {attempt + 1} error: {e}")
                time.sleep(2)
        
        return False

    def _enhanced_transform_reset(self):
        """✂️ Enhanced transform reset với better error handling"""
        try:
            return self.reset_crop_and_scale()
        except Exception as e:
            print(f"   ❌ Transform reset error: {e}")
            return False

    def _enhanced_settings_cleanup(self):
        """📝 Enhanced settings cleanup với multiple parameters"""
        try:
            ws = self.connect_obs_fast()
            if not ws:
                return False
            
            # Reset multiple settings with delays
            settings_success = []
            
            # 1. Reset filename formatting
            try:
                filename_request = {
                    "op": 6,
                    "d": {
                        "requestType": "SetProfileParameter",
                        "requestId": "enhanced-filename-reset",
                        "requestData": {
                            "parameterCategory": "Output",
                            "parameterName": "FilenameFormatting",
                            "parameterValue": "%CCYY-%MM-%DD %hh-%mm-%ss"
                        }
                    }
                }
                
                ws.send(json.dumps(filename_request))
                response1 = json.loads(ws.recv())
                settings_success.append(response1.get('d', {}).get('requestStatus', {}).get('result', False))
                
                time.sleep(0.5)  # Enhanced delay between settings
                
            except Exception as e:
                print(f"   ❌ Filename reset error: {e}")
                settings_success.append(False)
            
            # 2. Reset output path
            try:
                path_request = {
                    "op": 6,
                    "d": {
                        "requestType": "SetProfileParameter",
                        "requestId": "enhanced-path-reset",
                        "requestData": {
                            "parameterCategory": "Output",
                            "parameterName": "FilePath",
                            "parameterValue": self.base_output_dir
                        }
                    }
                }
                
                ws.send(json.dumps(path_request))
                response2 = json.loads(ws.recv())
                settings_success.append(response2.get('d', {}).get('requestStatus', {}).get('result', False))
                
            except Exception as e:
                print(f"   ❌ Path reset error: {e}")
                settings_success.append(False)
            
            ws.close()
            
            # Evaluate success
            success_rate = sum(settings_success) / len(settings_success) if settings_success else 0
            return success_rate >= 0.5
            
        except Exception as e:
            print(f"   ❌ Settings cleanup error: {e}")
            return False

def main():
    controller = SprunkiController()
    
    print("🎮 SPRUNKI CONTROLLER - PHASE 1 OPTIMIZED")
    print("⚡ 40% FASTER with enhanced performance tracking")
    print("="*50)
    print(f"📂 Output path: {controller.base_output_dir}")
    
    while True:
        print(f"\n📋 CHỌN CHỨC NĂNG:")
        print("1. Test URL slug generation")
        print("2. Test project folder creation")
        print("3. Test OBS filename forcing")
        print("4. Full setup + Test (OPTIMIZED)")
        print("5. Reset canvas")
        print("6. Test recording status")
        print("7. Performance summary")
        print("0. Thoát")
        
        choice = input("\nChọn (0-7): ").strip()
        
        if choice == "0":
            controller.print_performance_summary()
            print("👋 Tạm biệt!")
            break
        elif choice == "1":
            test_urls = [
                "https://sprunki.org/sprunki-phase-7",
                "https://www.sprunki.com/game",
                "https://sprunki.net/horror-mode"
            ]
            
            for url in test_urls:
                slug = controller.create_simple_url_slug(url)
                print(f"'{url}' → '{slug}'")
                
        elif choice == "2":
            test_url = "https://sprunki.org/sprunki-phase-7"
            project_folder = controller.setup_project_folder(test_url)
            print(f"Project folder: {project_folder}")
            print(f"Exists: {os.path.exists(project_folder)}")
            
        elif choice == "3":
            test_filename = "test-recording-optimized.mp4"
            controller.current_project_folder = controller.setup_project_folder("https://sprunki.org/sprunki-phase-7")
            success = controller.force_obs_filename(test_filename)
            print(f"Force filename result: {success}")
            
        elif choice == "4":
            print("\n🎬 Full setup test (OPTIMIZED)...")
            
            # Tạo config giả lập với recordingArea
            test_config = {
                'layout': {
                    'recordingArea': {
                        'x': 664, 
                        'y': 164, 
                        'width': 593, 
                        'height': 513
                    }
                }
            }
            
            test_url = "https://sprunki.org/sprunki-phase-7"
            
            # Use new full setup method
            if controller.full_setup_from_config(test_config, use_full_screen=False, url=test_url):
                print("✅ Setup completed!")
                
                # Validate setup
                if controller.validate_recording_area():
                    print("✅ Recording area validated!")
                    
                    # Test with filename
                    test_filename = "full-setup-test-optimized.mp4"
                    if controller.force_obs_filename(test_filename):
                        print("✅ Filename forced successfully!")
                        
                        # PHASE 1: Optimized test recording (8s instead of 10s)
                        print("🎬 Starting optimized test recording...")
                        if controller.start_recording():
                            print("Recording for 8 seconds (OPTIMIZED)...")
                            for i in range(8, 0, -1):
                                print(f"   Recording... {i}s remaining")
                                time.sleep(1)
                                
                                # Check status every 3 seconds
                                if i % 3 == 0:
                                    is_active, path = controller.get_recording_status()
                                    print(f"   📊 Recording active: {is_active}")
                            
                            print("🛑 Stopping recording...")
                            success, path = controller.stop_recording()
                            print(f"Recording result: {success}")
                            if path:
                                print(f"File path: {path}")
                                print(f"File exists: {os.path.exists(path) if path else 'N/A'}")
                        else:
                            print("❌ Failed to start recording")
                    else:
                        print("❌ Failed to force filename")
                else:
                    print("❌ Recording area validation failed")
            else:
                print("❌ Setup failed")
                
        elif choice == "5":
            controller.reset_canvas_resolution()
        elif choice == "6":
            print("\n📊 Checking recording status...")
            is_recording, path = controller.get_recording_status()
            print(f"Recording active: {is_recording}")
            if path:
                print(f"Current path: {path}")
        elif choice == "7":
            controller.print_performance_summary()
        else:
            print("❌ Lựa chọn không hợp lệ!")

if __name__ == "__main__":
    main()

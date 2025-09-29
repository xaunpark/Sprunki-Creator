# sprunki_ultimate_master.py - UNIFIED MASTER CONTROLLER
# Thay thế cả sprunki_master.py và sprunki_master_enhanced.py
# QUY TRÌNH: URL List → Auto JSON Generation → Batch Processing → Video Output

import json
import time
import threading
import os
import logging
from tkinter import Tk, Button, Label, StringVar, messagebox, filedialog, Frame, Listbox, Scrollbar, Text, Entry, Toplevel, Canvas, Label, StringVar, messagebox, filedialog, Frame, Listbox, Scrollbar, Text, Entry, Toplevel, Canvas, Toplevel, Canvas
from tkinter.ttk import Progressbar, Separator, Combobox
from urllib.parse import urlparse
import keyboard
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from pathlib import Path
from config_manager import initialize_config, get_config

# Import required modules
try:
    from sprunki_game_player import SprunkiGamePlayer
    from sprunki_obs_controller import SprunkiController
    from json_generator import SprunkiJSONGenerator
except ImportError as e:
    print(f"❌ Import error: {e}")
    print("Make sure all required files are in the same directory!")
    exit(1)

# ================================================================
# == PROCESSING MODES                                          ==
# ================================================================

MODE_OPTIONS = {
    "both": {
        "display": "💎 Both Modes",
        "description": "Master + Individual (complete package with remixes)"
    },
    "master": {
        "display": "🎬 Master Mode", 
        "description": "1 video dài chứa toàn bộ workflow (1920x1080)"
    },
    "individual": {
        "display": "🎭 Individual Mode",
        "description": "Icons riêng lẻ (recordingArea) + 3 remixes (1920x1080, 20s/bản)"
    },
    "skip": {
        "display": "⭐ Skip Item",
        "description": "Bỏ qua item này"
    }
}

# ================================================================
# == DATA STRUCTURES                                           ==
# ================================================================

@dataclass
class ProcessingItem:
    """Represents one URL/JSON + processing settings"""
    source_url: Optional[str] = None
    json_path: Optional[str] = None
    json_data: Optional[Dict[str, Any]] = None
    mode: str = "both"
    status: str = "pending"
    error_message: Optional[str] = None
    output_files: List[str] = field(default_factory=list)
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    project_slug: Optional[str] = None
    
    json_generation_status: str = "not_started" # "success", "failure", "skipped"
    master_mode_status: str = "not_started"     # "success", "failure", "skipped"
    individual_mode_status: str = "not_started" # "success", "failure", "skipped"

    workflow_report: Optional[Dict[str, Any]] = None

    @property
    def display_name(self) -> str:
        if self.source_url:
            return f"URL: {self.source_url.split('/')[-1] or 'Game'}"
        elif self.json_path:
            return f"JSON: {os.path.basename(self.json_path)}"
        else:
            return "Unknown Item"
    
    @property
    def status_emoji(self) -> str:
        status_map = {
            "pending": "⏳",
            "generating": "🚀", 
            "processing": "🔄",
            "completed": "✅",
            "failed": "❌",
            "skipped": "⭐"
        }
        return status_map.get(self.status, "❓")
    
    @property
    def mode_emoji(self) -> str:
        mode_map = {
            "master": "🎬",
            "individual": "🎭",
            "both": "💎",
            "skip": "⭐"
        }
        return mode_map.get(self.mode, "❓")

@dataclass
class ProcessingConfig:
    """Configuration for processing"""
    output_base_dir: str
    max_retries: int = 2
    auto_skip_on_error: bool = True
    pause_between_items: int = 5  # seconds

# ================================================================
# == Safe Operation Manager                                     ==
# ================================================================

class SafeOperationManager:
    """Quản lý operations với automatic cleanup"""
    
    def __init__(self, game_player, obs_controller, logger):
        self.game_player = game_player
        self.obs_controller = obs_controller
        self.logger = logger
        self.active_slots = []  # Track slots có icons
        
    def safe_single_icon_operation(self, icon, target_slot, filename, recording_duration=14):
        """
        Safe operation cho 1 icon với full cleanup
        Returns: (success, output_path, error_message)
        """
        icon_placed = False
        recording_started = False
        output_path = None
        
        try:
            # STEP 1: Drag icon
            self.logger.info(f"🎯 Dragging icon to slot index {target_slot.get('index', 'unknown')} at coords ({target_slot.get('x', 'N/A')}, {target_slot.get('y', 'N/A')})")
            drag_success = self.game_player.corrected_drag_and_drop(icon, target_slot)
            
            if not drag_success:
                return False, None, "Failed to drag icon"
            
            icon_placed = True
            self.active_slots.append(target_slot)
            self.logger.info(f"✅ Icon placed successfully in slot index {target_slot.get('index', 'unknown')}")
            self.logger.info(f"✅ Slot {target_slot.get('index', 'unknown')} added to active_slots for cleanup tracking")
            
            # STEP 2: OBS filename setup
            self.logger.info(f"🎬 Setting up OBS filename: {filename}")
            if not self.obs_controller.force_obs_filename(filename):
                raise Exception("OBS filename setup failed")
            
            # STEP 3: Start recording  
            self.logger.info(f"🔴 Starting recording...")
            if not self.obs_controller.start_recording():
                raise Exception("OBS recording start failed")
            
            recording_started = True
            self.logger.info(f"✅ Recording started successfully")
            
            # STEP 4: Record for duration
            self.logger.info(f"⏱️ Recording for {recording_duration} seconds...")
            for countdown in range(recording_duration, 0, -1):
                if self.game_player.should_stop:
                    raise Exception("Emergency stop during recording")
                time.sleep(1)
            
            # STEP 5: Stop recording
            self.logger.info(f"🛑 Stopping recording...")
            stop_success, output_path = self.obs_controller.stop_recording()
            
            if not stop_success:
                raise Exception("Failed to stop recording properly")
            
            recording_started = False  # Recording stopped successfully
            self.logger.info(f"✅ Recording completed: {output_path}")
            
            return True, output_path, None
            
        except Exception as e:
            error_msg = f"Operation failed: {str(e)}"
            self.logger.error(error_msg)
            
            return False, output_path, error_msg
            
        finally:
            # 🔥 CRITICAL: ALWAYS CLEANUP
            self._cleanup_operation(icon_placed, recording_started, target_slot)
    
    def safe_remix_operation(self, remix_icons, slots, filename, recording_duration=20):
        """
        Safe operation cho remix với multiple icons cleanup
        Returns: (success, output_path, error_message)
        """
        icons_placed = []
        recording_started = False
        output_path = None
        
        try:
            # STEP 1: Drag all icons
            self.logger.info(f"🎯 Dragging {len(remix_icons)} icons for remix...")
            
            for slot_position, icon_index in enumerate(remix_icons):
                if slot_position >= len(slots):
                    continue
                    
                current_icon = self.game_player.config['layout']['icons'][icon_index]
                target_slot = slots[slot_position]
                
                drag_success = self.game_player.corrected_drag_and_drop(current_icon, target_slot, False)
                
                if not drag_success:
                    raise Exception(f"Failed to drag icon {icon_index} to slot {slot_position}")
                
                icons_placed.append(target_slot)
                self.active_slots.append(target_slot)
                time.sleep(0.3)
            
            self.logger.info(f"✅ All {len(icons_placed)} icons placed successfully")
            
            # STEP 2: OBS setup
            self.logger.info(f"🎬 Setting up OBS for remix: {filename}")
            if not self.obs_controller.force_obs_filename(filename):
                raise Exception("OBS filename setup failed")
            
            if not self.obs_controller.start_recording():
                raise Exception("OBS recording start failed")
            
            recording_started = True
            self.logger.info(f"✅ Remix recording started")
            
            # STEP 3: Record remix
            self.logger.info(f"⏱️ Recording remix for {recording_duration} seconds...")
            for countdown in range(recording_duration, 0, -1):
                if self.game_player.should_stop:
                    raise Exception("Emergency stop during remix recording")
                if countdown % 5 == 0:
                    self.logger.info(f"      Remix recording: {countdown}s remaining")
                time.sleep(1)
            
            # STEP 4: Stop recording
            stop_success, output_path = self.obs_controller.stop_recording()
            
            if not stop_success:
                raise Exception("Failed to stop remix recording")
            
            recording_started = False
            self.logger.info(f"✅ Remix recording completed: {output_path}")
            
            return True, output_path, None
            
        except Exception as e:
            error_msg = f"Remix operation failed: {str(e)}"
            self.logger.error(error_msg)
            return False, output_path, error_msg
            
        finally:
            # 🔥 CRITICAL: CLEANUP ALL ICONS
            self._cleanup_remix_operation(icons_placed, recording_started)
    
    def _cleanup_operation(self, icon_placed, recording_started, target_slot):
        """Cleanup cho single icon operation"""
        try:
            # Stop recording if still active
            if recording_started:
                self.logger.info("🛑 Emergency: Stopping active recording...")
                try:
                    self.obs_controller.stop_recording()
                except Exception as e:
                    self.logger.error(f"Failed to emergency stop recording: {e}")
            
            # Return icon if placed
            if icon_placed and target_slot:
                slot_index = target_slot.get('index', 'unknown')
                self.logger.info(f"🔄 Cleanup: Returning icon from slot index {slot_index}")
                self.logger.info(f"🔄 Attempting return at coords ({target_slot.get('x', 'N/A')}, {target_slot.get('y', 'N/A')})")
                try:
                    return_success = self.game_player.corrected_return_icon(target_slot)
                    if return_success:
                        self.logger.info(f"✅ Icon returned successfully from slot index {slot_index}")
                        if target_slot in self.active_slots:
                            self.active_slots.remove(target_slot)
                            self.logger.info(f"✅ Slot index {slot_index} removed from active_slots")
                    else:
                        self.logger.warning(f"⚠️ Failed to return icon from slot index {slot_index}")
                except Exception as e:
                    self.logger.error(f"Error returning icon from slot index {slot_index}: {e}")
            
        except Exception as e:
            self.logger.error(f"Error during cleanup: {e}")
    
    def _cleanup_remix_operation(self, icons_placed, recording_started):
        """Cleanup cho remix operation với multiple icons"""
        try:
            # Stop recording if still active
            if recording_started:
                self.logger.info("🛑 Emergency: Stopping active remix recording...")
                try:
                    self.obs_controller.stop_recording()
                except Exception as e:
                    self.logger.error(f"Failed to emergency stop remix recording: {e}")
            
            # Return all placed icons
            if icons_placed:
                self.logger.info(f"🔄 Cleanup: Returning {len(icons_placed)} icons from remix...")
                for slot in icons_placed:
                    try:
                        return_success = self.game_player.corrected_return_icon(slot)
                        if return_success and slot in self.active_slots:
                            self.active_slots.remove(slot)
                        time.sleep(0.1)
                    except Exception as e:
                        self.logger.error(f"Error returning icon from slot during cleanup: {e}")
                
                self.logger.info("✅ Remix cleanup completed")
            
        except Exception as e:
            self.logger.error(f"Error during remix cleanup: {e}")
    
    def emergency_cleanup_all(self):
        """Emergency cleanup tất cả active slots"""
        try:
            self.logger.info(f"🚨 EMERGENCY CLEANUP: {len(self.active_slots)} active slots")
            
            # Stop any active recording
            try:
                self.obs_controller.stop_recording()
            except:
                pass
            
            # Return all active slots
            for slot in self.active_slots.copy():
                try:
                    self.game_player.corrected_return_icon(slot)
                    self.active_slots.remove(slot)
                    time.sleep(0.1)
                except:
                    pass
            
            self.logger.info("✅ Emergency cleanup completed")
            
        except Exception as e:
            self.logger.error(f"Error during emergency cleanup: {e}")

# ================================================================
# == UNIFIED PROCESSING MANAGER                                ==
# ================================================================

class UnifiedProcessingManager:
    """Manages the complete pipeline: URLs → JSON Generation → Video Processing"""
    
    def __init__(self, master_controller, stop_event=None):
        self.master_controller = master_controller
        self.stop_event = stop_event or threading.Event()
        self.queue: List[ProcessingItem] = []
        self.config: Optional[ProcessingConfig] = None
        self.current_item_index: int = 0
        self.is_running: bool = False
        self.should_stop: bool = False
        self.is_paused: bool = False
        
        # JSON Generator
        self.json_generator = SprunkiJSONGenerator(master_controller)
        
        # Statistics
        self.total_completed: int = 0
        self.total_failed: int = 0
        self.total_skipped: int = 0
        
        self.setup_logging()
    
    def setup_logging(self):
        """Setup logging"""
        self.logger = logging.getLogger('UnifiedProcessingManager')
        self.logger.setLevel(logging.DEBUG)  # Include all log levels
        
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        self.logger.addHandler(console_handler)
        
        # Store references to file handlers for cleanup
        self.current_file_handlers = []
    
    def _setup_mode_specific_logging(self, project_folder, project_slug, mode):
        """Setup separate log files for each mode in project folder"""
        try:
            # Clear any existing file handlers
            for handler in self.current_file_handlers:
                self.logger.removeHandler(handler)
                handler.close()
            self.current_file_handlers = []
            
            # Create log files based on mode
            formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
            
            if mode == "master":
                # Only master log for master mode
                log_file = os.path.join(project_folder, f"{project_slug}-master.log")
                file_handler = logging.FileHandler(log_file, mode='w', encoding='utf-8')
                file_handler.setLevel(logging.DEBUG)  # Include all log levels
                file_handler.setFormatter(formatter)
                self.logger.addHandler(file_handler)
                self.current_file_handlers.append(file_handler)
                self.logger.info(f"📝 Master mode log: {log_file}")
                
            elif mode == "individual":
                # Only individual log for individual mode
                log_file = os.path.join(project_folder, f"{project_slug}-individual.log")
                file_handler = logging.FileHandler(log_file, mode='w', encoding='utf-8')
                file_handler.setLevel(logging.DEBUG)  # Include all log levels
                file_handler.setFormatter(formatter)
                self.logger.addHandler(file_handler)
                self.current_file_handlers.append(file_handler)
                self.logger.info(f"📝 Individual mode log: {log_file}")
                
            elif mode == "both":
                # Both master and individual logs for both mode
                master_log = os.path.join(project_folder, f"{project_slug}-master.log")
                individual_log = os.path.join(project_folder, f"{project_slug}-individual.log")
                
                # Master log handler
                master_handler = logging.FileHandler(master_log, mode='w', encoding='utf-8')
                master_handler.setLevel(logging.DEBUG)  # Include all log levels
                master_handler.setFormatter(formatter)
                self.logger.addHandler(master_handler)
                self.current_file_handlers.append(master_handler)
                
                # Individual log handler  
                individual_handler = logging.FileHandler(individual_log, mode='w', encoding='utf-8')
                individual_handler.setLevel(logging.DEBUG)  # Include all log levels
                individual_handler.setFormatter(formatter)
                self.logger.addHandler(individual_handler)
                self.current_file_handlers.append(individual_handler)
                
                self.logger.info(f"📝 Both mode logs: {master_log}")
                self.logger.info(f"📝                  {individual_log}")
            
        except Exception as e:
            self.logger.error(f"Failed to setup mode-specific logging: {e}")
    
    def _switch_to_mode_logging(self, project_folder, project_slug, target_mode):
        """Switch to specific mode logging for both mode"""
        try:
            # Remove all current file handlers
            for handler in self.current_file_handlers:
                self.logger.removeHandler(handler)
                handler.close()
            self.current_file_handlers = []
            
            # Add only the target mode handler
            formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
            
            if target_mode == "master":
                log_file = os.path.join(project_folder, f"{project_slug}-master.log")
                mode_text = "MASTER"
            else:  # individual
                log_file = os.path.join(project_folder, f"{project_slug}-individual.log")
                mode_text = "INDIVIDUAL"
            
            file_handler = logging.FileHandler(log_file, mode='a', encoding='utf-8')  # Append mode
            file_handler.setLevel(logging.DEBUG)  # Include all log levels
            file_handler.setFormatter(formatter)
            self.logger.addHandler(file_handler)
            self.current_file_handlers.append(file_handler)
            
            self.logger.info(f"📝 Switched to {mode_text} mode logging: {os.path.basename(log_file)}")
            
        except Exception as e:
            self.logger.error(f"Failed to switch to {target_mode} logging: {e}")
    
    def _cleanup_logging_handlers(self):
        """Cleanup file handlers after processing"""
        try:
            for handler in self.current_file_handlers:
                self.logger.removeHandler(handler)
                handler.close()
            self.current_file_handlers = []
            self.logger.info("📝 Log files closed and finalized")
        except Exception as e:
            self.logger.error(f"Failed to cleanup logging handlers: {e}")

    def _generate_manifest_for_item(self, item: ProcessingItem):
        """
        Thu thập dữ liệu và tạo file manifest.json cho một item đã xử lý xong.
        """
        try:
            self.logger.info(f"📄 Generating manifest for {item.project_slug}...")
            
            if not self.config or not item.json_data:
                self.logger.error("Cannot generate manifest: Missing config or json_data.")
                return

            # Xác định thư mục output
            project_slug = item.project_slug or f"unknown-project-{datetime.now().strftime('%Y%m%d%H%M%S')}"
            project_folder = os.path.join(self.config.output_base_dir, project_slug)
            
            if not os.path.exists(project_folder):
                self.logger.warning(f"Project folder not found for manifest: {project_folder}, creating it.")
                os.makedirs(project_folder, exist_ok=True)
            
            # 1. Thu thập thông tin
            page_title = item.json_data.get('pageTitle', 'Unknown Title')
            source_url = item.json_data.get('currentUrl', item.source_url or '')
            domain = urlparse(source_url).netloc.replace('www.', '') if source_url else ''

            # Xác định overallStatus với logic nghiêm ngặt hơn
            if item.status == "completed":
                # Nếu cả hai mode đều thành công (trong chế độ "both"), hoặc mode đơn lẻ thành công
                if (item.mode == "both" and item.master_mode_status == "success" and item.individual_mode_status == "success") or \
                   (item.mode != "both" and item.status == "completed"):
                    overall_status = "success"
                else:
                    # Trường hợp "both" nhưng chỉ một mode thành công
                    overall_status = "partial_success"
            elif item.status == "failed":
                # Nếu job thất bại, nhưng một trong hai mode lại thành công (trường hợp hiếm)
                if item.mode == "both" and (item.master_mode_status == "success" or item.individual_mode_status == "success"):
                    overall_status = "partial_success"
                else:
                    overall_status = "failure"
            else:
                overall_status = item.status # pending, processing...

            # 2. Xây dựng cấu trúc manifest
            manifest_data = {
                "version": "1.1",
                "sessionId": f"{item.project_slug}-{item.start_time.strftime('%Y%m%d%H%M%S')}" if item.start_time else "unknown",
                "generatedAt": datetime.now().isoformat(),
                "source": { "type": "url" if item.source_url else "json_file", "value": item.source_url or item.json_path },
                "projectInfo": {
                    "sourceUrl": source_url, "projectSlug": item.project_slug,
                    "pageTitle": page_title, "domain": domain, "outputDirectory": project_folder
                },
                "layoutSummary": {
                    "recordingArea": item.json_data.get('layout', {}).get('recordingArea'),
                    "totalIcons": len(item.json_data.get('layout', {}).get('icons', [])),
                    "totalSlots": len(item.json_data.get('layout', {}).get('slots', []))
                },
                "processingSummary": {
                    "mode": item.mode, "overallStatus": overall_status,
                    "statusDetails": {
                        "jsonGeneration": item.json_generation_status,
                        "masterMode": item.master_mode_status,
                        "individualMode": item.individual_mode_status
                    },
                    "startTime": item.start_time.isoformat() if item.start_time else None,
                    "endTime": item.end_time.isoformat() if item.end_time else None,
                    "durationSeconds": (item.end_time - item.start_time).total_seconds() if item.start_time and item.end_time else 0,
                    "hasHorror": item.json_data.get('gameSettings', {}).get('hasHorror', False)
                },
                "outputs": {}, "errors": []
            }
            
            # 3. Điền thông tin outputs và errors
            outputs = {"masterVideo": None, "individualVideos": [], "remixVideos": [], "logFiles": [], "sourceJson": None}
            if item.error_message:
                manifest_data["errors"].append({"timestamp": datetime.now().isoformat(), "stage": "general", "message": item.error_message})
            
            for file_path in item.output_files:
                filename = os.path.basename(file_path)
                file_info = {
                    "status": "success", "filename": filename, "relativePath": f"./{filename}",
                    "fileSizeMb": round(os.path.getsize(file_path) / (1024*1024), 2) if os.path.exists(file_path) else 0
                }
                if 'showcase' in filename:
                    outputs['masterVideo'] = file_info
                elif 'remix' in filename:
                    outputs['remixVideos'].append(file_info)
                else:
                    outputs['individualVideos'].append(file_info)

            if item.json_path:
                outputs['sourceJson'] = os.path.basename(item.json_path)
            
            log_slug = f"{item.project_slug}"
            if os.path.exists(os.path.join(project_folder, f"{log_slug}-master.log")):
                outputs['logFiles'].append({"type": "master", "filename": f"{log_slug}-master.log"})
            if os.path.exists(os.path.join(project_folder, f"{log_slug}-individual.log")):
                outputs['logFiles'].append({"type": "individual", "filename": f"{log_slug}-individual.log"})

            manifest_data['outputs'] = outputs

            # 4. Ghi file
            manifest_path = os.path.join(project_folder, "manifest.json")
            with open(manifest_path, 'w', encoding='utf-8') as f:
                json.dump(manifest_data, f, indent=2, ensure_ascii=False)
            
            self.logger.info(f"✅ Manifest file created successfully: {manifest_path}")
            return manifest_data

        except Exception as e:
            self.logger.error(f"❌ Failed to generate manifest file: {e}")
            return None

    def _calculate_center_slot_index(self, slots):
        """Calculate center slot index for showcase"""
        try:
            total_slots = len(slots)
            if total_slots == 0:
                return 0
            
            # Calculate center index (0-based)
            # For odd numbers: middle slot
            # For even numbers: slightly left of middle 
            center_index = (total_slots - 1) // 2
            
            self.logger.info(f"📊 Slot calculation: {total_slots} slots → center index {center_index}")
            self.logger.info(f"   Examples: 7 slots → index 3, 9 slots → index 4, 6 slots → index 2")
            
            return center_index
            
        except Exception as e:
            self.logger.error(f"Error calculating center slot: {e}")
            return 3  # Fallback to hardcoded value
    
    def add_urls(self, urls: List[str]) -> int:
        """Add URLs to processing queue"""
        added_count = 0
        
        for url in urls:
            url = url.strip()
            if not url:
                continue
                
            if not url.startswith(('http://', 'https://')):
                self.logger.warning(f"Invalid URL: {url}")
                continue
            
            # Check if URL already in queue
            if any(item.source_url == url for item in self.queue):
                self.logger.info(f"URL already in queue: {url}")
                continue
            
            project_slug = self._create_project_slug_from_url(url)
            
            item = ProcessingItem(
                source_url=url,
                mode="both",
                project_slug=project_slug
            )
            
            self.queue.append(item)
            added_count += 1
            self.logger.info(f"Added URL to queue: {url}")
        
        return added_count
    
    def add_json_files(self, file_paths: List[str]) -> int:
        """Add existing JSON files to processing queue"""
        added_count = 0
        
        for file_path in file_paths:
            try:
                if not os.path.exists(file_path):
                    self.logger.warning(f"File not found: {file_path}")
                    continue
                
                if any(item.json_path == file_path for item in self.queue):
                    self.logger.info(f"File already in queue: {os.path.basename(file_path)}")
                    continue
                
                # Load and validate JSON
                with open(file_path, 'r', encoding='utf-8') as f:
                    json_data = json.load(f)
                
                if not self._validate_json_data(json_data):
                    self.logger.warning(f"Invalid JSON structure: {os.path.basename(file_path)}")
                    continue
                
                project_slug = self._create_project_slug_from_url(json_data.get('currentUrl', ''))
                
                item = ProcessingItem(
                    json_path=file_path,
                    json_data=json_data,
                    mode="both",
                    project_slug=project_slug
                )
                
                self.queue.append(item)
                added_count += 1
                self.logger.info(f"Added JSON to queue: {os.path.basename(file_path)}")
                
            except Exception as e:
                self.logger.error(f"Error adding JSON file {file_path}: {e}")
        
        return added_count
    
    def _validate_json_data(self, json_data: Dict[str, Any]) -> bool:
        """Validate JSON data structure"""
        try:
            required_fields = ['currentUrl', 'layout']
            for field in required_fields:
                if field not in json_data:
                    return False
            
            layout = json_data['layout']
            required_layout = ['icons', 'slots']
            for field in required_layout:
                if field not in layout:
                    return False
            
            return True
        except:
            return False
    
    def _create_project_slug_from_url(self, url: str) -> str:
        """
        [FIXED] Tạo URL slug duy nhất bằng cách kết hợp tên tác giả và tên dự án.
        Ví dụ: /@author/project-name -> author_project-name
        """
        try:
            from urllib.parse import urlparse
            import re
            
            parsed_url = urlparse(url)
            # Tách path thành các phần, loại bỏ các phần tử rỗng
            path_parts = [part for part in parsed_url.path.strip('/').split('/') if part]
            
            slug_base = ""
            if len(path_parts) >= 2 and path_parts[0].startswith('@'):
                # Trường hợp chuẩn: /@author/project-name
                author = path_parts[0][1:]
                project_name = path_parts[1]
                slug_base = f"{author}_{project_name}"
            elif len(path_parts) > 0:
                # Trường hợp khác
                slug_base = "_".join(path_parts)
            else:
                domain = parsed_url.netloc.lower().replace('www.', '')
                slug_base = domain.split('.')[0]
            
            slug = re.sub(r'[^a-zA-Z0-9\-_]', '-', slug_base)
            slug = re.sub(r'[-_]+', '-', slug)
            slug = slug.strip('-')
            slug = slug[:70]
            
            return slug or f"sprunki-game-{len(self.queue)}"
            
        except Exception:
            return f"sprunki-game-{len(self.queue)}"
    
    def remove_queue_item(self, index: int) -> bool:
        """Remove item from queue by index"""
        try:
            if 0 <= index < len(self.queue):
                removed_item = self.queue.pop(index)
                self.logger.info(f"Removed from queue: {removed_item.display_name}")
                return True
            return False
        except Exception as e:
            self.logger.error(f"Error removing queue item {index}: {e}")
            return False
    
    def set_item_mode(self, index: int, mode: str) -> bool:
        """Set processing mode for queue item"""
        try:
            if 0 <= index < len(self.queue):
                self.queue[index].mode = mode
                self.logger.info(f"Set mode '{mode}' for: {self.queue[index].display_name}")
                return True
            return False
        except Exception as e:
            self.logger.error(f"Error setting item mode: {e}")
            return False
    
    def get_queue_summary(self) -> Dict[str, Any]:
        """Get queue summary statistics"""
        total_items = len(self.queue)
        pending_items = sum(1 for item in self.queue if item.status == "pending")
        completed_items = sum(1 for item in self.queue if item.status == "completed")
        failed_items = sum(1 for item in self.queue if item.status == "failed")
        skipped_items = sum(1 for item in self.queue if item.status == "skipped")
        generating_items = sum(1 for item in self.queue if item.status == "generating")
        processing_items = sum(1 for item in self.queue if item.status == "processing")
        
        return {
            "total_items": total_items,
            "pending": pending_items,
            "generating": generating_items,
            "processing": processing_items,
            "completed": completed_items,
            "failed": failed_items,
            "skipped": skipped_items,
            "processing_status": "Running" if self.is_running else ("Paused" if self.is_paused else "Stopped")
        }
    
    def start_processing(self, config: ProcessingConfig) -> bool:
        """Start the unified processing pipeline"""
        if self.is_running:
            self.logger.warning("Processing already running!")
            return False
        
        if not self.queue:
            self.logger.warning("No items in queue!")
            return False
        
        self.config = config
        self.is_running = True
        self.should_stop = False
        self.is_paused = False
        self.current_item_index = 0
        
        # Reset statistics
        self.total_completed = 0
        self.total_failed = 0
        self.total_skipped = 0
        
        # Create output directory
        os.makedirs(config.output_base_dir, exist_ok=True)
        
        self.logger.info("Starting unified processing pipeline")
        self.logger.info(f"Output directory: {config.output_base_dir}")
        
        # [REFACTORED & FIXED] Start processing on the main thread via master_controller
        self.master_controller.root.after(100, self._process_next_queue_item) # Start after 100ms
        
        return True
    
    def stop_processing(self):
        """Stop processing"""
        self.should_stop = True
        self.is_running = False
        self.is_paused = False
        
        if self.master_controller:
            self.master_controller.should_stop = True
            if hasattr(self.master_controller, 'game_player') and self.master_controller.game_player:
                self.master_controller.game_player.should_stop = True
        
        self.logger.info("Processing stopped by user")
    
    def _process_next_queue_item(self):
        item = None
        try:
            if self.should_stop:
                self.master_controller.on_processing_complete()
                return

            if self.current_item_index >= len(self.queue):
                self.master_controller.on_processing_complete()
                return

            item = self.queue[self.current_item_index]
            
            if item.status != "pending":
                self.current_item_index += 1
                self.master_controller.root.after(10, self._process_next_queue_item)
                return

            self.logger.info(f"--- Processing item {self.current_item_index + 1}/{len(self.queue)}: {item.display_name} ---")
            
            if item.source_url and not item.json_data:
                json_success = self._generate_json_for_item(item)
                if not json_success: # Dừng lại nếu tạo JSON lỗi
                    self.total_failed += 1
                    return # Đi thẳng tới finally
            
            if item.json_data and item.status != "failed":
                video_success = self._process_video_for_item(item)
                if video_success: self.total_completed += 1
                else:
                    if item.status == "failed": self.total_failed += 1
                    elif item.status == "skipped": self.total_skipped += 1
            
        except Exception as e:
            self.logger.error(f"Critical error in processing pipeline: {e}")
            if item:
                item.status = "failed"
                item.error_message = f"Critical pipeline error: {e}"
        
        finally:
            # TẠO MANIFEST SAU KHI XỬ LÝ XONG (DÙ THÀNH CÔNG HAY THẤT BẠI)
            if item:
                self._generate_manifest_for_item(item)

            # Lên lịch cho item tiếp theo
            self.current_item_index += 1
            if self.current_item_index < len(self.queue) and not self.should_stop:
                pause_duration = getattr(self.config, 'pause_between_items', 5) * 1000
                self.logger.info(f"Pausing {pause_duration/1000}s before next item...")
                self.master_controller.root.after(pause_duration, self._process_next_queue_item)
            else:
                self.logger.info("--- Reached end of queue ---")
                self.master_controller.on_processing_complete()
    
    def _generate_json_for_item(self, item: ProcessingItem) -> bool:
        """Generate JSON for a URL item"""
        try:
            # Reset the generator's state to ensure no stale WebSocket URLs are used.
            self.json_generator.reset_state_for_new_job()

            item.status = "generating"
            item.start_time = datetime.now()
            
            self.logger.info(f"Generating JSON for: {item.source_url}")
            
            success, json_path, json_data = self.json_generator.generate_complete_json(item.source_url)
            
            if success and json_data:
                item.json_path = json_path
                item.json_data = json_data
                item.status = "pending"  # Ready for video processing
                item.json_generation_status = "success" # GHI LẠI TRẠNG THÁI
                self.logger.info(f"JSON generated successfully: {json_path}")
                return True
            else:
                item.status = "failed"
                item.error_message = "JSON generation failed"
                item.end_time = datetime.now()
                item.json_generation_status = "failure" # GHI LẠI TRẠNG THÁI
                self.logger.error(f"JSON generation failed for: {item.source_url}")
                return False
                
        except Exception as e:
            error_msg = f"JSON generation error: {str(e)}"
            self.logger.error(f"Error for {item.source_url}: {error_msg}", exc_info=True)
            
            item.status = "failed"
            item.error_message = error_msg  # Lưu lỗi chi tiết vào item
            item.end_time = datetime.now()
            item.json_generation_status = "failure"
            return False
    
    def _process_video_for_item(self, item: ProcessingItem) -> bool:
        """Enhanced video processing with proper mode isolation"""
        try:
            item.status = "processing"
            if not item.start_time:
                item.start_time = datetime.now()
            
            # BƯỚC 1: Tạo project_folder một lần duy nhất ở đây, dựa trên slug đã có
            config_manager = self.master_controller.config_manager
            output_base_dir = config_manager.get('output.base_directory', "C:\\Output")
            project_slug = getattr(item, 'project_slug', 'default_project')
            project_folder = os.path.join(output_base_dir, project_slug)
            os.makedirs(project_folder, exist_ok=True)
            self.logger.info(f"✅ [MASTER] Project folder defined: {project_folder}")
            
            # BƯỚC 2: Khởi tạo các component
            if not self.master_controller._initialize_components(item.json_data):
                raise Exception("Failed to initialize components")
            
            # BƯỚC 3: GÁN project_folder cho OBS Controller NGAY LẬP TỨC
            if hasattr(self.master_controller, 'obs_controller') and self.master_controller.obs_controller:
                self.master_controller.obs_controller.current_project_folder = project_folder
                self.logger.info(f"✅ [MASTER] OBS controller's project folder explicitly set to: {project_folder}")
            else:
                self.logger.error("❌ OBS controller not found after initialization!")
                raise Exception("OBS controller not available")
            
            self.logger.info(f"📁 All videos will be saved to: {project_folder}")
            if item.mode == "master":
                self.logger.info(f"   🎬 Master showcase video: {project_slug}-showcase.mp4")
            elif item.mode == "individual":
                self.logger.info(f"   🎯 Individual icon videos: n-1.mp4, h-1.mp4, etc.")
            elif item.mode == "both":
                self.logger.info(f"   🎪 Both modes: showcase + individual icons in same folder")
            
            # Setup mode-specific logging to project folder
            self._setup_mode_specific_logging(project_folder, project_slug, item.mode)
            
            # Process based on mode with enhanced handling
            success = False
            if item.mode == "master":
                self.logger.info(f"🎬 Processing MASTER mode for {item.display_name}")
                success = self._process_master_mode(item)
                
            elif item.mode == "individual":
                self.logger.info(f"🎭 Processing INDIVIDUAL mode for {item.display_name}")
                success = self._process_individual_mode(item)
                
            elif item.mode == "both":
                self.logger.info(f"💎 Processing BOTH mode for {item.display_name}")
                success = self._process_both_mode_enhanced(item)
                
            elif item.mode == "skip":
                self.logger.info(f"⭐ Skipping {item.display_name}")
                item.status = "skipped"
                self.total_skipped += 1
                return True
            
            if success:
                item.status = "completed"
                item.end_time = datetime.now()
                self.logger.info(f"✅ {item.mode.upper()} mode completed successfully for {item.display_name}")
                return True
            else:
                item.status = "failed"
                item.error_message = f"{item.mode.capitalize()} mode processing failed"
                item.end_time = datetime.now()
                self.logger.error(f"❌ {item.mode.upper()} mode failed for {item.display_name}")
                return False
                
        except Exception as e:
            error_msg = f"Video processing error: {str(e)}"
            self.logger.error(f"Error for {item.display_name}: {error_msg}", exc_info=True)
            
            item.status = "failed"
            item.error_message = error_msg # Lưu lỗi chi tiết
            item.end_time = datetime.now()
            return False
        
        finally:
            # ✨ THÊM: Comprehensive OBS cleanup sau mỗi job
            self.logger.info("🧹 Starting comprehensive OBS cleanup after job completion...")
            try:
                if hasattr(self.master_controller, 'obs_controller') and self.master_controller.obs_controller:
                    cleanup_success = self.master_controller.obs_controller.comprehensive_obs_cleanup()
                    if cleanup_success:
                        self.logger.info("✅ Comprehensive OBS cleanup completed successfully")
                    else:
                        self.logger.warning("⚠️ Some OBS cleanup steps may have failed")
                else:
                    self.logger.warning("⚠️ OBS controller not available for cleanup")
            except Exception as cleanup_error:
                self.logger.error(f"❌ Error during comprehensive OBS cleanup: {cleanup_error}")
            
            # Cleanup file handlers after processing
            self._cleanup_logging_handlers()

    def _process_both_mode_enhanced(self, item: ProcessingItem) -> bool:
        """Enhanced Both mode with proper isolation and comprehensive cleanup"""
        master_success = False
        individual_success = False
        master_output_count = 0
        individual_output_count = 0
        
        try:
            self.logger.info("🎭 BOTH MODE: Starting Master + Individual workflow with enhanced isolation")
            self.logger.info(f"📂 Output directory: {self.master_controller.obs_controller.current_project_folder}")
            
            # ================================================================
            # PHASE 1: MASTER MODE (with comprehensive cleanup)
            # ================================================================
            
            # Switch to master logging for this phase
            project_folder = self.master_controller.obs_controller.current_project_folder
            project_slug = getattr(item, 'project_slug', 'default_project')
            self._switch_to_mode_logging(project_folder, project_slug, "master")
            
            self.logger.info("=" * 70)
            self.logger.info("🎬 PHASE 1: MASTER MODE - Full Showcase Recording")
            self.logger.info("=" * 70)
            
            master_start_time = datetime.now()
            
            try:
                # Track output files before Master mode
                initial_output_count = len(item.output_files)
                
                self.logger.info("🎬 Executing Master mode with full cleanup support...")
                master_success = self._process_master_mode(item)
                
                # Calculate Master mode output
                master_output_count = len(item.output_files) - initial_output_count
                
                if master_success:
                    master_duration = (datetime.now() - master_start_time).total_seconds()
                    self.logger.info(f"✅ Master mode completed successfully in {master_duration:.1f}s")
                    self.logger.info(f"📁 Master mode generated {master_output_count} output file(s)")
                    
                    # CRITICAL: Wait for OBS to finalize Master video file
                    self.logger.info("⏳ CRITICAL: Waiting for OBS to finalize Master video file...")
                    self.logger.info("   This prevents file conflicts when switching to Individual mode")
                    
                    finalization_duration = 5
                    for countdown in range(finalization_duration, 0, -1):
                        if self.should_stop:
                            self.logger.warning("🛑 Emergency stop during OBS finalization wait")
                            self.logger.warning("   Master video may not be properly finalized")
                            return master_success  # Return partial success
                        
                        if countdown % 2 == 0 or countdown <= 2:  # Log every 2 seconds, or final 2 seconds
                            self.logger.info(f"   🔄 Finalizing Master video... {countdown}s remaining")
                        time.sleep(1)
                    
                    self.logger.info("✅ Master video finalization completed - ready for Individual mode")
                    
                else:
                    master_duration = (datetime.now() - master_start_time).total_seconds()
                    self.logger.error(f"❌ Master mode failed after {master_duration:.1f}s")
                    self.logger.error("   Will continue with Individual mode for partial results")
                    
            except Exception as e:
                master_duration = (datetime.now() - master_start_time).total_seconds()
                self.logger.error(f"💥 Master mode exception after {master_duration:.1f}s: {e}")
                self.logger.error("   Treating as Master mode failure, continuing with Individual mode")
                master_success = False
            
            # ================================================================
            # CRITICAL: BROWSER STATE ISOLATION AND CLEANUP
            # ================================================================
            self.logger.info("=" * 70)
            self.logger.info("🔄 BROWSER STATE ISOLATION - Ensuring Clean Slate")
            self.logger.info("=" * 70)
            
            cleanup_start_time = datetime.now()
            
            try:
                self.logger.info("🌐 Step 1: Closing Master mode browser session...")
                
                # Always ensure browser is closed after Master mode, regardless of success/failure
                if self.master_controller.game_player:
                    try:
                        # Stop any ongoing game operations
                        self.master_controller.game_player.should_stop = True
                        self.master_controller.game_player.is_playing = False
                        
                        # Force close browser window and cleanup resources
                        browser_close_success = self.master_controller.game_player.close_game_window()
                        
                        if browser_close_success:
                            self.logger.info("✅ Master mode browser closed successfully")
                        else:
                            self.logger.warning("⚠️ Browser close returned false, but continuing...")
                        
                    except Exception as e:
                        self.logger.error(f"❌ Error closing Master mode browser: {e}")
                        self.logger.warning("   Continuing with Individual mode despite browser cleanup issues")
                
                self.logger.info("⏳ Step 2: Waiting for complete browser resource cleanup...")
                
                # OPTIMIZED: Reduce wait time and improve feedback.
                # The game_player.close_game_window() already performs a thorough cleanup with verification.
                # This shorter wait is just an extra safety buffer.
                browser_cleanup_duration = 10  # Reduced from 60s to 10s.
                for countdown in range(browser_cleanup_duration, 0, -1):
                    if self.should_stop:
                        self.logger.warning("🛑 Emergency stop during browser cleanup wait")
                        return master_success
                    
                    # Improved logging for better feedback every 2 seconds.
                    if countdown % 2 == 0 or countdown <= 2:
                        self.logger.info(f"   🧹 Browser cleanup cooldown... {countdown}s remaining")
                    time.sleep(1)
                
                self.logger.info("✅ Browser state isolation completed - clean slate ready")
                
                cleanup_duration = (datetime.now() - cleanup_start_time).total_seconds()
                self.logger.info(f"🔄 Isolation phase completed in {cleanup_duration:.1f}s")
                
            except Exception as e:
                cleanup_duration = (datetime.now() - cleanup_start_time).total_seconds()
                self.logger.error(f"💥 Browser isolation error after {cleanup_duration:.1f}s: {e}")
                self.logger.warning("   Individual mode may have browser state issues")
            
            # Check for emergency stop before Individual mode
            if self.should_stop:
                self.logger.warning("🛑 Emergency stop detected before Individual mode")
                self.logger.info(f"📊 Partial BOTH mode results: Master={master_success}, Individual=skipped")
                return master_success  # Return partial success based on Master mode
            
            # ================================================================
            # PHASE 2: INDIVIDUAL MODE (with completely fresh browser)
            # ================================================================
            
            # Switch to individual logging for this phase
            self._switch_to_mode_logging(project_folder, project_slug, "individual")
            
            self.logger.info("=" * 70)
            self.logger.info("🎭 PHASE 2: INDIVIDUAL MODE - Fresh Browser Session")
            self.logger.info("=" * 70)
            
            individual_start_time = datetime.now()
            
            try:
                # Track output files before Individual mode
                pre_individual_output_count = len(item.output_files)
                
                self.logger.info("🎭 Executing Individual mode with fresh browser state...")
                self.logger.info("   Browser state: Clean (isolated from Master mode)")
                self.logger.info("   Mode: Individual icons + remixes with enhanced error handling")
                
                individual_success = self._process_individual_mode(item)
                
                # Calculate Individual mode output
                individual_output_count = len(item.output_files) - pre_individual_output_count
                
                if individual_success:
                    individual_duration = (datetime.now() - individual_start_time).total_seconds()
                    self.logger.info(f"✅ Individual mode completed successfully in {individual_duration:.1f}s")
                    self.logger.info(f"📁 Individual mode generated {individual_output_count} output file(s)")
                else:
                    individual_duration = (datetime.now() - individual_start_time).total_seconds()
                    self.logger.error(f"❌ Individual mode failed after {individual_duration:.1f}s")
                    
            except Exception as e:
                individual_duration = (datetime.now() - individual_start_time).total_seconds()
                self.logger.error(f"💥 Individual mode exception after {individual_duration:.1f}s: {e}")
                individual_success = False
            
            # ================================================================
            # FINAL RESULT EVALUATION AND REPORTING
            # ================================================================
            self.logger.info("=" * 70)
            self.logger.info("📊 BOTH MODE: Final Result Evaluation")
            self.logger.info("=" * 70)
            
            total_duration = (datetime.now() - master_start_time).total_seconds()
            total_output_files = master_output_count + individual_output_count
            
            self.logger.info(f"📈 BOTH MODE SUMMARY:")
            self.logger.info(f"   ⏱️ Total duration: {total_duration:.1f}s")
            self.logger.info(f"   🎬 Master mode: {'✅ Success' if master_success else '❌ Failed'} ({master_output_count} files)")
            self.logger.info(f"   🎭 Individual mode: {'✅ Success' if individual_success else '❌ Failed'} ({individual_output_count} files)")
            self.logger.info(f"   📁 Total output files: {total_output_files}")
            self.logger.info(f"   📂 Output directory: {self.master_controller.obs_controller.current_project_folder}")

            # Cập nhật trạng thái chi tiết cho manifest
            item.master_mode_status = "success" if master_success else "failure"
            item.individual_mode_status = "success" if individual_success else "failure"

            # Determine final success status based on strict rules
            final_success = master_success and individual_success

            if final_success:
                self.logger.info("🎉 BOTH MODE: COMPLETE SUCCESS!")
                self.logger.info("   Both Master and Individual modes met their success thresholds.")
            else:
                self.logger.error("💥 BOTH MODE: FAILED TO MEET THRESHOLDS")
                if not master_success:
                    self.logger.error("   - Master mode did not meet success criteria.")
                if not individual_success:
                    self.logger.error("   - Individual mode did not meet success criteria.")
            
            return final_success
                
        except Exception as e:
            error_msg = f"Critical error in BOTH mode processing: {str(e)}"
            self.logger.error(error_msg, exc_info=True)
            self.logger.info(f"📊 Partial results at exception: Master={master_success}, Individual={individual_success}")
            
            # Lưu lỗi vào item để gửi đi
            item.error_message = error_msg
            item.master_mode_status = item.master_mode_status if item.master_mode_status != "not_started" else "failure"
            item.individual_mode_status = item.individual_mode_status if item.individual_mode_status != "not_started" else "failure"
            return False
        
        finally:
            # ================================================================
            # FINAL COMPREHENSIVE CLEANUP
            # ================================================================
            self.logger.info("🧹 BOTH MODE: Final comprehensive cleanup...")
            
            try:
                # Ensure all browser resources are closed
                if self.master_controller.game_player:
                    try:
                        # Final browser cleanup
                        self.master_controller.game_player.should_stop = True
                        self.master_controller.game_player.is_playing = False
                        self.master_controller.game_player.close_game_window()
                        self.logger.info("✅ Final browser cleanup completed")
                    except Exception as e:
                        self.logger.error(f"⚠️ Error in final browser cleanup: {e}")
                
                # Ensure OBS is in clean state
                if self.master_controller.obs_controller:
                    try:
                        # Try to stop any potential lingering recordings
                        self.master_controller.obs_controller.stop_recording()
                    except:
                        # Expected to fail if no recording active
                        pass
                
                self.logger.info("✅ BOTH MODE final cleanup completed")
                
            except Exception as e:
                self.logger.error(f"⚠️ Error during BOTH MODE final cleanup: {e}")
    
    def _process_master_mode(self, item: ProcessingItem) -> bool:
        """Enhanced Master mode that understands WorkflowReport."""
        game_environment_ready = False
        recording_active = False
        browser_opened = False
        
        try:
            master_filename = f"{getattr(item, 'project_slug', 'default_project')}-showcase.mp4"
            self.logger.info(f"Executing master workflow for {item.display_name}")
            
            # Steps 1-4: Setup OBS, Game Environment, and Start Recording (no change)
            if not self.master_controller.obs_controller.full_setup(use_full_screen=True):
                raise Exception("OBS setup failed")
            if not self.master_controller.obs_controller.force_obs_filename(master_filename):
                raise Exception("OBS filename setup failed")
            if not self.master_controller.game_player.setup_game_environment(first_setup=True):
                raise Exception("Game environment setup failed")
            game_environment_ready = True
            browser_opened = True
            if not self.master_controller.obs_controller.start_recording():
                raise Exception("Could not start recording")
            recording_active = True
            
            # STEP 5: Execute Full Showcase Workflow and get detailed report
            self.logger.info("🎮 Executing full showcase workflow...")
            workflow_report = self.master_controller.game_player.execute_full_showcase_workflow_corrected()
            
            # Log any issues from the report
            if workflow_report.issues:
                self.logger.warning("Workflow completed with issues:")
                for issue in workflow_report.issues:
                    self.logger.warning(f"   - {issue}")

            # Intelligent success check based on the report
            if not workflow_report.was_successful:
                # This is a business logic failure (e.g., no icons)
                raise Exception(f"Master workflow failed: {workflow_report.message}")
            
            item.workflow_report = workflow_report
            
            self.logger.info(f"✅ Showcase workflow completed: {workflow_report.message}")
            
            # STEP 6: Stop Recording
            self.logger.info("🛑 Stopping Master recording...")
            stop_success, output_path = self.master_controller.obs_controller.stop_recording()
            recording_active = False
            
            if not stop_success or not output_path:
                raise Exception("Failed to stop recording or no output file generated")
            
            item.output_files.append(output_path)
            self.logger.info(f"✅ Master video saved: {output_path}")
            item.master_mode_status = "success"
            return True
            
        except Exception as e:
            error_msg = f"Master mode workflow failed: {str(e)}"
            self.logger.error(error_msg, exc_info=True)
            
            item.error_message = error_msg # Lưu lỗi chi tiết
            item.master_mode_status = "failure"
            return False
            
        finally:
            self._cleanup_master_mode(recording_active, game_environment_ready, browser_opened)

    def _cleanup_master_mode(self, recording_active, game_environment_ready, browser_opened):
        """Comprehensive cleanup for Master mode"""
        try:
            self.logger.info("🧹 Starting Master mode cleanup...")
            
            # Stop active recording
            if recording_active:
                self.logger.info("🛑 Emergency: Stopping active Master recording...")
                try:
                    self.master_controller.obs_controller.stop_recording()
                    self.logger.info("✅ Master recording stopped during cleanup")
                except Exception as e:
                    self.logger.error(f"Failed to stop recording during cleanup: {e}")
            
            # Emergency cleanup game state
            if game_environment_ready and self.master_controller.game_player:
                self.logger.info("🎮 Emergency: Cleaning up game state...")
                try:
                    # Stop any ongoing game operations
                    self.master_controller.game_player.should_stop = True
                    self.master_controller.game_player.is_playing = False
                    
                    # Try to cleanup any potential stuck icons/slots
                    # This is best-effort since we don't know exact state
                    self.logger.info("🔄 Attempting emergency icon cleanup...")
                    
                    # Small delay to let any ongoing operations finish
                    time.sleep(2)
                    
                    self.logger.info("✅ Game state cleanup completed")
                except Exception as e:
                    self.logger.error(f"Failed to cleanup game state: {e}")
            
            # Close browser (critical for Both mode)
            if browser_opened and self.master_controller.game_player:
                self.logger.info("🌐 Closing browser for clean slate...")
                try:
                    self.master_controller.game_player.close_game_window()
                    self.logger.info("✅ Browser closed successfully")
                except Exception as e:
                    self.logger.error(f"Failed to close browser: {e}")
            
            self.logger.info("✅ Master mode cleanup completed")
            
        except Exception as e:
            self.logger.error(f"Error during Master mode cleanup: {e}")
    
    def _process_individual_mode(self, item: ProcessingItem) -> bool:
            """Enhanced Individual mode with SafeOperationManager and intelligent success checking."""
            safe_ops = None
            meaningful_content_generated = False
            
            try:
                self.logger.info(f"Executing enhanced individual workflow for {item.display_name}")
                
                # Setup steps (remain the same, will raise exception on technical failure)
                if not self.master_controller.obs_controller.full_setup(use_full_screen=False):
                    raise Exception("OBS setup failed for individual mode")
                if not self.master_controller.game_player.setup_game_environment(first_setup=False):
                    raise Exception("Game environment re-setup failed")
                if not item.json_data or 'layout' not in item.json_data:
                    raise Exception("Invalid JSON data - missing layout")
                
                layout = item.json_data['layout']
                icons = layout['icons']
                slots = layout['slots']
                game_settings = item.json_data.get('gameSettings', {})
                has_horror = game_settings.get('hasHorror', False)
                
                # If there are no icons AND no remixes can be made, it's a failure
                if not icons:
                    self.logger.warning("No icons found in JSON. Individual mode cannot produce meaningful content.")
                    raise Exception("Individual mode failed: No icons available to record.")

                center_slot_index = self._calculate_center_slot_index(slots)
                target_slot = self.master_controller.game_player._find_slot_by_index(center_slot_index)
                if not target_slot:
                    raise Exception(f"Could not find slot at center index {center_slot_index}")

                safe_ops = SafeOperationManager(
                    self.master_controller.game_player, self.master_controller.obs_controller, self.logger
                )
                
                output_files = []
                remix_files = []
                
                # PHASE 1: Normal icons
                self.logger.info("🔹 PHASE 1: Safe individual icon recording (Normal)...")
                normal_icons_to_process = [icon for icon in icons if icon.get('index', 999) <= 18]
                for current_icon in normal_icons_to_process:
                    if self.should_stop: break
                    filename = f"n-{current_icon.get('index')}.mp4"
                    success, output_path, _ = safe_ops.safe_single_icon_operation(
                        current_icon, target_slot, filename
                    )
                    if success and output_path:
                        output_files.append(output_path)
                        meaningful_content_generated = True

                # PHASE 2: Horror icons
                if has_horror and not self.should_stop:
                    self.logger.info("🔹 PHASE 2: Safe individual icon recording (Horror)...")
                    if self.master_controller.game_player.trigger_horror_mode_transition_corrected():
                        horror_icons_to_process = icons # Process all icons in horror mode
                        for current_icon in horror_icons_to_process:
                            if self.should_stop: break
                            filename = f"h-{current_icon.get('index')}.mp4"
                            success, output_path, _ = safe_ops.safe_single_icon_operation(
                                current_icon, target_slot, filename
                            )
                            if success and output_path:
                                output_files.append(output_path)
                                meaningful_content_generated = True

                # PHASE 3: Remixes
                if not self.should_stop:
                    self.logger.info("🎵 PHASE 3: Safe remix recording...")
                    remix_files = self._safe_record_individual_remixes(item, icons, slots, safe_ops)
                    if remix_files:
                        output_files.extend(remix_files)
                        meaningful_content_generated = True

                # Final success check based on thresholds
                item.output_files.extend(output_files)

                total_icons_expected = len(normal_icons_to_process)
                if has_horror:
                    total_icons_expected += len(icons) # All icons in horror mode

                total_remixes_expected = 3
                
                # Đếm số file thực tế được tạo ra
                actual_icon_videos = len([f for f in output_files if 'remix' not in os.path.basename(f).lower()])
                actual_remix_videos = len(remix_files)

                icon_rate = actual_icon_videos / total_icons_expected if total_icons_expected > 0 else 1.0
                remix_rate = actual_remix_videos / total_remixes_expected if total_remixes_expected > 0 else 1.0
                
                ICON_THRESHOLD = 0.8  # 80%
                REMIX_THRESHOLD = 2/3 # At least 2 out of 3 remixes

                is_successful = (icon_rate >= ICON_THRESHOLD and remix_rate >= REMIX_THRESHOLD)

                if is_successful:
                    self.logger.info(f"✅ Enhanced Individual Mode completed successfully (Icons: {icon_rate:.0%}, Remixes: {remix_rate:.0%})")
                    item.individual_mode_status = "success"
                    return True
                else:
                    item.error_message = f"Individual mode failed thresholds (Icons: {icon_rate:.0%}, Remixes: {remix_rate:.0%})"
                    self.logger.error(item.error_message)
                    item.individual_mode_status = "failure"
                    return False
                
            except Exception as e:
                error_msg = f"Individual mode workflow failed: {str(e)}"
                self.logger.error(error_msg, exc_info=True)
                
                item.error_message = error_msg # Lưu lỗi chi tiết
                item.individual_mode_status = "failure"
                return False
            finally:
                if safe_ops:
                    safe_ops.emergency_cleanup_all()
                if self.master_controller.game_player:
                    self.master_controller.game_player.close_game_window()

    def _safe_record_individual_remixes(self, item, icons, slots, safe_ops) -> List[str]:
        """Safe remix recording using SafeOperationManager"""
        try:
            # Switch to Master mode for remixes
            if not self.master_controller.obs_controller.full_setup(use_full_screen=True):
                self.logger.error("Failed to switch to Master mode for remixes")
                return []
            
            # Generate remixes
            remixes = self._generate_individual_remixes(item.json_data, count=3)
            if not remixes:
                return []
            
            remix_output_files = []
            
            for remix_index, remix in enumerate(remixes):
                if self.should_stop:
                    break
                
                remix_number = remix_index + 1
                filename = f"remix-{remix_number}.mp4"
                
                # 🔥 USE SAFE REMIX OPERATION
                success, output_path, error_msg = safe_ops.safe_remix_operation(
                    remix['icons'], slots, filename, recording_duration=20
                )
                
                if success and output_path:
                    remix_output_files.append(output_path)
                    self.logger.info(f"✅ Remix {remix_number} recorded successfully")
                else:
                    self.logger.warning(f"⚠️ Remix {remix_number} failed: {error_msg}")
                
                if remix_index < len(remixes) - 1 and not self.should_stop:
                    time.sleep(2)
            
            return remix_output_files
            
        except Exception as e:
            self.logger.error(f"Error in safe remix recording: {e}")
            return []

    def _generate_individual_remixes(self, json_data, count=3) -> List[Dict]:
        """Generate remixes specifically for individual mode"""
        try:
            self.logger.info(f"🎼 Generating {count} remixes for individual mode...")
            
            # Check for horror mode to avoid icon 19 in normal remixes
            game_settings = json_data.get('gameSettings', {})
            has_horror = game_settings.get('hasHorror', False)
            
            # Use the game player's remix generation logic
            game_player = self.master_controller.game_player
            if not game_player:
                self.logger.error("Game player not available for remix generation")
                return []
            
            # Generate remixes using existing logic, limiting to requested count
            full_remix_library = game_player.generate_random_remixes(avoid_horror_icon=has_horror)
            
            # Select first 'count' remixes or generate exactly 'count' remixes
            if len(full_remix_library) >= count:
                selected_remixes = full_remix_library[:count]
            else:
                # If we don't have enough, generate more
                selected_remixes = full_remix_library.copy()
                attempts = 0
                while len(selected_remixes) < count and attempts < 10:  # Prevent infinite loop
                    additional_remixes = game_player.generate_random_remixes(avoid_horror_icon=has_horror)
                    for remix in additional_remixes:
                        if len(selected_remixes) >= count:
                            break
                        # Avoid duplicates by checking icon combinations
                        existing_combinations = [set(r['icons']) for r in selected_remixes]
                        if set(remix['icons']) not in existing_combinations:
                            selected_remixes.append(remix)
                    attempts += 1
            
            # Ensure we have exactly 'count' remixes
            selected_remixes = selected_remixes[:count]
            
            # Rename remixes for individual mode
            for i, remix in enumerate(selected_remixes):
                remix['name'] = f"Individual Remix {i+1}"
                remix['description'] = f"Generated for individual mode - {remix['description']}"
            
            self.logger.info(f"✅ Generated {len(selected_remixes)} remixes:")
            for i, remix in enumerate(selected_remixes):
                icon_count = len(remix['icons'])
                self.logger.info(f"   {i+1}. {remix['name']}: {icon_count} icons - {remix['description']}")
            
            return selected_remixes
            
        except Exception as e:
            self.logger.error(f"Error generating individual remixes: {e}")
            return []

# ================================================================
# == UNIFIED MASTER CONTROLLER                                 ==
# ================================================================

class SprunkiUltimateMaster:
    """Unified Master Controller with complete pipeline"""
    
    def __init__(self):
        # Initialize configuration first
        self.config_manager = initialize_config()
        
        # Core state
        self.config = None
        self.should_stop = False
        
        # Components
        self.game_player = None
        self.obs_controller = None
        
        # Unified processing manager
        self.processing_manager = UnifiedProcessingManager(self)
        
        # UI state
        self.selected_queue_index = None
        
        # Setup GUI
        self.setup_gui()
        
        print("✅ Ultimate Master initialized with centralized config")
    
    def open_url_input_window(self):
        """Open URL batch input window"""
        if hasattr(self, 'url_window') and self.url_window.winfo_exists():
            self.url_window.lift()
            return
        
        self.url_window = Toplevel(self.root)
        self.url_window.title("🌐 URL Batch Input")
        self.url_window.geometry("800x600")
        self.url_window.configure(bg="#f0f0f0")
        
        # Header
        header_frame = Frame(self.url_window, bg="#4CAF50", height=80)
        header_frame.pack(fill="x", pady=(0,10))
        header_frame.pack_propagate(False)
        
        Label(header_frame, text="🌐 URL BATCH INPUT", 
              font=("Arial", 16, "bold"), fg="white", bg="#4CAF50").pack(pady=5)
        Label(header_frame, text="🚀 Enter URLs → Auto Generate JSON → Process Videos", 
              font=("Arial", 11), fg="#C8E6C9", bg="#4CAF50").pack()
        
        content_frame = Frame(self.url_window, bg="#f0f0f0")
        content_frame.pack(fill="both", expand=True, padx=20, pady=10)
        
        # URL input area
        Label(content_frame, text="📝 Enter URLs (one per line):", 
              font=("Arial", 11, "bold"), bg="#f0f0f0").pack(anchor="w", pady=(0,5))
        
        # Create text area with proper layout
        text_frame = Frame(content_frame, bg="#f0f0f0")
        text_frame.pack(fill="both", expand=True, pady=(0,10))
        
        self.url_text = Text(text_frame, height=15, font=("Consolas", 10), wrap="word", 
                            relief="sunken", bd=1)
        url_scrollbar = Scrollbar(text_frame, orient="vertical", command=self.url_text.yview)
        self.url_text.configure(yscrollcommand=url_scrollbar.set)
        
        # Pack text widget and scrollbar properly
        self.url_text.pack(side="left", fill="both", expand=True)
        url_scrollbar.pack(side="right", fill="y")
        
        # Quick URLs
        quick_frame = Frame(content_frame, bg="#E8F5E8", relief="ridge", bd=1)
        quick_frame.pack(fill="x", pady=(0,10))
        
        Label(quick_frame, text="🚀 Quick URLs:", 
              font=("Arial", 10, "bold"), bg="#E8F5E8", fg="#2E7D32").pack(anchor="w", padx=10, pady=(10,5))
        
        quick_urls = [
            ("Sprunki Retake", "https://www.cocrea.world/@RedPixel/MyProject"),
            ("Sprunki Brud", "https://www.cocrea.world/@frazzledazzle/Incredibox-SprunkiBRUDTREATMENTEDITION"),
            ("Sprunki Wendy", "https://www.cocrea.world/@Monsterbaldideveloper/Incredibox-Sprunki20UpdateWendaTreatment")
        ]
        
        quick_buttons_frame = Frame(quick_frame, bg="#E8F5E8")
        quick_buttons_frame.pack(fill="x", padx=10, pady=(0,10))
        
        for name, url in quick_urls:
            Button(quick_buttons_frame, text=name, 
                   command=lambda u=url: self.add_url_to_text(u),
                   bg="#4CAF50", fg="white", font=("Arial", 9)).pack(side="left", padx=(0,5))
        
        # Buttons
        button_frame = Frame(content_frame, bg="#f0f0f0")
        button_frame.pack(fill="x")
        
        Button(button_frame, text="➕ Add URLs to Queue", 
               command=self.add_urls_from_text,
               bg="#FF5722", fg="white", font=("Arial", 12, "bold")).pack(side="left", padx=(0,10))
        
        Button(button_frame, text="🧹 Clear", 
               command=lambda: self.url_text.delete(1.0, 'end'),
               bg="#9E9E9E", fg="white", font=("Arial", 10)).pack(side="left")
    
    def add_url_to_text(self, url):
        """Add URL to text area"""
        current_text = self.url_text.get(1.0, 'end').strip()
        if current_text:
            self.url_text.insert('end', f'\n{url}')
        else:
            self.url_text.insert('end', url)
    
    def add_urls_from_text(self):
        """Add URLs from text area to queue"""
        url_text = self.url_text.get(1.0, 'end').strip()
        if not url_text:
            messagebox.showwarning("Empty Input", "Please enter at least one URL!")
            return
        
        urls = [line.strip() for line in url_text.split('\n') if line.strip()]
        if not urls:
            messagebox.showwarning("No Valid URLs", "No valid URLs found!")
            return
        
        added_count = self.processing_manager.add_urls(urls)
        
        if added_count > 0:
            self.update_queue_display()
            self.update_queue_summary()
            
            messagebox.showinfo("URLs Added", 
                f"Added {added_count} URLs to processing queue!\n\n"
                f"🚀 Pipeline: URL → JSON Generation → Video Processing\n"
                f"💎 Default mode: Both (Master + Individual)\n"
                f"🎯 Ready to start processing!")
            
            if hasattr(self, 'url_window'):
                self.url_window.destroy()
        else:
            messagebox.showwarning("No URLs Added", "Could not add any URLs to queue!")
    
    def add_json_files_to_queue(self):
        """Add existing JSON files to queue"""
        file_paths = filedialog.askopenfilenames(
            title="Choose existing JSON files",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        
        if file_paths:
            added_count = self.processing_manager.add_json_files(list(file_paths))
            
            if added_count > 0:
                self.update_queue_display()
                self.update_queue_summary()
                
                messagebox.showinfo("JSON Files Added", 
                    f"Added {added_count} JSON files to queue!\n\n"
                    f"💎 Default mode: Both (Master + Individual)\n"
                    f"🎯 Ready to start processing!")
            else:
                messagebox.showwarning("No Files Added", "Could not add any files to queue!")
    
    def remove_selected_queue_item(self):
        """Remove selected item from queue"""
        try:
            if hasattr(self, 'selected_queue_index') and self.selected_queue_index is not None:
                index = self.selected_queue_index
                if self.processing_manager.remove_queue_item(index):
                    self.selected_queue_index = None
                    self.update_queue_display()
                    self.update_queue_summary()
            else:
                messagebox.showwarning("No Selection", "Please select an item to remove!")
        except Exception as e:
            messagebox.showerror("Error", f"Could not remove item: {e}")
    
    def on_mode_changed(self, index, new_mode):
        """Callback when mode dropdown changes"""
        try:
            if self.processing_manager.set_item_mode(index, new_mode):
                self.update_queue_summary()
                print(f"✅ Changed mode for item {index}: {new_mode}")
        except Exception as e:
            print(f"Error changing mode: {e}")
    
    def on_queue_item_selected(self, index):
        """Callback when queue item is selected"""
        self.selected_queue_index = index
        self.update_queue_summary()
        print(f"Selected item {index}: {self.processing_manager.queue[index].display_name if index < len(self.processing_manager.queue) else 'Invalid'}")
    
    def update_queue_display(self):
        """Update queue display"""
        try:
            # Clear existing display
            for widget in self.queue_display_frame.winfo_children():
                widget.destroy()
            
            if not self.processing_manager.queue:
                Label(self.queue_display_frame, text="📄 Queue is empty - Add URLs or JSON files to start", 
                      font=("Arial", 10), bg="#f0f0f0", fg="gray").pack(pady=20)
                return
            
            # Header
            header_frame = Frame(self.queue_display_frame, bg="#e0e0e0", relief="ridge", bd=1)
            header_frame.pack(fill="x", pady=(0,5))
            
            Label(header_frame, text="📄 UNIFIED PROCESSING QUEUE:", 
                  font=("Arial", 10, "bold"), bg="#e0e0e0").pack(pady=5)
            
            # Scrollable frame for queue items
            canvas = Canvas(self.queue_display_frame, bg="#f0f0f0", height=300)
            scrollbar = Scrollbar(self.queue_display_frame, orient="vertical", command=canvas.yview)
            scrollable_frame = Frame(canvas, bg="#f0f0f0")
            
            scrollable_frame.bind(
                "<Configure>",
                lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
            )
            
            canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
            canvas.configure(yscrollcommand=scrollbar.set)
            
            # Create item frames
            for i, item in enumerate(self.processing_manager.queue):
                self.create_queue_item_frame(scrollable_frame, item, i)
            
            # Pack canvas and scrollbar
            canvas.pack(side="left", fill="both", expand=True)
            scrollbar.pack(side="right", fill="y")
            
        except Exception as e:
            print(f"Error updating queue display: {e}")
    
    def create_queue_item_frame(self, parent, item, index):
        """Create frame for queue item"""
        # Check if selected
        is_selected = hasattr(self, 'selected_queue_index') and self.selected_queue_index == index
        border_color = "#FF9800" if is_selected else "gray"
        border_width = 2 if is_selected else 1
        
        # Main item frame
        item_frame = Frame(parent, bg="white", relief="ridge", bd=border_width, highlightbackground=border_color)
        item_frame.pack(fill="x", padx=5, pady=2)
        
        # Configure grid
        item_frame.grid_columnconfigure(1, weight=1)
        
        # Status and selection
        status_frame = Frame(item_frame, bg="white")
        status_frame.grid(row=0, column=0, padx=10, pady=5, sticky="w")
        
        # Selection button
        select_btn_color = "#FF9800" if is_selected else "lightgray"
        select_btn_text = "📌" if is_selected else "📋"
        
        select_btn = Button(status_frame, text=select_btn_text, width=3,
                           command=lambda: self.on_queue_item_selected(index),
                           bg=select_btn_color, font=("Arial", 8))
        select_btn.pack(side="left")
        
        # Status emoji
        status_emoji = item.status_emoji
        Label(status_frame, text=status_emoji, font=("Arial", 12), bg="white").pack(side="left", padx=(5,0))
        
        # Info frame
        info_frame = Frame(item_frame, bg="white")
        info_frame.grid(row=0, column=1, padx=10, pady=5, sticky="ew")
        
        # Display name
        display_name_label = Label(info_frame, text=item.display_name, 
                                  font=("Arial", 10, "bold"), bg="white", anchor="w")
        display_name_label.pack(anchor="w")
        
        # Status info
        status_info = ""
        if item.status == "generating":
            status_info = "(🚀 Generating JSON...)"
        elif item.status == "processing":
            status_info = "(🔄 Processing video...)"
        elif item.status == "completed":
            duration = item.end_time - item.start_time if item.start_time and item.end_time else None
            duration_str = f"{duration.total_seconds():.1f}s" if duration else "N/A"
            status_info = f"(✅ Completed in {duration_str})"
        elif item.status == "failed":
            status_info = f"(❌ {item.error_message or 'Failed'})"
        elif item.status == "pending":
            source_type = "URL" if item.source_url else "JSON"
            status_info = f"(⏳ Ready - {source_type})"
        
        if status_info:
            Label(info_frame, text=status_info, font=("Arial", 9), 
                  bg="white", fg="gray", anchor="w").pack(anchor="w")
        
        # Mode dropdown
        mode_frame = Frame(item_frame, bg="white")
        mode_frame.grid(row=0, column=2, padx=10, pady=5, sticky="e")
        
        Label(mode_frame, text="Mode:", font=("Arial", 9), bg="white").pack(side="left")
        
        mode_var = StringVar(value=MODE_OPTIONS[item.mode]["display"])
        mode_dropdown = Combobox(mode_frame, textvariable=mode_var, 
                                values=[MODE_OPTIONS[key]["display"] for key in MODE_OPTIONS.keys()],
                                state="readonly", width=15)
        mode_dropdown.pack(side="left", padx=(5,0))
        
        # Mode change callback
        def on_mode_change(event):
            display_value = mode_var.get()
            actual_mode = None
            for mode_key, mode_info in MODE_OPTIONS.items():
                if mode_info["display"] == display_value:
                    actual_mode = mode_key
                    break
            
            if actual_mode and actual_mode != item.mode:
                self.on_mode_changed(index, actual_mode)
        
        mode_dropdown.bind('<<ComboboxSelected>>', on_mode_change)
        
        # Set background color based on status
        bg_color = "white"
        if is_selected:
            bg_color = "#FFF3E0"
        elif item.status == "completed":
            bg_color = "#E8F5E8"
        elif item.status == "failed":
            bg_color = "#FFEBEE"
        elif item.status == "generating":
            bg_color = "#FFF3E0"
        elif item.status == "processing":
            bg_color = "#E3F2FD"
        elif item.status == "skipped":
            bg_color = "#F3E5F5"
        
        # Apply background color
        for widget in [item_frame, status_frame, info_frame, mode_frame]:
            widget.configure(bg=bg_color)
        
        for frame in [status_frame, info_frame, mode_frame]:
            for widget in frame.winfo_children():
                if hasattr(widget, 'configure'):
                    try:
                        widget_class = widget.winfo_class()
                        if widget_class in ['Label', 'Button', 'Frame']:
                            # Try to set background color safely
                            try:
                                widget['bg'] = bg_color
                            except (KeyError, TypeError, AttributeError):
                                try:
                                    widget['background'] = bg_color
                                except (KeyError, TypeError, AttributeError):
                                    pass  # Widget doesn't support background color
                    except Exception:
                        pass  # Skip problematic widgets
    
    def update_queue_summary(self):
        """Update queue summary display"""
        try:
            summary = self.processing_manager.get_queue_summary()
            
            summary_text = f"""📊 UNIFIED PROCESSING QUEUE SUMMARY:
📝 Total items: {summary['total_items']}
⏳ Pending: {summary['pending']}
🚀 Generating JSON: {summary['generating']}
🔄 Processing video: {summary['processing']}
✅ Completed: {summary['completed']}
❌ Failed: {summary['failed']}
⭐ Skipped: {summary['skipped']}

⏰ Status: {summary['processing_status']}"""

            # Add selection info
            if hasattr(self, 'selected_queue_index') and self.selected_queue_index is not None:
                if self.selected_queue_index < len(self.processing_manager.queue):
                    selected_item = self.processing_manager.queue[self.selected_queue_index]
                    summary_text += f"\n\n📋 Selected: {selected_item.display_name}"
                    summary_text += f"\n   Mode: {MODE_OPTIONS[selected_item.mode]['display']}"
            
            self.queue_summary_var.set(summary_text)
        
        except Exception as e:
            print(f"Error updating queue summary: {e}")
    
    def start_processing(self):
        """Start unified processing with config-based settings"""
        if not self.processing_manager.queue:
            messagebox.showwarning("Empty Queue", "Please add URLs or JSON files to start processing!")
            return
        
        # Create processing config from config.json instead of hardcoded values
        processing_config_data = self.config_manager.get('processing', {})
        output_config = self.config_manager.get('output', {})
        
        processing_config = ProcessingConfig(
            output_base_dir=output_config.get('base_directory', r"C:\Users\dinht\Videos\SPRUNKI"),
            max_retries=processing_config_data.get('max_retries', 2),
            auto_skip_on_error=processing_config_data.get('auto_skip_on_error', True),
            pause_between_items=processing_config_data.get('pause_between_items', 5)
        )
        
        print(f"✅ Processing config loaded from config.json:")
        print(f"   Output: {processing_config.output_base_dir}")
        print(f"   Max retries: {processing_config.max_retries}")
        print(f"   Pause between items: {processing_config.pause_between_items}s")
        
        # Show confirmation with config-based values
        summary = self.processing_manager.get_queue_summary()
        
        # Get timing settings from config for display
        automation_config = self.config_manager.get('automation', {})
        recording_durations = automation_config.get('recording_duration', {})
        icon_duration = recording_durations.get('icon_showcase', 14)
        remix_duration = recording_durations.get('remix_playback', 20)
        
        result = messagebox.askyesno("Start Unified Processing",
            f"START UNIFIED PROCESSING PIPELINE?\n\n"
            f"📊 SUMMARY:\n"
            f"   📝 Total items: {summary['total_items']}\n"
            f"   ⏳ Pending: {summary['pending']}\n"
            f"   🚀 URLs to generate: {len([i for i in self.processing_manager.queue if i.source_url and not i.json_data])}\n"
            f"   🔄 Ready JSONs: {len([i for i in self.processing_manager.queue if i.json_data])}\n\n"
            f"📂 OUTPUT (from config.json):\n"
            f"   📁 Base folder: {processing_config.output_base_dir}\n"
            f"   🔄 Max retries: {processing_config.max_retries}\n"
            f"   ⏸️ Pause between items: {processing_config.pause_between_items}s\n\n"
            f"🔄 PIPELINE:\n"
            f"   1️⃣ Auto-generate JSON from URLs\n"
            f"   2️⃣ Setup game environment\n"
            f"   3️⃣ Record videos (Master + Individual + Remixes)\n"
            f"   4️⃣ Save to organized folders\n\n"
            f"🎭 INDIVIDUAL MODE ENHANCED (from config.json):\n"
            f"   • Individual icon videos (recordingArea frame, {icon_duration}s each)\n"
            f"   • 3 remix videos (1920x1080 Master frame, {remix_duration}s each)\n"
            f"   • Files: n-0.mp4, n-1.mp4, ..., remix-1.mp4, remix-2.mp4, remix-3.mp4\n\n"
            f"🎮 GLOBAL HOTKEYS (from config.json):\n"
            f"   • {self.config_manager.get('hotkeys.emergency_stop', 'esc').upper()} = Emergency stop\n\n"
            f"Continue?")
        
        if result:
            # Start processing
            if self.processing_manager.start_processing(processing_config):
                # Update UI state
                self.start_btn.config(state="disabled")
                self.stop_btn.config(state="normal")
                self.add_urls_btn.config(state="disabled")
                self.add_files_btn.config(state="disabled")
                
                # Setup progress tracking
                self.phase_var.set("🚀 UNIFIED PIPELINE RUNNING")
                self.action_var.set("Starting unified processing...")
                
                # Start UI update timer
                self.update_progress_timer()
                
                # Setup global hotkeys from config
                emergency_key = 'esc'  # Default value
                try:
                    hotkey_config = self.config_manager.get('hotkeys', {})
                    emergency_key = hotkey_config.get('emergency_stop', 'esc')
                    
                    if hotkey_config.get('global_enabled', True):
                        keyboard.add_hotkey(emergency_key, self.emergency_stop)
                        print(f"🎮 Global {emergency_key.upper()} hotkey registered")
                    else:
                        print("🎮 Global hotkeys disabled in config")
                        
                except Exception as e:
                    print(f"Warning: Could not register hotkeys: {e}")
                
                # Get notification settings from config
                notification_config = self.config_manager.get('notifications', {})
                show_startup_notification = notification_config.get('system_notifications', True)
                
                if show_startup_notification:
                    messagebox.showinfo("🚀 Pipeline Started", 
                        f"Unified processing pipeline started!\n\n"
                        f"🔄 PIPELINE ACTIVE:\n"
                        f"   • URLs → JSON Generation\n"
                        f"   • JSON → Video Processing\n"
                        f"   • Organized output folders\n\n"
                        f"📊 SETTINGS FROM CONFIG.JSON:\n"
                        f"   • Output: {processing_config.output_base_dir}\n"
                        f"   • Icon duration: {icon_duration}s\n"
                        f"   • Remix duration: {remix_duration}s\n"
                        f"   • Max retries: {processing_config.max_retries}\n\n"
                        f"🎮 {emergency_key.upper()} = Emergency Stop\n"
                        f"📊 Monitor progress in Status section")
    
    def stop_processing(self):
        """Stop processing"""
        self.processing_manager.stop_processing()
        self.on_processing_complete()
    
    def update_progress_timer(self):
        """Update progress display"""
        if self.processing_manager.is_running:
            # Update queue display
            self.update_queue_display()
            self.update_queue_summary()
            
            # Update current item info
            if self.processing_manager.current_item_index < len(self.processing_manager.queue):
                current_item = self.processing_manager.queue[self.processing_manager.current_item_index]
                
                if current_item.status == "generating":
                    progress_text = f"🚀 Generating JSON: {current_item.display_name}"
                elif current_item.status == "processing":
                    progress_text = f"🔄 Processing video: {current_item.display_name}"
                else:
                    progress_text = f"Processing: {current_item.display_name} ({self.processing_manager.current_item_index + 1}/{len(self.processing_manager.queue)})"
                
                self.action_var.set(progress_text)
            
            # Continue updating
            self.root.after(2000, self.update_progress_timer)
    
    def on_processing_complete(self):
        """Called when processing completes"""
        try:
            # Update UI state
            self.start_btn.config(state="normal")
            self.stop_btn.config(state="disabled")
            self.add_urls_btn.config(state="normal")
            self.add_files_btn.config(state="normal")
            
            # Update displays
            self.update_queue_display()
            self.update_queue_summary()
            
            # Update status
            self.phase_var.set("✅ PIPELINE COMPLETED")
            
            # Show completion message
            summary = self.processing_manager.get_queue_summary()
            
            completion_message = (
                f"🎉 UNIFIED PIPELINE COMPLETED!\n\n"
                f"📊 RESULTS:\n"
                f"   ✅ Completed: {summary['completed']}\n"
                f"   ❌ Failed: {summary['failed']}\n"
                f"   ⭐ Skipped: {summary['skipped']}\n\n"
                f"📁 All output saved to:\n"
                f"   C:\\Users\\dinht\\Videos\\SPRUNKI\n\n"
                f"🎯 Pipeline processed URLs → JSON → Videos!"
            )
            
            messagebox.showinfo("🎉 Pipeline Completed", completion_message)
            
            # Remove hotkeys
            try:
                keyboard.remove_hotkey('esc')
            except:
                pass
        
        except Exception as e:
            print(f"Error in processing completion: {e}")
    
    def emergency_stop(self):
        """Enhanced emergency stop with comprehensive cleanup for all modes"""
        emergency_start_time = datetime.now()
        
        try:
            self.processing_manager.logger.info("🚨 EMERGENCY STOP INITIATED")
            self.processing_manager.logger.info("=" * 60)
            
            # ================================================================
            # PHASE 1: STOP PROCESSING PIPELINE
            # ================================================================
            if self.processing_manager.is_running:
                self.processing_manager.logger.info("🛑 Phase 1: Stopping unified processing pipeline...")
                
                # Set global stop flags
                self.processing_manager.stop_processing()
                self.should_stop = True
                
                self.processing_manager.logger.info("✅ Processing pipeline stop signal sent")

                # ================================================================
                # PHASE 1.5: GRACEFUL RECORDING STOP
                # ================================================================
                self.processing_manager.logger.info("🛑 Phase 1.5: Attempting graceful OBS stop to save timestamp...")
                if hasattr(self, 'obs_controller') and self.obs_controller:
                    try:
                        # Hàm này sẽ tự động gọi logic lưu file timestamp
                        stop_success, final_path = self.obs_controller.stop_recording()
                        if stop_success:
                            self.processing_manager.logger.info(f"   ✅ Recording stopped gracefully. Timestamp file saved for: {final_path}")
                        else:
                            self.processing_manager.logger.warning("   ⚠️ Graceful stop failed, comprehensive cleanup will take over.")
                    except Exception as e:
                        self.processing_manager.logger.error(f"   ❌ Error during graceful stop_recording call: {e}")
                else:
                    self.processing_manager.logger.info("   ℹ️ OBS controller not available for graceful stop.")

                # ================================================================
                # PHASE 2: OBS EMERGENCY CLEANUP
                # ================================================================
                self.processing_manager.logger.info("🛑 Phase 2: OBS emergency cleanup...")

                if hasattr(self, 'obs_controller') and self.obs_controller:
                    try:
                        self.processing_manager.logger.info("   🧹 Running comprehensive emergency cleanup...")
                        cleanup_success = self.obs_controller.comprehensive_obs_cleanup()
                        if cleanup_success:
                            self.processing_manager.logger.info("   ✅ Comprehensive emergency cleanup completed")
                        else:
                            self.processing_manager.logger.warning("   ⚠️ Some emergency cleanup steps failed")
                        
                        self.processing_manager.logger.info("✅ OBS emergency cleanup completed")
                        
                    except Exception as e:
                        self.processing_manager.logger.error(f"   ❌ Comprehensive emergency cleanup error: {e}")
                        self.processing_manager.logger.warning("   Continuing with other cleanup operations...")
                else:
                    self.processing_manager.logger.info("   ℹ️ OBS controller not available")
                
                # ================================================================
                # PHASE 3: GAME PLAYER EMERGENCY CLEANUP
                # ================================================================
                self.processing_manager.logger.info("🛑 Phase 3: Game player emergency cleanup...")
                
                if hasattr(self, 'game_player') and self.game_player:
                    try:
                        self.processing_manager.logger.info("   🎮 Stopping game player operations...")
                        
                        # Set stop flags
                        self.game_player.should_stop = True
                        self.game_player.is_playing = False
                        
                        self.processing_manager.logger.info("   ✅ Game player stop flags set")
                        
                        # Emergency icon/slot cleanup attempt
                        self.processing_manager.logger.info("   🔄 Attempting emergency icon/slot cleanup...")
                        
                        try:
                            # Small delay to let any ongoing drag operations finish
                            max_wait = 3  # Maximum 3 seconds
                            
                            self.processing_manager.logger.info(f"   ⏳ Allowing {max_wait}s for ongoing operations to halt...")
                            
                            for i in range(max_wait):
                                time.sleep(1)
                                self.processing_manager.logger.info(f"   ⏳ Emergency wait: {i+1}/{max_wait}s")
                            
                            self.processing_manager.logger.info("   ✅ Emergency operation halt completed")
                            
                        except Exception as e:
                            self.processing_manager.logger.error(f"   ⚠️ Error during emergency operation halt: {e}")
                        
                        # Browser emergency cleanup
                        self.processing_manager.logger.info("   🌐 Emergency browser cleanup...")
                        
                        try:
                            browser_close_success = self.game_player.close_game_window()
                            
                            if browser_close_success:
                                self.processing_manager.logger.info("   ✅ Browser closed successfully")
                            else:
                                self.processing_manager.logger.warning("   ⚠️ Browser close returned false")
                            
                            # Additional wait for browser resource cleanup
                            self.processing_manager.logger.info("   ⏳ Waiting for browser resource cleanup...")
                            time.sleep(2)
                            
                            self.processing_manager.logger.info("   ✅ Browser resource cleanup completed")
                            
                        except Exception as e:
                            self.processing_manager.logger.error(f"   ❌ Browser emergency cleanup error: {e}")
                        
                        self.processing_manager.logger.info("✅ Game player emergency cleanup completed")
                        
                    except Exception as e:
                        self.processing_manager.logger.error(f"   ❌ Game player emergency cleanup error: {e}")
                        self.processing_manager.logger.warning("   Continuing with other cleanup operations...")
                else:
                    self.processing_manager.logger.info("   ℹ️ Game player not available")
                
                # ================================================================
                # PHASE 4: RESOURCE CLEANUP AND STATE RESET
                # ================================================================
                self.processing_manager.logger.info("🛑 Phase 4: Resource cleanup and state reset...")
                
                try:
                    # Update UI state immediately
                    self.processing_manager.logger.info("   🖥️ Updating UI state...")
                    
                    if hasattr(self, 'start_btn'):
                        self.start_btn.config(state="normal")
                    if hasattr(self, 'stop_btn'):
                        self.stop_btn.config(state="disabled")
                    if hasattr(self, 'add_urls_btn'):
                        self.add_urls_btn.config(state="normal")
                    if hasattr(self, 'add_files_btn'):
                        self.add_files_btn.config(state="normal")
                    
                    # Update status variables
                    if hasattr(self, 'phase_var'):
                        self.phase_var.set("🛑 EMERGENCY STOPPED")
                    if hasattr(self, 'action_var'):
                        self.action_var.set("Emergency stop completed - Ready for new operations")
                    
                    self.processing_manager.logger.info("   ✅ UI state updated")
                    
                    # Refresh queue display
                    self.processing_manager.logger.info("   📋 Refreshing queue display...")
                    try:
                        self.update_queue_display()
                        self.update_queue_summary()
                        self.processing_manager.logger.info("   ✅ Queue display refreshed")
                    except Exception as e:
                        self.processing_manager.logger.error(f"   ⚠️ Queue display refresh error: {e}")
                    
                    self.processing_manager.logger.info("✅ Resource cleanup and state reset completed")
                    
                except Exception as e:
                    self.processing_manager.logger.error(f"   ❌ Resource cleanup error: {e}")
                
                # ================================================================
                # PHASE 5: HOTKEY CLEANUP
                # ================================================================
                self.processing_manager.logger.info("🛑 Phase 5: Hotkey cleanup...")
                
                try:
                    # Remove emergency stop hotkey
                    keyboard.remove_hotkey('esc')
                    self.processing_manager.logger.info("   ✅ Emergency stop hotkey removed")
                except Exception as e:
                    self.processing_manager.logger.info(f"   ℹ️ Hotkey removal: {e} (may not have been registered)")

                # ================================================================
                # PHASE 6: FINAL DATA PERSISTENCE (SAVE PARTIAL MANIFEST)
                # ================================================================
                self.processing_manager.logger.info("🛑 Phase 6: Saving partial manifest for interrupted item...")
                try:
                    if self.processing_manager and self.processing_manager.queue:
                        current_item_index = self.processing_manager.current_item_index
                        if 0 <= current_item_index < len(self.processing_manager.queue):
                            current_item = self.processing_manager.queue[current_item_index]
                            
                            # Cập nhật trạng thái của item trước khi lưu
                            current_item.status = "failed"
                            current_item.error_message = "Processing stopped by user (ESC key)"
                            current_item.end_time = datetime.now()
                            
                            self.processing_manager.logger.info(f"   Updating status for '{current_item.display_name}' to FAILED.")
                            
                            # Gọi hàm tạo manifest
                            self.processing_manager._generate_manifest_for_item(current_item)
                        else:
                            self.processing_manager.logger.info("   ℹ️ No active item index to generate a manifest for.")
                    else:
                        self.processing_manager.logger.info("   ℹ️ Processing manager or queue not available.")
                except Exception as e:
                    self.processing_manager.logger.error(f"   ❌ Failed to save partial manifest: {e}")

                # ================================================================
                # EMERGENCY STOP COMPLETION SUMMARY
                # ================================================================
                emergency_duration = (datetime.now() - emergency_start_time).total_seconds()
                
                self.processing_manager.logger.info("=" * 60)
                self.processing_manager.logger.info("✅ EMERGENCY STOP COMPLETED")
                self.processing_manager.logger.info(f"⏱️ Emergency stop duration: {emergency_duration:.1f}s")
                self.processing_manager.logger.info("🧹 Cleanup summary:")
                self.processing_manager.logger.info("   • Processing pipeline stopped")
                self.processing_manager.logger.info("   • OBS recording stopped")
                self.processing_manager.logger.info("   • Game operations halted")
                self.processing_manager.logger.info("   • Browser closed and cleaned")
                self.processing_manager.logger.info("   • UI state reset")
                self.processing_manager.logger.info("   • Hotkeys removed")
                self.processing_manager.logger.info("=" * 60)
            
            # ================================================================
            # FALLBACK: EMERGENCY STOP WHEN NO PIPELINE RUNNING
            # ================================================================
            else:
                self.processing_manager.logger.info("ℹ️ No processing pipeline running")
                self.processing_manager.logger.info("🛑 Performing general emergency stop...")
                
                # Set global stop flag anyway
                self.should_stop = True
                
                # Stop any individual components that might be running
                if hasattr(self, 'game_player') and self.game_player:
                    try:
                        self.game_player.should_stop = True
                        self.game_player.is_playing = False
                        
                        # Try to close browser
                        self.game_player.close_game_window()
                        self.processing_manager.logger.info("✅ Game player emergency stopped")
                        
                    except Exception as e:
                        self.processing_manager.logger.error(f"⚠️ Game player emergency stop error: {e}")
                
                # Update UI
                if hasattr(self, 'phase_var'):
                    self.phase_var.set("🛑 EMERGENCY STOP")
                if hasattr(self, 'action_var'):
                    self.action_var.set("Emergency stopped")
                
                emergency_duration = (datetime.now() - emergency_start_time).total_seconds()
                
                self.processing_manager.logger.info(f"✅ General emergency stop completed in {emergency_duration:.1f}s")
        
        except Exception as e:
            # Critical error during emergency stop
            emergency_duration = (datetime.now() - emergency_start_time).total_seconds()
            
            print(f"💥 CRITICAL: Error during emergency stop after {emergency_duration:.1f}s: {e}")
            
            # Minimal fallback cleanup
            try:
                self.should_stop = True
                if hasattr(self, 'processing_manager'):
                    self.processing_manager.should_stop = True
                    self.processing_manager.is_running = False
                
                # Try basic UI reset
                if hasattr(self, 'phase_var'):
                    self.phase_var.set("🛑 EMERGENCY (ERROR)")
                if hasattr(self, 'action_var'):
                    self.action_var.set("Emergency stop had errors")
                    
            except:
                pass
    
    def _initialize_components(self, config_data: Dict[str, Any]) -> bool:
        """
        [CHUẨN HÓA] Khởi tạo và liên kết các component cho một job.
        """
        try:
            print("⚙️ Initializing and linking components for new job...")
            
            # 1. Tạo các component chính
            self.game_player = SprunkiGamePlayer()
            self.obs_controller = SprunkiController()
            
            # 2. LIÊN KẾT CÁC COMPONENT
            # Gán obs_controller vào game_player để tất cả các hàm bên trong nó có thể truy cập
            self.game_player.obs_controller = self.obs_controller
            
            # 3. Nạp config và tạo remix
            self.game_player.config = config_data
            
            game_settings = config_data.get('gameSettings', {})
            has_horror = game_settings.get('hasHorror', False)
            total_slots = len(config_data.get('layout', {}).get('slots', []))
            
            self.game_player.generate_dynamic_patterns(total_slots)
            self.game_player.remix_library = self.game_player.generate_random_remixes(avoid_horror_icon=not has_horror)
            self.game_player.current_remix_index = 0
            
            # 4. Nạp config cho OBS controller
            if not self.obs_controller.load_config_from_dict(config_data):
                return False
            
            # 5. Gán config chung (không cần thiết nhưng giữ lại cho nhất quán)
            self.config = config_data
            
            print("✅ Components initialized and linked successfully.")
            return True
            
        except Exception as e:
            print(f"❌ Error initializing components: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def setup_gui(self):
        """Setup main GUI"""
        self.root = Tk()
        self.root.title("🎮 Sprunki Ultimate Master Controller - Unified Pipeline")
        self.root.geometry("950x900")
        self.root.configure(bg="#f0f0f0")
        
        # Header
        header_frame = Frame(self.root, bg="#2196F3", height=120)
        header_frame.pack(fill="x", pady=(0,10))
        header_frame.pack_propagate(False)
        
        Label(header_frame, text="🎮 SPRUNKI ULTIMATE MASTER", 
              font=("Arial", 18, "bold"), fg="white", bg="#2196F3").pack(pady=5)
        Label(header_frame, text="⚡ Unified Pipeline: URL List → JSON Generation → Video Processing", 
              font=("Arial", 12), fg="#E3F2FD", bg="#2196F3").pack()
        Label(header_frame, text="🔄 Single Interface, Complete Automation, Zero Manual Steps", 
              font=("Arial", 10), fg="#BBDEFB", bg="#2196F3").pack()
        
        # Status variables
        self.status_var = StringVar(value="Ready to start")
        self.phase_var = StringVar(value="READY")
        self.action_var = StringVar(value="Add URLs or JSON files to begin...")
        self.queue_summary_var = StringVar(value="📊 Queue: Empty")
        
        # Main content
        main_frame = Frame(self.root, bg="#f0f0f0")
        main_frame.pack(fill="both", expand=True, padx=20, pady=10)
        
        # Pipeline info
        pipeline_info_frame = Frame(main_frame, bg="#E8F5E8", relief="ridge", bd=2)
        pipeline_info_frame.pack(fill="x", pady=(0,15))
        
        Label(pipeline_info_frame, text="⚡ UNIFIED PROCESSING PIPELINE:", 
              font=("Arial", 11, "bold"), bg="#E8F5E8", fg="#2E7D32").pack(anchor="w", padx=10, pady=(10,5))
        
        pipeline_text = """🌐 URL Input → 🚀 Auto JSON Generation → 🎮 Game Automation → 🔹 OBS Recording → 📁 Organized Output
        ✨ Zero manual steps between URL and final video files
        🎯 Individual Mode: Icons riêng lẻ (recordingArea) + 3 remixes (1920x1080, 20s/bản)
        🎯 Master Mode: Complete showcases với tất cả remixes (1920x1080)
        📊 Full progress tracking and error handling"""
        
        Label(pipeline_info_frame, text=pipeline_text, justify="left",
              font=("Arial", 9), fg="#1B5E20", bg="#E8F5E8").pack(anchor="w", padx=20, pady=(0,10))
        
        # Input section
        input_section = Frame(main_frame, bg="#f0f0f0")
        input_section.pack(fill="x", pady=(0,15))
        
        Label(input_section, text="📥 INPUT METHODS:", 
              font=("Arial", 11, "bold"), bg="#f0f0f0").pack(anchor="w")
        
        buttons_frame = Frame(input_section, bg="#f0f0f0")
        buttons_frame.pack(fill="x", pady=5)
        
        self.add_urls_btn = Button(buttons_frame, text="🌐 Add URL List (Batch)", 
                                  command=self.open_url_input_window,
                                  bg="#4CAF50", fg="white", font=("Arial", 11, "bold"))
        self.add_urls_btn.pack(side="left", padx=(0,10))
        
        self.add_files_btn = Button(buttons_frame, text="📄 Add Existing JSON Files", 
                                   command=self.add_json_files_to_queue,
                                   bg="#FF9800", fg="white", font=("Arial", 11, "bold"))
        self.add_files_btn.pack(side="left", padx=(0,10))
        
        Button(buttons_frame, text="🗑️ Remove Selected", 
               command=self.remove_selected_queue_item,
               bg="#f44336", fg="white", font=("Arial", 10)).pack(side="left")
        
        # Queue section
        queue_section = Frame(main_frame, bg="#f0f0f0")
        queue_section.pack(fill="both", expand=True, pady=10)
        
        Label(queue_section, text="📋 PROCESSING QUEUE:", 
              font=("Arial", 11, "bold"), bg="#f0f0f0").pack(anchor="w")
        
        queue_content_frame = Frame(queue_section, bg="#f0f0f0")
        queue_content_frame.pack(fill="both", expand=True, pady=10)
        
        # Queue display (left)
        queue_left_frame = Frame(queue_content_frame, bg="#f0f0f0")
        queue_left_frame.pack(side="left", fill="both", expand=True, padx=(0,10))
        
        self.queue_display_frame = Frame(queue_left_frame, bg="#f0f0f0")
        self.queue_display_frame.pack(fill="both", expand=True)
        
        # Summary panel (right)
        summary_frame = Frame(queue_content_frame, bg="#FFF9C4", relief="ridge", bd=1, width=300)
        summary_frame.pack(side="right", fill="y")
        summary_frame.pack_propagate(False)
        
        Label(summary_frame, text="📊 QUEUE SUMMARY:", 
              font=("Arial", 10, "bold"), bg="#FFF9C4").pack(anchor="w", padx=10, pady=(10,5))
        
        Label(summary_frame, textvariable=self.queue_summary_var, justify="left",
              font=("Arial", 9), bg="#FFF9C4", wraplength=280).pack(anchor="w", padx=10, pady=(0,10))
        
        # Mode legend
        legend_frame = Frame(summary_frame, bg="#FFF9C4")
        legend_frame.pack(fill="x", padx=10, pady=(10,0))
        
        Label(legend_frame, text="🎯 MODE LEGEND:", 
              font=("Arial", 9, "bold"), bg="#FFF9C4").pack(anchor="w")

        Label(legend_frame, text="💎 Both = Master(1920x1080)+Individual", 
              font=("Arial", 8), bg="#FFF9C4", anchor="w").pack(anchor="w", padx=10)
        Label(legend_frame, text="🎬 Master = 1 complete video (1920x1080)", 
              font=("Arial", 8), bg="#FFF9C4", anchor="w").pack(anchor="w", padx=10)
        Label(legend_frame, text="🎭 Individual = Icons(recordingArea)+Remixes(1920x1080)", 
              font=("Arial", 8), bg="#FFF9C4", anchor="w").pack(anchor="w", padx=10)
        
        # Control buttons
        controls_frame = Frame(main_frame, bg="#f0f0f0")
        controls_frame.pack(fill="x", pady=15)
        
        # Main start button
        self.start_btn = Button(controls_frame, 
                               text="🚀 START UNIFIED PIPELINE ⚡",
                               command=self.start_processing,
                               bg="#FF5722", fg="white", 
                               font=("Arial", 14, "bold"),
                               height=2)
        self.start_btn.pack(fill="x", pady=(0,10))
        
        # Control buttons
        ui_controls_frame = Frame(controls_frame, bg="#f0f0f0")
        ui_controls_frame.pack(fill="x")
        
        self.stop_btn = Button(ui_controls_frame, text="🛑 STOP PIPELINE", 
                              command=self.stop_processing, state="disabled",
                              bg="#9E9E9E", fg="white", font=("Arial", 11))
        self.stop_btn.pack(side="left", fill="x", expand=True)
        
        # Status section
        self.setup_status_section()
        
        # Footer
        footer_frame = Frame(self.root, bg="#37474F", height=60)
        footer_frame.pack(fill="x", side="bottom")
        footer_frame.pack_propagate(False)
        
        Label(footer_frame, text="🚀 UNIFIED PIPELINE: URLs → JSON → Videos | 🎮 ESC = Emergency Stop", 
              font=("Arial", 10), fg="white", bg="#37474F").pack(pady=8)
        Label(footer_frame, text="⚡ Single Interface, Complete Automation, Professional Results", 
              font=("Arial", 9), fg="#B0BEC5", bg="#37474F").pack()
    
    def setup_status_section(self):
        """Setup status section"""
        status_container = Frame(self.root, bg="#f0f0f0")
        status_container.pack(fill="x", padx=20, pady=10)
        
        Label(status_container, text="📊 PIPELINE STATUS:", 
              font=("Arial", 11, "bold"), bg="#f0f0f0").pack(anchor="w")
        
        # Phase status
        phase_frame = Frame(status_container, bg="#f0f0f0")
        phase_frame.pack(fill="x", pady=5)
        Label(phase_frame, text="🔄 Phase:", font=("Arial", 10), bg="#f0f0f0").pack(side="left")
        Label(phase_frame, textvariable=self.phase_var, font=("Arial", 10, "bold"), 
              fg="#FF5722", bg="#f0f0f0").pack(side="left", padx=(10,0))
        
        # Current action
        Label(status_container, text="⚡ Current Action:", font=("Arial", 10), bg="#f0f0f0").pack(anchor="w", pady=(10,0))
        Label(status_container, textvariable=self.action_var, font=("Arial", 10), 
              fg="#2196F3", bg="#f0f0f0", wraplength=800, justify="left").pack(anchor="w")
        
        Separator(status_container, orient="horizontal").pack(fill="x", pady=15)
        
        # Progress
        Label(status_container, text="📈 PROGRESS:", 
              font=("Arial", 11, "bold"), bg="#f0f0f0").pack(anchor="w")
        
        self.progress = Progressbar(status_container, length=800, mode='indeterminate')
        self.progress.pack(fill="x", pady=10)
        
        # Emergency stop
        emergency_btn = Button(status_container, text="🛑 EMERGENCY STOP", 
                              command=self.emergency_stop,
                              bg="#f44336", fg="white", font=("Arial", 12, "bold"))
        emergency_btn.pack(pady=15)
    
    def run(self):
        """Run application"""
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            print("Stopped Controller")
        finally:
            try:
                keyboard.remove_hotkey('esc')
            except:
                pass

# ================================================================
# == SERVICE MODE CLASSES                                      ==
# ================================================================

class SprunkiCreatorService:
    """
    API-driven Creator Service
    Replaces tkinter GUI with API integration
    """
    
    def __init__(self):
        # Use existing config system
        self.config_manager = initialize_config()
        
        # Initialize processing manager (existing code)
        self.processing_manager = UnifiedProcessingManager(self)
        
        # API client
        try:
            from creator_api_client import CreatorService
            self.service = CreatorService()
        except ImportError:
            print("⚠️ creator_api_client not available - service mode disabled")
            self.service = None
        
        print("🤖 Creator Service Mode - No GUI")
    
    def run_as_service(self):
        """Run as API-driven service instead of GUI"""
        if not self.service:
            print("❌ Cannot run service mode - creator_api_client not available")
            return
            
        try:
            print("🚀 Starting Creator Service...")
            
            if self.service.start():
                print("✅ Creator Service running")
                print("🔗 Connected to Tracker for job processing")
                print("⌨️ Press Ctrl+C to stop")
                
                # Keep service running
                while self.service.running:
                    time.sleep(1)
            else:
                print("❌ Failed to start Creator Service")
                
        except KeyboardInterrupt:
            print("\n🛑 Service shutdown requested")
        finally:
            if self.service:
                self.service.stop()
            print("✅ Creator Service stopped")
    
    # Keep existing methods for compatibility
    def _initialize_components(self, config_data):
        """Existing method - no changes"""
        # Implementation would go here but not needed for basic service
        pass
    
    def on_processing_complete(self):
        """Modified for service mode"""
        print("✅ Job processing completed")
        # No GUI updates needed

# ================================================================
# == MAIN EXECUTION                                           ==
# ================================================================

if __name__ == "__main__":
    print("🚀 Sprunki Ultimate Master Controller - Unified Pipeline")
    print("⚡ COMPLETE AUTOMATION: URL List → JSON Generation → Video Processing")
    print("🔄 UNIFIED ARCHITECTURE: Single interface, zero manual steps")
    print("📊 PROFESSIONAL RESULTS: Organized output, full error handling")
    print("="*75)
    
    import sys
    
    # Check for service mode
    if len(sys.argv) > 1 and sys.argv[1] == "--service":
        # Run as API service
        print("🤖 Starting Sprunki Creator Service Mode")
        service = SprunkiCreatorService()
        service.run_as_service()
    else:
        # Run existing GUI mode
        try:
            print("🎮 Starting Sprunki Ultimate Master GUI")
            master = SprunkiUltimateMaster()
            master.run()
        except Exception as e:
            print(f"❌ Error: {e}")
            input("Press Enter to exit...")
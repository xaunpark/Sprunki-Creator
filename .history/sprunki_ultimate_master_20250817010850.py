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
        "display": "⏭️ Skip Item",
        "description": "Bỏ qua item này"
    }
}

# ================================================================
# == DATA STRUCTURES                                           ==
# ================================================================

@dataclass
class ProcessingItem:
    """Represents one URL/JSON + processing settings"""
    source_url: Optional[str] = None  # URL for JSON generation
    json_path: Optional[str] = None   # Path to existing/generated JSON
    json_data: Optional[Dict[str, Any]] = None
    mode: str = "both"  # Processing mode
    status: str = "pending"  # "pending", "generating", "processing", "completed", "failed", "skipped"
    error_message: Optional[str] = None
    output_files: List[str] = field(default_factory=list)
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    project_slug: Optional[str] = None
    
    @property
    def display_name(self) -> str:
        """Get display name for UI"""
        if self.source_url:
            return f"URL: {self.source_url.split('/')[-1] or 'Game'}"
        elif self.json_path:
            return f"JSON: {os.path.basename(self.json_path)}"
        else:
            return "Unknown Item"
    
    @property
    def status_emoji(self) -> str:
        """Get emoji for status"""
        status_map = {
            "pending": "⏳",
            "generating": "🚀", 
            "processing": "🔄",
            "completed": "✅",
            "failed": "❌",
            "skipped": "⏭️"
        }
        return status_map.get(self.status, "❓")
    
    @property
    def mode_emoji(self) -> str:
        """Get emoji for mode"""
        mode_map = {
            "master": "🎬",
            "individual": "🎭",
            "both": "💎",
            "skip": "⏭️"
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
# == UNIFIED PROCESSING MANAGER                                ==
# ================================================================

class UnifiedProcessingManager:
    """Manages the complete pipeline: URLs → JSON Generation → Video Processing"""
    
    def __init__(self, master_controller):
        self.master_controller = master_controller
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
        self.logger.setLevel(logging.INFO)
        
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        self.logger.addHandler(console_handler)
    
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
        """Create project slug from URL"""
        try:
            from urllib.parse import urlparse
            import re
            
            parsed_url = urlparse(url)
            path = parsed_url.path.strip('/')
            
            if path:
                slug = path.split('/')[-1]
            else:
                domain = parsed_url.netloc.lower()
                if domain.startswith('www.'):
                    domain = domain[4:]
                slug = domain.split('.')[0]
            
            slug = re.sub(r'[^a-zA-Z0-9\-]', '-', slug)
            slug = re.sub(r'-+', '-', slug).strip('-')
            
            return slug or "sprunki-game"
            
        except Exception:
            return f"sprunki-{len(self.queue)}"
    
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
        
        # Start processing thread
        processing_thread = threading.Thread(target=self._processing_loop, daemon=True)
        processing_thread.start()
        
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
    
    def _processing_loop(self):
        """Main processing loop"""
        try:
            self.logger.info("=== UNIFIED PROCESSING PIPELINE STARTED ===")
            
            for i, item in enumerate(self.queue):
                if self.should_stop:
                    break
                
                self.current_item_index = i
                
                if item.status != "pending":
                    continue
                
                self.logger.info(f"Processing item {i+1}/{len(self.queue)}: {item.display_name}")
                
                # PHASE 1: JSON Generation (if needed)
                if item.source_url and not item.json_data:
                    success = self._generate_json_for_item(item)
                    if not success and item.status == "failed":
                        self.total_failed += 1
                        continue
                
                # PHASE 2: Video Processing
                if item.json_data:
                    success = self._process_video_for_item(item)
                    if success:
                        self.total_completed += 1
                    else:
                        if item.status == "failed":
                            self.total_failed += 1
                        elif item.status == "skipped":
                            self.total_skipped += 1
                
                # Pause between items
                if not self.should_stop and i < len(self.queue) - 1:
                    pause_duration = self.config.pause_between_items
                    self.logger.info(f"Pausing {pause_duration}s before next item...")
                    time.sleep(pause_duration)
            
            self.logger.info("=== UNIFIED PROCESSING PIPELINE COMPLETED ===")
            
        except Exception as e:
            self.logger.error(f"Critical error in processing pipeline: {e}")
        finally:
            self.is_running = False
            self.is_paused = False
            # Notify main thread
            if hasattr(self.master_controller, 'root'):
                self.master_controller.root.after(100, self.master_controller.on_processing_complete)
    
    def _generate_json_for_item(self, item: ProcessingItem) -> bool:
        """Generate JSON for a URL item"""
        try:
            item.status = "generating"
            item.start_time = datetime.now()
            
            self.logger.info(f"Generating JSON for: {item.source_url}")
            
            success, json_path, json_data = self.json_generator.generate_complete_json(item.source_url)
            
            if success and json_data:
                item.json_path = json_path
                item.json_data = json_data
                item.status = "pending"  # Ready for video processing
                
                self.logger.info(f"JSON generated successfully: {json_path}")
                return True
            else:
                item.status = "failed"
                item.error_message = "JSON generation failed"
                item.end_time = datetime.now()
                
                self.logger.error(f"JSON generation failed for: {item.source_url}")
                return False
                
        except Exception as e:
            item.status = "failed"
            item.error_message = f"JSON generation error: {str(e)}"
            item.end_time = datetime.now()
            
            self.logger.error(f"JSON generation error for {item.source_url}: {e}")
            return False
    
    def _process_video_for_item(self, item: ProcessingItem) -> bool:
        """Process video for an item with JSON data"""
        try:
            item.status = "processing"
            if not item.start_time:
                item.start_time = datetime.now()
            
            # Initialize components
            if not self.master_controller._initialize_components(item.json_data):
                raise Exception("Failed to initialize components")
            
            # Setup project folder
            if item.mode in ["individual", "both"]:
                project_folder = os.path.join(self.config.output_base_dir, item.project_slug)
                os.makedirs(project_folder, exist_ok=True)
                self.master_controller.obs_controller.current_project_folder = project_folder
            else:
                self.master_controller.obs_controller.current_project_folder = self.config.output_base_dir
            
            # Process based on mode
            success = False
            if item.mode == "master":
                success = self._process_master_mode(item)
            elif item.mode == "individual":
                success = self._process_individual_mode(item)
            elif item.mode == "both":
                master_success = self._process_master_mode(item)
                
                # CHO OBS THỜI GIAN ĐỂ HOÀN TẤT VIỆC LƯU VIDEO MASTER
                if master_success:
                    self.logger.info("Master mode completed. Pausing for 5s to allow OBS to finalize the video file...")
                    time.sleep(5) # Khoảng nghỉ quan trọng!

                individual_success = self._process_individual_mode(item)
                success = master_success and individual_success
            elif item.mode == "skip":
                item.status = "skipped"
                self.total_skipped += 1
                return True
            
            if success:
                item.status = "completed"
                item.end_time = datetime.now()
                return True
            else:
                item.status = "failed"
                item.error_message = "Video processing failed"
                item.end_time = datetime.now()
                return False
                
        except Exception as e:
            item.status = "failed"
            item.error_message = f"Video processing error: {str(e)}"
            item.end_time = datetime.now()
            
            self.logger.error(f"Video processing error for {item.display_name}: {e}")
            return False
    
    def _process_master_mode(self, item: ProcessingItem) -> bool:
        """Process item in master mode"""
        try:
            master_filename = f"{item.project_slug}-showcase.mp4"
            
            self.logger.info(f"Executing master workflow for {item.display_name}")
            
            if not self.master_controller.obs_controller.full_setup(use_full_screen=True):
                raise Exception("OBS setup failed")
            
            if not self.master_controller.obs_controller.force_obs_filename(master_filename):
                self.logger.warning("Could not force filename")
            
            # CHỈ ĐỊNH ĐÂY LÀ LẦN SETUP ĐẦU TIÊN
            if not self.master_controller.game_player.setup_game_environment(first_setup=True):
                raise Exception("Game environment setup failed")
            
            if not self.master_controller.obs_controller.start_recording():
                raise Exception("Could not start recording")
            
            success = self.master_controller.game_player.execute_full_showcase_workflow_corrected()
            
            stop_success, output_path = self.master_controller.obs_controller.stop_recording()
            
            if success and stop_success and output_path:
                item.output_files.append(output_path)
                self.logger.info(f"Master video saved: {output_path}")
                return True
            else:
                raise Exception("Master workflow execution failed")
        
        except Exception as e:
            self.logger.error(f"Master mode error for {item.display_name}: {e}")
            return False
    
    def _process_individual_mode(self, item: ProcessingItem) -> bool:
        """Enhanced Individual mode with remix recording"""
        try:
            self.logger.info(f"Executing enhanced individual workflow for {item.display_name}")
            
            if not self.master_controller.obs_controller.full_setup(use_full_screen=False):
                raise Exception("OBS setup failed for individual mode")
            
            # TÁI SỬ DỤNG TAB, KHÔNG PHẢI LẦN SETUP ĐẦU
            if not self.master_controller.game_player.setup_game_environment(first_setup=False):
                raise Exception("Game environment re-setup failed")

            layout = item.json_data['layout']
            icons = layout['icons']
            slots = layout['slots']
            game_settings = item.json_data.get('gameSettings', {})
            has_horror = game_settings.get('hasHorror', False)
            
            target_slot = self.master_controller.game_player._find_slot_by_index(3)
            if not target_slot:
                raise Exception("Could not find target slot")
            
            output_files = []
            
            # ================================================================
            # PHASE 1: INDIVIDUAL ICON RECORDING (ENHANCED with cleanup)
            # ================================================================
            self.logger.info("🔹 PHASE 1: Recording individual icons (recordingArea frame)...")
            self.logger.info(f"📍 Using recordingArea: {item.json_data.get('layout', {}).get('recordingArea', 'Not found')}")
            
            # Process normal mode icons
            normal_range = range(len(icons) - 1) if has_horror else range(len(icons))
            for icon_index in normal_range:
                if self.should_stop:
                    break
                
                while self.is_paused and not self.should_stop:
                    time.sleep(0.5)
                
                if self.should_stop:
                    break
                
                filename = f"n-{icon_index}.mp4"
                
                # 🔥 BƯỚC 1: DRAG ICON
                current_icon = icons[icon_index]
                drag_success = self.master_controller.game_player.corrected_drag_and_drop(
                    current_icon, target_slot, is_first_icon=(icon_index == 0)
                )
                
                if not drag_success:
                    self.logger.warning(f"Failed to drag icon {icon_index}")
                    continue  # Drag thất bại -> không cần cleanup
                
                # 🔥 BƯỚC 2: OBS SETUP - THÊM ERROR HANDLING
                icon_placed_successfully = True  # Flag để track trạng thái
                
                try:
                    # Force OBS filename
                    if not self.master_controller.obs_controller.force_obs_filename(filename):
                        self.logger.warning(f"Could not force OBS filename for {filename}")
                        raise Exception("OBS filename setup failed")
                    
                    # Start recording
                    if not self.master_controller.obs_controller.start_recording():
                        self.logger.warning(f"Could not start recording for {filename}")
                        raise Exception("OBS recording start failed")
                    
                    # 🔥 BƯỚC 3: RECORDING THÀNH CÔNG
                    recording_duration = 14
                    for countdown in range(recording_duration, 0, -1):
                        if self.should_stop:
                            break
                        time.sleep(1)
                    
                    # Stop recording
                    stop_success, output_path = self.master_controller.obs_controller.stop_recording()
                    if stop_success and output_path:
                        output_files.append(output_path)
                        self.logger.info(f"Recorded: {output_path}")
                    else:
                        self.logger.warning(f"Failed to stop recording properly for {filename}")
                    
                except Exception as e:
                    # 🔥 QUAN TRỌNG: CLEANUP KHI OBS THẤT BẠI
                    self.logger.error(f"OBS error for icon {icon_index}: {e}")
                    self.logger.info(f"🔄 Cleaning up icon {icon_index} due to OBS failure...")
                    
                    # Return icon về vị trí ban đầu
                    cleanup_success = self.master_controller.game_player.corrected_return_icon(target_slot)
                    if cleanup_success:
                        self.logger.info(f"✅ Icon {icon_index} returned successfully after OBS failure")
                    else:
                        self.logger.warning(f"⚠️ Failed to return icon {icon_index} - slot may be occupied!")
                    
                    icon_placed_successfully = False
                    
                    # Try to stop any ongoing recording
                    try:
                        self.master_controller.obs_controller.stop_recording()
                    except:
                        pass
                
                # 🔥 BƯỚC 4: RETURN ICON (chỉ khi icon vẫn đang ở slot)
                if icon_placed_successfully:
                    self.master_controller.game_player.corrected_return_icon(target_slot)
                    self.logger.info(f"🔄 Icon {icon_index} returned after successful recording")
                
                if not self.should_stop:
                    time.sleep(1)
            
            # Process horror mode if applicable (SAME PATTERN)
            if has_horror and not self.should_stop:
                if self.master_controller.game_player.trigger_horror_mode_transition_corrected():
                    
                    for icon_index in range(len(icons)):
                        if self.should_stop:
                            break
                        
                        filename = f"h-{icon_index}.mp4"
                        current_icon = icons[icon_index]
                        
                        # 🔥 SAME ENHANCED PATTERN FOR HORROR ICONS
                        drag_success = self.master_controller.game_player.corrected_drag_and_drop(current_icon, target_slot, False)
                        
                        if not drag_success:
                            self.logger.warning(f"Failed to drag horror icon {icon_index}")
                            continue
                        
                        icon_placed_successfully = True
                        
                        try:
                            if not self.master_controller.obs_controller.force_obs_filename(filename):
                                raise Exception("OBS filename setup failed")
                            
                            if not self.master_controller.obs_controller.start_recording():
                                raise Exception("OBS recording start failed")
                            
                            for countdown in range(14, 0, -1):
                                if self.should_stop:
                                    break
                                time.sleep(1)
                            
                            stop_success, output_path = self.master_controller.obs_controller.stop_recording()
                            if stop_success and output_path:
                                output_files.append(output_path)
                                self.logger.info(f"Horror recorded: {output_path}")
                            
                        except Exception as e:
                            # 🔥 CLEANUP FOR HORROR ICONS TOO
                            self.logger.error(f"OBS error for horror icon {icon_index}: {e}")
                            self.logger.info(f"🔄 Cleaning up horror icon {icon_index} due to OBS failure...")
                            
                            cleanup_success = self.master_controller.game_player.corrected_return_icon(target_slot)
                            if cleanup_success:
                                self.logger.info(f"✅ Horror icon {icon_index} returned successfully after OBS failure")
                            else:
                                self.logger.warning(f"⚠️ Failed to return horror icon {icon_index}")
                            
                            icon_placed_successfully = False
                            
                            try:
                                self.master_controller.obs_controller.stop_recording()
                            except:
                                pass
                        
                        # Return icon if still placed
                        if icon_placed_successfully:
                            self.master_controller.game_player.corrected_return_icon(target_slot)
                        
                        if not self.should_stop:
                            time.sleep(1)
            
            # ================================================================
            # PHASE 2: REMIX RECORDING (giữ nguyên)
            # ================================================================
            if not self.should_stop:
                self.logger.info("🎵 PHASE 2: Recording remix videos (1920x1080 Master mode frame)...")
                remix_files = self._record_individual_remixes(item, icons, slots)
                output_files.extend(remix_files)
            
            item.output_files.extend(output_files)
            total_individual = len([f for f in output_files if not f.endswith(('remix-1.mp4', 'remix-2.mp4', 'remix-3.mp4'))])
            total_remixes = len([f for f in output_files if f.endswith(('remix-1.mp4', 'remix-2.mp4', 'remix-3.mp4'))])
            
            self.logger.info("✅ Enhanced Individual Mode completed successfully!")
            self.logger.info(f"📊 Summary: {total_individual} individual videos (recordingArea) + {total_remixes} remix videos (1920x1080) = {len(output_files)} total videos")
            self.logger.info("📍 Frame sizes: Individual icons use recordingArea crop, Remixes use Master mode 1920x1080")
            return len(output_files) > 0
            
        except Exception as e:
            self.logger.error(f"Individual mode error for {item.display_name}: {e}")
            return False
        finally:
            # LUÔN ĐÓNG CỬA SỔ GAME SAU KHI XONG INDIVIDUAL MODE
            if self.master_controller.game_player:
                self.master_controller.game_player.close_game_window()

    def _record_individual_remixes(self, item: ProcessingItem, icons, slots) -> List[str]:
        """Record remix videos for individual mode with Master mode frame size"""
        try:
            self.logger.info("🎼 Starting remix recording phase...")
            
            # SWITCH TO MASTER MODE RECORDING SETUP FOR REMIXES
            self.logger.info("🖥️ Switching to Master mode recording setup for remixes...")
            if not self.master_controller.obs_controller.full_setup(use_full_screen=True):
                self.logger.error("Failed to switch to Master mode setup for remixes")
                return []
            
            self.logger.info("✅ Master mode setup complete - remixes will be recorded in 1920x1080")
            
            # Generate 3 remixes specifically for individual mode
            remix_count = 3
            remixes = self._generate_individual_remixes(item.json_data, remix_count)
            
            if not remixes:
                self.logger.warning("Could not generate remixes for individual mode")
                return []
            
            remix_output_files = []
            
            for remix_index, remix in enumerate(remixes):
                if self.should_stop:
                    break
                
                remix_number = remix_index + 1
                filename = f"remix-{remix_number}.mp4"
                
                self.logger.info(f"🎵 Recording remix {remix_number}/3: {remix['name']}")
                
                # Set OBS filename
                self.master_controller.obs_controller.force_obs_filename(filename)
                
                # STEP 1: Drag all remix icons to slots
                self.logger.info(f"   🎯 Dragging {len(remix['icons'])} icons to slots...")
                
                drag_success = True
                for slot_position, icon_index in enumerate(remix['icons']):
                    if self.should_stop:
                        break
                    
                    # Validate indices
                    if icon_index >= len(icons) or slot_position >= len(slots):
                        self.logger.warning(f"Invalid index: icon {icon_index} or slot {slot_position}")
                        continue
                    
                    current_icon = icons[icon_index]
                    target_slot = slots[slot_position]
                    
                    self.logger.info(f"      → Icon {icon_index} to slot {slot_position}")
                    if not self.master_controller.game_player.corrected_drag_and_drop(current_icon, target_slot, False):
                        self.logger.warning(f"Failed to drag icon {icon_index} for remix {remix_number}")
                        drag_success = False
                        continue
                    
                    # Short delay between drags
                    time.sleep(0.3)
                
                if not drag_success or self.should_stop:
                    self.logger.warning(f"Skipping remix {remix_number} due to drag failures")
                    # Clean up any icons that were dragged
                    self._cleanup_remix_slots(slots, len(remix['icons']))
                    continue
                
                # STEP 2: Start recording AFTER all icons are in place
                self.logger.info(f"   🎬 Starting recording for remix {remix_number}...")
                
                if not self.master_controller.obs_controller.start_recording():
                    self.logger.warning(f"Could not start recording for remix {remix_number}")
                    # Clean up icons
                    self._cleanup_remix_slots(slots, len(remix['icons']))
                    continue
                
                # STEP 3: Record for 20 seconds (longer than individual icons)
                remix_duration = 20
                self.logger.info(f"   ⏱️ Recording remix for {remix_duration} seconds...")
                
                for countdown in range(remix_duration, 0, -1):
                    if self.should_stop:
                        break
                    if countdown % 5 == 0:  # Log every 5 seconds
                        self.logger.info(f"      Remix {remix_number} recording: {countdown}s remaining")
                    time.sleep(1)
                
                # STEP 4: Stop recording
                stop_success, output_path = self.master_controller.obs_controller.stop_recording()
                
                if stop_success and output_path:
                    remix_output_files.append(output_path)
                    self.logger.info(f"✅ Remix {remix_number} recorded: {output_path}")
                else:
                    self.logger.warning(f"Failed to stop recording for remix {remix_number}")
                
                # STEP 5: Clean up - return all icons from slots
                if not self.should_stop:
                    self.logger.info(f"   🧹 Cleaning up remix {remix_number}...")
                    self._cleanup_remix_slots(slots, len(remix['icons']))
                    
                    # Pause between remixes
                    if remix_index < len(remixes) - 1:
                        time.sleep(2)
            
            self.logger.info(f"🎉 Remix recording completed: {len(remix_output_files)}/3 remixes recorded")
            self.logger.info("📏 Remixes recorded in Master mode frame size (1920x1080)")
            self.logger.info("📋 Individual mode summary: Individual icons (recordingArea) + Remixes (1920x1080)")
            return remix_output_files
            
        except Exception as e:
            self.logger.error(f"Error in remix recording: {e}")
            return []

    def _cleanup_remix_slots(self, slots, icon_count):
        """Clean up icons from slots after remix recording"""
        try:
            self.logger.info(f"   🔄 Returning {icon_count} icons from slots...")
            
            for slot_position in range(min(icon_count, len(slots))):
                if self.should_stop:
                    break
                
                target_slot = slots[slot_position]
                self.master_controller.game_player.corrected_return_icon(target_slot)
                time.sleep(0.1)  # Small delay between returns
            
            # Additional wait for all animations to complete
            time.sleep(0.5)
            
        except Exception as e:
            self.logger.error(f"Error cleaning up remix slots: {e}")

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
        select_btn_text = "📍" if is_selected else "📌"
        
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
                if hasattr(widget, 'configure') and widget.winfo_class() in ['Label']:
                    widget.configure(bg=bg_color)
    
    def update_queue_summary(self):
        """Update queue summary display"""
        try:
            summary = self.processing_manager.get_queue_summary()
            
            summary_text = f"""📊 UNIFIED PROCESSING QUEUE SUMMARY:
📁 Total items: {summary['total_items']}
⏳ Pending: {summary['pending']}
🚀 Generating JSON: {summary['generating']}
🔄 Processing video: {summary['processing']}
✅ Completed: {summary['completed']}
❌ Failed: {summary['failed']}
⏭️ Skipped: {summary['skipped']}

⏱️ Status: {summary['processing_status']}"""

            # Add selection info
            if hasattr(self, 'selected_queue_index') and self.selected_queue_index is not None:
                if self.selected_queue_index < len(self.processing_manager.queue):
                    selected_item = self.processing_manager.queue[self.selected_queue_index]
                    summary_text += f"\n\n📌 Selected: {selected_item.display_name}"
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
            f"   📁 Total items: {summary['total_items']}\n"
            f"   ⏳ Pending: {summary['pending']}\n"
            f"   🚀 URLs to generate: {len([i for i in self.processing_manager.queue if i.source_url and not i.json_data])}\n"
            f"   📄 Ready JSONs: {len([i for i in self.processing_manager.queue if i.json_data])}\n\n"
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
                f"   ⏭️ Skipped: {summary['skipped']}\n\n"
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
        """Emergency stop"""
        if self.processing_manager.is_running:
            print("🛑 EMERGENCY STOP - Stopping unified pipeline...")
            self.processing_manager.stop_processing()
            
            messagebox.showwarning("🛑 Emergency Stop", "Unified processing pipeline stopped!")
            return
        
        self.should_stop = True
        
        if self.game_player:
            self.game_player.should_stop = True
            self.game_player.is_playing = False
        
        self.phase_var.set("🛑 EMERGENCY STOP")
        self.action_var.set("Stopped")
        
        messagebox.showwarning("⚠️ Emergency Stop", "Processing stopped!")
    
    def _initialize_components(self, config_data: Dict[str, Any]) -> bool:
        """Initialize game player and OBS controller"""
        try:
            # Initialize game player
            self.game_player = SprunkiGamePlayer()
            self.game_player.config = config_data
            
            # Check domain support (only cocrea.world supported)
            current_url = config_data.get('currentUrl', '').lower()
            if 'cocrea.world' not in current_url:
                from urllib.parse import urlparse
                parsed_url = urlparse(current_url)
                domain = parsed_url.netloc.lower().replace('www.', '')
                
                messagebox.showerror("Unsupported Domain", 
                    f"Domain '{domain}' is not supported!\n\n"
                    f"This version is optimized for 'cocrea.world' only.")
                return False
            
            # Check hasHorror setting để tạo remix phù hợp
            game_settings = config_data.get('gameSettings', {})
            has_horror = game_settings.get('hasHorror', False)
            
            # Generate remix library với logic tránh horror icon
            self.game_player.remix_library = self.game_player.generate_random_remixes(avoid_horror_icon=has_horror)
            self.game_player.current_remix_index = 0
            
            # Log remix generation info
            if has_horror:
                print("👻 Horror mode detected - Generated remixes avoid icon 19 for normal mode")
            else:
                print("🎪 Normal mode only - Generated remixes use all 20 icons")
            
            print(f"🎼 Created {len(self.game_player.remix_library)} horror-aware remixes")
            
            self.obs_controller = SprunkiController()
            
            # NOTE: OBS Controller uses hardcoded settings from sprunki_obs_controller.py
            # Host: 192.168.1.17, Port: 4455 - DO NOT override from config.json
            # OBS Controller uses hardcoded settings - skip config override
            # if not self.obs_controller.load_config_from_dict(config_data):
            #     messagebox.showerror("OBS Error", "Could not load config into OBS!")
            #     return False
            
            # Instead, just verify OBS controller can load basic recording area
            if not self.obs_controller.load_config_from_dict(config_data):
                messagebox.showerror("OBS Error", "Could not load recording area config!")
                return False
            
            self.config = config_data
            
            print("✅ Components initialized successfully for cocrea.world with horror-aware remix generation")
            return True
            
        except Exception as e:
            print(f"Error initializing components: {e}")
            messagebox.showerror("Initialization Error", f"Could not initialize components: {str(e)}")
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
        
        pipeline_text = """🌐 URL Input → 🚀 Auto JSON Generation → 🎮 Game Automation → 📹 OBS Recording → 📁 Organized Output
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
        
        self.add_files_btn = Button(buttons_frame, text="📁 Add Existing JSON Files", 
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

if __name__ == "__main__":
    print("🚀 Sprunki Ultimate Master Controller - Unified Pipeline")
    print("⚡ COMPLETE AUTOMATION: URL List → JSON Generation → Video Processing")
    print("🔄 UNIFIED ARCHITECTURE: Single interface, zero manual steps")
    print("📊 PROFESSIONAL RESULTS: Organized output, full error handling")
    print("="*75)
    
    try:
        master = SprunkiUltimateMaster()
        master.run()
    except Exception as e:
        print(f"❌ Error: {e}")
        input("Press Enter to exit...")

# Add this class to replace tkinter GUI
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
        from creator_api_client import CreatorService
        self.service = CreatorService()
        
        print("🤖 Creator Service Mode - No GUI")
    
    def run_as_service(self):
        """Run as API-driven service instead of GUI"""
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
            self.service.stop()
            print("✅ Creator Service stopped")
    
    # Keep existing methods for compatibility
    def _initialize_components(self, config_data):
        """Existing method - no changes"""
        # ... existing code unchanged
        pass
    
    def on_processing_complete(self):
        """Modified for service mode"""
        print("✅ Job processing completed")
        # No GUI updates needed

# Add service mode entry point
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "--service":
        # Run as API service
        print("🤖 Starting Sprunki Creator Service Mode")
        service = SprunkiCreatorService()
        service.run_as_service()
    else:
        # Run existing GUI mode
        print("🎮 Starting Sprunki Ultimate Master GUI")
        master = SprunkiUltimateMaster()
        master.run()        
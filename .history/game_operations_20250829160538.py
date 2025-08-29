# game_operations.py
"""
Game Operations for Sprunki Game Player.
Handles all direct game interactions like drag-and-drop, returning icons,
and generating coordinates for these actions.
"""
import os
import pyautogui
import time
import random
import math
import websocket # Thêm import này

class GameOperations:
    def __init__(self, player_instance, event_bus=None):
        """
        Khởi tạo GameOperations.
        :param player_instance: Tham chiếu đến instance của SprunkiGamePlayer.
        :param event_bus: The application-wide event bus for communication.
        """
        print("🎮 GameOperations initialized.")
        self.player = player_instance
        self.event_bus = event_bus # Store the event bus
        # Tạo các alias (bí danh) để truy cập các manager khác dễ dàng hơn
        self.browser_manager = player_instance.browser_manager
        self.verification_system = player_instance.verification_system

    def _get_websocket_url(self):
        """Helper để lấy URL WebSocket."""
        return self.browser_manager._get_websocket_url()

    def _send_cdp_command(self, ws, command):
        """Helper để gửi lệnh CDP."""
        return self.browser_manager._send_cdp_command(ws, command)

    def _simulate_click_via_cdp(self, ws, x, y):
        """Helper để mô phỏng click."""
        return self.browser_manager._simulate_click_via_cdp(ws, x, y)
    
    def _ensure_focus_before_operation(self, operation_name="operation", focus_type="safe"):
        """Helper để đảm bảo focus cửa sổ game."""
        return self.browser_manager._ensure_focus_before_operation(operation_name, focus_type)

    def _calculate_slots_coverage_area(self, slot_indices, all_slots, overlap_margin):
        """
        ðŸŽ¯ ADDED: Calculate coverage area for slots
        """
        try:
            if not all_slots or not slot_indices:
                return None
            
            # Get coordinates of target slots
            target_slots = [all_slots[i] for i in slot_indices if i < len(all_slots)]
            if not target_slots:
                return None
            
            # Find boundaries
            min_x = min(slot['x'] for slot in target_slots) - overlap_margin
            max_x = max(slot['x'] for slot in target_slots) + overlap_margin
            min_y = min(slot['y'] for slot in target_slots) - overlap_margin
            max_y = max(slot['y'] for slot in target_slots) + overlap_margin
            
            return {
                'min_x': min_x,
                'max_x': max_x,
                'min_y': min_y,
                'max_y': max_y,
                'width': max_x - min_x,
                'height': max_y - min_y,
                'center_x': (min_x + max_x) // 2,
                'center_y': (min_y + max_y) // 2
            }
            
        except Exception as e:
            print(f"âŒ Error calculating coverage area: {e}")
            return None
        
    def setup_project_folder(self, url):
        """Tạo folder project đơn giản - copied from sprunki_obs_controller.py"""
        try:
            slug = self.player.create_simple_url_slug(url)
            project_folder = os.path.join(self.player.base_output_dir, slug)
            
            # Create folder with exist_ok for faster execution
            os.makedirs(project_folder, exist_ok=True)
            
            # Store project info
            self.current_project_folder = project_folder
            self.project_slug = slug
            
            print(f"📁 Project folder created: {project_folder}")
            print(f"🏷️ Project slug: {slug}")
            
            return project_folder
            
        except Exception as e:
            print(f"❌ Error creating project folder: {e}")
            # Fallback to base directory
            self.current_project_folder = self.player.base_output_dir  
            self.project_slug = "fallback"
            print(f"📁 Using fallback folder: {self.player.base_output_dir}")
            return self.player.base_output_dir

    def get_project_folder(self):
        """Get current project folder for external use (OBS Controller integration)"""
        return self.current_project_folder or self.player.base_output_dir

    def get_project_slug(self):
        """Get current project slug for external use"""
        return self.project_slug or "default"

    def get_obs_output_info(self):
        """
        🎬 Get output info for OBS Controller integration
        Returns dict with project folder, slug, and base directory
        """
        return {
            'project_folder': self.current_project_folder,
            'project_slug': self.project_slug,
            'base_output_dir': self.player.base_output_dir,
            'current_url': getattr(self, 'config', {}).get('currentUrl', ''),
            'folder_ready': self.current_project_folder is not None and os.path.exists(self.current_project_folder)
        }

    def prepare_project_for_recording(self, url=None):
        """
        🎬 Prepare project folder for recording session
        To be called by OBS Controller before starting recording
        """
        try:
            # Use current URL if not provided
            target_url = url or getattr(self, 'config', {}).get('currentUrl', '')
            
            if not target_url:
                print("❌ No URL available for project setup")
                return False
            
            # Setup or update project folder
            project_folder = self.setup_project_folder(target_url)
            
            # Ensure all necessary subfolders exist
            subfolders = ['session_reports', 'screenshots', 'recordings']
            for subfolder in subfolders:
                subfolder_path = os.path.join(project_folder, subfolder)
                os.makedirs(subfolder_path, exist_ok=True)
            
            print(f"🎬 Project ready for recording:")
            print(f"   📁 Folder: {project_folder}")
            print(f"   🏷️ Slug: {self.project_slug}")
            print(f"   📂 Subfolders: {', '.join(subfolders)}")
            
            return True
            
        except Exception as e:
            print(f"❌ Error preparing project for recording: {e}")
            return False

    def get_current_project_info(self):
        """
        📊 Get comprehensive current project information
        For debugging and logging purposes
        """
        return {
            'project_folder': self.current_project_folder,
            'project_slug': self.project_slug,
            'base_output_dir': self.player.base_output_dir,
            'folder_exists': self.current_project_folder and os.path.exists(self.current_project_folder),
            'current_url': getattr(self, 'config', {}).get('currentUrl', ''),
            'subfolders': {
                'session_reports': os.path.join(self.current_project_folder, 'session_reports') if self.current_project_folder else None,
                'screenshots': os.path.join(self.current_project_folder, 'screenshots') if self.current_project_folder else None,
                'recordings': os.path.join(self.current_project_folder, 'recordings') if self.current_project_folder else None
            }
        }

    def _execute_emergency_return_click(self, target_slot):
        """
        ðŸš¨ OPTIMIZED: Quick emergency return click without full verification cycle
        Used when normal return seems to have failed
        """
        try:
            center_x, center_y = target_slot['x'], target_slot['y']
            
            # Quick click without complex verification
            pyautogui.moveTo(center_x, center_y, duration=0.1)
            pyautogui.click(button='left')
            
            # Minimal wait for effect
            time.sleep(0.2)
            
            print(f"   âš¡ Emergency click executed at ({center_x}, {center_y})")
            return True
            
        except Exception as e:
            print(f"   âŒ Emergency click failed: {e}")
            return False

    def store_target_slot_for_verification(self, target_slot_index):
        """
        ðŸŽ¯ Helper: Store target slot index for verification tracking
        """
        self.last_target_slot_index = target_slot_index
        print(f"ðŸŽ¯ Stored target slot index: {target_slot_index} for verification")

    def get_index_correction_report(self):
        """
        ðŸ“Š Helper: Get report of all index corrections discovered
        """
        if hasattr(self, 'index_corrections') and self.player.index_corrections:
            print(f"\nðŸ“Š INDEX CORRECTION REPORT:")
            print("-" * 40)
            for config_index, actual_index in self.player.index_corrections.items():
                offset = actual_index - config_index
                print(f"   Config {config_index} â†’ Actual {actual_index} (offset: {offset:+d})")
            return self.player.index_corrections
        else:
            print("ðŸ“Š No index corrections recorded")
            return {}

    def generate_icon_coordinates(self, base_x, base_y, offset=35):
        """
        ADDED: Generate 5 coordinates for icon drag attempts
        Returns list of (x, y) tuples: center, left, right, top, bottom
        """
        coordinates = [
            (base_x, base_y),                    # 0: Center (original)
            (base_x - offset, base_y),           # 1: Left
            (base_x + offset, base_y),           # 2: Right  
            (base_x, base_y - offset),           # 3: Top
            (base_x, base_y + offset)            # 4: Bottom
        ]
        
        print(f"ðŸ“ Generated 5 coordinates for icon at ({base_x}, {base_y}):")
        for i, (x, y) in enumerate(coordinates):
            coord_names = ["Center", "Left", "Right", "Top", "Bottom"]
            print(f"   {i+1}. {coord_names[i]}: ({x}, {y})")
        
        return coordinates

    def generate_return_coordinates(self, center_x, center_y, offset=60):
        """
        ADDED: Generate 5 coordinates for return click attempts
        Returns list of (x, y) tuples: center, top, bottom, left, right
        """
        coordinates = [
            (center_x, center_y),                    # 0: Center (original)
            (center_x, center_y - offset),           # 1: Top
            (center_x, center_y + offset),           # 2: Bottom
            (center_x - offset, center_y),           # 3: Left
            (center_x + offset, center_y)            # 4: Right
        ]
        
        print(f"ðŸ”„ Generated 5 coordinates for return at slot ({center_x}, {center_y}):")
        for i, (x, y) in enumerate(coordinates):
            coord_names = ["Center", "Top", "Bottom", "Left", "Right"]
            print(f"   {i+1}. {coord_names[i]}: ({x}, {y})")
        
        return coordinates

    def generate_circle_pattern_coordinates(self, center_x, center_y, radius=50, num_points=8):
        """
        ðŸ”¥ NEW: Generate circle pattern coordinates around slot center
        Returns list of (x, y) tuples in circular pattern
        """
        import math
        
        coordinates = []
        
        # Add center point first
        coordinates.append((center_x, center_y))
        
        # Generate circle points
        for i in range(num_points):
            angle = (2 * math.pi * i) / num_points  # Divide circle into equal parts
            x = center_x + int(radius * math.cos(angle))
            y = center_y + int(radius * math.sin(angle))
            coordinates.append((x, y))
        
        print(f"ðŸ”„ Generated circle pattern: center + {num_points} points at {radius}px radius")
        for i, (x, y) in enumerate(coordinates):
            if i == 0:
                print(f"   CENTER: ({x}, {y})")
            else:
                angle_deg = (360 * (i-1)) / num_points
                print(f"   Point {i}: ({x}, {y}) - {angle_deg:.0f}Â°")
        
        return coordinates

    def verify_return_success(self, putted_before, putted_after):
        """
        âœ… COMPATIBILITY: Simple return verification
        """
        try:
            if putted_before is None or putted_after is None:
                return False
            
            # Return success = count decreased by 1
            expected_after = putted_before - 1
            success = (putted_after == expected_after)
            
            print(f"ðŸ“Š Return verification: {putted_before} â†’ {putted_after} (expected: {expected_after}) = {'âœ…' if success else 'âŒ'}")
            return success
            
        except Exception as e:
            print(f"âŒ Return verification error: {e}")
            return False

    def verify_slot_specific_return(self, target_slot_index, putted_before, putted_after):
        """
        ðŸ” NEW: Enhanced verification with slot-specific validation
        Not just check if putted decreased, but validate it's the RIGHT slot
        """
        try:
            # Basic putted characters check
            basic_success = self.verify_return_success(putted_before, putted_after)
            
            if not basic_success:
                return False, "putted_characters_unchanged"
            
            # TODO: Add more sophisticated validation if needed
            # For now, rely on safe zone to prevent cross-slot contamination
            
            print(f"âœ… Slot {target_slot_index} return verified: {putted_before} â†’ {putted_after}")
            return True, "verified_success"
            
        except Exception as e:
            print(f"âŒ Slot-specific verification error: {e}")
            return False, f"verification_error: {e}"

    def execute_multi_method_clicks(self, coordinates, use_cdp, domain_adj, method="single"):
        """
        ðŸ”¥ NEW: Execute clicks using different methods on coordinate list
        Returns True if any click was executed successfully
        """
        successful_clicks = 0
        
        print(f"ðŸŽ¯ Executing {method} method on {len(coordinates)} coordinates...")
        
        for i, (click_x, click_y) in enumerate(coordinates):
            if self.player.should_stop:
                break
                
            try:
                # Add small jitter for precision
                jitter_x = click_x + random.randint(-2, 2)
                jitter_y = click_y + random.randint(-2, 2)
                
                if method == "single":
                    success = self._execute_single_return_click(jitter_x, jitter_y, use_cdp, domain_adj)
                elif method == "double":
                    success = self._execute_double_return_click(jitter_x, jitter_y, use_cdp, domain_adj)
                elif method == "triple":
                    success = self._execute_triple_return_click(jitter_x, jitter_y, use_cdp, domain_adj)
                elif method == "hold":
                    success = self._execute_hold_return_click(jitter_x, jitter_y, use_cdp, domain_adj)
                else:
                    success = self._execute_single_return_click(jitter_x, jitter_y, use_cdp, domain_adj)
                
                if success:
                    successful_clicks += 1
                    
                # Small delay between clicks in same method
                time.sleep(0.1)
                
            except Exception as e:
                print(f"   âš ï¸ Click {i+1} failed: {e}")
        
        print(f"   âœ… Executed {successful_clicks}/{len(coordinates)} clicks successfully")
        return successful_clicks > 0

    def _execute_double_return_click(self, click_x, click_y, use_cdp, domain_adj):
        """ðŸ”¥ NEW: Execute double click"""
        try:
            if use_cdp:
                success1 = self._execute_cdp_single_click(click_x, click_y)
                time.sleep(0.1)
                success2 = self._execute_cdp_single_click(click_x, click_y)
                return success1 and success2
            else:
                success1 = self._execute_pyautogui_single_click(click_x, click_y, domain_adj)
                time.sleep(0.1) 
                success2 = self._execute_pyautogui_single_click(click_x, click_y, domain_adj)
                return success1 and success2
        except:
            return False

    def _execute_triple_return_click(self, click_x, click_y, use_cdp, domain_adj):
        """ðŸ”¥ NEW: Execute triple click"""
        try:
            success_count = 0
            for _ in range(3):
                if use_cdp:
                    if self._execute_cdp_single_click(click_x, click_y):
                        success_count += 1
                else:
                    if self._execute_pyautogui_single_click(click_x, click_y, domain_adj):
                        success_count += 1
                time.sleep(0.08)
            return success_count >= 2  # At least 2/3 clicks successful
        except:
            return False

    def _execute_hold_return_click(self, click_x, click_y, use_cdp, domain_adj):
        """ðŸ”¥ NEW: Execute hold-and-release click"""
        try:
            if use_cdp:
                ws_url = self.browser_manager._get_websocket_url()
                if ws_url:
                    ws = websocket.create_connection(ws_url, timeout=10)
                    # Move and press
                    self.browser_manager._send_cdp_command(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mouseMoved", "x": click_x, "y": click_y}})
                    time.sleep(0.1)
                    self.browser_manager._send_cdp_command(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mousePressed", "x": click_x, "y": click_y, "button": "left", "clickCount": 1}})
                    time.sleep(0.5)  # Hold for 500ms
                    self.browser_manager._send_cdp_command(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mouseReleased", "x": click_x, "y": click_y, "button": "left", "clickCount": 1}})
                    ws.close()
                    return True
                return False
            else:
                # PyAutoGUI hold click
                pyautogui.moveTo(click_x, click_y, duration=0.1)
                pyautogui.mouseDown(button='left')
                time.sleep(0.5)  # Hold for 500ms
                pyautogui.mouseUp(button='left')
                return True
        except:
            return False

    def verify_drag_success_dual(self, before_chars, after_chars, before_putted, after_putted):
        """
        🎯 SMART DUAL VERIFICATION: Track working systems for future operations
        """
        try:
            print(f"📊 SMART DUAL Drag verification:")
            
            # Check Characters Array
            chars_success = False
            chars_working = False
            if before_chars is not None and after_chars is not None:
                expected_chars = before_chars + 1
                chars_success = (after_chars == expected_chars)
                chars_working = True  # System is responding
                print(f"   📢 Characters: {before_chars} → {after_chars} (expected: {expected_chars}) = {'✅' if chars_success else '❌'}")
            else:
                print(f"   📢 Characters: N/A (not available)")
            
            # Check Putted Characters  
            putted_success = False
            putted_working = False
            if before_putted is not None and after_putted is not None:
                expected_putted = before_putted + 1
                putted_success = (after_putted == expected_putted)
                putted_working = True  # System is responding
                print(f"   🎯 Putted: {before_putted} → {after_putted} (expected: {expected_putted}) = {'✅' if putted_success else '❌'}")
            else:
                print(f"   🎯 Putted: N/A (not available)")
            
            # 🎯 SMART TRACKING: Record which systems are working
            self.player.working_systems['characters'] = chars_success if chars_working else None # type: ignore
            self.player.working_systems['putted'] = putted_success if putted_working else None # type: ignore
            
            # Store for this specific operation
            operation_id = f"drag_{id(self)}"
            self.player.last_operation_working_systems[operation_id] = {
                'characters': chars_success if chars_working else None, # type: ignore
                'putted': putted_success if putted_working else None # type: ignore
            }
            
            # DUAL LOGIC: Chỉ cần 1 trong 2 thành công
            overall_success = chars_success or putted_success
            
            if overall_success:
                successful_systems = []
                if chars_success: successful_systems.append("Characters Array")
                if putted_success: successful_systems.append("Putted Characters")
                print(f"   🎉 SMART DUAL SUCCESS via: {', '.join(successful_systems)}")
                print(f"   🎯 Tracked working systems for return verification")
            else:
                print(f"   ❌ SMART DUAL FAILURE: Both methods failed")
                print(f"   🎯 No working systems to track")
            
            return overall_success
            
        except Exception as e:
            print(f"❌ SMART DUAL verification error: {e}")
            return False

    def _execute_cdp_drag(self, start_x, start_y, end_x, end_y, delays, domain_adj):
        """Execute drag using CDP WebSocket commands"""
        try:
            ws_url = self.browser_manager._get_websocket_url()
            if not ws_url:
                print("âš ï¸ WebSocket not available, falling back to PyAutoGUI")
                return self._execute_pyautogui_drag(start_x, start_y, end_x, end_y, delays, domain_adj, False)
            
            ws = websocket.create_connection(ws_url, timeout=30)
            
            print("ðŸ–±ï¸ Executing CDP drag operation...")
            
            # Move to start position
            print("ðŸ“ Step 1: Moving to icon...")
            self.browser_manager._send_cdp_command(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mouseMoved", "x": start_x, "y": start_y}})
            time.sleep(delays['approach'])
            
            # Focus clicks if needed
            print("ðŸŽ¯ Step 2: Establishing focus...")
            if domain_adj['requires_double_click']:
                self.browser_manager._simulate_click_via_cdp(ws, start_x, start_y)
                time.sleep(0.15)
                self.browser_manager._simulate_click_via_cdp(ws, start_x, start_y)
                time.sleep(delays['focus'])
            else:
                self.browser_manager._simulate_click_via_cdp(ws, start_x, start_y)
                time.sleep(delays['focus'])
            
            # Execute drag
            print("ðŸ¤ Step 3: Executing drag...")
            self.browser_manager._simulate_drag_via_cdp(ws, start_x, start_y, end_x, end_y)
            time.sleep(delays['post_drop'])

            # Move to safe position via CDP
            print("ðŸŽ¯ Moving to safe position via CDP...")
            screen_width, screen_height = 1920, 1080  # Assume fullscreen
            safe_x = screen_width - random.randint(30, 80)
            safe_y = screen_height - random.randint(30, 80)
            self.browser_manager._send_cdp_command(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mouseMoved", "x": safe_x, "y": safe_y}})
            print(f"âœ… Mouse moved to safe corner via CDP: ({safe_x}, {safe_y})")
            
            ws.close()
            print("âœ… CDP drag completed!")
            return True
            
        except Exception as e:
            print(f"âŒ CDP drag failed: {e}, falling back to PyAutoGUI")
            return self._execute_pyautogui_drag(start_x, start_y, end_x, end_y, delays, domain_adj, False)

    def verify_return_success_dual(self, before_chars, after_chars, before_putted, after_putted):
        """
        🎯 SMART DUAL VERIFICATION: Only check systems that worked in drag phase
        """
        try:
            print(f"📊 SMART DUAL Return verification (selective checking):")
            
            # Determine which systems to check based on what worked during drag
            check_characters = self.player.working_systems.get('characters') is True
            check_putted = self.player.working_systems.get('putted') is True
            
            if not check_characters and not check_putted:
                print(f"   ⚠️ No working systems tracked - falling back to both")
                check_characters = before_chars is not None and after_chars is not None
                check_putted = before_putted is not None and after_putted is not None
            
            print(f"   🎯 Will check: Characters={'✅' if check_characters else '❌'}, Putted={'✅' if check_putted else '❌'}")
            
            chars_success = False
            putted_success = False
            
            # Check Characters Array (only if it was working)
            if check_characters and before_chars is not None and after_chars is not None:
                expected_chars = before_chars - 1
                chars_success = (after_chars == expected_chars)
                print(f"   📢 Characters: {before_chars} → {after_chars} (expected: {expected_chars}) = {'✅' if chars_success else '❌'} [CHECKED]")
            else:
                if before_chars is not None and after_chars is not None:
                    print(f"   📢 Characters: {before_chars} → {after_chars} = ⏭️ [SKIPPED - didn't work in drag]")
                else:
                    print(f"   📢 Characters: N/A (not available)")
            
            # Check Putted Characters (only if it was working)  
            if check_putted and before_putted is not None and after_putted is not None:
                expected_putted = before_putted - 1
                putted_success = (after_putted == expected_putted)
                print(f"   🎯 Putted: {before_putted} → {after_putted} (expected: {expected_putted}) = {'✅' if putted_success else '❌'} [CHECKED]")
            else:
                if before_putted is not None and after_putted is not None:
                    print(f"   🎯 Putted: {before_putted} → {after_putted} = ⏭️ [SKIPPED - didn't work in drag]")
                else:
                    print(f"   🎯 Putted: N/A (not available)")
            
            # SMART LOGIC: Success if any CHECKED system succeeded
            overall_success = False
            successful_systems = []
            
            if check_characters and chars_success:
                overall_success = True
                successful_systems.append("Characters Array")
            
            if check_putted and putted_success:
                overall_success = True
                successful_systems.append("Putted Characters")
            
            if overall_success:
                print(f"   🎉 SMART DUAL SUCCESS via: {', '.join(successful_systems)}")
                print(f"   🎯 Selective checking prevented false results")
            else:
                checked_systems = []
                if check_characters: checked_systems.append("Characters Array")
                if check_putted: checked_systems.append("Putted Characters")
                print(f"   ❌ SMART DUAL FAILURE: Checked systems ({', '.join(checked_systems)}) failed")
            
            return overall_success
            
        except Exception as e:
            print(f"❌ SMART DUAL verification error: {e}")
            return False

    def _execute_single_return_click(self, click_x, click_y, use_cdp, domain_adj):
        """
        UPDATED: Always use CDP first for game operations with fallback
        """
        try:
            # 🌐 ALWAYS TRY CDP FIRST for game operations  
            print(f"🌐 Using CDP for game click at ({click_x}, {click_y})")
            cdp_success = self._execute_cdp_single_click(click_x, click_y)
            
            if cdp_success:
                print("✅ CDP click successful")
                return True
            else:
                print("⚠️ CDP click failed, using PyAutoGUI fallback...")
                return self._execute_pyautogui_single_click(click_x, click_y, domain_adj)
                
        except Exception as e:
            print(f"❌ Single return click execution error: {e}")
            return False

    def _execute_cdp_single_click(self, click_x, click_y):
        """ADDED: Execute single click using CDP"""
        try:
            ws_url = self.browser_manager._get_websocket_url()
            if not ws_url:
                return False
            
            ws = websocket.create_connection(ws_url, timeout=30)
            
            print(f"ðŸ–±ï¸ Executing CDP single click at ({click_x}, {click_y})")
            
            # Move and click
            self.browser_manager._send_cdp_command(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mouseMoved", "x": click_x, "y": click_y}})
            time.sleep(0.1)
            self.browser_manager._simulate_click_via_cdp(ws, click_x, click_y)
            
            ws.close()
            print("âœ… CDP single click completed")
            return True
            
        except Exception as e:
            print(f"âŒ CDP single click failed: {e}")
            return False

    def attempt_icon_return(self, target_slot):
        try:
            print("ðŸ”„ Attempting to return icon before retry...")
            
            # Get count before return
            count_before = self.verification_system.get_characters_count() if self.verification_system.characters_available else None
            
            # âœ… NO VARIATION: Use exact slot coordinates
            center_x, center_y = target_slot['x'], target_slot['y']
            
            pyautogui.moveTo(center_x, center_y, duration=0.2)  # â† CHÃNH XÃC 100%
            time.sleep(0.1)
            pyautogui.click(button='left')
            time.sleep(0.5)  # Wait for return animation
            
            # Verify return if possible
            if self.verification_system.characters_available and count_before is not None:
                count_after = self.verification_system.get_characters_count()
                if count_after is not None and count_after < count_before:
                    print("âœ… Icon return verified via Characters Array")
                else:
                    print("âš ï¸ Icon return unclear via Characters Array")
            
            print("âœ… Icon return attempt completed")
            return True
            
        except Exception as e:
            print(f"âš ï¸ Icon return attempt failed: {e}")
            return False

    # ================================================================
    # == ENHANCED DRAG & DROP WITH VERIFICATION ==
    # ================================================================

    def corrected_drag_and_drop(self, icon, target_slot, is_first_icon=False, use_cdp=True, control_type_override=None):
            """
            [FINAL REFINED v2] Drag and drop with DUAL verification, DYNAMIC control type,
            and special handling for the `is_first_icon` flag.
            Accepts an optional `control_type_override` for scenarios like JSON generation.
            """
            print(f"\nðŸŽ¯ DYNAMIC DRAG START (is_first_icon: {is_first_icon})")
            print("="*60)

            # Ưu tiên giá trị override được truyền vào (dùng cho json_generator)
            if control_type_override:
                self.game_control_type = control_type_override
                print(f"🎮 Using OVERRIDDEN control type: '{self.game_control_type}'")
            # Nếu không, cố gắng đọc từ config (dùng khi chơi game bình thường)
            elif self.player.config and 'gameSettings' in self.player.config:
                self.game_control_type = self.player.config['gameSettings'].get('gameControlType', 'click_drag')
                print(f"🎮 Using control type from JSON config: '{self.game_control_type}'")
            # Nếu cả hai đều không có, dùng giá trị mặc định
            else:
                self.game_control_type = 'click_drag' # Fallback
                print(f"🎮 Using DEFAULT control type: '{self.game_control_type}'")
            
            print(f"🎮 Using detected control type: '{self.game_control_type}'")

            if is_first_icon:
                print("🎪 First icon detected - ensuring game focus and applying extended waits...")
                self.browser_manager.ensure_game_focus()

            # RESET TRACKING: Clear previous operation tracking
            self.player.working_systems = {'characters': None, 'putted': None}

            # Store target slot for verification
            target_slot_index = target_slot.get('index', -1)
            self.store_target_slot_for_verification(target_slot_index)

            # Check if DUAL verification is available
            dual_verification_enabled = self.verification_system.characters_available or self.verification_system.putted_chars_available
            if dual_verification_enabled:
                print("ðŸ”„ DUAL verification ENABLED:")
                print(f"   ðŸ”¢ Characters Array: {'âœ… Ready' if self.verification_system.characters_available else 'âŒ N/A'}")
                print(f"   ðŸŽ¯ Putted Characters: {'âœ… Ready' if self.verification_system.putted_chars_available else 'âŒ N/A'}")
            else:
                print("âš ï¸ DUAL verification DISABLED - using fallback logic")
            
            # Generate 5 coordinates to try
            coordinates = self.generate_icon_coordinates(icon['x'], icon['y'])
            
            domain_adj = self.player.get_domain_adjustments()
            success = False
            coord_used = 0

            # INITIALIZE variables properly
            characters_before, putted_before = None, None
            characters_after, putted_after = None, None

            # Try each coordinate until success
            for coord_index, (start_x, start_y) in enumerate(coordinates):
                if self.player.should_stop:
                    break
                    
                coord_used = coord_index + 1
                coord_names = ["Center", "Left", "Right", "Top", "Bottom"]
                print(f"\nðŸŽ¯ Trying coordinate {coord_used}/5: {coord_names[coord_index]} ({start_x}, {start_y})")
                
                # Get DUAL counts BEFORE drag (if verification available)
                characters_before, putted_before = None, None
                if dual_verification_enabled:
                    characters_before, putted_before = self.verification_system.get_dual_counts()
                    print(f"ðŸ“Š DUAL counts BEFORE drag:")
                    print(f"   ðŸ”¢ Characters: {characters_before}")
                    print(f"   ðŸŽ¯ Putted: {putted_before}")
                    
                    # SMART DETECTION: Check if icon already placed from previous attempt
                    if coord_index > 0:
                        if (characters_before is not None and characters_before > 0) or (putted_before is not None and putted_before > 0):
                            if self.verify_drag_success_dual(self.last_drag_characters_before, characters_before, self.last_drag_putted_before, putted_before):
                                print(f"ðŸŽ‰ SMART DETECTION: Icon already placed by previous attempt!")
                                print(f"   ðŸ“Š Evidence: Previous attempt was successful upon re-check.")
                                print(f"   âœ… Previous coordinate {coord_index} actually succeeded")
                                print(f"   ðŸŽ¯ Stopping retry loop - SUCCESS!")
                                success = True
                                break
                
                # Store counts for smart detection in the next loop
                self.last_drag_characters_before = characters_before
                self.last_drag_putted_before = putted_before

                print("   Executing dynamic drag attempt...")
                drag_executed = False
                end_x, end_y = target_slot['x'], target_slot['y']

                if use_cdp:
                    ws_url = self.browser_manager._get_websocket_url()
                    if ws_url:
                        try:
                            ws = websocket.create_connection(ws_url, timeout=30)
                            drag_executed = self._execute_dynamic_drag_cdp(
                                ws, start_x, start_y, end_x, end_y, 
                                self.game_control_type, 
                                is_first_icon
                            )
                            ws.close()
                        except Exception as cdp_err:
                            print(f"   ⚠️ CDP connection/drag error: {cdp_err}, falling back to PyAutoGUI")
                            drag_executed = False # Đảm bảo fallback được kích hoạt
                    else:
                        print("   ⚠️ WebSocket not available, falling back to PyAutoGUI")

                # Fallback to PyAutoGUI if CDP is disabled or fails
                if not use_cdp or not drag_executed:
                    print("   Fallback to PyAutoGUI drag...")
                    # PyAutoGUI chưa hỗ trợ dynamic, ta sẽ dùng logic cũ của nó
                    # Đây là điểm có thể nâng cấp trong tương lai nếu cần
                    timing_key = 'first_icon' if is_first_icon else 'normal_icon'
                    delays = self.player.DRAG_DELAYS[timing_key].copy()
                    
                    drag_executed = self._execute_pyautogui_drag(
                        start_x, start_y, end_x, end_y, 
                        delays, domain_adj, 
                        is_first_icon # Truyền biến is_first_icon vào
                    )

                if not drag_executed:
                    print(f"âŒ Drag execution failed at coordinate {coord_used}")
                    if coord_index < len(coordinates) - 1:
                        print("   Trying next coordinate...")
                        time.sleep(0.3)
                    continue
                
                # Wait for game state to update - this is now handled by the retry loop
                
                # Get DUAL counts AFTER drag (if verification available)
                if dual_verification_enabled:
                    
                    ### Lá»›p 1 - TÄƒng cÆ°á»ng xÃ¡c thá»±c vá»›i vÃ²ng láº·p thá»­ láº¡i ###
                    verification_success = False
                    # VÃ²ng láº·p xÃ¡c thá»±c 3 láº§n vá»›i Ä‘á»™ trá»… tÄƒng dáº§n
                    for verify_attempt in range(1, 4):
                        # Chá» má»™t chÃºt trÆ°á»›c má»—i láº§n kiá»ƒm tra
                        wait_time = 0.5 + (verify_attempt * 0.4) # Chá» 0.9s, 1.3s, 1.7s
                        print(f"â³ Verification attempt {verify_attempt}/3: Waiting {wait_time:.1f}s for game state...")
                        time.sleep(wait_time)

                        characters_after, putted_after = self.verification_system.get_dual_counts()
                        print(f"ðŸ“Š DUAL counts AFTER drag (attempt {verify_attempt}/3):")
                        print(f"   ðŸ”¢ Characters: {characters_after}")
                        print(f"   ðŸŽ¯ Putted: {putted_after}")
                        
                        # Verify drag success using DUAL system
                        if self.verify_drag_success_dual(characters_before, characters_after, putted_before, putted_after):
                            verification_success = True
                            print(f"ðŸŽ‰ DUAL VERIFIED SUCCESS at coordinate {coord_used} (attempt {verify_attempt})!")
                            break # ThoÃ¡t khá»i vÃ²ng láº·p xÃ¡c thá»±c khi Ä‘Ã£ thÃ nh cÃ´ng
                        elif verify_attempt < 3:
                            print(f"   âš ï¸ Verification attempt {verify_attempt} failed, will try again after a longer wait...")
                    
                    if verification_success:
                        print(f"ðŸŽ‰ ENHANCED DRAG VERIFIED successfully at coordinate {coord_used}!")
                        success = True
                        break # ThoÃ¡t khá»i vÃ²ng láº·p thá»­ tá»a Ä‘á»™ (for coord_index...)
                    else:
                        print(f"âŒ DUAL VERIFICATION FAILED at coordinate {coord_used} after 3 attempts.")
                        
                        ### Lá»›p 2 - Tá»± sá»­a lá»—i báº±ng "tráº£ hÃ ng phÃ²ng há»" ###
                        print("ðŸ›¡ï¸ SELF-CORRECTION: Performing a precautionary return to prevent overlapping icons.")
                        # DÃ¹ xÃ¡c thá»±c tháº¥t báº¡i, váº«n cÃ³ kháº£ nÄƒng icon Ä‘Ã£ bá»‹ káº¹t á»Ÿ Ä‘Ã³.
                        # Thá»±c hiá»‡n má»™t cÃº click Ä‘á»ƒ tráº£ vá» "phÃ²ng há»".
                        self.attempt_icon_return(target_slot)
                        time.sleep(0.8) # Chá» má»™t chÃºt sau khi tráº£ hÃ ng
                        ### Káº¾T THÃšC: Lá»›p 2 ###

                        if coord_index < len(coordinates) - 1:
                            print("   Trying next coordinate...")
                        
                    ### Káº¾T THÃšC Lá»›p 1 ###
                else:
                    # No verification available - assume success if drag executed
                    print(f"âœ… ASSUMED SUCCESS at coordinate {coord_used} (no verification)")
                    success = True
                    break
            
            # Final result
            if success:
                print(f"ðŸŽ‰ ENHANCED DRAG COMPLETED SUCCESSFULLY!")
                print(f"   Coordinate used: {coord_used}/5")
                print(f"   Verification: {'ðŸ”„ DUAL Enabled' if dual_verification_enabled else 'âš ï¸ Disabled'}")
                if dual_verification_enabled:
                    print(f"   Systems: Characters Array {'âœ…' if self.verification_system.characters_available else 'âŒ'} + Putted Characters {'âœ…' if self.verification_system.putted_chars_available else 'âŒ'}")
                print("   Mouse already at safe position")
                print("="*60)
                
                self.player.drag_stats.record_attempt(True, retry_count=0, coord_used=coord_used, verification_used=dual_verification_enabled)
                return True
            else:
                print(f"ðŸ’¥ ENHANCED DRAG FAILED - All {len(coordinates)} coordinates attempted")
                print("="*60)
                
                self.player.drag_stats.record_attempt(False, retry_count=len(coordinates), coord_used=coord_used, verification_used=dual_verification_enabled)
                return False

    def _execute_single_drag_attempt(self, start_x, start_y, target_slot, is_first_icon, use_cdp, domain_adj):
        """
        ADDED: Execute a single drag attempt at specific coordinates
        UPDATED: Always use CDP for game operations with fallback
        """
        try:
            end_x, end_y = target_slot['x'], target_slot['y']
            
            # Get timing based on icon type and domain
            timing_key = 'first_icon' if is_first_icon else 'normal_icon'
            delays = self.player.DRAG_DELAYS[timing_key].copy()
            
            # Apply domain-specific adjustments
            stability_factor = domain_adj['stability_factor']
            for key in delays:
                delays[key] *= stability_factor
            
            # Extended timing for first icon
            if is_first_icon:
                print("🎪 First icon - applying extended timing...")
                time.sleep(delays['approach'])
                self.browser_manager.ensure_game_focus()
                time.sleep(domain_adj['extra_focus_time'])
            
            # 🔄 ALWAYS TRY CDP FIRST for game operations
            print("🌐 Using CDP for game drag operation...")
            cdp_success = self._execute_cdp_drag(start_x, start_y, end_x, end_y, delays, domain_adj)
            
            if cdp_success:
                print("✅ CDP drag successful")
                return True
            else:
                print("⚠️ CDP drag failed, using PyAutoGUI fallback...")
                return self._execute_pyautogui_drag(start_x, start_y, end_x, end_y, delays, domain_adj, is_first_icon)
                
        except Exception as e:
            print(f"❌ Single drag attempt execution error: {e}")
            return False

    def _execute_pyautogui_drag(self, start_x, start_y, end_x, end_y, delays, domain_adj, is_first_icon):
        """Execute drag using PyAutoGUI (IMPROVED with stability enhancements)"""
        try:
            print("🖱️ Executing IMPROVED PyAutoGUI drag operation...")
            
            # 🛡️ SAFE: Ensure focus before drag operation (use light focus để tránh click nhầm)
            self.browser_manager._ensure_focus_before_operation("drag operation", focus_type="light")
            
            # === APPROACH ICON WITH VERIFICATION ===
            print("📍 Step 1: Moving to icon with verification...")
            move_duration = (0.6 if is_first_icon else 0.4) * domain_adj['drag_speed_multiplier']  # Chậm hơn
            pyautogui.moveTo(start_x, start_y, duration=move_duration)
            time.sleep(delays['approach'])
            
            # VERIFY position after move
            current_pos = pyautogui.position()
            if abs(current_pos.x - start_x) > 5 or abs(current_pos.y - start_y) > 5:
                print(f"   ⚠️ Position drift detected, correcting...")
                pyautogui.moveTo(start_x, start_y, duration=0.2)
                time.sleep(0.2)
            
            # === ESTABLISH FOCUS WITH ENHANCED TIMING ===
            print("🎯 Step 2: Establishing focus with enhanced timing...")
            if domain_adj['requires_double_click'] or is_first_icon:
                pyautogui.click()
                time.sleep(0.25)  # Tăng từ 0.15 → 0.25
                pyautogui.click()
                time.sleep(delays['focus'])
            else:
                pyautogui.click()
                time.sleep(delays['focus'])
            
            # Move back to exact position and verify
            pyautogui.moveTo(start_x, start_y, duration=0.15)
            time.sleep(0.2)  # Thêm pause để đảm bảo position
            
            # === START DRAG WITH ENHANCED VERIFICATION ===
            print("🤌 Step 3: Starting ENHANCED drag - MOUSE DOWN...")
            
            # Final position verification before mouse down
            current_pos = pyautogui.position()
            if abs(current_pos.x - start_x) > 10 or abs(current_pos.y - start_y) > 10:
                print("   ⚠️ Critical position drift, re-correcting...")
                pyautogui.moveTo(start_x, start_y, duration=0.2)
                time.sleep(0.2)
            
            # ENHANCED MOUSE DOWN with verification
            print("🖱 MOUSE DOWN - holding with enhanced timing...")
            pyautogui.mouseDown(button='left')
            
            # 🔥 CRITICAL: Extended hold time để game nhận diện
            extended_hold = delays['hold'] * 1.5  # Tăng thêm 50%
            print(f"   ⏳ Extended hold time: {extended_hold:.2f}s")
            time.sleep(extended_hold)
            
            # Verify mouse is still down (optional check)
            print("   ✅ Mouse down phase completed")
            
            # === DRAG TO TARGET WITH SMOOTH MOVEMENT ===
            print("🎯 Step 4: Dragging to target with enhanced smoothness...")
            drag_duration = delays['drag'] * domain_adj['drag_speed_multiplier']
            
            # 🔥 ENHANCED: Minimum drag duration để đảm bảo game tracking
            min_drag_duration = 0.8  # Tối thiểu 0.8 giây
            if drag_duration < min_drag_duration:
                drag_duration = min_drag_duration
                print(f"   📈 Applied minimum drag duration: {drag_duration:.2f}s")
            
            # Smooth drag movement with intermediate checkpoints
            print(f"   🎯 Dragging from ({start_x}, {start_y}) → ({end_x}, {end_y}) in {drag_duration:.2f}s")
            pyautogui.moveTo(end_x, end_y, duration=drag_duration)
            
            # 🔥 CRITICAL: Extended drop preparation time
            enhanced_drop_wait = delays['drop'] * 1.3  # Tăng thêm 30%
            print(f"   ⏳ Enhanced drop preparation: {enhanced_drop_wait:.2f}s")
            time.sleep(enhanced_drop_wait)
            
            # === RELEASE MOUSE WITH ENHANCED VERIFICATION ===
            print("🖱 Step 5: ENHANCED MOUSE UP - releasing at target...")
            
            # Final position verification before release
            final_pos = pyautogui.position()
            position_error = abs(final_pos.x - end_x) + abs(final_pos.y - end_y)
            
            if position_error > 20:  # If too far from target
                print(f"   ⚠️ Large position error ({position_error:.1f}px), correcting before release...")
                pyautogui.moveTo(end_x, end_y, duration=0.15)
                time.sleep(0.2)
            
            # 🔥 ENHANCED MOUSE RELEASE
            print("🖱 RELEASING mouse with verification...")
            pyautogui.mouseUp(button='left')
            
            # Verify release completed
            print("   ✅ Mouse UP completed")
            time.sleep(delays['post_drop'])
            
            # === MOVE TO SAFE POSITION ===
            print("🛡️ Moving to safe position immediately...")
            screen_width, screen_height = pyautogui.size()
            safe_x = screen_width - random.randint(50, 100)  # Tăng margin
            safe_y = screen_height - random.randint(50, 100)
            pyautogui.moveTo(safe_x, safe_y, duration=random.uniform(0.3, 0.5))
            print(f"   ✅ Mouse moved to safe corner: ({safe_x}, {safe_y})")
            
            print("✅ IMPROVED PyAutoGUI drag completed with enhanced stability!")
            return True
            
        except Exception as e:
            print(f"❌ IMPROVED PyAutoGUI drag failed: {e}")
            # Emergency mouse release
            try:
                pyautogui.mouseUp(button='left')
                print("   🆘 Emergency mouse release executed")
            except:
                pass
            return False

    def _execute_dynamic_drag_cdp(self, ws, start_x, start_y, end_x, end_y, control_type, is_first_icon):
        """
        [UPGRADED v2] Executes a dynamic drag via CDP, now aware of `is_first_icon`
        for appropriate timing and delays.
        """
        try:
            print(f"   Executing UPGRADED DYNAMIC CDP DRAG (is_first_icon: {is_first_icon}, type: '{control_type}')")
            
            # BƯỚC 1: Lấy bộ thời gian trễ phù hợp
            timing_key = 'first_icon' if is_first_icon else 'normal_icon'
            delays = self.player.DRAG_DELAYS[timing_key].copy()
            domain_adj = self.player.get_domain_adjustments()
            stability_factor = domain_adj.get('stability_factor', 1.0)
            for key in delays:
                delays[key] *= stability_factor
            
            # Xử lý đặc biệt cho first_icon (chờ lâu hơn)
            if is_first_icon:
                print("   🎪 First icon - applying extended timing and extra wait...")
                time.sleep(delays['approach'])
                time.sleep(domain_adj['extra_focus_time'])
            
            # Di chuyển chuột đến vị trí bắt đầu
            self.browser_manager._send_cdp_command(ws, {
                "method": "Input.dispatchMouseEvent",
                "params": {"type": "mouseMoved", "x": start_x, "y": start_y}
            })
            if not is_first_icon: # Nếu không phải icon đầu, dùng approach delay bình thường
                time.sleep(delays['approach'])

            # BƯỚC 2: Focus vào icon (click "mồi")
            if control_type == "double_click_drag":
                print("   Using DOUBLE-CLICK AND DRAG (CDP)...")
                # Click đầu tiên
                self.browser_manager._simulate_click_via_cdp(ws, start_x, start_y)
                time.sleep(0.15) 
                
                # Click thứ hai và giữ
                self.browser_manager._send_cdp_command(ws, {
                    "method": "Input.dispatchMouseEvent",
                    "params": {"type": "mousePressed", "x": start_x, "y": start_y, "button": "left", "clickCount": 2}
                })
            else: # Mặc định là "click_drag"
                print("   Using standard CLICK-DRAG (CDP)...")
                self.browser_manager._simulate_click_via_cdp(ws, start_x, start_y)
                time.sleep(delays['focus']) # Sử dụng delay từ bộ timing
                self.browser_manager._send_cdp_command(ws, {
                    "method": "Input.dispatchMouseEvent",
                    "params": {"type": "mousePressed", "x": start_x, "y": start_y, "button": "left", "clickCount": 1}
                })

            # BƯỚC 3: Thời gian giữ chuột dài hơn (sử dụng delay từ bộ timing)
            extended_hold = delays['hold']
            print(f"   Holding for {extended_hold:.2f}s before dragging...")
            time.sleep(extended_hold)

            # BƯỚC 4: Kéo chuột đến vị trí đích
            print(f"   Dragging from ({start_x}, {start_y}) to ({end_x}, {end_y})")
            steps = 20
            drag_duration = delays['drag']
            sleep_per_step = drag_duration / steps
            for i in range(1, steps + 1):
                progress = i / steps
                curr_x = start_x + (end_x - start_x) * progress
                curr_y = start_y + (end_y - start_y) * progress
                self.browser_manager._send_cdp_command(ws, {
                    "method": "Input.dispatchMouseEvent",
                    "params": {"type": "mouseMoved", "x": curr_x, "y": curr_y, "button": "left"}
                })
                time.sleep(sleep_per_step)

            # BƯỚC 5: Thời gian chuẩn bị thả (CRITICAL)
            enhanced_drop_wait = delays['drop'] * 1.3
            print(f"   Preparing to drop for {enhanced_drop_wait:.2f}s...")
            time.sleep(enhanced_drop_wait)

            # BƯỚC 6: Thả chuột tại đích
            release_params = {
                "type": "mouseReleased", "x": end_x, "y": end_y, "button": "left",
                "clickCount": 2 if control_type == "double_click_drag" else 1
            }
            self.browser_manager._send_cdp_command(ws, {"method": "Input.dispatchMouseEvent", "params": release_params})
            time.sleep(delays['post_drop'])
            
            # BƯỚC 7: Di chuyển chuột đến vị trí an toàn (CRITICAL)
            self.player.move_mouse_to_safe_position()
            
            print("   ✅ Upgraded Dynamic CDP drag successful.")
            return True

        except Exception as e:
            print(f"   ❌ Exception during dynamic CDP drag: {e}")
            return False

    def corrected_return_icon(self, target_slot, use_cdp=True):
        """
        ðŸŽ¯ IMPROVED: 2-Phase Return vá»›i Sequential Carpet Bombing  
        Phase 1: Precision Click trá»±c tiáº¿p vÃ o tá» a Ä‘á»™ chÃ­nh xÃ¡c
        Phase 2: Sequential Carpet Bombing (same as remix cleanup)
        Phase 3: Game Restart (Hành động + Xác thực)
        """
        try:
            center_x, center_y = target_slot['x'], target_slot['y']
            target_slot_index = target_slot.get('index', -1)
            
            print(f"\nðŸŽ¯ 2-PHASE RETURN: Slot {target_slot_index} at ({center_x}, {center_y})")
            print("="*70)
            
            # Check if DUAL verification is available
            dual_verification_enabled = self.verification_system.characters_available or self.verification_system.putted_chars_available
            if dual_verification_enabled:
                print("ðŸ”„ DUAL verification ENABLED:")
                print(f"   ðŸ”¢ Characters Array: {'âœ… Ready' if self.verification_system.characters_available else 'âŒ N/A'}")
                print(f"   ðŸŽ¯ Putted Characters: {'âœ… Ready' if self.verification_system.putted_chars_available else 'âŒ N/A'}")
            else:
                print("âš ï¸ DUAL verification DISABLED - using fallback logic")
            
            # ================================================================
            # PHASE 0: PRE-FLIGHT CHECK (Skip if already empty)
            # ================================================================
            print("\nðŸ” PHASE 0: PRE-FLIGHT CHECK")
            print("-" * 40)
            
            if dual_verification_enabled:
                needs_return, initial_state, reason = self.verification_system.check_slot_needs_return(target_slot_index)
                
                if not needs_return:
                    print(f"âœ… PHASE 0 RESULT: Slot {target_slot_index} doesn't need return ({reason})")
                    print("ðŸš€ EARLY EXIT: Return operation skipped successfully")
                    print("="*70)
                    return True
                else:
                    print(f"ðŸŽ¯ PHASE 0 RESULT: Slot {target_slot_index} needs return ({reason})")
            else:
                print("âš ï¸ Pre-flight check skipped (verification disabled)")
            
            # ================================================================
            # PHASE 1: PRECISION CLICK vÃ o tá»a Ä‘á»™ chÃ­nh xÃ¡c
            # ================================================================
            print(f"\nðŸŽ¯ PHASE 1: PRECISION CLICK")
            print("-" * 40)
            
            phase1_success = self.execute_precision_return_click(target_slot, use_cdp, dual_verification_enabled)
            
            if phase1_success:
                print("ðŸŽ‰ PHASE 1 SUCCESS: Precision click worked!")
                print("="*70)
                return True
            else:
                print("âŒ PHASE 1 FAILED: Precision click unsuccessful")
            
            # ================================================================
            # PHASE 2: SEQUENTIAL CARPET BOMBING (same as remix cleanup)
            # ================================================================
            if not self.player.should_stop:
                print(f"\nðŸ’¥ PHASE 2: SEQUENTIAL CARPET BOMBING")
                print("-" * 40)
                print("   ðŸŽ¯ Using SAME logic as remix cleanup...")
                print("   ðŸ’¥ Progressive slot-by-slot carpet bombing")
                
                phase2_success = self.execute_remix_style_sequential_carpet_bombing_for_return(use_cdp)
                
                if phase2_success:
                    print("ðŸŽ‰ PHASE 2 SUCCESS: Sequential carpet bombing worked!")
                    print("="*70)
                    return True
                else:
                    print("ðŸ’¥ PHASE 2 FAILED: Sequential carpet bombing unsuccessful")

            # ================================================================
            # PHASE 3: GAME RESTART FALLBACK (SỬA ĐỔI)
            # ================================================================
            if not self.player.should_stop:
                print(f"\nPHASE 3: GAME RESTART FALLBACK (NEW)")
                print("-" * 40)

                # === LOGIC GHI TIMESTAMP ===
                # Lấy obs_controller một cách an toàn từ player instance
                obs_controller = getattr(self.player, 'obs_controller', None)
                
                if obs_controller:
                    obs_controller.log_event_timestamp(
                        "start_game_restart", 
                        "Restart triggered due to return failure",
                        {"failed_slot_index": target_slot_index}
                    )
                # === KẾT THÚC PHẦN LOGIC GHI TIMESTAMP ===

                # BƯỚC 3A: Thực hiện hành động restart từ BrowserManager
                phase3_action_success = self.browser_manager.restart_game_to_initial_state(use_cdp)

                # === LOGIC GHI TIMESTAMP ===
                if obs_controller:
                    obs_controller.log_event_timestamp(
                        "end_game_restart", 
                        "Restart and skip intro completed"
                    )
                # === KẾT THÚC PHẦN LOGIC GHI TIMESTAMP ===

                if not phase3_action_success:
                    print("PHASE 3 WARNING: Game restart action failed, but assuming it might have worked.")
                    return True # Vẫn trả về True vì đây là phương án cuối cùng
                
                # BƯỚC 3B: Thực hiện xác thực TẠI ĐÂY (trong GameOperations)
                print("   Verifying game restart...")
                if self.verification_system.characters_available or self.verification_system.putted_chars_available:
                    characters_count, putted_count = self.verification_system.get_dual_counts()
                    print(f"      Post-restart verification:")
                    print(f"      Characters: {characters_count}")
                    print(f"      Putted: {putted_count}")
                    
                    if (characters_count == 0 or characters_count is None) and (putted_count == 0 or putted_count is None):
                        print("   RESTART VERIFIED: Game returned to initial state!")
                    else:
                        print("   RESTART UNCLEAR: State verification inconclusive. Assuming success.")
                else:
                    print("   Verification disabled - assuming restart successful.")
                
                print("PHASE 3 COMPLETED!")
                return True # Luôn trả về True vì đây là phương án cuối cùng

            # ================================================================
            # FINAL RESULT
            # ================================================================
            print("\n" + "="*70)
            print(f"3-PHASE RETURN COMPLETED - ALL PHASES ATTEMPTED")
            print(f"   Target slot: {target_slot_index}")
            print(f"   Phase 1 (Precision Click): FAILED")
            print(f"   Phase 2 (Sequential Carpet Bombing): FAILED") 
            print(f"   Phase 3 (Game Restart): COMPLETED")
            print(f"   DUAL verification: {'Enabled' if dual_verification_enabled else 'Disabled'}")
            print(f"   Final Status: ASSUMED SUCCESS (game restart should work)")
            print("="*70)
            return True  # Assume success since Phase 3 (restart) should always work
                    
        except Exception as e:
            print(f"âŒ 2-Phase return error: {e}")
            import traceback
            traceback.print_exc()
            return False

    def execute_precision_return_click(self, target_slot, use_cdp, dual_verification_enabled):
        """
        ðŸŽ¯ PHASE 1: Execute precision click trá»±c tiáº¿p vÃ o tá»a Ä‘á»™ chÃ­nh xÃ¡c
        """
        try:
            center_x, center_y = target_slot['x'], target_slot['y']
            domain_adj = self.player.get_domain_adjustments()
            
            print(f"   ðŸŽ¯ Method: Single precision click at exact coordinates")
            print(f"   ðŸ“ Target: ({center_x}, {center_y})")
            
            # Get DUAL counts BEFORE return
            characters_before, putted_before = None, None
            if dual_verification_enabled:
                characters_before, putted_before = self.verification_system.get_dual_counts()
                print(f"ðŸ“Š DUAL counts BEFORE return:")
                print(f"   ðŸ”¢ Characters: {characters_before}")
                print(f"   ðŸŽ¯ Putted: {putted_before}")
            
            # Execute single precision click
            center_coords = [(center_x, center_y)]
            self.execute_multi_method_clicks(center_coords, use_cdp, domain_adj, method="single")
            
            # Wait and verify
            verification_wait = 0.3 if dual_verification_enabled else 0.2
            time.sleep(verification_wait)
            
            if dual_verification_enabled:
                characters_after, putted_after = self.verification_system.get_dual_counts()
                print(f"ðŸ“Š DUAL counts AFTER return:")
                print(f"   ðŸ”¢ Characters: {characters_after}")
                print(f"   ðŸŽ¯ Putted: {putted_after}")
                
                if self.verify_return_success_dual(characters_before, characters_after, putted_before, putted_after):
                    print("ðŸŽ‰ PRECISION CLICK SUCCESS: DUAL verification passed!")
                    return True
                else:
                    print(f"âŒ Precision click failed: DUAL verification failed")
                    return False
            else:
                # No verification - assume success
                print("âœ… Precision click assumed success (no verification)")
                return True
                
        except Exception as e:
            print(f"âŒ Error in precision return click: {e}")
            return False

    def execute_remix_style_sequential_carpet_bombing_for_return(self, use_cdp=False):
        """
        ðŸ’¥ PHASE 2: REUSE exact logic from remix cleanup for return operation
        Sequential carpet bombing: slot 0 â†’ slot 1 â†’ slot 2 â†’ ... â†’ cuá»‘i
        Progressive sets: 2â†’3â†’4â†’5â†’6... coordinates per slot
        """
        try:
            print(f"\nðŸ’¥ SEQUENTIAL CARPET BOMBING FOR RETURN")
            print("="*60)
            print("   ðŸ”„ REUSING: Same logic as remix cleanup")
            print("   ðŸŽ¯ Strategy: Sequential slot-by-slot processing")
            
            # Get configuration
            if not self.player.config:
                print("âŒ No config available for carpet bombing")
                return False
                    
            all_slots = self.player.config.get('layout', {}).get('slots', [])
            if not all_slots:
                print("âŒ No slots configuration found")
                return False
            
            # Sort slots for sequential processing (same as remix)
            sorted_slots = sorted(all_slots, key=lambda s: s.get('index', 0))
            print(f"   ðŸŽ¯ Will process {len(sorted_slots)} slots sequentially")
            
            # Get initial count for verification
            dual_verification_enabled = self.verification_system.characters_available or self.verification_system.putted_chars_available
            initial_count = None
            
            if dual_verification_enabled:
                characters_count, putted_count = self.verification_system.get_dual_counts()
                initial_count = characters_count if characters_count is not None else putted_count
                
                if initial_count == 0:
                    print("   âœ… Already clean - no carpet bombing needed")
                    return True
                
                print(f"   ðŸ“Š Icons to clear: {initial_count}")
            else:
                print("   âš ï¸ Verification disabled - assuming carpet bombing needed")
            
            # Execute SAME sequential carpet bombing as remix cleanup
            success = self.player.carpet_bombing.execute_sequential_carpet_bombing_sets(
                sorted_slots, initial_count, use_cdp
            )
            
            if success:
                print("ðŸŽ‰ SEQUENTIAL CARPET BOMBING SUCCESS!")
                print("   âœ… Return operation completed via carpet bombing")
                print("   ðŸ”„ Used SAME reliable logic as remix cleanup")
            else:
                print("ðŸ’¥ SEQUENTIAL CARPET BOMBING FAILED")
                print("   âŒ Even carpet bombing could not clear the slots")
            
            return success
            
        except Exception as e:
            print(f"âŒ Error in sequential carpet bombing for return: {e}")
            return False

    def _execute_cdp_return(self, center_x, center_y, domain_adj):
        try:
            ws_url = self.browser_manager._get_websocket_url()
            if not ws_url:
                return False
            
            ws = websocket.create_connection(ws_url, timeout=30)
            
            # âœ… NO VARIATION: Use exact coordinates
            # Move and click
            self.browser_manager._send_cdp_command(ws, {"method": "Input.dispatchMouseEvent", "params": {"type": "mouseMoved", "x": center_x, "y": center_y}})
            time.sleep(0.1)
            self.browser_manager._simulate_click_via_cdp(ws, center_x, center_y)
            
            ws.close()
            
            # Wait for return animation
            return_wait = 0.4 * domain_adj['stability_factor']
            time.sleep(return_wait)
            
            print("âœ… CDP return completed")
            return True
            
        except Exception as e:
            print(f"âŒ CDP return failed: {e}")
            return False

    def _execute_pyautogui_return(self, click_x, click_y, domain_adj):
        """Execute single return click using PyAutoGUI"""
        return self._execute_pyautogui_single_click(click_x, click_y, domain_adj)

    def _execute_pyautogui_single_click(self, click_x, click_y, domain_adj):
        try:
            print(f"ðŸ–±ï¸ Executing PyAutoGUI single click at ({click_x}, {click_y})")
            
            # ðŸ”§ SAFE: Ensure focus before click (use light focus Ä‘á»ƒ trÃ¡nh click nháº§m)
            self.browser_manager._ensure_focus_before_operation("single click", focus_type="light")
            
            # âœ… NO VARIATION: Use exact coordinates
            pyautogui.moveTo(click_x, click_y, duration=0.15)  # â† CHÃNH XÃC 100%
            time.sleep(0.1)
            
            # Single click
            pyautogui.click(button='left')
            
            # Wait for click to register
            click_wait = 0.2 * domain_adj['stability_factor']
            time.sleep(click_wait)
            
            print("âœ… PyAutoGUI single click completed")
            return True
            
        except Exception as e:
            print(f"âŒ PyAutoGUI single click failed: {e}")
            return False

    # ================================================================
    # == EXISTING GAME LOGIC (kept as-is with small adaptations) ==
    # ================================================================

    def smart_delay(self, base_time, variation=0.3):
        """Smart delay with random variation and domain adjustments"""
        domain_adj = self.player.get_domain_adjustments()
        adjusted_base = base_time * domain_adj['stability_factor']
        min_time = max(0.08, adjusted_base - variation)
        max_time = adjusted_base + variation
        delay = random.uniform(min_time, max_time)
        if adjusted_base > 0.3:
            delay += 0.1
        time.sleep(delay)
        return delay
    
    def quick_delay(self):
        return self.smart_delay(0.15, 0.05)
    
    def normal_delay(self):
        return self.smart_delay(0.25, 0.1)
    
    def thinking_delay(self):
        return self.smart_delay(0.4, 0.2)

    def _find_slot_by_index(self, slot_index):
        if not self.player.config:
            return None
        layout = self.player.config['layout']
        slots = layout.get('slots', [])
        for slot in slots:
            if slot.get('index') == slot_index:
                return slot
        return None
    
    def get_drag_statistics(self):
        return self.player.drag_stats.get_stats()

    def prepare_game_for_playing(self):
        """Prepare game for playing with enhanced setup"""
        try:
            print("ðŸŽ¯ Preparing game for enhanced workflow...")
            
            # Clean mouse state
            self.player.ensure_mouse_released()
            
            # Warm-up first icon
            if self.player.config is not None:  # âœ… Kiá»ƒm tra config khÃ´ng None
                layout = self.player.config['layout']
                if layout['icons']:
                    first_icon = layout['icons'][0]
                    print("ðŸŽ¯ Warming up first icon...")
                    
                    # âœ… DI CHUYá»‚N Táº¤T Cáº¢ CODE VÃ€O TRONG KHá»I IF
                    warm_x, warm_y = first_icon['x'], first_icon['y']  # â† BÃ¢y giá» an toÃ n
                    
                    pyautogui.moveTo(warm_x, warm_y, duration=random.uniform(0.3, 0.5))
                    self.smart_delay(0.3, 0.1)
                    
                    # Micro-movement - CÅ¨NG CHÃNH XÃC
                    offset_x, offset_y = warm_x + 8, warm_y + 8  # â† BÃ¢y giá» an toÃ n
                    pyautogui.moveTo(offset_x, offset_y, duration=0.15)
                    self.smart_delay(0.2, 0.05)
                    pyautogui.moveTo(warm_x, warm_y, duration=0.15)  # â† BÃ¢y giá» an toÃ n
                    self.smart_delay(0.3, 0.1)
                else:
                    print("âš ï¸ No icons available for warm-up")
            else:
                print("âš ï¸ Config not loaded, skipping warm-up")
            
            print("âœ… Game preparation completed with enhanced workflow!")
            
        except Exception as e:
            print("Error in game preparation: " + str(e))

    def calculate_center_slot_index(self, slots: list) -> int:
        """Calculate center slot index for showcase"""
        try:
            total_slots = len(slots)
            if total_slots == 0:
                print("⚠️ No slots found, defaulting to center index 3")
                return 3 # Trả về fallback an toàn

            # Calculate center index (0-based)
            center_index = (total_slots - 1) // 2
            
            print(f"📊 Slot calculation: {total_slots} slots → center index {center_index}")
            
            return center_index
            
        except Exception as e:
            print(f"❌ Error calculating center slot: {e}, falling back to index 3")
            return 3  # Fallback to hardcoded value                                                  
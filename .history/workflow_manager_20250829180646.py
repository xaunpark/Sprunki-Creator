# workflow_manager.py
"""
Workflow Manager for Sprunki Game Player.
Orchestrates high-level game scenarios like 'Full Showcase', 'Remix Only',
and individual showcase/remix sequences.
"""
import gc
import time

import psutil
from utils.resource_manager import ResourceManager

class WorkflowManager:
    def __init__(self, player_instance):
        """
        Khởi tạo WorkflowManager.
        :param player_instance: Tham chiếu đến instance của SprunkiGamePlayer.
        """
        print("📜 WorkflowManager initialized.")
        self.player = player_instance
        # Tạo alias để truy cập các manager khác
        self.game_operations = player_instance.game_operations
        self.verification_system = player_instance.verification_system
    
    # ================================================================
    # == HIGH-LEVEL GAME OPERATIONS (ENHANCED) ==
    # ================================================================
    
    def showcase_single_icon_corrected(self, current_icon, mode="Normal", listen_duration=15, progress_callback=None):
        """Enhanced version with resource management"""
        try:
            if not self.player.config or not current_icon:
                return False

            # Lấy các thông tin cần thiết
            obs_controller = getattr(self.player, 'obs_controller', None)
            icon_index = current_icon.get('index', -1)
            target_slot = self.game_operations._find_slot_by_index(3) # Logic cũ vẫn giữ nguyên, có thể cải tiến sau
            if not target_slot:
                return False

            # Setup resource tracking
            if not hasattr(self, 'resource_manager'):
                self.resource_manager = ResourceManager()

            # Find target slot
            layout = self.player.config['layout']
            slots = layout.get('slots', [])
            center_slot_index = self.game_operations.calculate_center_slot_index(slots)
            target_slot = self.game_operations._find_slot_by_index(center_slot_index)
            if not target_slot:
                return False
            
            icon_index = current_icon.get('index', -1)

            showcase_context = {
                "operation": "icon_showcase",
                "icon_index": icon_index,
                "mode": mode
            }

            print(f"STARTING ENHANCED SHOWCASE: {mode} icon {icon_index + 1}")
            
            # PHASE 0: Enhanced cleanup with resource management
            try:
                self.execute_fast_showcase_cleanup()
            except Exception as e:
                print(f"Cleanup warning: {e}")
                # Continue anyway

            # === GHI TIMESTAMP BẮT ĐẦU SHOWCASE (TRƯỚC KHI KÉO) ===
            if obs_controller:
                obs_controller.log_event_timestamp(
                    "start_icon_showcase", 
                    f"Start showcase for icon {icon_index} ({mode})",
                    {"mode": mode, "icon_index": icon_index}
                )
            # === KẾT THÚC PHẦN THÊM MỚI ===

            # PHASE 1: Drag with exception handling
            try:
                drag_success = self.game_operations.corrected_drag_and_drop(current_icon, target_slot, False, True, context=showcase_context)
                if not drag_success:
                    print(f"Failed to drag icon {icon_index}")
                    return False
            except Exception as e:
                print(f"Drag error: {e}")
                return False
            
            if self.player.should_stop:
                return False
            
            # PHASE 2: ENHANCED LISTENING with memory-safe updates
            print(f"PHASE 2: Listening to music for {listen_duration}s...")
            
            # Track memory usage
            initial_memory = psutil.Process().memory_info().rss / 1024 / 1024
            
            for countdown in range(listen_duration, 0, -1):
                if self.player.should_stop:
                    return False
                
                # Memory-safe progress update
                try:
                    if progress_callback and hasattr(self, 'thread_safe_progress_callback'):
                        self.player.thread_safe_progress_callback(f"Listening {mode} icon {icon_index + 1} ({countdown}s)")
                    elif progress_callback:
                        # Fallback with exception handling
                        try:
                            progress_callback(f"Listening {mode} icon {icon_index + 1} ({countdown}s)")
                        except Exception as gui_error:
                            print(f"GUI update failed: {gui_error}")
                            # Continue without GUI updates to prevent crash
                except Exception as callback_error:
                    print(f"Progress callback error: {callback_error}")
                
                # Memory monitoring every 5 seconds
                if countdown % 5 == 0:
                    try:
                        current_memory = psutil.Process().memory_info().rss / 1024 / 1024
                        memory_increase = current_memory - initial_memory
                        
                        if memory_increase > 50:  # More than 50MB increase
                            print(f"Memory increase detected: {memory_increase:.1f}MB - forcing cleanup")
                            gc.collect()  # Force garbage collection
                            
                    except Exception as memory_error:
                        print(f"Memory monitoring error: {memory_error}")
                
                time.sleep(1)
            
            if self.player.should_stop:
                return False
            
            # PHASE 3: Return with enhanced error handling
            try:
                return_success = self.player.execute_smart_return_with_auto_recovery(target_slot, f"showcase_{mode}_icon_{icon_index}", max_retries=2, use_cdp=True, context=showcase_context)
                
                if return_success:
                    print(f"Icon {icon_index + 1} returned successfully")
                else:
                    print(f"Return warning for icon {icon_index}")
                    # Try emergency cleanup
                    try:
                        self.game_operations._execute_emergency_return_click(target_slot)
                    except:
                        pass
                
            except Exception as return_error:
                print(f"Return error: {return_error}")
                # Continue anyway, don't fail entire showcase
                return_success = False
                
            # === GHI TIMESTAMP KẾT THÚC SHOWCASE (SAU KHI TRẢ VỀ) ===
            if obs_controller:
                if return_success:
                    obs_controller.log_event_timestamp(
                        "end_icon_showcase", 
                        f"End showcase for icon {icon_index} ({mode})",
                        {"mode": mode, "icon_index": icon_index}
                    )
                else:
                    # Ghi lại lỗi nếu return thất bại
                    obs_controller.log_event_timestamp(
                        "icon_showcase_failed",
                        f"Return failed for icon {icon_index}",
                        {"mode": mode, "icon_index": icon_index, "stage": "return"}
                    )
            # === KẾT THÚC PHẦN THÊM MỚI ===

            # Force cleanup after each icon
            try:
                if hasattr(self, 'resource_manager'):
                    self.resource_manager.force_cleanup()
            except Exception as cleanup_error:
                print(f"Resource cleanup error: {cleanup_error}")
            
            time.sleep(0.1)
            
            print(f"COMPLETED ENHANCED SHOWCASE: {mode} icon {icon_index + 1}")
            return True
            
        except Exception as e:
            icon_index_for_error = current_icon.get('index', 'unknown') if current_icon else 'unknown'
            print(f"Critical error in showcase icon {icon_index_for_error}: {e}")
            
            # Emergency resource cleanup
            try:
                if hasattr(self, 'resource_manager'):
                    self.resource_manager.force_cleanup()
                gc.collect()
            except:
                pass
            
            return False

    def execute_fast_showcase_cleanup(self):
            """
            ðŸš€ FAST: Optimized carpet bombing cho showcase mode
            - Early exit náº¿u Ä‘Ã£ clean
            - Reduced timing cho UX mÆ°á»£t mÃ   
            - Limited sets (chá»‰ 2-3 sets thay vÃ¬ 6)
            - Faster delays
            """
            try:
                print(f"\nðŸ§¹ FAST SHOWCASE CLEANUP (Intelligent Safety Net)")
                print("-" * 40)
                
                ### Lá»›p 3 - "Kiá»ƒm tra trÆ°á»›c, dá»n dáº¹p sau" ###
                
                # BÆ°á»›c 1: LuÃ´n kiá»ƒm tra state trÆ°á»›c tiÃªn
                dual_verification_enabled = self.verification_system.characters_available or self.verification_system.putted_chars_available
                initial_count = None

                if dual_verification_enabled:
                    characters_count, putted_count = self.verification_system.get_dual_counts()
                    initial_count = characters_count if characters_count is not None else putted_count
                    
                    # BÆ°á»›c 2: Ra quyáº¿t Ä‘á»‹nh
                    if initial_count == 0:
                        print("   âœ… Board is already clean. Skipping cleanup.")
                        print("   âš¡ Fast exit executed in ~0.2s.")
                        return True # ÄÃ¢y lÃ  lá»‘i thoÃ¡t nhanh, giÃºp tÄƒng tá»‘c Ä‘á»™
                    
                    print(f"   âš ï¸ Board is not clean! Found {initial_count} icon(s). Activating safety net...")
                else:
                    print("   âš ï¸ Verification disabled - proceeding with precautionary carpet bombing.")
                
                ### Káº¾T THÃšC Lá»›p 3 ###

                # Pháº§n code carpet bombing dÆ°á»›i Ä‘Ã¢y chá»‰ Ä‘Æ°á»£c thá»±c thi Náº¾U bÃ n cá» khÃ´ng sáº¡ch.
                print("   ðŸŽ¯ Activating full cleanup logic...")

                # Get configuration
                if not self.player.config:
                    print("   âŒ No config - skipping")
                    return True
                        
                all_slots = self.player.config.get('layout', {}).get('slots', [])
                if not all_slots:
                    print("   âŒ No slots - skipping")
                    return True
                
                # Sort slots for sequential processing
                sorted_slots = sorted(all_slots, key=lambda s: s.get('index', 0))
                
                # ðŸš€ SHOWCASE-OPTIMIZED SETTINGS
                showcase_config = {
                    'max_sets': 3,
                    'base_coords_per_slot': 2,
                    'inter_click_delay': 0.02,
                    'inter_slot_delay': 0.8,
                    'inter_set_delay': 0.2
                }
                
                print(f"   âš¡ FAST mode: {showcase_config['max_sets']} sets, {len(sorted_slots)} slots")
                print(f"   â±ï¸ Estimated time: ~{self.estimate_cleanup_time(len(sorted_slots), showcase_config)}s")
                
                # Execute fast carpet bombing
                success = self.execute_fast_carpet_bombing_sets(
                    sorted_slots, initial_count, showcase_config, use_cdp=False
                )
                
                if success:
                    print("   âœ… Fast cleanup completed!")
                else:
                    print("   âš ï¸ Fast cleanup incomplete - continuing anyway")
                
                return True  # LuÃ´n tráº£ vá» True Ä‘á»ƒ khÃ´ng block showcase
                
            except Exception as e:
                print(f"   âŒ Fast cleanup error: {e}")
                return True  # LuÃ´n tráº£ vá» True Ä‘á»ƒ khÃ´ng block showcase

    def execute_fast_carpet_bombing_sets(self, sorted_slots, initial_count, config, use_cdp=False):
        """
        âš¡ Execute fast carpet bombing with reduced timing for showcase
        """
        try:
            max_sets = config['max_sets']
            base_coords = config['base_coords_per_slot']
            
            for set_num in range(1, max_sets + 1):
                if self.player.should_stop:
                    break
                    
                print(f"   ðŸ’¥ Fast set {set_num}/{max_sets}")
                
                # Check if already clean
                if self.verification_system.characters_available or self.verification_system.putted_chars_available:
                    current_chars, current_putted = self.verification_system.get_dual_counts()
                    current_count = current_chars if current_chars is not None else current_putted
                    
                    if current_count == 0:
                        print(f"   âœ… Clean after set {set_num} - early exit!")
                        return True
                
                # Generate coordinates for this set
                coords_per_slot = base_coords + (set_num - 1)
                
                # Execute all slots in this set
                for slot in sorted_slots:
                    if self.player.should_stop:
                        break
                        
                    # Generate coordinates for this slot
                    slot_coords = self.game_operations.generate_slot_coordinates(
                        slot['x'], slot['y'], coords_per_slot, set_num
                    )
                    
                    # Execute clicks for this slot
                    for coord_x, coord_y in slot_coords:
                        self.game_operations._execute_single_return_click(
                            coord_x, coord_y, use_cdp, self.player.get_domain_adjustments()
                        )
                        time.sleep(config['inter_click_delay'])
                    
                    # Fast inter-slot delay
                    time.sleep(config['inter_slot_delay'])
                
                # Fast inter-set delay
                time.sleep(config['inter_set_delay'])
            
            return True
            
        except Exception as e:
            print(f"   âŒ Fast carpet bombing error: {e}")
            return False

    def estimate_cleanup_time(self, num_slots, config):
        """
        â±ï¸ Estimate cleanup time for user feedback
        """
        try:
            sets = config['max_sets']
            avg_coords_per_slot = 2.5  # Average of 2,3,4 coords
            
            # Calculate components
            click_time = num_slots * avg_coords_per_slot * config['inter_click_delay'] * sets
            slot_delays = (num_slots - 1) * config['inter_slot_delay'] * sets  
            set_delays = (sets - 1) * config['inter_set_delay']
            
            total = click_time + slot_delays + set_delays
            return round(total, 1)
            
        except:
            return 5  # Fallback estimate
        
    # ================================================================
    # == WORKFLOW METHODS (redirected to enhanced versions) ==
    # ================================================================

    def showcase_all_icons_corrected(self, mode="Normal", skip_icon_19=False, listen_duration=15, progress_callback=None):
            """ENHANCED: Showcase all icons with verification system - NOW PASSES ICON OBJECTS"""
            try:
                if not self.player.config:
                    return False
                    
                layout = self.player.config['layout']
                all_icons = layout['icons']
                
                # --- CENTRALIZED ICON FILTERING LOGIC ---
                icons_to_showcase = []
                if mode == "Normal":
                    # For Normal mode, ALWAYS use icons with index 0-18
                    icons_to_showcase = [icon for icon in all_icons if icon.get('index', 999) <= 18]
                    print(f"ðŸŽ¯ Normal Mode: Filtering to icons 0-18. Found {len(icons_to_showcase)} icons.")
                elif mode == "Horror":
                    # For Horror mode, use icons 0-19
                    icons_to_showcase = [icon for icon in all_icons if icon.get('index', 999) <= 19]
                    print(f"ðŸ‘» Horror Mode: Filtering to icons 0-19. Found {len(icons_to_showcase)} icons.")
                else:
                    # Fallback for other modes
                    icons_to_showcase = all_icons

                print(f"\nÃ°Å¸Å½Âª STARTING ENHANCED {mode.upper()} SHOWCASE")
                print(f"ðŸ“Š Will showcase {len(icons_to_showcase)} icons with verification system")
                if self.verification_system.characters_available:
                    print("ðŸ”  Drag verification: ENABLED")
                else:
                    print("âš ï¸  Drag verification: DISABLED (fallback mode)")
                print("="*60)
                
                success_count = 0
                # --- LOOP DIRECTLY THROUGH ICON OBJECTS ---
                for icon_object in icons_to_showcase:
                    if self.player.should_stop:
                        break
                    
                    # Pass the entire icon object to the handler function
                    if self.showcase_single_icon_corrected(icon_object, mode, listen_duration, progress_callback):
                        success_count += 1
                    else:
                        icon_index = icon_object.get('index', 'unknown')
                        print(f"âš ï¸  Failed to showcase icon {icon_index}")
                
                print(f"\nÃ¢Å“â€¦ ENHANCED {mode.upper()} SHOWCASE COMPLETED")
                print(f"ðŸ“Š Success: {success_count}/{len(icons_to_showcase)} icons")
                print("="*60)
                return success_count == len(icons_to_showcase)
                
            except Exception as e:
                print("â Œ Error in enhanced " + mode + " showcase: " + str(e))
                return False

    def play_complete_remix_corrected(self, remix_data, mode="Normal", listen_duration=15, progress_callback=None):
        """ENHANCED: Play remix with verification system"""
        try:
            if not self.player.config or not remix_data:
                return False
                
            layout = self.player.config['layout']
            icons = layout['icons']
            slots = layout['slots']
            
            remix_name = remix_data.get('name', 'Unknown Remix')
            remix_icons = remix_data.get('icons', [])
            
            emoji = "ðŸŽµ" if mode == "Test" else ("ðŸ‘»" if mode == "Horror" else "ðŸŽª")
            
            print(f"\n{emoji} STARTING ENHANCED REMIX: {remix_name}")

            obs_controller = getattr(self.player, 'obs_controller', None)

            remix_context = {
                "operation": "remix_playback",
                "remix_name": remix_name
            }

            if self.verification_system.characters_available:
                print("ðŸ” Verification system ENABLED for all drags")
            else:
                print("âš ï¸ Verification system DISABLED - using fallback")
            print("="*60)
            
            # Phase 1: Drag all icons to slots with verification
            print("ðŸŽ¯ PHASE 1: Dragging all icons to slots with verification...")
            for slot_position, icon_index in enumerate(remix_icons):
                if self.player.should_stop:
                    return False
                
                # Validate indices
                if icon_index >= len(icons) or slot_position >= len(slots):
                    print(f"âš ï¸ Invalid index: icon {icon_index} or slot {slot_position}")
                    continue
                
                current_icon = icons[icon_index]
                target_slot = slots[slot_position]
                
                if progress_callback:
                    progress_callback(f"{emoji} {remix_name}: Icon {icon_index} â†’ Slot {slot_position}")
                
                print(f"  ðŸŽ¯ Dragging icon {icon_index} to slot {slot_position} with verification...")
                
                # Use enhanced drag with verification
                use_cdp = True  # Can be customized
                if not self.game_operations.corrected_drag_and_drop(current_icon, target_slot, False, use_cdp=True, context=remix_context):
                    print(f"  âŒ Failed to drag icon {icon_index} with verification")
                    continue
                
                # Short delay between drags
                time.sleep(0.3)
            
            if self.player.should_stop:
                return False

            # === THÊM LOGIC GHI TIMESTAMP BẮT ĐẦU REMIX ===
            if obs_controller:
                remix_metadata = {
                    "remix_name": remix_name,
                    "icon_indices": remix_icons,
                    "pattern": remix_data.get('pattern', 'unknown')
                }
                obs_controller.log_event_timestamp(
                    "start_remix_playback", 
                    f"Start playback for {remix_name}", 
                    remix_metadata
                )
            # === KẾT THÚC PHẦN THÊM MỚI ===

            # Phase 2: Listen to remix music - THREAD SAFE
            print(f"PHASE 2: Enjoying remix for {listen_duration}s...")
            if progress_callback:
                try:
                    self.player.thread_safe_progress_callback(f"Enjoying {remix_name}")
                except Exception as e:
                    print(f"Progress callback warning: {e}")

            for countdown in range(listen_duration, 0, -1):
                if self.player.should_stop:
                    return False
                
                # THREAD SAFE: Only update every 3 seconds to reduce GUI pressure
                if progress_callback and countdown % 3 == 0:
                    try:
                        self.player.thread_safe_progress_callback(f"Enjoying {remix_name} ({countdown}s)")
                    except Exception as e:
                        print(f"Progress callback warning: {e}")
                
                time.sleep(1)
            
            if self.player.should_stop:
                return False

            # === THÊM LOGIC GHI TIMESTAMP KẾT THÚC REMIX ===
            if obs_controller:
                obs_controller.log_event_timestamp(
                    "end_remix_playback", 
                    f"End playback for {remix_name}"
                )
            # === KẾT THÚC PHẦN THÊM MỚI ===

            # Phase 3: Smart batch return with mode detection
            print("ðŸ”„ PHASE 3: Smart batch returning with mode detection...")
            if progress_callback:
                progress_callback(f"ðŸ§¹ Smart cleanup {remix_name}")

            # Set explicit remix mode for batch operations
            self.player.set_return_mode_explicit(self.player.RETURN_MODE_REMIX, f"remix_cleanup_{len(remix_icons)}_icons")

            print(f"  ðŸ”„ Using mode-aware batch return for {len(remix_icons)} icons...")

            # Use smart batch return with mode awareness
            success_count, total_attempted, error_log = self.player.smart_batch_return_all_slots_with_mode(use_cdp=False, context=remix_context)

            if success_count == total_attempted:
                print(f"  âœ… Smart batch return: {success_count}/{total_attempted} icons returned successfully")
            else:
                print(f"  âš ï¸ Smart batch return: {success_count}/{total_attempted} icons returned")
                if error_log:
                    print(f"  ðŸ“‹ Errors: {len(error_log)} issues encountered")
                    # Continue anyway, don't fail the entire remix

            # Brief stabilization pause
            time.sleep(0.5)
            
            print(f"âœ… COMPLETED ENHANCED REMIX: {remix_name}")
            print("="*60)
            return True
            
        except Exception as e:
            print("âŒ Error in enhanced remix " + remix_data.get('name', 'Unknown') + ": " + str(e))
            return False

    def execute_full_showcase_workflow_corrected(self, progress_callback=None):
        """ENHANCED: Execute full workflow with verification system"""
        try:
            if not self.player.config:
                return False
                
            game_settings = self.player.config.get('gameSettings', {})
            has_horror = game_settings.get('hasHorror', False)
            has_bonus = game_settings.get('hasBonus', False)
            
            print("\nðŸŽª STARTING ENHANCED FULL SHOWCASE WORKFLOW")
            if self.verification_system.characters_available:
                print("ðŸ” Drag verification system: ENABLED")
            else:
                print("âš ï¸ Drag verification system: DISABLED (fallback mode)")
            print("="*60)
            
            # === NORMAL MODE ===
            if progress_callback:
                progress_callback("ðŸŽª Starting Enhanced Normal Mode...")
            
            # Normal icons (skip icon 19 if horror mode exists)
            if not self.showcase_all_icons_corrected("Normal", skip_icon_19=has_horror, progress_callback=progress_callback):
                return False
            
            # Normal bonus (if any)
            if has_bonus:
                if not self.showcase_all_bonus_corrected("Normal", progress_callback=progress_callback):
                    return False
            
            # Normal remixes
            if not self.play_all_remixes_corrected("Normal", progress_callback=progress_callback):
                return False
            
            # === HORROR MODE (if available) ===
            if has_horror and not self.player.should_stop:
                # Trigger horror mode
                if not self.trigger_horror_mode_transition_corrected(progress_callback=progress_callback):
                    return False
                
                # Horror showcase (all icons 0-19)
                if not self.showcase_all_icons_corrected("Horror", skip_icon_19=False, progress_callback=progress_callback):
                    return False
                
                # Horror bonus
                if has_bonus:
                    if not self.showcase_all_bonus_corrected("Horror", progress_callback=progress_callback):
                        return False
                
                # Horror remixes
                if not self.play_all_remixes_corrected("Horror", progress_callback=progress_callback):
                    return False
            
            if progress_callback:
                progress_callback("ðŸŽ‰ Enhanced full showcase workflow completed!")

            print("\nðŸŽ‰ ENHANCED FULL SHOWCASE WORKFLOW COMPLETED!")
            print(self.game_operations.get_drag_statistics())

            # ================================================================
            # ðŸ§¹ FINAL SMART CLEANUP & VALIDATION
            # ================================================================
            print("\nðŸ§¹ FINAL SMART CLEANUP & COMPREHENSIVE VALIDATION")
            print("="*70)

            try:
                # Smart batch cleanup validation - ensure all slots are clean
                print("ðŸ”„ Phase 1: Smart batch cleanup validation...")
                cleanup_success_count, cleanup_total, cleanup_errors = self.player.smart_batch_return_all_slots(use_cdp=False)
                
                if cleanup_total == 0:
                    print("âœ… No cleanup needed - all slots already clean")
                elif cleanup_success_count == cleanup_total:
                    print(f"âœ… Smart cleanup: {cleanup_success_count}/{cleanup_total} slots cleaned perfectly")
                else:
                    print(f"âš ï¸ Smart cleanup: {cleanup_success_count}/{cleanup_total} slots cleaned")
                    if cleanup_errors:
                        print(f"   ðŸ“‹ Cleanup issues: {len(cleanup_errors)} errors")
                        for error in cleanup_errors[:2]:  # Show first 2 errors
                            print(f"      - {error}")
                
                # Comprehensive workflow validation
                print("\nðŸ” Phase 2: Comprehensive workflow validation...")
                is_complete, remaining_issues, recommendations = self.verification_system.validate_workflow_completion()
                
                if is_complete:
                    print("âœ… WORKFLOW VALIDATION: PERFECT COMPLETION! ðŸŽ‰")
                    print("   ðŸ›¡ï¸ All slots clean")
                    print("   ðŸ“Š State consistency verified")
                    print("   ðŸŽ¯ Cross-contamination: NONE")
                else:
                    print(f"âš ï¸ WORKFLOW VALIDATION: {len(remaining_issues)} issues detected")
                    print("ðŸ“‹ Issues found:")
                    for issue in remaining_issues[:3]:  # Show first 3 issues
                        print(f"   âŒ {issue}")
                    
                    if recommendations:
                        print("ðŸ’¡ Recommendations:")
                        for rec in recommendations[:2]:  # Show first 2 recommendations
                            print(f"   ðŸ’¡ {rec}")
                
                # Enhanced summary
                print("\nðŸ“Š ENHANCED WORKFLOW SUMMARY:")
                summary = self.player.get_enhanced_workflow_summary()
                print(summary)
                
            except Exception as e:
                print(f"âš ï¸ Error during final validation: {e}")
                print("ðŸ“Š Basic statistics:")
                print(self.game_operations.get_drag_statistics())

            print("="*70)
            return True
            
        except Exception as e:
            print("âŒ Error in enhanced full showcase workflow: " + str(e))
            return False

    def showcase_all_bonus_corrected(self, mode="Normal", listen_duration=15, progress_callback=None):
        """ENHANCED: Showcase bonus with verification system - LIMITED TO FIRST 20"""
        try:
            if not self.player.config:
                return False
                
            layout = self.player.config['layout']
            all_bonus_chars = layout.get('bonus', [])
            
            # LIMIT BONUS TO FIRST 19 (INDEX 0-18) - FIXED
            limited_bonus = []
            for bonus in all_bonus_chars:
                bonus_index = bonus.get('index', 0)  # Default to 0 if no index
                if bonus_index <= 18:
                    limited_bonus.append(bonus)
            
            print(f"\nðŸŽ¯ BONUS LIMITATION APPLIED:")
            print(f"   ðŸ“Š Original bonus: {len(all_bonus_chars)}")
            print(f"   ðŸŽ¯ Limited bonus: {len(limited_bonus)} (index â‰¤19)")
            
            if not limited_bonus:
                print(f"â„¹ï¸ No bonus characters for {mode} mode (after limitation)")
                return True
            
            bonus_chars = limited_bonus
            
            target_slot = self.game_operations._find_slot_by_index(3)
            if not target_slot:
                return False
            
            emoji = "ðŸŽ" if mode == "Normal" else "ðŸ‘»"
            print(f"\n{emoji} STARTING ENHANCED {mode.upper()} BONUS SHOWCASE")
            print(f"ðŸ“Š Will showcase {len(bonus_chars)} bonus characters")
            if self.verification_system.characters_available:
                print("ðŸ” Drag verification: ENABLED")
            else:
                print("âš ï¸ Drag verification: DISABLED (fallback mode)")
            print("="*60)
            
            success_count = 0
            for bonus_index, bonus_char in enumerate(bonus_chars):
                if self.player.should_stop:
                    break
                
                if progress_callback:
                    progress_callback(f"{emoji} Showcasing {mode} bonus {bonus_index + 1}")
                
                print(f"{emoji} Showcasing {mode} bonus {bonus_index + 1}")
                
                # Drag bonus to slot with enhanced verification
                use_cdp = True  # Can be customized
                if not self.game_operations.corrected_drag_and_drop(bonus_char, target_slot, False, use_cdp):
                    print(f"âŒ Failed to drag bonus {bonus_index} with verification")
                    continue
                
                if self.player.should_stop:
                    break
                
                # Listen to music - THREAD SAFE
                print(f"Listening to bonus {bonus_index + 1} for {listen_duration}s...")
                for countdown in range(listen_duration, 0, -1):
                    if self.player.should_stop:
                        break
                    
                    # THREAD SAFE: Update every 3 seconds
                    if progress_callback and countdown % 3 == 0:
                        try:
                            self.player.thread_safe_progress_callback(f"Listening {mode} bonus {bonus_index + 1} ({countdown}s)")
                        except Exception as e:
                            print(f"Progress callback warning: {e}")
                    
                    time.sleep(1)
                
                if self.player.should_stop:
                    break
                
                # Return bonus
                print(f"ðŸ”„ Returning bonus {bonus_index + 1}...")
                self.game_operations.corrected_return_icon(target_slot, use_cdp)
                time.sleep(0.2)
                success_count += 1
            
            print(f"\nâœ… ENHANCED {mode.upper()} BONUS SHOWCASE COMPLETED")
            print(f"ðŸ“Š Success: {success_count}/{len(bonus_chars)} characters")
            print("="*60)
            return success_count == len(bonus_chars)
            
        except Exception as e:
            print("âŒ Error in enhanced " + mode + " bonus showcase: " + str(e))
            return False
    
    def play_all_remixes_corrected(self, mode="Normal", listen_duration=15, progress_callback=None):
        """ENHANCED: Play all remixes with verification system"""
        try:
            if not self.player.remix_library:
                print("â„¹ï¸ No remixes in library")
                return True
            
            emoji = "ðŸŽµ" if mode == "Test" else ("ðŸ‘»" if mode == "Horror" else "ðŸŽª")
            print(f"\n{emoji} STARTING ENHANCED {mode.upper()} REMIX SESSION")
            print(f"ðŸ“Š Will play {len(self.player.remix_library)} remixes")
            if self.verification_system.characters_available:
                print("ðŸ” Drag verification: ENABLED for all remix drags")
            else:
                print("âš ï¸ Drag verification: DISABLED (fallback mode)")
            print("="*60)
            
            success_count = 0
            for remix_index, remix in enumerate(self.player.remix_library):
                if self.player.should_stop:
                    break
                
                self.current_remix_index = remix_index
                
                if progress_callback:
                    progress_callback(f"{emoji} Remix {remix_index + 1}/{len(self.player.remix_library)}: {remix['name']}")
                
                if self.play_complete_remix_corrected(remix, mode, listen_duration, progress_callback):
                    success_count += 1
                else:
                    print(f"âš ï¸ Failed to play remix: {remix['name']}")
                
                # Delay between remixes
                if remix_index < len(self.player.remix_library) - 1 and not self.player.should_stop:
                    time.sleep(2.0)
            
            print(f"\nâœ… ENHANCED {mode.upper()} REMIX SESSION COMPLETED")
            print(f"ðŸ“Š Success: {success_count}/{len(self.player.remix_library)} remixes")
            print("="*60)
            return success_count == len(self.player.remix_library)
            
        except Exception as e:
            print("âŒ Error in enhanced " + mode + " remix session: " + str(e))
            return False
    
    def trigger_horror_mode_transition_corrected(self, transition_duration=15, progress_callback=None):
        """
        ENHANCED: Trigger horror mode with verification system - FIXED with Icon Return
        UPDATED: Added Phase 3 - Return Icon 19 after transformation
        FIXED: Proper access to icon 19 for horror trigger
        """
        try:
            if not self.player.config:
                return False
                
            layout = self.player.config['layout']
            all_icons = layout['icons']
            
            # FIXED: For horror trigger, we NEED to access icon 19 specifically from original list
            print(f"🎯 HORROR MODE TRIGGER - Accessing icon 19 specifically")
            print(f"   📊 Total icons available: {len(all_icons)}")
            print(f"   💡 Normal operations limited to 0-18, but horror trigger needs icon 19")
            
            # Find icon 19 in the original (unlimited) icon list
            horror_icon = None
            for icon in all_icons:
                if icon.get('index') == 19:
                    horror_icon = icon
                    print(f"   🎯 Icon 19 found at coordinates: ({icon.get('x')}, {icon.get('y')})")
                    break
            
            if not horror_icon:
                print("❌ Icon 19 not found in config - cannot trigger horror mode")
                print("   💡 Make sure your JSON config includes icon with index 19")
                return False
            
            print(f"   ✅ Horror trigger ready - will use icon 19 specifically")
            
            # Find slot index 3 (rest of function remains the same)
            layout = self.player.config['layout']
            slots = layout.get('slots', [])
            center_slot_index = self.game_operations.calculate_center_slot_index(slots)
            target_slot = self.game_operations._find_slot_by_index(center_slot_index)
            if not target_slot:
                return False

            obs_controller = getattr(self.player, 'obs_controller', None)

            if progress_callback:
                progress_callback("ðŸ‘» Triggering Horror Mode with verification...")
            
            print("\nðŸ‘» TRIGGERING ENHANCED HORROR MODE TRANSITION...")

            # === THÊM LOGIC GHI TIMESTAMP BẮT ĐẦU ===
            if obs_controller:
                obs_controller.log_event_timestamp(
                    "start_horror_transition", 
                    "Dragging horror icon to trigger transition"
                )
            # === KẾT THÚC PHẦN THÊM MỚI ===

            if self.verification_system.characters_available:
                print("ðŸ” Using verification for horror icon drag")
            else:
                print("âš ï¸ Verification disabled - using fallback")
            print("="*60)
            
            # ================================================================
            # PHASE 1: DRAG ICON 19 TO SLOT 3 (trigger horror mode)
            # ================================================================
            print("ðŸŽ¯ PHASE 1: Dragging horror icon (19) to slot 3 with verification...")
            use_cdp = True  # Can be customized
            if not self.game_operations.corrected_drag_and_drop(horror_icon, target_slot, False, use_cdp):
                print("âŒ Failed to trigger horror mode with verification")
                return False
            
            # ================================================================
            # PHASE 2: WAIT FOR TRANSFORMATION
            # ================================================================
            print(f"PHASE 2: Waiting for horror transformation ({transition_duration}s)...")
            for countdown in range(transition_duration, 0, -1):
                if self.player.should_stop:
                    return False
                
                # THREAD SAFE: Update every 5 seconds for longer transitions
                if progress_callback and countdown % 5 == 0:
                    try:
                        self.player.thread_safe_progress_callback(f"Horror mode transition... ({countdown}s)")
                    except Exception as e:
                        print(f"Progress callback warning: {e}")
                
                time.sleep(1)
            
            if self.player.should_stop:
                return False

            # === THÊM LOGIC GHI TIMESTAMP KẾT THÚC ===
            if obs_controller:
                obs_controller.log_event_timestamp(
                    "end_horror_transition", 
                    "Horror transition visual effect completed"
                )
            # === KẾT THÚC PHẦN THÊM MỚI ===

            print("âœ… ENHANCED HORROR MODE TRANSITION COMPLETED!")
            
            # ================================================================
            # PHASE 3: ðŸ†• RETURN ICON 19 FROM SLOT 3 (clear slot for showcase)
            # ================================================================
            print("\nðŸ”„ PHASE 3: RETURNING ICON 19 FROM SLOT 3 (prepare for showcase)")
            print("="*60)
            print("   ðŸ’¡ Reason: Slot 3 must be empty for horror showcase to work")
            print("   ðŸŽ¯ Target: Return Icon 19 from Slot 3 back to icon area")
            
            if progress_callback:
                progress_callback("ðŸ”„ Returning horror trigger icon for showcase...")
            
            # Check current state before return
            dual_verification_enabled = self.verification_system.characters_available or self.verification_system.putted_chars_available
            if dual_verification_enabled:
                before_chars, before_putted = self.verification_system.get_dual_counts()
                print(f"   ðŸ“Š BEFORE return: Characters={before_chars}, Putted={before_putted}")
                
                if before_chars == 0 and before_putted == 0:
                    print("   âœ… Slot already empty - no return needed")
                    print("   ðŸŽ¯ Horror mode ready for showcase immediately")
                else:
                    print(f"   ðŸŽ¯ Slot occupied - returning icon 19...")
                    
                    # Use enhanced return with all fallback methods
                    return_success = self.game_operations.corrected_return_icon(target_slot, use_cdp)
                    
                    if return_success:
                        # Verify return worked
                        time.sleep(1.0)  # Wait for state update
                        after_chars, after_putted = self.verification_system.get_dual_counts()
                        print(f"   ðŸ“Š AFTER return: Characters={after_chars}, Putted={after_putted}")
                        
                        if after_chars == 0 and after_putted == 0:
                            print("   âœ… RETURN SUCCESSFUL: Slot 3 is now empty")
                            print("   ðŸŽ¯ Horror mode ready for showcase")
                        else:
                            print("   âš ï¸ RETURN UNCLEAR: State may not be fully cleared")
                            print("   ðŸŽ¯ Proceeding with showcase anyway")
                    else:
                        print("   âŒ RETURN FAILED: Using emergency methods...")
                        
                        # Emergency cleanup attempts
                        for emergency_attempt in range(3):
                            print(f"   ðŸš¨ Emergency return attempt {emergency_attempt + 1}/3")
                            
                            # Multiple clicks on slot 3
                            emergency_coords = [
                                (target_slot['x'], target_slot['y']),
                                (target_slot['x'] - 10, target_slot['y']),
                                (target_slot['x'] + 10, target_slot['y']),
                                (target_slot['x'], target_slot['y'] - 10),
                                (target_slot['x'], target_slot['y'] + 10)
                            ]
                            
                            domain_adj = self.player.get_domain_adjustments()
                            self.game_operations.execute_multi_method_clicks(emergency_coords, use_cdp, domain_adj, method="single")
                            time.sleep(0.5)
                            
                            # Check if emergency worked
                            emergency_chars, emergency_putted = self.verification_system.get_dual_counts()
                            if emergency_chars == 0 and emergency_putted == 0:
                                print(f"   âœ… EMERGENCY SUCCESS: Slot cleared on attempt {emergency_attempt + 1}")
                                break
                        else:
                            print("   ðŸ’¥ ALL EMERGENCY ATTEMPTS FAILED")
                            print("   âš ï¸ Proceeding with showcase - may have slot conflicts")
            else:
                print("   âš ï¸ Verification disabled - assuming icon return needed")
                print("   ðŸ”„ Performing blind return attempt...")
                
                # Blind return attempt
                self.game_operations.corrected_return_icon(target_slot, use_cdp)
                time.sleep(1.0)
                print("   âœ… Blind return completed")
            
            # ================================================================
            # HORROR MODE STATE VALIDATION (existing code with better logging)
            # ================================================================
            print("\nðŸ‘» HORROR MODE STATE VALIDATION")
            print("="*50)
            
            try:
                # Validate horror mode activation state
                horror_state = self.verification_system.get_comprehensive_slot_state()
                
                if horror_state and dual_verification_enabled:
                    characters_count = horror_state.get('characters_count', 0)
                    
                    print(f"ðŸ“Š Final horror mode state:")
                    print(f"   Characters count: {characters_count}")
                    
                    # Expect 0 after return (slot should be empty for showcase)
                    if characters_count == 0:
                        print("âœ… Horror mode properly prepared - slot empty for showcase")
                        print("ðŸŽ¯ Ready to showcase all horror icons (0-19)")
                    else:
                        print(f"âš ï¸ Horror mode state: {characters_count} icons still on slots")
                        print("ðŸŽ¯ Proceeding anyway - showcase will handle conflicts")
                else:
                    print("âš ï¸ Cannot validate horror mode state (verification disabled)")
                    print("ðŸŽ¯ Assuming horror mode ready")
                
            except Exception as e:
                print(f"âš ï¸ Error validating horror mode state: {e}")
                print("ðŸŽ¯ Assuming horror mode ready")
            
            print("="*50)
            
            # Final confirmation
            if progress_callback:
                progress_callback("ðŸ‘» Horror Mode ready for showcase!")
            
            print("ðŸŽ‰ ENHANCED HORROR MODE FULLY PREPARED!")
            print("   âœ… Phase 1: Horror trigger completed")
            print("   âœ… Phase 2: Transformation completed") 
            print("   âœ… Phase 3: Icon return completed")
            print("   ðŸŽ¯ Ready for horror showcase (icons 0-19)")
            
            time.sleep(1.0)
            return True
            
        except Exception as e:
            print("âŒ Error triggering enhanced horror mode: " + str(e))
            return False
    
    def execute_remix_only_workflow_corrected(self, progress_callback=None):
        """ENHANCED: Remix-only workflow with verification system"""
        try:
            if progress_callback:
                progress_callback("ðŸŽµ Starting enhanced remix-only workflow...")
            
            print("\nðŸŽµ STARTING ENHANCED REMIX-ONLY WORKFLOW")
            if self.verification_system.characters_available:
                print("ðŸ” Drag verification: ENABLED for all remixes")
            else:
                print("âš ï¸ Drag verification: DISABLED (fallback mode)")
            print("="*60)
            
            success = self.play_all_remixes_corrected("Test", progress_callback=progress_callback)
            
            if success:
                if progress_callback:
                    progress_callback("ðŸŽ‰ Enhanced remix-only workflow completed!")
                print("\nðŸŽ‰ ENHANCED REMIX-ONLY WORKFLOW COMPLETED!")
                
                # ================================================================
                # ðŸ§¹ REMIX-ONLY SMART CLEANUP & VALIDATION
                # ================================================================
                print("\nðŸ§¹ REMIX-ONLY SMART CLEANUP & VALIDATION")
                print("="*60)
                
                try:
                    # Smart cleanup for remix-only mode
                    print("ðŸ”„ Smart batch cleanup for remix-only mode...")
                    cleanup_success_count, cleanup_total, cleanup_errors = self.player.smart_batch_return_all_slots(use_cdp=False)
                    
                    if cleanup_total == 0:
                        print("âœ… No remix cleanup needed - all slots clean")
                    elif cleanup_success_count == cleanup_total:
                        print(f"âœ… Remix cleanup: {cleanup_success_count}/{cleanup_total} perfect")
                    else:
                        print(f"âš ï¸ Remix cleanup: {cleanup_success_count}/{cleanup_total} with issues")
                    
                    # Remix-specific validation
                    is_complete, remaining_issues, recommendations = self.verification_system.validate_workflow_completion()
                    
                    if is_complete:
                        print("âœ… REMIX VALIDATION: PERFECT! All remixes cleaned properly")
                    else:
                        print(f"âš ï¸ REMIX VALIDATION: {len(remaining_issues)} cleanup issues")
                        for issue in remaining_issues[:2]:
                            print(f"   - {issue}")
                    
                    # Enhanced remix summary
                    print("\nðŸ“Š ENHANCED REMIX WORKFLOW STATS:")
                    summary = self.player.get_enhanced_workflow_summary()
                    print(summary)
                    
                except Exception as e:
                    print(f"âš ï¸ Error during remix validation: {e}")
                    print("ðŸ“Š Basic remix statistics:")
                    print(self.game_operations.get_drag_statistics())
                
                print("="*60)

            return success
            
        except Exception as e:
            print("âŒ Error in enhanced remix-only workflow: " + str(e))
            return False        
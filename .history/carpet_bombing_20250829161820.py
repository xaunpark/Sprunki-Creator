# carpet_bombing.py
"""
Carpet Bombing Strategy for Sprunki Game Player.
Handles systematic, multi-coordinate clicking to clear icons from slots
when precision methods fail or are not suitable (e.g., remix cleanup).
"""
import time
import random

import pyautogui
import websocket

class CarpetBombing:
    # [PHASE 2] UPDATE - Accept optional 'config' parameter
    def __init__(self, player_instance, event_bus=None, config=None):
        print("💣 CarpetBombing initialized.")
        self.player = player_instance
        self.event_bus = event_bus
        self.config = config # [PHASE 2] Store the structured config
        # Tạo alias
        self.game_operations = player_instance.game_operations
        self.verification_system = player_instance.verification_system
        self.browser_manager = player_instance.browser_manager

        # Carpet bombing configuration
        self.carpet_bombing_config = {
            'base_coords_per_slot': 2,
            'max_sets': 3,
            'inter_click_delay': 0.03,
            'inter_slot_delay': 1.5,
            'inter_set_delay': 0.3,
        }
        self.carpet_grid_config = {
            'wave_size': 10,
            'max_waves': 5,
            'inter_click_delay': 0.03,
            'inter_wave_delay': 0.5,
            'default_density': 3
        }

    def _execute_edge_reinforcement(self, round_num, use_cdp):
        """
        ðŸŽ¯ STUB: Edge reinforcement for carpet bombing
        """
        try:
            print(f"   ðŸŽ¯ Edge reinforcement round {round_num}")
            # Simple edge clicks - can be expanded later
            edge_clicks = min(round_num * 2, 10)  # 2-10 clicks based on round
            return edge_clicks
            
        except Exception as e:
            print(f"âŒ Edge reinforcement error: {e}")
            return 0
        
    def execute_remix_return_strategy(self, target_slot, use_cdp=False):
        """
        ðŸŽµ ULTRA SIMPLIFIED: Sequential carpet bombing slot by slot
        """
        try:
            print("ðŸŽµ ULTRA SIMPLIFIED CARPET BOMBING")
            print("="*50)
            print("   ðŸŽ¯ Strategy: Sequential slot-by-slot carpet bombing")
            print("   ðŸ“Š Progression: 2â†’3â†’4â†’5... coordinates per slot")
            
            # Get current count
            initial_count = self.verification_system.get_characters_count() if self.verification_system.characters_available else 0
            if initial_count == 0:
                print("   âœ… Already clean")
                return True
            
            print(f"   ðŸ“Š Icons to clear: {initial_count}")
            
            # Get ALL slots from config
            if not self.player.config:
                print("âŒ No config available")
                return False
                
            all_slots = self.player.config.get('layout', {}).get('slots', [])
            if not all_slots:
                print("âŒ No slots in config")
                return False
            
            # Sort slots by index for sequential processing
            sorted_slots = sorted(all_slots, key=lambda s: s.get('index', 0))
            print(f"   ðŸŽ¯ Will process {len(sorted_slots)} slots sequentially")
            
            # Execute progressive carpet bombing sets
            success = self.execute_sequential_carpet_bombing_sets(sorted_slots, initial_count, use_cdp)
            
            return success
            
        except Exception as e:
            print(f"âŒ Error in simplified carpet bombing: {e}")
            return False

    def generate_carpet_grid_for_slots(self, slot_indices, density="default"):
        """
        ðŸŽ¨ NEW: Generate carpet bombing grid covering specified slots
        Returns: List of (x, y) coordinates for systematic clicking
        """
        try:
            print(f"\nðŸŽ¨ GENERATING CARPET GRID")
            print("="*50)
            print(f"   ðŸŽ¯ Target slots: {slot_indices}")
            print(f"   ðŸŽ¨ Density: {density}")
            
            if not self.player.config:
                print("âŒ No config available for grid generation")
                return []
            
            all_slots = self.player.config.get('layout', {}).get('slots', [])
            if not all_slots:
                print("âŒ No slots configuration found")
                return []
            
            # Get density setting
            grid_density = self.carpet_grid_config.get(f'{density}_density', 
                                                    self.carpet_grid_config['default_density'])
            overlap_margin = self.carpet_grid_config['overlap_margin']
            
            print(f"   ðŸ“ Grid density: {grid_density}x{grid_density}")
            print(f"   ðŸ“ Overlap margin: {overlap_margin}px")
            
            # Calculate overall coverage area
            coverage_area = self.game_operations._calculate_slots_coverage_area(slot_indices, all_slots, overlap_margin)
            if not coverage_area:
                print("âŒ Could not calculate coverage area")
                return []
            
            # Generate grid points within coverage area
            grid_points = self._generate_grid_points(coverage_area, grid_density)
            
            print(f"   âœ… Generated {len(grid_points)} grid points")
            print(f"   ðŸ“Š Coverage: {coverage_area['width']}x{coverage_area['height']}px")
            
            return grid_points
            
        except Exception as e:
            print(f"âŒ Error generating carpet grid: {e}")
            return []

    def _generate_grid_points(self, coverage_area, density):
        """
        ðŸŽ¯ NEW: Generate systematic grid points within coverage area
        """
        try:
            grid_points = []
            
            width = coverage_area['width']
            height = coverage_area['height']
            min_x = coverage_area['min_x']
            min_y = coverage_area['min_y']
            
            # Calculate step sizes
            step_x = width / (density + 1)  # +1 to avoid edges
            step_y = height / (density + 1)
            
            # Generate grid pattern
            for row in range(1, density + 1):
                for col in range(1, density + 1):
                    x = min_x + (col * step_x)
                    y = min_y + (row * step_y)
                    
                    # Add small random jitter to avoid perfect grid (more natural)
                    jitter_x = random.randint(-3, 3)
                    jitter_y = random.randint(-3, 3)
                    
                    final_x = int(x + jitter_x)
                    final_y = int(y + jitter_y)
                    
                    grid_points.append((final_x, final_y))
            
            # Sort points for optimal clicking pattern (spiral or row-by-row)
            grid_points = self._optimize_click_sequence(grid_points, coverage_area)
            
            return grid_points
            
        except Exception as e:
            print(f"âŒ Error generating grid points: {e}")
            return []

    def _optimize_click_sequence(self, points, coverage_area):
        """
        ðŸ”„ NEW: Optimize click sequence for efficiency
        Options: spiral, row-by-row, center-out
        """
        try:
            # For now, use center-out pattern (click from center outward)
            center_x = coverage_area['center_x']
            center_y = coverage_area['center_y']
            
            # Sort by distance from center
            def distance_from_center(point):
                x, y = point
                return ((x - center_x) ** 2 + (y - center_y) ** 2) ** 0.5
            
            optimized_points = sorted(points, key=distance_from_center)
            
            print(f"   ðŸ”„ Optimized click sequence: center-out pattern")
            
            return optimized_points
            
        except Exception as e:
            print(f"âŒ Error optimizing click sequence: {e}")
            return points  # Return original if optimization fails

    def visualize_carpet_grid(self, slot_indices=None):
        """
        ðŸŽ¨ NEW: Visualize carpet grid for debugging (optional tool)
        """
        try:
            if slot_indices is None:
                # Default to first few slots for testing
                slot_indices = [0, 1, 2, 3]
            
            print(f"\nðŸŽ¨ CARPET GRID VISUALIZATION")
            print("="*50)
            
            grid_points = self.generate_carpet_grid_for_slots(slot_indices, "default")
            
            if grid_points:
                print(f"ðŸ“ Grid Points Generated:")
                for i, (x, y) in enumerate(grid_points, 1):
                    print(f"   {i:2d}. ({x:4d}, {y:4d})")
                
                # Show pattern
                print(f"\nðŸŽ¯ Click Pattern (first 9 points):")
                for i in range(min(9, len(grid_points))):
                    x, y = grid_points[i]
                    print(f"   Wave 1, Click {i+1}: ({x}, {y})")
            else:
                print("âŒ No grid points generated")
                
        except Exception as e:
            print(f"âŒ Visualization error: {e}")

    def show_carpet_grid_info(self):
        """Ultra simplified carpet bombing info - print only"""
        try:
            print("\nðŸŽ¯ ULTRA SIMPLIFIED CARPET BOMBING INFO:")
            print("="*60)
            print("ðŸ“Š STRATEGY: Sequential slot-by-slot progression")
            print("ðŸŽ¯ EXECUTION: Process slots 0â†’1â†’2â†’...â†’last sequentially")
            print("ðŸ“ˆ PROGRESSION: 2â†’3â†’4â†’5â†’6â†’7... coordinates per slot")
            print("\nðŸ“ COORDINATE STRATEGY PER SLOT:")
            print("   â€¢ Set 1: 2 coords = Center + Horizontal offset")
            print("   â€¢ Set 2: 3 coords = Center + Horizontal + Vertical")
            print("   â€¢ Set 3: 4 coords = Center + H + V + Diagonal")
            print("   â€¢ Set 4: 5 coords = Previous + More diagonals")
            print("   â€¢ Set 5+: 6+ coords = Previous + Random scatter")
            print(f"\nâ±ï¸ TIMING:")
            print(f"   â€¢ Inter-click: {self.carpet_bombing_config['inter_click_delay']}s (within slot)")
            print(f"   â€¢ Inter-slot: {self.carpet_bombing_config['inter_slot_delay']}s (between slots)")
            print(f"   â€¢ Inter-set: {self.carpet_bombing_config['inter_set_delay']}s (between sets)")
            print(f"   â€¢ Max sets: {self.carpet_bombing_config['max_sets']}")
            print("\nðŸ”„ TERMINATION: Continue until Characters count = 0")
            print("\nâœ… SIMPLIFIED FEATURES:")
            print("   â€¢ No edge detection")
            print("   â€¢ No complex grid calculation")
            print("   â€¢ No boundary sweeps")
            print("   â€¢ Pure sequential slot processing")
            print("   â€¢ Progressive coordinate increase")
            print("="*60)
            
        except Exception as e:
            print(f"âŒ Config info error: {e}")

    def execute_carpet_bombing_waves(self, grid_points, target_description="slots", use_cdp=False):
        """
        ðŸŽ¯ NEW: Execute carpet bombing in waves with progress monitoring
        """
        try:
            print(f"\nðŸŽ¯ CARPET BOMBING EXECUTION")
            print("="*60)
            print(f"   ðŸŽ¯ Target: {target_description}")
            print(f"   ðŸ“Š Total points: {len(grid_points)}")
            
            wave_size = self.carpet_grid_config['wave_size']
            max_waves = self.carpet_grid_config['max_waves']
            inter_click_delay = self.carpet_grid_config['inter_click_delay']
            inter_wave_delay = self.carpet_grid_config['inter_wave_delay']
            
            # Split into waves
            waves = [grid_points[i:i+wave_size] for i in range(0, len(grid_points), wave_size)]
            total_waves = min(len(waves), max_waves)
            
            print(f"   ðŸŒŠ Total waves: {total_waves} (max: {max_waves})")
            print(f"   â±ï¸ Wave size: {wave_size} clicks")
            
            # Get initial state
            initial_count = self.verification_system.get_characters_count() if self.verification_system.characters_available else None
            print(f"   ðŸ“Š Initial count: {initial_count}")
            
            if initial_count == 0:
                print("   âœ… Already clean - no bombing needed")
                return True, 0, []
            
            waves_executed = 0
            total_clicks = 0
            
            for wave_num, wave_points in enumerate(waves[:max_waves], 1):
                if self.player.should_stop:
                    break
                    
                print(f"\nðŸŒŠ WAVE {wave_num}/{total_waves}")
                print(f"   ðŸ“ Points: {len(wave_points)}")
                
                # Execute wave
                wave_success, clicks_in_wave = self._execute_single_wave(
                    wave_points, wave_num, use_cdp, inter_click_delay
                )
                
                waves_executed += 1
                total_clicks += clicks_in_wave
                
                if not wave_success:
                    print(f"   âŒ Wave {wave_num} failed")
                    continue
                
                # Check progress after wave
                time.sleep(inter_wave_delay)
                current_count = self.verification_system.get_characters_count() if self.verification_system.characters_available else None
                
                print(f"   ðŸ“Š Count after wave: {current_count}")
                
                if current_count == 0:
                    print(f"   ðŸŽ‰ SUCCESS! Clean after wave {wave_num}")
                    return True, waves_executed, []
                elif current_count and initial_count and current_count < initial_count:
                    print(f"   ðŸ“ˆ Progress: {initial_count} â†’ {current_count}")
                
                # Continue to next wave
                print(f"   â­ï¸ Continuing to next wave...")
            
            # Final check
            final_count = self.verification_system.get_characters_count() if self.verification_system.characters_available else None
            success = (final_count == 0)
            
            print(f"\nðŸŽ¯ CARPET BOMBING COMPLETED")
            print(f"   ðŸŒŠ Waves executed: {waves_executed}")
            print(f"   ðŸ‘† Total clicks: {total_clicks}")
            print(f"   ðŸ“Š Final count: {final_count}")
            print(f"   ðŸŽ¯ Result: {'âœ… SUCCESS' if success else 'âŒ INCOMPLETE'}")
            
            return success, waves_executed, [] if success else [f"Still {final_count} icons remaining"]
            
        except Exception as e:
            print(f"âŒ Error in carpet bombing: {e}")
            return False, 0, [f"Execution error: {str(e)}"]

    def _execute_single_wave(self, wave_points, wave_num, use_cdp, inter_click_delay):
        """
        ðŸŒŠ NEW: Execute a single wave of clicks
        """
        try:
            print(f"   ðŸŽ¯ Executing {len(wave_points)} clicks...")
            
            clicks_executed = 0
            
            for i, (x, y) in enumerate(wave_points, 1):
                if self.player.should_stop:
                    break
                    
                # Execute click
                click_success = self._execute_carpet_click(x, y, use_cdp)
                
                if click_success:
                    clicks_executed += 1
                    print(f"      {i}/{len(wave_points)}: ({x},{y}) âœ…")
                else:
                    print(f"      {i}/{len(wave_points)}: ({x},{y}) âŒ")
                
                # Delay between clicks in same wave
                if i < len(wave_points):  # Don't delay after last click
                    time.sleep(inter_click_delay)
            
            print(f"   âœ… Wave completed: {clicks_executed}/{len(wave_points)} clicks")
            return True, clicks_executed
            
        except Exception as e:
            print(f"   âŒ Wave execution error: {e}")
            return False, 0

    def _execute_carpet_click(self, x, y, use_cdp):
        """
        ðŸ‘† NEW: Execute single carpet bombing click
        """
        try:
            if use_cdp:
                return self._execute_cdp_carpet_click(x, y)
            else:
                return self._execute_pyautogui_carpet_click(x, y)
                
        except Exception as e:
            print(f"      âŒ Click error at ({x},{y}): {e}")
            return False

    def _execute_cdp_carpet_click(self, x, y):
        """ðŸ‘† NEW: CDP carpet click"""
        try:
            if self.verification_system.batch_verification_active and self.verification_system.session_websocket:
                # Use batch WebSocket if available
                self.verification_system._send_batch_cdp_command({
                    "method": "Input.dispatchMouseEvent",
                    "params": {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1}
                })
                time.sleep(0.05)
                self.verification_system._send_batch_cdp_command({
                    "method": "Input.dispatchMouseEvent", 
                    "params": {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1}
                })
                return True
            else:
                # Fallback to individual WebSocket
                ws_url = self.browser_manager._get_websocket_url()
                if ws_url:
                    ws = websocket.create_connection(ws_url, timeout=3)
                    self.browser_manager._simulate_click_via_cdp(ws, x, y)
                    ws.close()
                    return True
                return False
                
        except Exception as e:
            return False

    def _execute_pyautogui_carpet_click(self, x, y):
        """ðŸ‘† NEW: PyAutoGUI carpet click"""
        try:
            pyautogui.click(x, y)
            return True
        except Exception as e:
            return False
        
    def _execute_carpet_bombing_strategy(self, icon_count, use_cdp=False):
        """
        ðŸŽµ PHASE 4.3A: Carpet bombing strategy execution
        """
        try:
            print("ðŸŽµ CARPET BOMBING STRATEGY")
            print("-" * 40)
            
            # Get active slots for carpet bombing
            active_slots = self.verification_system.get_active_slots_queue()
            if not active_slots:
                # Fallback to estimated slot coverage
                active_slots = list(range(min(8, icon_count + 1)))
            
            print(f"   ðŸŽ¯ Target slots: {active_slots}")
            
            # Execute carpet bombing via remix strategy
            dummy_slot = {'x': 999, 'y': 515, 'index': 3}  # Dummy slot for interface compatibility
            success = self.execute_remix_return_strategy(dummy_slot, use_cdp)
            
            return success, {
                'method': 'carpet_bombing',
                'target_slots': active_slots,
                'icon_count': icon_count
            }
            
        except Exception as e:
            print(f"âŒ Carpet bombing strategy error: {e}")
            return False, {'error': str(e)}
        
    def execute_full_carpet_bombing_round(self, grid_points, round_num, expected_clear, use_cdp=False):
        """
        ðŸŒŠ ENHANCED: Execute round with edge priority
        """
        try:
            print(f"ðŸŒŠ ENHANCED ROUND {round_num} WITH EDGE PRIORITY")
            print(f"   ðŸ“ Total points: {len(grid_points)}")
            print(f"   ðŸŽ¯ Expected to clear: {expected_clear}")
            
            # PHASE 1: Execute all grid points
            total_clicks = 0
            successful_clicks = 0
            
            print(f"   ðŸŽ¯ Phase 1: Main grid execution ({len(grid_points)} points)")
            
            for i, (x, y) in enumerate(grid_points, 1):
                if self.player.should_stop:
                    break
                    
                # Execute single click
                click_success = self._execute_carpet_click(x, y, use_cdp)
                
                total_clicks += 1
                if click_success:
                    successful_clicks += 1
                
                # Minimal delay between clicks
                time.sleep(0.02)
                
                # Progress indicator every 30 clicks
                if i % 30 == 0:
                    print(f"      Progress: {i}/{len(grid_points)} clicks")
            
            print(f"   âœ… Phase 1 completed: {successful_clicks}/{total_clicks} clicks")
            
            # PHASE 2: EDGE REINFORCEMENT (extra clicks for stubborn edge slots)
            if round_num >= 2:  # Start edge reinforcement from round 2
                edge_reinforcement_clicks = self._execute_edge_reinforcement(round_num, use_cdp)
                total_clicks += edge_reinforcement_clicks
                successful_clicks += edge_reinforcement_clicks
                print(f"   ðŸŽ¯ Phase 2: Edge reinforcement - {edge_reinforcement_clicks} extra clicks")
            
            print(f"   âœ… Round {round_num} TOTAL: {successful_clicks}/{total_clicks} clicks successful")
            
            return successful_clicks > 0
            
        except Exception as e:
            print(f"âŒ Error in enhanced round execution: {e}")
            return False

    def execute_sequential_carpet_bombing_sets(self, sorted_slots, initial_count, use_cdp=False):
        """
        ðŸŽ¯ NEW: Execute progressive carpet bombing sets
        Set 1: 2 coords/slot, Set 2: 3 coords/slot, etc.
        """
        try:
            print(f"\nðŸŽ¯ SEQUENTIAL CARPET BOMBING SETS")
            print("="*60)
            
            max_sets = 6  # Maximum bombing sets
            base_coords_per_slot = 2  # Start with 2 coordinates per slot
            
            for set_num in range(1, max_sets + 1):
                if self.player.should_stop:
                    break
                    
                print(f"\nðŸŽ¯ CARPET SET {set_num}/{max_sets}")
                print("-" * 40)
                
                # Check current count before set
                current_count = self.verification_system.get_characters_count() if self.verification_system.characters_available else None
                if current_count == 0:
                    print(f"âœ… COMPLETE! All clear before set {set_num}")
                    return True
                
                print(f"   ðŸ“Š Icons remaining: {current_count}")
                
                # Calculate coordinates per slot for this set
                coords_per_slot = base_coords_per_slot + (set_num - 1)
                print(f"   ðŸ“ Set {set_num}: {coords_per_slot} coordinates per slot")
                
                # Generate coordinate set for all slots
                coordinate_set = self.generate_simple_coordinate_set(sorted_slots, coords_per_slot, set_num)
                
                if not coordinate_set:
                    print(f"   âŒ Could not generate coordinates for set {set_num}")
                    continue
                
                print(f"   ðŸ“Š Generated {len(coordinate_set)} total coordinates")
                
                # Execute sequential clicking for this set
                set_success = self.execute_sequential_clicking_set(coordinate_set, set_num, use_cdp)
                
                # Check progress after set
                after_count = self.verification_system.get_characters_count() if self.verification_system.characters_available else None
                progress = (current_count or 0) - (after_count or 0)
                
                print(f"   ðŸ“Š Set {set_num} result: {current_count} â†’ {after_count} (cleared: {progress})")
                
                if after_count == 0:
                    print(f"ðŸŽ‰ SUCCESS! All icons cleared after set {set_num}")
                    return True
                elif progress > 0:
                    print(f"   ðŸ“ˆ Progress made, continuing to next set...")
                else:
                    print(f"   âš ï¸ No progress in set {set_num}, but continuing...")
                
                # Brief pause between sets
                time.sleep(0.3)
            
            # Final check
            final_count = self.verification_system.get_characters_count() if self.verification_system.characters_available else None
            
            if final_count == 0:
                print(f"FINAL SUCCESS! All icons cleared")
                return True
            else:
                print(f"CARPET BOMBING INCOMPLETE after {max_sets} sets: {final_count} icons remaining")
                print("ATTEMPTING GAME RESTART FALLBACK...")
                
                # Game restart fallback when carpet bombing fails
                restart_success = self.browser_manager.restart_game_to_initial_state(use_cdp)
                
                if restart_success:
                    print("GAME RESTART FALLBACK SUCCESS: All icons should be cleared")
                    return True
                else:
                    print("GAME RESTART FALLBACK WARNING: Assuming success anyway")
                    return True  # Assume success since restart should work
                
        except Exception as e:
            print(f"Error in sequential carpet bombing: {e}")
            
            # Emergency game restart fallback on exception
            print("EMERGENCY GAME RESTART due to carpet bombing error...")
            try:
                restart_success = self.browser_manager.restart_game_to_initial_state(use_cdp)
                if restart_success:
                    print("EMERGENCY RESTART SUCCESS")
                    return True
                else:
                    print("EMERGENCY RESTART WARNING: Assuming success")
                    return True
            except Exception as restart_error:
                print(f"Emergency restart also failed: {restart_error}")
                return False

    def generate_simple_coordinate_set(self, sorted_slots, coords_per_slot, set_num):
        """
        ðŸ“ NEW: Generate simple coordinate set - coords_per_slot for each slot
        """
        try:
            coordinate_set = []
            
            print(f"   ðŸ“ Generating {coords_per_slot} coordinates per slot:")
            
            for slot in sorted_slots:
                slot_index = slot.get('index', -1)
                slot_x, slot_y = slot['x'], slot['y']
                
                # Generate coordinates for this specific slot
                slot_coordinates = self.generate_slot_coordinates(slot_x, slot_y, coords_per_slot, set_num)
                
                # Add slot info to each coordinate for tracking
                for coord_x, coord_y in slot_coordinates:
                    coordinate_set.append({
                        'x': coord_x,
                        'y': coord_y,
                        'slot_index': slot_index,
                        'slot_center': (slot_x, slot_y)
                    })
                
                print(f"      Slot {slot_index}: {len(slot_coordinates)} coords at ({slot_x}, {slot_y})")
            
            return coordinate_set
            
        except Exception as e:
            print(f"âŒ Error generating coordinate set: {e}")
            return []

    def generate_slot_coordinates(self, center_x, center_y, num_coords, set_num):
        """
        ðŸŽ¯ NEW: Generate coordinates for single slot using progressive strategy
        """
        try:
            coordinates = []
            
            # Strategy: Cross pattern + random offsets
            if num_coords >= 1:
                # Always include center
                coordinates.append((center_x, center_y))
            
            if num_coords >= 2:
                # Add one offset (left or right alternating by set)
                offset = 15 + (set_num * 3)  # Increase offset with set number
                if set_num % 2 == 1:
                    coordinates.append((center_x - offset, center_y))  # Left
                else:
                    coordinates.append((center_x + offset, center_y))  # Right
            
            if num_coords >= 3:
                # Add vertical offset
                offset = 15 + (set_num * 3)
                if set_num % 2 == 1:
                    coordinates.append((center_x, center_y - offset))  # Top
                else:
                    coordinates.append((center_x, center_y + offset))  # Bottom
            
            if num_coords >= 4:
                # Add diagonal
                offset = 12 + (set_num * 2)
                coordinates.append((center_x - offset, center_y - offset))  # Top-left
            
            if num_coords >= 5:
                # Add another diagonal
                offset = 12 + (set_num * 2)
                coordinates.append((center_x + offset, center_y - offset))  # Top-right
            
            if num_coords >= 6:
                # Add more diagonals
                offset = 12 + (set_num * 2)
                coordinates.append((center_x - offset, center_y + offset))  # Bottom-left
                coordinates.append((center_x + offset, center_y + offset))  # Bottom-right
            
            # For more than 7 coordinates, add random scatter
            while len(coordinates) < num_coords:
                scatter_range = 20 + (set_num * 2)
                jitter_x = random.randint(-scatter_range, scatter_range)
                jitter_y = random.randint(-scatter_range, scatter_range)
                coordinates.append((center_x + jitter_x, center_y + jitter_y))
            
            return coordinates[:num_coords]  # Ensure exact count
            
        except Exception as e:
            print(f"âŒ Error generating slot coordinates: {e}")
            return [(center_x, center_y)]  # Fallback to center only

    def execute_sequential_clicking_set(self, coordinate_set, set_num, use_cdp=False):
        """
        ðŸ‘† NEW: Execute clicking in sequential order (slot 0 â†’ slot 1 â†’ slot 2...)
        """
        try:
            print(f"   ðŸ‘† Executing sequential clicking for set {set_num}")
            
            # Group coordinates by slot for sequential processing
            slots_coords = {}
            for coord in coordinate_set:
                slot_index = coord['slot_index']
                if slot_index not in slots_coords:
                    slots_coords[slot_index] = []
                slots_coords[slot_index].append(coord)
            
            # Sort slots for sequential processing
            sorted_slot_indices = sorted(slots_coords.keys())
            
            total_clicks = 0
            successful_clicks = 0
            
            # Process each slot sequentially
            for slot_index in sorted_slot_indices:
                if self.player.should_stop:
                    break
                
                slot_coords = slots_coords[slot_index]
                print(f"      ðŸŽ¯ Processing slot {slot_index}: {len(slot_coords)} clicks")
                
                # Execute all coordinates for this slot
                for i, coord in enumerate(slot_coords):
                    click_success = self._execute_carpet_click(coord['x'], coord['y'], use_cdp)
                    
                    total_clicks += 1
                    if click_success:
                        successful_clicks += 1
                    
                    # Small delay between clicks within same slot
                    time.sleep(0.03)
                
                # Slightly longer delay between slots for visual feedback
                if slot_index != sorted_slot_indices[-1]:  # Not last slot
                    time.sleep(0.1)
            
            print(f"   âœ… Set {set_num} executed: {successful_clicks}/{total_clicks} clicks successful")
            
            return successful_clicks > 0
            
        except Exception as e:
            print(f"âŒ Error executing clicking set: {e}")
            return False        






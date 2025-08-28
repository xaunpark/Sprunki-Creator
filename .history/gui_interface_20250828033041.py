# gui_interface.py
"""
GUI Interface for Sprunki Game Player.
Handles all Tkinter window creation, widget layout, and event handling.
Interacts with the main player instance to trigger actions and display status.
"""
from tkinter import Tk, Button, Label, StringVar, messagebox, filedialog, Frame, Radiobutton, IntVar
from tkinter.ttk import Progressbar

import websocket

from utils.resource_manager import ThreadSafeProgressUpdater

class GameGUI:
    def __init__(self, player_instance):
        """
        Khởi tạo giao diện người dùng.
        :param player_instance: Tham chiếu đến instance của SprunkiGamePlayer.
        """
        print("🎨 GameGUI initialized.")
        self.player = player_instance
        self.root = Tk()
        self.player.root = self.root # Gán root lại cho player để ThreadSafeUpdater hoạt động

        # Khởi tạo các biến Tkinter
        self.mode_var = IntVar(value=1)
        self.status_var = StringVar(value="Config not loaded")
        self.action_var = StringVar(value="ðŸš€ Ready for ENHANCED workflow...")
        self.remix_display_var = StringVar(value="ðŸ“‹ Will generate library when loading JSON...")
        
        # Gán các biến này lại cho player để các module khác có thể truy cập nếu cần
        # (Mặc dù lý tưởng là không cần, nhưng để an toàn cho việc tái cấu trúc)
        self.player.mode = self.mode_var
        self.player.status_var = self.status_var
        self.player.action_var = self.action_var
        
        self.setup_widgets()

    def run(self):
        """Chạy vòng lặp chính của GUI."""
        self.root.mainloop()
        
    # ================================================================
    # == GUI SETUP (ENHANCED) ==
    # ================================================================
    
    def setup_widgets(self):
        self.root = Tk()
        self.root.title("Sprunki Game Player - ENHANCED with Characters Array Verification + Smart Page Verification")
        self.root.geometry("650x900")
        
        self.mode_var = IntVar(value=1)
        self.status_var = StringVar()
        self.status_var.set("Config not loaded")
        self.action_var = StringVar()
        self.action_var.set("ðŸš€ Ready for ENHANCED workflow with Smart Verification...")

        self.progress_updater = ThreadSafeProgressUpdater(self.root, self.action_var)

        # Header
        Label(self.root, text="Sprunki Game Player", font=("Arial", 16, "bold")).pack(pady=10)
        Label(self.root, text="ðŸŽ® ENHANCED: Chrome Debug + Characters Array Verification + Smart Page Verification ðŸš€", 
            font=("Arial", 12), fg="green").pack()
        
        # Enhanced workflow info
        enhanced_info_frame = Frame(self.root, bg="#E8F5E8", relief="ridge", bd=1)
        enhanced_info_frame.pack(fill="x", padx=10, pady=5)
        
        Label(enhanced_info_frame, text="âš¡ ENHANCED WORKFLOW:", 
            font=("Arial", 10, "bold"), bg="#E8F5E8", fg="#2E7D32").pack(anchor="w", padx=10, pady=(5,2))
        
        enhanced_features = """âœ… Characters Array Verification: Count "1" values in Characters Array for accurate state
        âœ… 5-Coordinate Retry: Center â†’ Left â†’ Right â†’ Top â†’ Bottom positions for each icon
        âœ… Smart Fallback: Auto-fallback to PyAutoGUI if CDP verification fails
        âœ… Enhanced Statistics: Track verification success rate and coordinate usage
        âœ… Game-Aware: Real-time monitoring of Characters Array state
        âœ… Intelligent Retry: Return icon and retry at different coordinate if verification fails"""
        
        Label(enhanced_info_frame, text=enhanced_features, justify="left",
            font=("Arial", 8), fg="#1B5E20", bg="#E8F5E8").pack(anchor="w", padx=15, pady=(0,5))
        
        # Smart verification system info
        verification_frame = Frame(self.root, bg="#E3F2FD", relief="ridge", bd=1)
        verification_frame.pack(fill="x", padx=10, pady=5)
        
        Label(verification_frame, text="ðŸ”¢ CHARACTERS ARRAY SYSTEM:", 
            font=("Arial", 10, "bold"), bg="#E3F2FD", fg="#1565C0").pack(anchor="w", padx=10, pady=(5,2))
        
        verification_text = """ðŸ”¢ Characters Array Monitoring: Real-time tracking of character placement state
        ðŸŽ¯ Count-Based Verification: Count "1" values in array = number of icons on slots
        âš¡ Stable Across Mods: Works reliably on all game modifications
        ðŸŽ® High Performance: Direct VM access with optimized JavaScript functions
        ðŸ“Š Accurate State: No dependency on unreliable Putted Characters or Polos Array
        ðŸ”„ Real-Time Updates: Instant detection of drag/return operations"""
        
        Label(verification_frame, text=verification_text, justify="left",
            font=("Arial", 8), fg="#0D47A1", bg="#E3F2FD").pack(anchor="w", padx=15, pady=(0,5))
        
        # Image detection info
        detection_frame = Frame(self.root, bg="#FFF3E0", relief="ridge", bd=1)
        detection_frame.pack(fill="x", padx=10, pady=5)
        
        Label(detection_frame, text="ðŸ–¼ï¸ ENHANCED IMAGE DETECTION:", 
            font=("Arial", 10, "bold"), bg="#FFF3E0", fg="#E65100").pack(anchor="w", padx=10, pady=(5,2))
        
        detection_text = """ðŸŽ¯ Smart Auto-Scroll: Automatic PageDown if Run/Fullscreen buttons not in viewport
        ðŸ” Multi-Scale Detection: Test multiple scale factors for button recognition
        ðŸŽª Domain-Optimized: Specific adjustments for cocrea.world game timing
        ðŸ” Template Matching: Advanced CV2 image matching with confidence thresholds
        ðŸ”„ Retry Logic: Multiple attempts with different scroll positions
        âš™ï¸ Config-Driven: All detection parameters configurable via config.json"""
        
        Label(detection_frame, text=detection_text, justify="left",
            font=("Arial", 8), fg="#BF360C", bg="#FFF3E0").pack(anchor="w", padx=15, pady=(0,5))
        
        # Mode selection
        mode_frame = Frame(self.root)
        mode_frame.pack(pady=10)
        
        Label(mode_frame, text="ðŸŽ¯ Select play mode:", font=("Arial", 11, "bold")).pack()
        
        Radiobutton(mode_frame, text="ðŸ”€ Showcase + Remix (ENHANCED)", 
                variable=self.mode_var, value=1, font=("Arial", 10)).pack(anchor="w")
        
        Radiobutton(mode_frame, text="ðŸŽµ Remix Only (ENHANCED)", 
                variable=self.mode_var, value=2, font=("Arial", 10)).pack(anchor="w")
        
        # Debug frame with Characters Array System
        debug_frame = Frame(self.root, bg="#E3F2FD", relief="ridge", bd=1)
        debug_frame.pack(fill="x", padx=10, pady=5)
        
        Label(debug_frame, text="ðŸ”§ DEBUG TOOLS:", 
            font=("Arial", 10, "bold"), bg="#E3F2FD", fg="#1565C0").pack(anchor="w", padx=10, pady=(5,2))
        
        debug_buttons = Frame(debug_frame, bg="#E3F2FD")
        debug_buttons.pack(fill="x", padx=10, pady=(0,5))
        
        Button(debug_buttons, text="ðŸ“Š Enhanced Stats", 
            command=self.show_drag_stats, bg="#4CAF50", fg="white", font=("Arial", 9)).pack(side="left")
        
        Button(debug_buttons, text="ðŸŒ Test WebSocket", 
            command=self.test_websocket_connection, bg="#FF9800", fg="white", font=("Arial", 9)).pack(side="left", padx=(5,0))
        
        Button(debug_buttons, text="ðŸ”¢ Test Characters Array", 
            command=self.test_verification_system, bg="#9C27B0", fg="white", font=("Arial", 9)).pack(side="left", padx=(5,0))
        
        Button(debug_buttons, text="âš¡ Test Smart Verification", 
            command=self.test_smart_verification_system, bg="#2196F3", fg="white", font=("Arial", 9)).pack(side="left", padx=(5,0))

        Button(debug_buttons, text="ðŸŽ¯ Check Mode", 
            command=self.player.show_current_mode_info, bg="#607D8B", fg="white", font=("Arial", 9)).pack(side="left", padx=(5,0))

        Button(debug_buttons, text="ðŸŽ¨ Grid Info", 
            command=self.player.show_carpet_grid_info, bg="#795548", fg="white", font=("Arial", 9)).pack(side="left", padx=(5,0))

        Button(debug_buttons, text="ðŸ§ª Test Carpet", 
            command=self.show_carpet_bombing_test_results, bg="#4CAF50", fg="white", font=("Arial", 9)).pack(side="left", padx=(5,0))

        Button(debug_buttons, text="🎯 DUAL Tracking", 
            command=self.show_smart_dual_tracking_status, bg="#FF5722", fg="white", font=("Arial", 9)).pack(side="left", padx=(5,0))

        # Remix info display
        self.display_remix_library_info()
        
        # Main button
        Button(self.root, text="ðŸ”¢ Load JSON & Start ENHANCED Auto with Characters Array Verification", 
            command=self.load_config_and_start, 
            bg="#4CAF50", fg="white", font=("Arial", 12, "bold")).pack(pady=15)
        
        # Status
        Label(self.root, text="ðŸ“Š Status:").pack(pady=(20,5))
        Label(self.root, textvariable=self.status_var, fg="blue").pack()
        
        # Instructions with Characters Array System
        instruction_text = """
    ðŸŽ¯ ENHANCED WORKFLOW WITH CHARACTERS ARRAY VERIFICATION:

    ðŸ”¢ CHARACTERS ARRAY SYSTEM:
    1. Characters Array Monitoring: Real-time tracking of character placement array
    2. Count-Based Verification: Count "1" values = accurate number of placed icons
    3. Stable Across Mods: Works reliably on all game modifications (no Putted Characters bugs)
    4. High Performance: Direct VM access with optimized JavaScript functions

    âš¡ 5-COORDINATE STRATEGY (UNCHANGED):
    - Coordinate 1: Center (original icon position)
    - Coordinate 2: Left (icon_x - 15, icon_y)
    - Coordinate 3: Right (icon_x + 15, icon_y)  
    - Coordinate 4: Top (icon_x, icon_y - 15)
    - Coordinate 5: Bottom (icon_x, icon_y + 15)

    ðŸ–¼ï¸ IMAGE DETECTION (PRESERVED):
    - Original Run button & Fullscreen detection logic kept exactly as-is
    - Auto-scroll capability for buttons outside viewport
    - Multi-scale template matching with confidence thresholds
    - Domain-specific timing adjustments for cocrea.world

    ðŸš€ NEW WORKFLOW:
    Page Load â†’ Smart Verification â†’ Game Verification â†’ âœ… Verification Success
    â†’ 3s Stabilization â†’ Start Image Detection (Run Button) â†’ Game Load
    â†’ Image Detection (Fullscreen Button) â†’ F11 Fullscreen â†’ Characters Array Setup
    â†’ Ready for Automation with Characters Array Verification

    ðŸŽ® BENEFITS:
    - Replaces unreliable Putted Characters with stable Characters Array
    - Works consistently across all game mods and versions
    - Real-time accurate state monitoring
    - No dependency on buggy game variables
    - Enhanced reliability for drag/return operations
        """
        Label(self.root, text=instruction_text, justify="left", 
            font=("Arial", 8), bg="#f0f0f0").pack(pady=10, padx=10, fill="x")
        
        # Control buttons
        self.stop_btn = Button(self.root, text="ðŸ›‘ Stop Enhanced Auto", 
                            command=self.stop_all, state="disabled",
                            bg="#f44336", fg="white", font=("Arial", 12, "bold"))
        self.stop_btn.pack(pady=10)
        
        # Progress
        Label(self.root, text="ðŸ“ˆ Progress:").pack(pady=(10,5))
        self.progress_bar = Progressbar(self.root, length=500, mode='determinate')
        self.progress.pack(pady=5)
        
        # Current action
        Label(self.root, textvariable=self.action_var, fg="green", 
            font=("Arial", 10, "bold")).pack(pady=5)
        
        # Domain support with Characters Array System
        domains_text = f"ðŸŒ Enhanced for: cocrea.world (Chrome Debug + Characters Array Verification + Image Detection)"
        Label(self.root, text=domains_text, fg="gray", font=("Arial", 9)).pack(pady=5)
        
        # Hotkey
        Label(self.root, text="âŒ¨ï¸ ESC = Emergency stop", 
            fg="red", font=("Arial", 10, "bold")).pack(pady=5)
        
    def show_drag_stats(self):
        """Show enhanced drag statistics"""
        stats = self.player.game_operations.get_drag_statistics()
        print("\n" + "="*60)
        print("ðŸ“Š ENHANCED DRAG STATISTICS:")
        print("="*60)
        print(stats)
        print("="*60)

    def test_websocket_connection(self):
        """Test WebSocket connection to Chrome debug"""
        try:
            ws_url = self.player.browser_manager._get_websocket_url()
            if ws_url:
                ws = websocket.create_connection(ws_url, timeout=5)
                
                # Test with simple command
                test_response = self.player.browser_manager._send_cdp_command(ws, {
                    "method": "Runtime.evaluate", 
                    "params": {"expression": "window.location.href", "returnByValue": True}
                })
                
                ws.close()
                
                if test_response:
                    print("âœ… WEBSOCKET TEST SUCCESSFUL:")
                    print(f"   Debug Port: {self.debugging_port}")
                    print(f"   Current URL: {test_response.get('result', {}).get('result', {}).get('value', 'Unknown')}")
                    print(f"   CDP commands working correctly")
                else:
                    print("âš ï¸ WEBSOCKET TEST WARNING:")
                    print("   WebSocket connected but CDP commands failed")
            else:
                messagebox.showerror("WebSocket Test", 
                    "âŒ Could not establish WebSocket connection\n\n"
                    "Make sure Chrome debug mode is running")
        except Exception as e:
            messagebox.showerror("WebSocket Test", f"âŒ WebSocket test failed: {e}")

    def test_verification_system(self):
        """Test Characters Array verification system"""
        try:
            if not self.characters_setup_done:
                messagebox.showinfo("Verification Test", 
                    "ðŸ”¢ Characters Array system not setup yet.\n\n"
                    "Load a JSON file and start the workflow to initialize verification.")
                return
            
            if not self.characters_available:
                messagebox.showwarning("Verification Test", 
                    "âš ï¸ Characters Array system is disabled.\n\n"
                    "WebSocket connection may not be available.")
                return
            
            # Test getting current count
            current_count = self.verification_system.get_characters_count()
            
            if current_count is not None:
                print("âœ… CHARACTERS ARRAY VERIFICATION TEST:")
                print(f"   ðŸ“Š Current Characters count: {current_count}")
                print(f"   ðŸ”§ JavaScript monitoring: ACTIVE")
                print(f"   âš¡ High-performance access: ENABLED")
                print(f"   ðŸŽ¯ Status: Ready for drag verification!")
            else:
                print("âŒ CHARACTERS ARRAY VERIFICATION TEST FAILED:")
                print("   Could not get Characters count")
                print("   Game may not be loaded or VM not found")
                    
        except Exception as e:
            messagebox.showerror("Verification Test", f"âŒ Characters Array test error: {e}")

    def show_smart_dual_tracking_status(self):
        """Debug: Show current SMART DUAL tracking status"""
        try:
            print("\n🎯 SMART DUAL TRACKING STATUS:")
            print("="*50)
            print(f"Working Systems State:")
            print(f"   📢 Characters Array: {self.working_systems.get('characters', 'None')}")
            print(f"   🎯 Putted Characters: {self.working_systems.get('putted', 'None')}")
            
            available_systems = []
            if self.characters_available: available_systems.append("Characters Array")
            if self.putted_chars_available: available_systems.append("Putted Characters")
            
            print(f"Available Systems: {', '.join(available_systems) if available_systems else 'None'}")
            print(f"Last Operation Working Systems: {len(self.last_operation_working_systems)} stored")
            print("="*50)
            
            messagebox.showinfo("SMART DUAL Tracking", 
                f"📢 Characters: {self.working_systems.get('characters', 'None')}\n"
                f"🎯 Putted: {self.working_systems.get('putted', 'None')}\n\n"
                f"Available: {', '.join(available_systems) if available_systems else 'None'}")
                
        except Exception as e:
            messagebox.showerror("Tracking Status Error", f"❌ Error: {e}")

    def test_smart_verification_system(self):
            """Test Smart Verification System"""
            try:
                if not hasattr(self, 'page_verification_enabled'):
                    messagebox.showinfo("Smart Verification Test", 
                        "ðŸ” Smart Verification system not initialized yet.\n\n"
                        "Load a JSON file and start the workflow to initialize verification.")
                    return
                
                # Test WebSocket connection first
                ws_url = self.browser_manager._get_websocket_url()
                if not ws_url:
                    messagebox.showerror("Smart Verification Test", 
                        "âŒš WebSocket connection not available.\n\n"
                        "Make sure Chrome debug mode is running.")
                    return
                
                # Test page verification
                print("ðŸ” Testing Smart Page Verification...")
                page_result = self.browser_manager.verify_page_fully_loaded("test")
                
                # Test game verification  
                print("ðŸŽ® Testing Smart Game Verification...")
                game_result = self.browser_manager.verify_game_components_ready()
                
                # Show results
                status_page = "âœ… PASSED" if page_result else "âš ï¸ FAILED"
                status_game = "âœ… PASSED" if game_result else "âš ï¸ FAILED"
                
                print("ðŸ” SMART VERIFICATION SYSTEM TEST RESULTS:")
                print("="*50)
                print(f"   ðŸ”„ Page Verification: {status_page}")
                print(f"   ðŸŽ® Game Verification: {status_game}")
                print(f"\nâš™ï¸ Configuration:")
                print(f"   ðŸ“„ Page verification: {'âœ… Enabled' if self.page_verification_enabled else 'âŒ Disabled'}")
                print(f"   ðŸŽ® Game verification: {'âœ… Enabled' if self.game_verification_enabled else 'âŒ Disabled'}")
                print(f"   â±ï¸ Page timeout: {getattr(self, 'page_verification_timeout', 'N/A')}s")
                print(f"   â±ï¸ Game timeout: {getattr(self, 'game_verification_timeout', 'N/A')}s")
                print(f"   ðŸ”„ Stabilization wait: {getattr(self, 'stabilization_wait', 'N/A')}s")
                print(f"\nðŸš€ Smart Verification ready to replace fixed 40s waits!")
                print("="*50)
                        
            except Exception as e:
                messagebox.showerror("Smart Verification Test", f"âŒš Test error: {e}")

    def display_remix_library_info(self):
        info_frame = Frame(self.root)
        info_frame.pack(pady=10)
        
        Label(info_frame, text="ðŸŽ¼ Remix Library:", font=("Arial", 11, "bold")).pack()
        
        if not self.remix_library:
            remix_summary = "ðŸ“‹ Will generate library when loading JSON with ENHANCED workflow"
        else:
            remix_summary = f"ðŸ“‹ {len(self.remix_library)} remixes with ENHANCED verification\n"
            for i, remix in enumerate(self.remix_library):
                remix_summary += f"{i+1}. {remix['name']}: {remix['description']}\n"
        
        self.remix_display_var = StringVar()
        self.remix_display_var.set(remix_summary)
        
        Label(info_frame, textvariable=self.remix_display_var, justify="left", 
              font=("Arial", 9), fg="blue", wraplength=550).pack(pady=5)
    
    def update_remix_display(self):
        if hasattr(self, 'remix_display_var'):
            if self.remix_library:
                remix_summary = f"ðŸ“‹ {len(self.remix_library)} remixes with ENHANCED verification\n"
                for i, remix in enumerate(self.remix_library):
                    remix_summary += f"{i+1}. {remix['name']}: {remix['description']}\n"
                self.remix_display_var.set(remix_summary)
    
    def load_config_and_start(self):
            """Load config and start enhanced auto process"""
            try:
                file_path = filedialog.askopenfilename(
                    title="Choose JSON configuration file",
                    filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
                )
                
                if not file_path:
                    return
                
                # Load JSON
                with open(file_path, 'r', encoding='utf-8') as f:
                    self.config = json.load(f)
                
                # Basic validation
                if 'currentUrl' not in self.config or 'layout' not in self.config:
                    raise ValueError("Invalid JSON structure")
                
                layout = self.config['layout']
                if 'icons' not in layout or 'slots' not in layout:
                    raise ValueError("Missing icons or slots in layout")
                
                # Check domain support
                current_url = self.config.get('currentUrl', '').lower()
                if 'cocrea.world' not in current_url:
                    print(f"âŒ DOMAIN ERROR: This version is optimized for 'cocrea.world' only!")
                    print(f"   Current URL: {current_url}")
                    print("   ðŸ’¡ Please use a cocrea.world game URL")
                    return
                
                # Check hasHorror setting for remix generation
                game_settings = self.config.get('gameSettings', {})
                has_horror = game_settings.get('hasHorror', False)
                
                # Generate remix library with horror-aware logic
                self.remix_library = self.generate_random_remixes(avoid_horror_icon=has_horror)
                self.current_remix_index = 0
                self.drag_stats = DragOperationStats()
                self.update_remix_display()

                # Check domain support
                current_url = self.config.get('currentUrl', '').lower()

                # Enhanced confirmation with horror info and verification status
                horror_info = ""
                if has_horror:
                    horror_info = f"ðŸ‘» Horror Mode: YES (Icon 19 avoided in normal remixes)\n"
                else:
                    horror_info = f"ðŸŽª Horror Mode: NO (All 20 icons available)\n"
                
                # Confirmation
                print(f"ðŸŽ¯ ENHANCED WORKFLOW READY:")
                print(f"   ðŸŽ® Icons: {len(layout['icons'])}")
                print(f"   ðŸŽª Slots: {len(layout['slots'])}")  
                print(f"   ðŸŽ¼ Remixes: {len(self.remix_library)}")
                print(f"   {horror_info}")
                print(f"   âš¡ ENHANCED FEATURES:")
                print(f"      â€¢ Chrome Debug Mode (port {self.debugging_port})")
                print(f"      â€¢ Drag Verification via 'Putted characters'")
                print(f"      â€¢ 5-Coordinate Retry System")
                print(f"      â€¢ WebSocket/CDP for advanced control")
                print(f"      â€¢ PyAutoGUI with smart fallback")
                print(f"      â€¢ Real-time game state monitoring")
                print(f"      â€¢ Enhanced statistics tracking") 
                print(f"      â€¢ Horror-aware remix generation")
                print(f"âœ… AUTO-STARTING ENHANCED WORKFLOW...")

                # Auto-start without user confirmation
                self.start_full_auto_process()
                
            except Exception as e:
                print(f"âŒ CONFIG LOAD ERROR: {str(e)}")
                print("   ðŸ’¡ Please check JSON file format and try again")        
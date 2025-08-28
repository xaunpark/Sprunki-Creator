# gui_interface.py
"""
GUI Interface for Sprunki Game Player.
Handles all Tkinter window creation, widget layout, and event handling.
Interacts with the main player instance to trigger actions and display status.
"""
import json
from tkinter import Tk, Button, Label, StringVar, messagebox, filedialog, Frame, Radiobutton, IntVar
from tkinter.ttk import Progressbar

class GameGUI:
    def __init__(self, player_instance):
        """
        Khởi tạo giao diện người dùng.
        """
        print("🎨 GameGUI initialized.")
        self.player = player_instance
        self.root = Tk()
        self.player.root = self.root

        # Gán lại master cho các biến đã được tạo
        self.player.mode.set_master(self.root)
        self.player.status_var.set_master(self.root)
        self.player.action_var.set_master(self.root)

        # Sử dụng các biến đã có sẵn từ player
        self.mode_var = self.player.mode
        self.status_var = self.player.status_var
        self.action_var = self.player.action_var
        
        self.remix_display_var = StringVar(value="ðŸ“‹ Will generate library when loading JSON...")
        
        self.stop_button = None
        self.progress_bar = None
        
        self.setup_widgets()

    def run(self):
        """Chạy vòng lặp chính của GUI và báo hiệu sẵn sàng."""
        # Báo hiệu rằng GUI đã sẵn sàng TRƯỚC KHI chạy mainloop
        self.player.gui_ready_event.set()
        self.root.mainloop()
        
    def setup_widgets(self):
        self.root.title("Sprunki Game Player - ENHANCED (Modular Architecture)")
        self.root.geometry("650x900")
        
        # Header
        Label(self.root, text="Sprunki Game Player", font=("Arial", 16, "bold")).pack(pady=10)
        Label(self.root, text="ðŸŽ® Modular Architecture: Logic separated from GUI ðŸš€", 
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
        
        Label(detection_frame, text="ðŸ–¼ï¸  ENHANCED IMAGE DETECTION:", 
            font=("Arial", 10, "bold"), bg="#FFF3E0", fg="#E65100").pack(anchor="w", padx=10, pady=(5,2))
        
        detection_text = """ðŸŽ¯ Smart Auto-Scroll: Automatic PageDown if Run/Fullscreen buttons not in viewport
        ðŸ”  Multi-Scale Detection: Test multiple scale factors for button recognition
        ðŸŽª Domain-Optimized: Specific adjustments for cocrea.world game timing
        ðŸ”  Template Matching: Advanced CV2 image matching with confidence thresholds
        ðŸ”„ Retry Logic: Multiple attempts with different scroll positions
        âš™ï¸  Config-Driven: All detection parameters configurable via config.json"""
        
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
        
        # Debug frame
        debug_frame = Frame(self.root, bg="#E3F2FD", relief="ridge", bd=1)
        debug_frame.pack(fill="x", padx=10, pady=5)
        
        Label(debug_frame, text="ðŸ”§ DEBUG TOOLS:", 
            font=("Arial", 10, "bold"), bg="#E3F2FD", fg="#1565C0").pack(anchor="w", padx=10, pady=(5,2))
        
        debug_buttons = Frame(debug_frame, bg="#E3F2FD")
        debug_buttons.pack(fill="x", padx=10, pady=(0,5))
        
        Button(debug_buttons, text="ðŸ“Š Enhanced Stats", command=self.show_drag_stats, bg="#4CAF50", fg="white", font=("Arial", 9)).pack(side="left")
        Button(debug_buttons, text="ðŸŒ  Test WebSocket", command=self.test_websocket_connection, bg="#FF9800", fg="white", font=("Arial", 9)).pack(side="left", padx=(5,0))
        Button(debug_buttons, text="ðŸ”¢ Test Characters Array", command=self.test_verification_system, bg="#9C27B0", fg="white", font=("Arial", 9)).pack(side="left", padx=(5,0))
        Button(debug_buttons, text="âš¡ Test Smart Verification", command=self.test_smart_verification_system, bg="#2196F3", fg="white", font=("Arial", 9)).pack(side="left", padx=(5,0))
        Button(debug_buttons, text="🎯 DUAL Tracking", command=self.show_smart_dual_tracking_status, bg="#FF5722", fg="white", font=("Arial", 9)).pack(side="left", padx=(5,0))
        
        # Remix info display - Gọi hàm display
        self.display_remix_library_info()
        
        # Main button
        Button(self.root, text="ðŸ”¢ Load JSON & Start ENHANCED Auto", 
            command=self.load_config_and_start, 
            bg="#4CAF50", fg="white", font=("Arial", 12, "bold")).pack(pady=15)
        
        # Status
        Label(self.root, text="ðŸ“Š Status:").pack(pady=(20,5))
        Label(self.root, textvariable=self.status_var, fg="blue").pack()
        
        # Control buttons
        self.stop_button = Button(self.root, text="ðŸ›‘ Stop Enhanced Auto", 
                            command=self.player.stop_all, state="disabled",
                            bg="#f44336", fg="white", font=("Arial", 12, "bold"))
        self.stop_button.pack(pady=10)
        self.player.stop_btn = self.stop_button # Gán lại cho player
        
        # Progress
        Label(self.root, text="ðŸ“ˆ Progress:").pack(pady=(10,5))
        self.progress_bar = Progressbar(self.root, length=500, mode='determinate')
        self.progress_bar.pack(pady=5)
        self.player.progress = self.progress_bar # Gán lại cho player
        
        # Current action
        Label(self.root, textvariable=self.action_var, fg="green", 
            font=("Arial", 10, "bold")).pack(pady=5)
        
        # Domain support
        domains_text = f"ðŸŒ  Enhanced for: cocrea.world (Chrome Debug + Characters Array Verification)"
        Label(self.root, text=domains_text, fg="gray", font=("Arial", 9)).pack(pady=5)
        
        # Hotkey
        Label(self.root, text="âŒ¨ï¸  ESC = Emergency stop", 
            fg="red", font=("Arial", 10, "bold")).pack(pady=5)
            
    def show_drag_stats(self):
        """Show enhanced drag statistics"""
        stats = self.player.game_operations.get_drag_statistics()
        messagebox.showinfo("Enhanced Drag Statistics", stats)

    def test_websocket_connection(self):
        """Test WebSocket connection to Chrome debug"""
        try:
            ws_url = self.player.browser_manager._get_websocket_url()
            if ws_url:
                from websocket import create_connection
                ws = create_connection(ws_url, timeout=5)
                test_response = self.player.browser_manager._send_cdp_command(ws, {
                    "method": "Runtime.evaluate", 
                    "params": {"expression": "window.location.href", "returnByValue": True}
                })
                ws.close()
                if test_response:
                    current_url = test_response.get('result', {}).get('result', {}).get('value', 'Unknown')
                    messagebox.showinfo("WebSocket Test", 
                        f"âœ… WEBSOCKET TEST SUCCESSFUL:\n\n"
                        f"Debug Port: {self.player.browser_manager.debugging_port}\n"
                        f"Current URL: {current_url}\n"
                        f"CDP commands working correctly.")
                else:
                    messagebox.showwarning("WebSocket Test", "WebSocket connected but CDP commands failed.")
            else:
                messagebox.showerror("WebSocket Test", 
                    "â Œ Could not establish WebSocket connection.\nMake sure Chrome debug mode is running.")
        except Exception as e:
            messagebox.showerror("WebSocket Test", f"â Œ WebSocket test failed: {e}")

    def test_verification_system(self):
        """Test Characters Array verification system"""
        try:
            vs = self.player.verification_system
            if not vs.characters_setup_done:
                messagebox.showinfo("Verification Test", 
                    "ðŸ”¢ Characters Array system not setup yet.\nLoad a JSON and start workflow to initialize.")
                return
            if not vs.characters_available:
                messagebox.showwarning("Verification Test", "âš ï¸  Characters Array system is disabled.")
                return
            
            current_count = vs.get_characters_count()
            if current_count is not None:
                messagebox.showinfo("Verification Test",
                    f"âœ… CHARACTERS ARRAY VERIFICATION TEST:\n\n"
                    f"Current Count: {current_count}\n"
                    f"Status: Ready for drag verification!")
            else:
                messagebox.showerror("Verification Test", "Could not get Characters count.")
        except Exception as e:
            messagebox.showerror("Verification Test", f"â Œ Characters Array test error: {e}")

    def show_smart_dual_tracking_status(self):
        """Debug: Show current SMART DUAL tracking status"""
        try:
            player = self.player
            vs = self.player.verification_system
            
            available_systems = []
            if vs.characters_available: available_systems.append("Characters Array")
            if vs.putted_chars_available: available_systems.append("Putted Characters")
            
            messagebox.showinfo("SMART DUAL Tracking", 
                f"📢 Characters: {player.working_systems.get('characters', 'None')}\n"
                f"🎯 Putted: {player.working_systems.get('putted', 'None')}\n\n"
                f"Available Systems: {', '.join(available_systems) if available_systems else 'None'}")
        except Exception as e:
            messagebox.showerror("Tracking Status Error", f"❌ Error: {e}")

    def test_smart_verification_system(self):
        """Test Smart Verification System"""
        try:
            bm = self.player.browser_manager
            messagebox.showinfo("Smart Verification Test",
                f"ðŸ”  This test will now run in the console.\n"
                f"Please check the terminal output for detailed results.")
            
            print("ðŸ”  Testing Smart Page Verification...")
            page_result = bm.verify_page_fully_loaded("test")
            print("ðŸŽ® Testing Smart Game Verification...")
            game_result = bm.verify_game_components_ready()
            
            status_page = "âœ… PASSED" if page_result else "âš ï¸  FAILED"
            status_game = "âœ… PASSED" if game_result else "âš ï¸  FAILED"
            print("ðŸ”  SMART VERIFICATION SYSTEM TEST RESULTS:")
            print(f"   Page Verification: {status_page}\n   Game Verification: {status_game}")
        except Exception as e:
            messagebox.showerror("Smart Verification Test", f"âŒš Test error: {e}")

    def display_remix_library_info(self):
        info_frame = Frame(self.root)
        info_frame.pack(pady=10)
        
        Label(info_frame, text="ðŸŽ¼ Remix Library:", font=("Arial", 11, "bold")).pack()
        
        self.update_remix_display()
        
        Label(info_frame, textvariable=self.remix_display_var, justify="left", 
              font=("Arial", 9), fg="blue", wraplength=550).pack(pady=5)
    
    def update_remix_display(self):
        if self.player.remix_library:
            remix_summary = f"ðŸ“‹ {len(self.player.remix_library)} remixes loaded.\n"
            for i, remix in enumerate(self.player.remix_library[:3]): # Show first 3
                remix_summary += f"{i+1}. {remix['name']}: {remix['description']}\n"
            if len(self.player.remix_library) > 3:
                remix_summary += "..."
            self.remix_display_var.set(remix_summary)
        else:
            self.remix_display_var.set("ðŸ“‹ Will generate library when loading JSON...")

    def load_config_and_start(self):
        """Load config and start enhanced auto process in a new thread."""
        import threading

        def start_logic():
            try:
                # Logic tải config vẫn chạy ở đây
                file_path = filedialog.askopenfilename(
                    title="Choose JSON configuration file",
                    filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
                )
                if not file_path:
                    return
                
                with open(file_path, 'r', encoding='utf-8') as f:
                    self.player.config = json.load(f)
            
                if 'currentUrl' not in self.player.config or 'layout' not in self.player.config:
                    raise ValueError("Invalid JSON structure")
                
                current_url = self.player.config.get('currentUrl', '').lower()
                if 'cocrea.world' not in current_url:
                    messagebox.showerror("Domain Error", "This version is optimized for 'cocrea.world' only!")
                    return
                
                game_settings = self.player.config.get('gameSettings', {})
                has_horror = game_settings.get('hasHorror', False)
                
                self.player.remix_library = self.player.generate_random_remixes(avoid_horror_icon=not has_horror)
                self.update_remix_display()

                print(f"ðŸŽ¯ ENHANCED WORKFLOW READY: Auto-starting...")
                self.player.start_full_auto_process()
            
            except Exception as e:
                messagebox.showerror("Config Load Error", f"Failed to load or start: {e}")
        
        # Chạy toàn bộ logic trong một luồng riêng để không làm treo GUI
        logic_thread = threading.Thread(target=start_logic, daemon=True)
        logic_thread.start()                
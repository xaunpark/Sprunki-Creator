# verification_system.py
"""
Verification System for Sprunki Game Player.
Handles all game state verification using the Chrome DevTools Protocol.
- Dual Verification: Characters Array + Putted Characters.
- Batch Session Management for high-performance state checks.
- Smart tracking of working verification methods.
"""
import websocket
import time
import json
import random

class VerificationSystem:
    def __init__(self, browser_manager):
        """
        Khởi tạo VerificationSystem.
        :param browser_manager: Một instance của BrowserManager để tương tác với trình duyệt.
        """
        print("🧠 VerificationSystem initialized.")
        self.browser_manager = browser_manager
        
        # Trạng thái của hệ thống xác thực
        self.characters_setup_done = False
        self.characters_available = False
        self.putted_chars_setup_done = False
        self.putted_chars_available = False
        
        # Trạng thái session hàng loạt (batch session)
        self.batch_verification_active = False
        self.session_websocket = None
        self.session_websocket_url = None
        self.verification_functions_ready = False
        self.last_health_check = 0
        self.health_check_interval = 30  # Giây
        self.session_start_time = None

    def _get_websocket_url(self):
        """Helper để lấy URL WebSocket từ BrowserManager."""
        return self.browser_manager._get_websocket_url()

    def _send_cdp_command(self, ws, command):
        """Helper để gửi lệnh CDP qua BrowserManager."""
        return self.browser_manager._send_cdp_command(ws, command)

    # ================================================================
    # == NEW: DRAG VERIFICATION SYSTEM ==
    # ================================================================

    def setup_dual_verification_system(self):
        """
        ðŸ”„ DUAL VERIFICATION: Setup cáº£ Characters Array + Putted Characters
        Chá»‰ cáº§n 1 trong 2 hoáº¡t Ä‘á»™ng lÃ  Ä‘á»§
        """
        try:
            print("ðŸ”„ Setting up DUAL verification system...")
            
            ws_url = self.browser_manager._get_websocket_url()
            if not ws_url:
                print("âš ï¸ WebSocket not available, dual verification disabled")
                self.characters_available = False
                self.putted_chars_available = False
                return False
            
            # Setup JavaScript cho Cáº¢ HAI systems
            dual_setup_js = '''
            (() => {
                console.log("ðŸš€ Setting up DUAL verification system...");
                
                // VM Finder
                function findVMInWindow(win) {
                    if (!win) return null;
                    if (win.__vm?.runtime) return win.__vm;
                    if (win.Scratch?.vm?.runtime) return win.Scratch.vm;
                    if (win.vm?.runtime) return win.vm;
                    
                    function getReactKey(el){ return Object.keys(el).find(k => k.startsWith('__react')); }
                    const selectors = ['canvas', '.ccw-stage-wrapper', '#stage-canvas-wrapper', '.gandi_stage_stage_1fD7k'];
                    for (const selector of selectors) {
                        const nodes = win.document?.querySelectorAll?.(selector) || [];
                        for (const el of nodes) {
                            const fiberKey = getReactKey(el);
                            if (!fiberKey) continue;
                            let fiber = el[fiberKey];
                            let hop = 0;
                            while (fiber && hop++ < 200) {
                                const props = fiber.pendingProps || fiber.memoizedProps || fiber.props;
                                if (props?.vm?.runtime) return props.vm;
                                fiber = fiber.return;
                            }
                        }
                    }
                    return null;
                }
                
                const vm = findVMInWindow(window);
                if (!vm) {
                    console.error("âŒ Cannot find VM for dual verification");
                    return { characters: false, putted: false };
                }
                
                const runtime = vm.runtime || vm;
                const stage = (runtime.targets || []).find(t => t.isStage) || (runtime.targets || [])[0];
                if (!stage) {
                    console.error("âŒ Cannot find Stage for dual verification");
                    return { characters: false, putted: false };
                }
                
                // Store VM vÃ  Stage
                window.__dual_vm = vm;
                window.__dual_stage = stage;
                
                // Helper functions
                function getListByName(name) {
                    for (const id in stage.variables) {
                        const v = stage.variables[id];
                        if (v?.name === name && Array.isArray(v.value)) return v;
                    }
                    if (stage.lists) {
                        for (const id in stage.lists) {
                            const L = stage.lists[id];
                            if (L?.name === name) return L;
                        }
                    }
                    return null;
                }
                
                function getVarByName(name) {
                    for (const id in stage.variables) {
                        const v = stage.variables[id];
                        if (v?.name === name) return v;
                    }
                    return null;
                }
                
                let charactersSuccess = false;
                let puttedSuccess = false;
                
                // SYSTEM 1: Characters Array
                try {
                    window.getCharactersCount = () => {
                        try {
                            const entry = getListByName("Characters") || getVarByName("Characters");
                            const raw = Array.isArray(entry?.value) ? entry.value.slice() : [];
                            const normalized = raw.map(v => (v === 1 ? "1" : v === 0 ? "0" : String(v)));
                            return normalized.filter(v => v === "1").length;
                        } catch (e) {
                            console.error("Error getting Characters count:", e);
                            return null;
                        }
                    };
                    
                    const testChars = window.getCharactersCount();
                    if (testChars !== null) {
                        charactersSuccess = true;
                        console.log("âœ… Characters Array system ready:", testChars);
                    }
                } catch (e) {
                    console.warn("âš ï¸ Characters Array setup failed:", e);
                }
                
                // SYSTEM 2: Putted Characters (from old code)
                try {
                    const puttedVar = getVarByName("Putted characters");
                    if (puttedVar) {
                        window.__putted_var_id = puttedVar.id;
                        
                        window.getPuttedChars = () => {
                            try {
                                return window.__dual_vm.runtime.getTargetForStage().variables[window.__putted_var_id].value;
                            } catch (e) {
                                console.error("Error getting Putted characters:", e);
                                return null;
                            }
                        };
                        
                        const testPutted = window.getPuttedChars();
                        if (testPutted !== null) {
                            puttedSuccess = true;
                            console.log("âœ… Putted Characters system ready:", testPutted);
                        }
                    }
                } catch (e) {
                    console.warn("âš ï¸ Putted Characters setup failed:", e);
                }
                
                console.log("ðŸ”„ DUAL VERIFICATION SETUP RESULT:");
                console.log("   Characters Array:", charactersSuccess ? "âœ… Ready" : "âŒ Failed");
                console.log("   Putted Characters:", puttedSuccess ? "âœ… Ready" : "âŒ Failed");
                
                return {
                    characters: charactersSuccess,
                    putted: puttedSuccess,
                    hasBackup: charactersSuccess || puttedSuccess
                };
            })();
            '''
            
            ws = websocket.create_connection(ws_url, timeout=30)
            
            response = self.browser_manager._send_cdp_command(ws, {
                "method": "Runtime.evaluate",
                "params": {"expression": dual_setup_js, "returnByValue": True}
            })
            
            ws.close()
            
            if response and 'result' in response:
                result = response.get('result', {}).get('result', {}).get('value', {})
                
                self.characters_available = result.get('characters', False)
                self.putted_chars_available = result.get('putted', False)
                has_backup = result.get('hasBackup', False)
                
                if has_backup:
                    print("âœ… DUAL verification system setup successful!")
                    print(f"   Characters Array: {'âœ… Ready' if self.characters_available else 'âŒ Failed'}")
                    print(f"   Putted Characters: {'âœ… Ready' if self.putted_chars_available else 'âŒ Failed'}")
                    
                    # Test cáº£ hai systems
                    if self.characters_available:
                        test_chars = self.get_characters_count()
                        print(f"   ðŸ§ª Characters test: {test_chars}")
                    
                    if self.putted_chars_available:
                        test_putted = self.get_putted_chars_value()
                        print(f"   ðŸ§ª Putted test: {test_putted}")
                    
                    self.characters_setup_done = True
                    self.putted_chars_setup_done = True
                    return True
                else:
                    print("âŒ DUAL verification setup failed - no systems available")
                    return False
            else:
                print("âŒ No response from dual verification setup")
                return False
                
        except Exception as e:
            print(f"âŒ Error setting up dual verification: {e}")
            self.characters_available = False
            self.putted_chars_available = False
            return False

    def start_batch_verification_session(self):
        """
        ðŸš€ NEW: Start batch verification session with persistent WebSocket (FIXED LOGIC)
        """
        try:
            print("ðŸš€ STARTING BATCH VERIFICATION SESSION...")
            print("="*60)
            
            # Get WebSocket URL
            ws_url = self.browser_manager._get_websocket_url()
            if not ws_url:
                print("âŒ Cannot get WebSocket URL for batch session")
                return False
            
            # Create persistent WebSocket connection
            self.session_websocket = websocket.create_connection(ws_url, timeout=10)
            # ðŸ”§ NEW: Set default timeout for operations
            self.session_websocket.settimeout(5)  # 5 second default timeout
            self.session_websocket_url = ws_url
            self.session_start_time = time.time()
            
            # ðŸ”§ FIX: Set active BEFORE testing to allow _send_batch_cdp_command to work
            self.batch_verification_active = True
            
            print(f"âœ… Batch WebSocket connected: {ws_url}")
            
            # Test connection with active flag set
            print("ðŸ” Testing batch WebSocket connection...")
            test_response = self._send_batch_cdp_command({
                "method": "Runtime.evaluate",
                "params": {"expression": "window.location.href", "returnByValue": True}
            })
            
            if not test_response:
                print("âŒ Batch WebSocket test failed - trying simple test...")
                
                # Try simpler test
                simple_test = self._send_batch_cdp_command({
                    "method": "Runtime.evaluate",
                    "params": {"expression": "true", "returnByValue": True}
                })
                
                if not simple_test:
                    print("âŒ Even simple batch test failed")
                    self.stop_batch_verification_session()
                    return False
                else:
                    print("âœ… Simple batch test passed, continuing...")
            else:
                print("âœ… Batch WebSocket test successful")
            
            # Setup verification functions
            success = self._setup_batch_verification_functions()
            if success:
                # Keep batch_verification_active = True (already set above)
                print("ðŸŽ¯ BATCH VERIFICATION SESSION ACTIVE!")
                print(f"   ðŸ“Š Performance: WebSocket reuse enabled")
                print(f"   âš¡ Speed: Pre-warmed verification functions")
                print(f"   ðŸ›¡ï¸ Reliability: Connection health monitoring")
                print("="*60)
                return True
            else:
                print("âŒ Failed to setup batch verification functions")
                self.stop_batch_verification_session()
                return False
                
        except Exception as e:
            print(f"âŒ Error starting batch verification session: {e}")
            self.stop_batch_verification_session()
            return False

    def stop_batch_verification_session(self):
        """
        ðŸ›‘ NEW: Stop batch verification session and cleanup
        """
        try:
            print("\nðŸ›‘ STOPPING BATCH VERIFICATION SESSION...")
            
            if self.session_websocket:
                try:
                    self.session_websocket.close()
                    print("   âœ… Batch WebSocket closed")
                except:
                    print("   âš ï¸ WebSocket close warning (already closed)")
            
            # Reset session state
            self.batch_verification_active = False
            self.session_websocket = None
            self.session_websocket_url = None
            self.verification_functions_ready = False
            self.session_start_time = None
            
            print("âœ… Batch verification session stopped")
            
        except Exception as e:
            print(f"âš ï¸ Error stopping batch session: {e}")
            # Force reset
            self.batch_verification_active = False
            self.session_websocket = None

    def _setup_batch_verification_functions(self):
        """
        âš¡ NEW: Setup verification functions once for entire session
        """
        try:
            print("âš¡ Setting up batch verification functions...")
            
            # Enhanced JavaScript setup for batch operations
            batch_setup_js = '''
            (() => {
                console.log("âš¡ Setting up BATCH verification functions...");
                
                // VM Finder (same as before)
                function findVMInWindow(win) {
                    if (!win) return null;
                    if (win.__vm?.runtime) return win.__vm;
                    if (win.Scratch?.vm?.runtime) return win.Scratch.vm;
                    if (win.vm?.runtime) return win.vm;
                    
                    function getReactKey(el){ return Object.keys(el).find(k => k.startsWith('__react')); }
                    const selectors = ['canvas', '.ccw-stage-wrapper', '#stage-canvas-wrapper', '.gandi_stage_stage_1fD7k'];
                    for (const selector of selectors) {
                        const nodes = win.document?.querySelectorAll?.(selector) || [];
                        for (const el of nodes) {
                            const fiberKey = getReactKey(el);
                            if (!fiberKey) continue;
                            let fiber = el[fiberKey];
                            let hop = 0;
                            while (fiber && hop++ < 200) {
                                const props = fiber.pendingProps || fiber.memoizedProps || fiber.props;
                                if (props?.vm?.runtime) return props.vm;
                                fiber = fiber.return;
                            }
                        }
                    }
                    return null;
                }
                
                const vm = findVMInWindow(window);
                if (!vm) {
                    console.error("âŒ VM not found for batch verification");
                    return { success: false, reason: "vm_not_found" };
                }
                
                const runtime = vm.runtime || vm;
                const stage = (runtime.targets || []).find(t => t.isStage) || (runtime.targets || [])[0];
                if (!stage) {
                    console.error("âŒ Stage not found for batch verification");
                    return { success: false, reason: "stage_not_found" };
                }
                
                // Store VM and Stage globally for batch operations
                window.__batch_vm = vm;
                window.__batch_stage = stage;
                
                // Helper functions for batch operations
                function getListByName(name) {
                    for (const id in stage.variables) {
                        const v = stage.variables[id];
                        if (v?.name === name && Array.isArray(v.value)) return v;
                    }
                    if (stage.lists) {
                        for (const id in stage.lists) {
                            const L = stage.lists[id];
                            if (L?.name === name) return L;
                        }
                    }
                    return null;
                }
                
                function getVarByName(name) {
                    for (const id in stage.variables) {
                        const v = stage.variables[id];
                        if (v?.name === name) return v;
                    }
                    return null;
                }
                
                // BATCH FUNCTION 1: Characters Array count
                window.getBatchCharactersCount = () => {
                    try {
                        const entry = getListByName("Characters") || getVarByName("Characters");
                        const raw = Array.isArray(entry?.value) ? entry.value.slice() : [];
                        const normalized = raw.map(v => (v === 1 ? "1" : v === 0 ? "0" : String(v)));
                        return normalized.filter(v => v === "1").length;
                    } catch (e) {
                        console.error("Error in getBatchCharactersCount:", e);
                        return null;
                    }
                };
                
                // BATCH FUNCTION 2: Putted Characters count
                const puttedVar = getVarByName("Putted characters");
                if (puttedVar) {
                    window.__batch_putted_var_id = puttedVar.id;
                    
                    window.getBatchPuttedChars = () => {
                        try {
                            return window.__batch_vm.runtime.getTargetForStage().variables[window.__batch_putted_var_id].value;
                        } catch (e) {
                            console.error("Error in getBatchPuttedChars:", e);
                            return null;
                        }
                    };
                }
                
                // BATCH FUNCTION 3: Dual state in single call (PERFORMANCE BOOST)
                window.getBatchDualState = () => {
                    try {
                        const charactersCount = window.getBatchCharactersCount();
                        const puttedCount = window.getBatchPuttedChars ? window.getBatchPuttedChars() : null;
                        
                        return {
                            characters: charactersCount,
                            putted: puttedCount,
                            timestamp: Date.now()
                        };
                    } catch (e) {
                        console.error("Error in getBatchDualState:", e);
                        return null;
                    }
                };
                
                // Test all functions
                const testChars = window.getBatchCharactersCount();
                const testPutted = window.getBatchPuttedChars ? window.getBatchPuttedChars() : null;
                const testDual = window.getBatchDualState();
                
                console.log("âš¡ BATCH VERIFICATION FUNCTIONS READY:");
                console.log("   Characters:", testChars);
                console.log("   Putted:", testPutted); 
                console.log("   Dual state:", testDual);
                
                return {
                    success: true,
                    characters_ready: testChars !== null,
                    putted_ready: testPutted !== null,
                    dual_ready: testDual !== null
                };
            })();
            '''
            
            # Execute batch setup
            response = self._send_batch_cdp_command({
                "method": "Runtime.evaluate",
                "params": {"expression": batch_setup_js, "returnByValue": True}
            })
            
            if response and 'result' in response:
                result = response.get('result', {}).get('result', {}).get('value', {})
                
                if result.get('success', False):
                    self.verification_functions_ready = True
                    chars_ready = result.get('characters_ready', False)
                    putted_ready = result.get('putted_ready', False)
                    dual_ready = result.get('dual_ready', False)
                    
                    print(f"   âœ… Batch functions setup successful!")
                    print(f"   ðŸ“Š Characters function: {'âœ…' if chars_ready else 'âŒ'}")
                    print(f"   ðŸ“Š Putted function: {'âœ…' if putted_ready else 'âŒ'}")  
                    print(f"   ðŸ“Š Dual state function: {'âœ…' if dual_ready else 'âŒ'}")
                    
                    return True
                else:
                    reason = result.get('reason', 'unknown')
                    print(f"   âŒ Batch setup failed: {reason}")
                    return False
            else:
                print("   âŒ No response from batch setup")
                return False
                
        except Exception as e:
            print(f"âŒ Error setting up batch functions: {e}")
            return False

    def _send_batch_cdp_command(self, command):
        """
        âš¡ NEW: Send CDP command using persistent batch WebSocket (DEBUG ENHANCED)
        """
        try:
            if not self.session_websocket or not self.batch_verification_active:
                print(f"   ðŸ” Batch command failed: WebSocket={bool(self.session_websocket)}, Active={self.batch_verification_active}")
                return None
            
            command_id = random.randint(1, 1000000)
            command['id'] = command_id
            
            print(f"   ðŸ“¤ Sending batch command: {command['method']}")
            self.session_websocket.send(json.dumps(command))
            
            # Reduced timeout for batch operations (faster)
            max_wait_time = 5  # Increased from 3s for debugging
            start_time = time.time()
            
            while time.time() - start_time < max_wait_time:
                try:
                    # Set a short timeout for individual recv calls
                    self.session_websocket.settimeout(0.5)
                    response_str = self.session_websocket.recv()
                    response_data = json.loads(response_str)
                    
                    print(f"   ðŸ“¥ Received response for command ID: {response_data.get('id', 'NO_ID')}")
                    
                    if response_data.get('id') == command_id:
                        print(f"   âœ… Batch command successful: {command['method']}")
                        return response_data
                        
                except Exception as timeout_e:
                    if "timeout" in str(timeout_e).lower():
                        continue
                    else:
                        print(f"   âš ï¸ WebSocket recv error: {timeout_e}")
                        break
            
            print(f"   â° Batch CDP command timeout after {max_wait_time}s")
            return None
            
        except Exception as e:
            print(f"   âŒ Batch CDP command error: {e}")
            # Mark session as failed for health check
            self.batch_verification_active = False
            return None

    def check_batch_session_health(self):
        """
        ðŸ” NEW: Check batch session health and auto-reconnect if needed
        """
        try:
            current_time = time.time()
            
            # Skip if health check not due
            if current_time - self.last_health_check < self.health_check_interval:
                return True
            
            self.last_health_check = current_time
            
            if not self.batch_verification_active or not self.session_websocket:
                return False
            
            print("ðŸ” Batch session health check...")
            
            # Quick health test
            test_response = self._send_batch_cdp_command({
                "method": "Runtime.evaluate",
                "params": {"expression": "Date.now()", "returnByValue": True}
            })
            
            if test_response:
                session_duration = current_time - (self.session_start_time or current_time)
                print(f"   âœ… Batch session healthy (running {session_duration:.1f}s)")
                return True
            else:
                print("   âŒ Batch session unhealthy - attempting reconnect...")
                return self._attempt_batch_session_reconnect()
                
        except Exception as e:
            print(f"âš ï¸ Health check error: {e}")
            return False

    def _attempt_batch_session_reconnect(self):
        """
        ðŸ”„ NEW: Attempt to reconnect batch session
        """
        try:
            print("ðŸ”„ Attempting batch session reconnect...")
            
            # Stop current session
            self.stop_batch_verification_session()
            time.sleep(1)
            
            # Try to restart
            if self.start_batch_verification_session():
                print("âœ… Batch session reconnected successfully")
                return True
            else:
                print("âŒ Batch session reconnect failed")
                return False
                
        except Exception as e:
            print(f"âŒ Reconnect error: {e}")
            return False

    def get_characters_count(self):
        """
        ðŸ“¢ BATCH OPTIMIZED: Get Characters count via batch session or fallback
        """
        try:
            # TRY BATCH FIRST (FAST PATH)
            if self.batch_verification_active and self.verification_functions_ready:
                if self.check_batch_session_health():
                    response = self._send_batch_cdp_command({
                        "method": "Runtime.evaluate",
                        "params": {"expression": "window.getBatchCharactersCount()", "returnByValue": True}
                    })
                    
                    if response and 'result' in response:
                        count = response.get('result', {}).get('result', {}).get('value')
                        if isinstance(count, (int, float)):
                            return int(count)
            
            # FALLBACK TO INDIVIDUAL (SLOW PATH)
            print("âš ï¸ Batch unavailable, using individual verification")
            return self._get_characters_count_individual()
            
        except Exception as e:
            print(f"âŒ Error in batch characters count: {e}")
            return self._get_characters_count_individual()

    def _get_characters_count_individual(self):
        """
        ðŸ“¢ FALLBACK: Individual Characters count (original method)
        """
        try:
            if not self.characters_available:
                return None
                
            ws_url = self.browser_manager._get_websocket_url()
            if not ws_url:
                return None
            
            ws = websocket.create_connection(ws_url, timeout=3)
            
            response = self.browser_manager._send_cdp_command(ws, {
                "method": "Runtime.evaluate",
                "params": {"expression": "window.getCharactersCount()", "returnByValue": True}
            })
            
            ws.close()
            
            if response and 'result' in response:
                count = response.get('result', {}).get('result', {}).get('value')
                if isinstance(count, (int, float)):
                    return int(count)
            
            return None
            
        except Exception as e:
            print(f"âŒ Error getting individual Characters count: {e}")
            return None
        
    def get_enabled_list_array(self):
        """
        ðŸ” NEW: Get Enabled List array for cross-validation
        Returns: list of strings ["0","1","0"...] or None if error
        """
        try:
            if not self.characters_available:
                return None
                
            ws_url = self.browser_manager._get_websocket_url()
            if not ws_url:
                return None
            
            ws = websocket.create_connection(ws_url, timeout=10)
            
            # JavaScript to get Enabled List array from Stage variables
            js_get_enabled = '''
            (() => {
                try {
                    if (!window.__sprunki_vm) {
                        console.error("VM not found for Enabled List");
                        return null;
                    }
                    
                    const stage = window.__sprunki_vm.runtime.getTargetForStage();
                    if (!stage) {
                        console.error("Stage not found for Enabled List");
                        return null;
                    }
                    
                    // Find Enabled List variable in Stage variables
                    const enabledVariable = Object.values(stage.variables).find(v => v.name === 'Enabled List');
                    
                    if (!enabledVariable) {
                        console.error("Enabled List variable not found in Stage");
                        return null;
                    }
                    
                    console.log("âœ… Found Enabled List array:", enabledVariable.value);
                    return enabledVariable.value;
                    
                } catch (e) {
                    console.error("Error getting Enabled List array:", e);
                    return null;
                }
            })();
            '''
            
            response = self.browser_manager._send_cdp_command(ws, {
                "method": "Runtime.evaluate",
                "params": {"expression": js_get_enabled, "returnByValue": True}
            })
            
            ws.close()
            
            if response and 'result' in response:
                enabled_value = response.get('result', {}).get('result', {}).get('value')
                
                if isinstance(enabled_value, list):
                    print(f"ðŸ” Enabled List retrieved: {enabled_value}")
                    return enabled_value
                else:
                    print(f"âš ï¸ Unexpected Enabled List type: {type(enabled_value)} = {enabled_value}")
                    return None
            else:
                print("âš ï¸ No response when getting Enabled List")
                return None
                
        except Exception as e:
            print(f"âŒ Error getting Enabled List: {e}")
            return None

    def get_putted_chars_value(self):
        """
        ðŸ“¢ BATCH OPTIMIZED: Get Putted Characters via batch session or fallback
        """
        try:
            # TRY BATCH FIRST (FAST PATH)
            if self.batch_verification_active and self.verification_functions_ready:
                if self.check_batch_session_health():
                    response = self._send_batch_cdp_command({
                        "method": "Runtime.evaluate",
                        "params": {"expression": "window.getBatchPuttedChars()", "returnByValue": True}
                    })
                    
                    if response and 'result' in response:
                        value = response.get('result', {}).get('result', {}).get('value')
                        if isinstance(value, (int, float)):
                            return int(value)
            
            # FALLBACK TO INDIVIDUAL (SLOW PATH)  
            print("âš ï¸ Batch unavailable, using individual verification")
            return self._get_putted_chars_individual()
            
        except Exception as e:
            print(f"âŒ Error in batch putted chars: {e}")
            return self._get_putted_chars_individual()

    def _get_putted_chars_individual(self):
        """
        ðŸ“¢ FALLBACK: Individual Putted Characters (original method)
        """
        try:
            if not self.putted_chars_available:
                return None
                
            ws_url = self.browser_manager._get_websocket_url()
            if not ws_url:
                return None
            
            ws = websocket.create_connection(ws_url, timeout=3)
            
            response = self.browser_manager._send_cdp_command(ws, {
                "method": "Runtime.evaluate",
                "params": {"expression": "window.getPuttedChars()", "returnByValue": True}
            })
            
            ws.close()
            
            if response and 'result' in response:
                value = response.get('result', {}).get('result', {}).get('value')
                if isinstance(value, (int, float)):
                    return int(value)
            
            return None
            
        except Exception as e:
            print(f"âŒ Error getting individual putted chars: {e}")
            return None

    def get_dual_counts_batch(self):
        """
        ðŸš€ FIXED: Get BOTH counts with None safety
        """
        try:
            if self.batch_verification_active and self.verification_functions_ready:
                if self.check_batch_session_health():
                    
                    # Force state refresh before getting counts
                    refresh_response = self._send_batch_cdp_command({
                        "method": "Runtime.evaluate",
                        "params": {"expression": "void 0", "returnByValue": True}
                    })
                    
                    # Small delay for state stabilization
                    time.sleep(0.1)
                    
                    response = self._send_batch_cdp_command({
                        "method": "Runtime.evaluate", 
                        "params": {"expression": "window.getBatchDualState()", "returnByValue": True}
                    })
                    
                    if response and 'result' in response:
                        result = response.get('result', {}).get('result', {}).get('value')
                        if result:
                            characters_count = result.get('characters')
                            putted_count = result.get('putted')
                            return characters_count, putted_count
            
            # Fallback to individual calls
            return self.get_dual_counts_individual()
            
        except Exception as e:
            print(f"âŒ Error in batch dual counts: {e}")
            return self.get_dual_counts_individual()

    def get_dual_counts_individual(self):
        """
        ðŸ”„ FALLBACK: Individual dual counts (original method)
        """
        characters_count = self._get_characters_count_individual()
        putted_count = self._get_putted_chars_individual()
        return characters_count, putted_count

    def get_dual_counts(self):
        """
        ðŸ”„ BATCH OPTIMIZED: Get BOTH VALUES using batch optimization
        """
        # Try batch first for maximum performance
        return self.get_dual_counts_batch()
    
    def get_comprehensive_slot_state(self):
        """
        ðŸ“Š NEW: Get comprehensive slot state for verification - UPDATED for Characters Array
        FIXED: None safety checks
        """
        try:
            characters_count = self.get_characters_count()
            
            if characters_count is None:
                print("âš ï¸ Could not get Characters count")
                return None
            
            # Build comprehensive state using Characters Array
            state = {
                'characters_count': characters_count,
                'timestamp': time.time()
            }
            
            print(f"ðŸ“Š Comprehensive state captured:")
            print(f"   Characters count: {characters_count}")
            
            return state
            
        except Exception as e:
            print(f"âŒ Error getting comprehensive state: {e}")
            return None

    def check_slot_needs_return(self, target_slot_index):
        """
        ðŸ” NEW: Pre-flight check - UPDATED for Characters Array
        FIXED: None safety checks
        """
        try:
            print(f"\nðŸ” PRE-FLIGHT CHECK: Slot {target_slot_index}")
            
            state = self.get_comprehensive_slot_state()
            if not state:  # âœ… CHECK None state
                return True, None, "cannot_verify_state"
            
            characters_count = state.get('characters_count', 0)  # âœ… SAFE get with default
            
            if characters_count == 0:
                print(f"   âœ… No characters on slots (count: {characters_count}) - SKIPPING")
                return False, state, "no_characters_placed"
            else:
                print(f"   ðŸŽ¯ Characters on slots (count: {characters_count}) - NEEDS RETURN")
                return True, state, "characters_need_return"
                
        except Exception as e:
            print(f"âŒ Pre-flight check error: {e}")
            return True, None, f"error: {e}"

    def get_active_slots_queue(self):
        """
        ðŸ“‹ NEW: Get smart-ordered queue - UPDATED for Characters Array
        FIXED: Dá»±a vÃ o Characters count thay vÃ¬ Polos array
        Returns: list of slot indices (simplified for Characters Array system)
        """
        try:
            print(f"\nðŸ“‹ BUILDING SMART RETURN QUEUE")
            print("-" * 40)
            
            state = self.get_comprehensive_slot_state()
            if not state:
                print("âš ï¸ Cannot get slot state, using fallback")
                return []
            
            characters_count = state.get('characters_count', 0)
            
            print(f"   ðŸ“Š Characters count: {characters_count}")
            
            if characters_count == 0:
                print(f"   âœ… No characters to return")
                return []
            
            # For Characters Array system, we don't know specific slot indices
            # Just return a simple range based on typical slot usage
            if characters_count > 0:
                # Return slots in reverse order (high â†’ low) for safety
                active_slots_estimated = list(range(min(characters_count, 7) - 1, -1, -1))
                print(f"   ðŸ“‹ Estimated active slots: {active_slots_estimated}")
                print(f"   ðŸ’¡ Reason: Characters Array doesn't track specific slot indices")
                return active_slots_estimated
            
            return []
            
        except Exception as e:
            print(f"âŒ Error building return queue: {e}")
            return []              

    def validate_workflow_completion(self):
        """
        âœ… NEW: Validate workflow completion - UPDATED for Characters Array
        FIXED: Chá»‰ sá»­ dá»¥ng Characters count validation
        Returns: (is_complete, remaining_issues, recommendations)
        """
        try:
            print(f"\nâœ… WORKFLOW COMPLETION VALIDATION")
            print("="*50)
            
            state = self.get_comprehensive_slot_state()
            if not state:
                return False, ["Cannot get final state"], ["Manual verification needed"]
            
            characters_count = state.get('characters_count', 0)
            
            issues = []
            recommendations = []
            
            # Check: Characters count should be 0
            if characters_count != 0:
                issues.append(f"Characters count = {characters_count} (expected: 0)")
                recommendations.append("Run additional return operations")
            else:
                print("âœ… Characters count = 0 (perfect)")
            
            is_complete = len(issues) == 0
            
            print(f"\nðŸ“Š VALIDATION RESULT: {'âœ… COMPLETE' if is_complete else 'âš ï¸ ISSUES FOUND'}")
            if issues:
                print("âŒ Issues found:")
                for issue in issues:
                    print(f"   - {issue}")
                print("ðŸ’¡ Recommendations:")
                for rec in recommendations:
                    print(f"   - {rec}")
            else:
                print("ðŸŽ‰ Characters Array validation passed!")
                print("   âœ… Characters count = 0")
                print("   âœ… All characters returned successfully")
            
            return is_complete, issues, recommendations
            
        except Exception as e:
            print(f"âŒ Validation error: {e}")
            return False, [f"Validation error: {str(e)}"], ["Manual check required"]              